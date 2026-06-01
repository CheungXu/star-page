from __future__ import annotations

import asyncio
import re
from typing import Protocol, runtime_checkable

from app.core.config import get_settings
from app.services.llm.client import create_llm_client
from app.services.llm.types import LlmMessage
from app.services.skills.registry import SkillDefinition, get_skill_registry

# 路由是"选一个词"的轻任务，给较短超时；超时即走关键词兜底，避免拖慢生成。
_ROUTER_TIMEOUT_SECONDS = 12.0
# 路由只需要意图，截断超长 prompt（如含上传资料的长文）以省 token、提速。
_MAX_PROMPT_CHARS = 2000

_ROUTER_SYSTEM_PROMPT = (
    "你是一个网页类型分类器。根据用户的页面需求，从给定候选技能中选出最匹配的一个，"
    "只输出该技能的 key（英文标识）。如果没有任何技能明显匹配，只输出 NONE。"
    "不要输出解释、标点或多余文字。"
)

_ROUTER_USER_TEMPLATE = """候选技能：
{listing}

用户页面需求：
{prompt}

请只输出一个最匹配的 key，或在都不匹配时输出 NONE："""


@runtime_checkable
class SkillSelector(Protocol):
    """技能选择器接口。方案 B 用 LLM 分类；方案 C 可实现基于工具调用的 Agentic 选择器。"""

    async def select(self, prompt: str, skills: list[SkillDefinition]) -> str | None: ...


def _extract_key(raw: str, valid_keys: set[str]) -> str | None:
    """从模型返回里抽取一个合法 key；解析不到则返回 None（含显式 NONE）。"""
    if not raw:
        return None
    for token in re.split(r"[^A-Za-z0-9_\-]+", raw):
        if token in valid_keys:
            return token
    return None


def _keyword_match(prompt: str, skills: list[SkillDefinition]) -> str | None:
    """关键词兜底：按技能 triggers 在需求文本中的命中顺序选第一个。"""
    text = prompt.lower()
    for skill in skills:
        for trigger in skill.triggers:
            if trigger and trigger.lower() in text:
                return skill.key
    return None


class LlmClassifierSelector:
    """用一次轻量、非流式 LLM 调用做技能路由；失败/超时回退关键词匹配，再回退 None。"""

    def __init__(self, router_model: str | None = None) -> None:
        self.router_model = router_model

    async def select(self, prompt: str, skills: list[SkillDefinition]) -> str | None:
        if not skills:
            return None

        clean_prompt = (prompt or "").strip()[:_MAX_PROMPT_CHARS]
        if not clean_prompt:
            return None

        valid_keys = {skill.key for skill in skills}
        try:
            raw = await asyncio.wait_for(
                self._classify(clean_prompt, skills),
                timeout=_ROUTER_TIMEOUT_SECONDS,
            )
            key = _extract_key(raw, valid_keys)
            if key:
                return key
        except Exception:
            # 超时、模型不可用、网络错误等：不阻断生成，降级到关键词兜底。
            pass

        return _keyword_match(clean_prompt, skills)

    async def _classify(self, prompt: str, skills: list[SkillDefinition]) -> str:
        client = create_llm_client(self.router_model)
        listing = "\n".join(
            f"- key: {skill.key} ｜ 名称: {skill.name} ｜ 适用: {skill.description}" for skill in skills
        )
        messages = [
            LlmMessage(role="system", content=_ROUTER_SYSTEM_PROMPT),
            LlmMessage(role="user", content=_ROUTER_USER_TEMPLATE.format(listing=listing, prompt=prompt)),
        ]
        return await client.complete_text(messages, require_content=False)


async def select_skill_for_prompt(prompt: str, router_model: str | None = None) -> str | None:
    """便捷入口：在技能开启且有可用技能时，为给定需求选出一个技能 key（或 None）。"""
    settings = get_settings()
    if not settings.page_skills_enabled:
        return None

    skills = get_skill_registry().list_skills()
    if not skills:
        return None

    selector = LlmClassifierSelector(router_model=router_model or settings.skill_router_model)
    return await selector.select(prompt, skills)
