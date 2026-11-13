#!/usr/bin/env python3
"""探测 config/llm.models.json 中各可用模型在「续写局部编辑（SEARCH/REPLACE）」模式下的真实表现。

设计约束：
1. 数据库纯只读：连接级强制 default_transaction_read_only=on 且在 readonly 事务内抽样真实已发布页面，严禁任何写库。
2. 隐私脱敏：不在终端或 JSON 报告中打印用户需求原文、HTML 原文或模型生成的具体文本片段，仅记录长度、耗时、Token 与结构化命中状态。
3. 真实复刻生成链路：
   - 消息结构与 GenerationService._build_llm_messages 完全一致：
     system(HTML_PAGE_SYSTEM_PROMPT) -> user(历次需求清单) -> assistant(上一版完整 HTML) -> user(本轮续写指令)
   - 直接调用 code/backend/app/services/page_edit.py 的 classify_model_output / apply_page_edits 做真实验证。
4. 并发与耗时控制：按模型维度做独立并发槽位（默认每模型并发 3 路、单请求总超时 180s，兼容 --timeout=120 快速压测），支持 9 模型全并发执行。

用法示例（仓库根目录）：
    code/backend/.venv/bin/python script/probe_page_edit.py
    code/backend/.venv/bin/python script/probe_page_edit.py --only=qwen,doubao --per-model-concurrency=3
    code/backend/.venv/bin/python script/probe_page_edit.py --output=/tmp/page_edit_probe_report.json
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "code" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

CATALOG_PATH = ROOT / "config" / "llm.models.json"
DEFAULT_REPORT_PATH = Path("/tmp/page_edit_probe_report.json")
ENV_FILES = (
    ROOT / "config" / ".env",
    ROOT / "config" / "db.env",
    ROOT / "config" / "llm.env",
    ROOT / "config" / "oss.env",
    ROOT / "config" / "huoshan.env",
    ROOT / "config" / "aliyun.env",
)


def load_env_file(path: Path) -> None:
    """按 script/probe-llm-models.py 的方式加载 env 文件（不覆盖已有环境变量）。"""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_all_envs() -> None:
    for env_path in ENV_FILES:
        load_env_file(env_path)


load_all_envs()

from app.core.config import LlmModelRegistry, get_model_registry, get_settings  # noqa: E402
from app.services.html_sanitizer import extract_html_document  # noqa: E402
from app.services.llm.types import LlmModelConfig  # noqa: E402
from app.services.page_edit import (  # noqa: E402
    ModelOutputKind,
    PageEditResult,
    apply_page_edits,
    classify_model_output,
)

# ---------------------------------------------------------------------------
# 候选局部编辑续写提示词模板（经 9 模型实测后原样迁入 prompt.py）
# ---------------------------------------------------------------------------

HTML_PAGE_EDIT_CONTINUE_PROMPT_TEMPLATE = """请在上面这版 HTML 页面的基础上，按本轮新要求完成修改（未提及的部分保持不变）。

【输出方式：二选一，严禁混用】
请根据本轮改动范围，在以下两种输出方式中任选其一（本轮对系统提示中「只输出完整 HTML」规则做如下特例扩展）：

方式 A —— 局部编辑块（推荐用于小中幅修改：改文案/数字、调局部样式或配色、增删改单个模块/卡片/交互等）
只输出一个或多个如下格式的查找替换块，不要输出未改动的代码，也不要附带任何解释文字或代码块围栏：
<<<<<<< SEARCH
（从上一版 HTML 原文逐字复制的连续片段）
=======
（替换后的新片段）
>>>>>>> REPLACE

局部编辑硬性规则：
1. 三行标记 `<<<<<<< SEARCH`、`=======`、`>>>>>>> REPLACE` 必须完全一致且各自独占一行，严禁缺省、拼写变形、嵌套或只写半截标记。
2. 每个 `SEARCH` 块必须从上一版 HTML 原文中原封不动复制（保留原有缩进、空格、换行与属性顺序），严禁自行缩写或用 `...` / `<!-- 省略 -->` 跳过中间内容。
3. 每个 `SEARCH` 块在上一版 HTML 中必须**唯一命中 1 处**：若目标行在页面中重复出现（如 `</div>`、通用样式或相似卡片），必须连带上下相邻的 2～5 行特征上下文一起复制，确保唯一性；同时避免把大段无关代码塞进同一个块。
4. 需要在某处新增内容（如加一个卡片、一段 CSS 或脚本）时，`SEARCH` 块必须选取插入点紧邻的一小段已有原文作为锚点，在 `REPLACE` 块中保留该锚点并附上新增内容（`SEARCH` 绝不能为空）。
5. 如有多处修改，按在上一版 HTML 中从上到下的顺序依次输出多个编辑块，且各块的 `SEARCH` 范围不要重叠。
6. 若启用了深度思考，请快速定位目标行号与锚点后立即输出编辑块，不要在思考中反复重写未改动段落。

方式 B —— 完整 HTML 页面（用于大幅改版：整体换风格/重做视觉体系、重排大部分版式结构，或改动遍布全页超过一半代码时）
直接输出修改后的完整 HTML 文档（从 `<!doctype html>` 开始到 `</html>` 结束），不要使用 ```html 代码块，更不要混入任何 `<<<<<<< SEARCH` 标记。

【本轮修改要求】
{prompt}"""


def build_edit_continue_prompt(user_prompt: str) -> str:
    return HTML_PAGE_EDIT_CONTINUE_PROMPT_TEMPLATE.format(prompt=user_prompt.strip())


def read_html_page_system_prompt() -> str:
    """优先导入 HTML_PAGE_SYSTEM_PROMPT；若并发修改导致导入波动则直接从源码解析常量。"""
    try:
        from app.services.llm.prompt import HTML_PAGE_SYSTEM_PROMPT

        if isinstance(HTML_PAGE_SYSTEM_PROMPT, str) and HTML_PAGE_SYSTEM_PROMPT.strip():
            return HTML_PAGE_SYSTEM_PROMPT
    except Exception:
        pass

    prompt_file = BACKEND / "app" / "services" / "llm" / "prompt.py"
    source = prompt_file.read_text(encoding="utf-8")
    match = re.search(
        r'HTML_PAGE_SYSTEM_PROMPT\s*=\s*"""(.*?)"""',
        source,
        flags=re.DOTALL,
    )
    if not match:
        raise RuntimeError("无法读取 HTML_PAGE_SYSTEM_PROMPT")
    return match.group(1)


# ---------------------------------------------------------------------------
# 数据结构与抽样定义
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SampledPage:
    tier: str  # short | medium | long
    source: str  # db_model_output | oss_version | fallback_showcase
    page_hash: str  # 脱敏短哈希标识，不暴露真实线上 URL/用户隐私
    html: str
    html_bytes: int
    html_chars: int
    html_lines: int
    history_text: str
    ancestor_turns: int


@dataclass(frozen=True)
class EditProbeCase:
    case_id: str
    tier: str
    kind: str  # micro_copy | local_card_style | full_redesign
    expected_primary_mode: str  # edit_blocks | edit_or_full
    description: str
    instruction: str


CASE_BLUEPRINTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "micro_copy",
        "edit_blocks",
        "小幅文案与数字角标修改",
        (
            "做两处小幅文案与数字微调，其余设计、布局和文案完全不动："
            "1）在浏览器标签页 `<title>` 末尾追加「 · 2026 修订版」；"
            "2）把主标题 `<h1>` 紧邻下方的首句副标题/导语调整得更精炼，并在其中自然加入「支持 99.9% 离线秒开 · 3 分钟快速浏览」这组数字信息。"
        ),
    ),
    (
        "local_card_style",
        "edit_blocks",
        "局部主题点缀色调整 + 底部新增信息小卡片",
        (
            "做两处局部增强，保持页面主体结构与其他模块不变："
            "1）在 `<style>` 中微调页面的主点缀色（accent/高亮按钮或核心徽章的配色），改用更有质感的祖母绿系（`#0d9488` 与 `#14b8a6`），让主按钮与重点标签同步生效；"
            "2）在页面主体末尾（页脚之前，若无页脚则放在主容器最底部）新增一个精巧的「阅读速览」横排信息小卡片，内含 3 项指标：「预计阅读 3 分钟」「离线自包含 100%」「适配双端视口」，并为该卡片补齐配套的响应式普通 CSS 样式。"
        ),
    ),
    (
        "full_redesign",
        "edit_or_full",
        "大幅赛博朋克暗黑风格改版与版式重排",
        (
            "整体改成赛博朋克暗黑风格并重排全部版式：采用深色底配青/品红霓虹点缀，把原有核心内容重组为紧凑精炼的暗黑科技卡片版式（保持代码精炼高质）。"
        ),
    ),
)


# 兜底代表性真实结构展示页（仅在无数据库连接或样本不足 3 个时启用）
FALLBACK_SHOWCASE_PAGES: dict[str, tuple[str, list[str]]] = {
    "short": (
        """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>星屿轻量笔记 · 产品速览</title>
  <style>
    :root { --bg: #f8fafc; --ink: #0f172a; --muted: #475569; --accent: #2563eb; --card: #ffffff; --line: #e2e8f0; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--ink); line-height: 1.65; }
    .container { max-width: 920px; margin: 0 auto; padding: 64px 24px; }
    .hero { text-align: center; margin-bottom: 48px; }
    .badge { display: inline-block; padding: 4px 12px; border-radius: 999px; background: #dbeafe; color: var(--accent); font-size: 13px; font-weight: 600; margin-bottom: 16px; }
    h1 { font-size: clamp(28px, 4vw, 44px); letter-spacing: -0.02em; margin-bottom: 14px; }
    .lead { color: var(--muted); font-size: 18px; max-width: 620px; margin: 0 auto 28px; }
    .cta-btn { display: inline-block; padding: 12px 28px; border-radius: 10px; background: var(--accent); color: #fff; text-decoration: none; font-weight: 600; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 20px; }
    .card { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 24px; }
    .card h3 { font-size: 18px; margin-bottom: 8px; }
    .card p { color: var(--muted); font-size: 14px; }
    footer { margin-top: 48px; text-align: center; color: var(--muted); font-size: 13px; }
  </style>
</head>
<body>
  <main class="container">
    <header class="hero">
      <span class="badge">桌面与移动双端同步</span>
      <h1>把零散灵感整理成可分享的知识卡片</h1>
      <p class="lead">无需繁琐配置，打开即写，支持本地离线归档与一键导出单文件页面。</p>
      <a class="cta-btn" href="#features">了解核心特性</a>
    </header>
    <section id="features" class="grid">
      <article class="card"><h3>毫秒级全文检索</h3><p>内置轻量本地索引，上万条卡片依然即时定位关键段落。</p></article>
      <article class="card"><h3>结构化双向链接</h3><p>自动串联上下文主题脉络，让每一次回顾都有迹可循。</p></article>
      <article class="card"><h3>纯净单页发布</h3><p>一键生成零外部依赖的静态展示页，随时发给团队审阅。</p></article>
    </section>
    <footer>星屿工作室 · 专注高效创作工具</footer>
  </main>
</body>
</html>""",
        ["做一个轻量笔记软件的产品速览单页，包含主视觉区和三个核心卖点卡片。"],
    ),
}


# ---------------------------------------------------------------------------
# 数据库只读抽样（严禁写库）
# ---------------------------------------------------------------------------

BUCKET_SPECS: tuple[tuple[str, int, int, int], ...] = (
    ("short", 5000, 3200, 8500),
    ("medium", 15000, 11500, 19500),
    ("long", 30000, 24500, 36000),
)

SAMPLE_PAGES_QUERY = """
SELECT
    p.id AS page_id,
    p.parent_page_id,
    p.current_version_id,
    p.conversation_id,
    t.model_output_text,
    pv.storage_key
FROM pages p
LEFT JOIN LATERAL (
    SELECT gt.model_output_text
    FROM generation_tasks gt
    WHERE gt.page_id = p.id
      AND gt.status = 'succeeded'
      AND gt.model_output_text IS NOT NULL
      AND btrim(gt.model_output_text) <> ''
    ORDER BY gt.created_at DESC
    LIMIT 1
) t ON TRUE
LEFT JOIN page_versions pv ON pv.id = p.current_version_id
WHERE p.status = 'ready'
  AND p.deleted_at IS NULL
  AND p.moderation_status = 'ok'
  AND p.visibility = 'public'
  AND COALESCE(p.skill_key, '') <> 'resume'
  AND (
      (t.model_output_text IS NOT NULL
       AND octet_length(t.model_output_text) BETWEEN $1 AND $2
       AND position('</html>' IN lower(t.model_output_text)) > 0
       AND position('cdn.tailwindcss.com' IN lower(t.model_output_text)) = 0)
      OR (t.model_output_text IS NULL AND pv.storage_key IS NOT NULL)
  )
ORDER BY
    (t.model_output_text IS NULL) ASC,
    abs(COALESCE(octet_length(t.model_output_text), $3) - $3) ASC,
    p.created_at DESC
LIMIT 25;
"""

ANCESTOR_CHAIN_QUERY = """
WITH RECURSIVE chain AS (
    SELECT p.id, p.parent_page_id, 0 AS depth
    FROM pages p
    WHERE p.id = $1
    UNION ALL
    SELECT parent.id, parent.parent_page_id, chain.depth + 1
    FROM pages parent
    JOIN chain ON parent.id = chain.parent_page_id
    WHERE chain.depth < 25
)
SELECT
    chain.depth,
    COALESCE(gt.user_prompt, gt.prompt) AS instruction
FROM chain
LEFT JOIN LATERAL (
    SELECT user_prompt, prompt
    FROM generation_tasks
    WHERE page_id = chain.id
    ORDER BY created_at DESC
    LIMIT 1
) gt ON TRUE
ORDER BY chain.depth DESC;
"""


def _format_history_text(instructions: list[str]) -> str:
    cleaned = [item.strip() for item in instructions if item and item.strip()]
    if cleaned:
        lines = "\n".join(f"{idx}. {text}" for idx, text in enumerate(cleaned, start=1))
        return "这是该页面到目前为止的历次需求（按先后顺序）：\n" + lines
    return "这是该页面到目前为止的需求。"


def _has_suitable_structure(html: str) -> bool:
    lowered = html.lower()
    return (
        "<html" in lowered
        and "</html>" in lowered
        and "<title" in lowered
        and "<h1" in lowered
        and "<style" in lowered
        and "cdn.tailwindcss.com" not in lowered
    )


async def sample_readonly_pages_from_db() -> list[SampledPage]:
    """从线上 PostgreSQL 纯只读抽样短（~5KB）、中（~15KB）、长（~30KB）三个真实历史页面。"""
    import asyncpg

    dsn = get_settings().async_database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn[len("postgresql+asyncpg://") :]

    conn = await asyncpg.connect(
        dsn=dsn,
        server_settings={"default_transaction_read_only": "on"},
        timeout=15,
    )
    sampled: list[SampledPage] = []
    used_conversations: set[uuid.UUID] = set()
    used_pages: set[uuid.UUID] = set()

    try:
        storage = None
        async with conn.transaction(readonly=True):
            for tier, target_bytes, min_bytes, max_bytes in BUCKET_SPECS:
                rows = await conn.fetch(SAMPLE_PAGES_QUERY, min_bytes, max_bytes, target_bytes)
                chosen: SampledPage | None = None

                for row in rows:
                    page_id: uuid.UUID = row["page_id"]
                    conv_id: uuid.UUID | None = row["conversation_id"]
                    if page_id in used_pages:
                        continue
                    if conv_id is not None and conv_id in used_conversations:
                        continue

                    raw_html: str | None = None
                    source = "db_model_output"
                    if row["model_output_text"]:
                        raw_html = extract_html_document(str(row["model_output_text"]))
                    elif row["storage_key"]:
                        if storage is None:
                            from app.services.storage.factory import create_storage_provider

                            storage = create_storage_provider()
                        try:
                            fetched = await storage.get_text(str(row["storage_key"]))
                            raw_html = extract_html_document(fetched) if fetched else None
                            source = "oss_version"
                        except Exception:
                            raw_html = None

                    if not raw_html or not _has_suitable_structure(raw_html):
                        continue

                    byte_len = len(raw_html.encode("utf-8"))
                    if not (min_bytes <= byte_len <= max_bytes):
                        continue

                    anc_rows = await conn.fetch(ANCESTOR_CHAIN_QUERY, page_id)
                    instructions = [str(r["instruction"]) for r in anc_rows if r["instruction"]]

                    short_hash = hashlib.sha256(str(page_id).encode("utf-8")).hexdigest()[:10]
                    chosen = SampledPage(
                        tier=tier,
                        source=source,
                        page_hash=f"page_{tier}_{short_hash}",
                        html=raw_html,
                        html_bytes=byte_len,
                        html_chars=len(raw_html),
                        html_lines=raw_html.count("\n") + 1,
                        history_text=_format_history_text(instructions),
                        ancestor_turns=max(1, len(instructions)),
                    )
                    used_pages.add(page_id)
                    if conv_id is not None:
                        used_conversations.add(conv_id)
                    break

                if chosen is not None:
                    sampled.append(chosen)
    finally:
        await conn.close()

    # 若个别桶在库中缺失，用本地脱敏展示页补齐
    existing_tiers = {item.tier for item in sampled}
    for tier, _target, _lo, _hi in BUCKET_SPECS:
        if tier in existing_tiers:
            continue
        base_html, base_inst = FALLBACK_SHOWCASE_PAGES["short"]
        sampled.append(
            SampledPage(
                tier=tier,
                source="fallback_showcase",
                page_hash=f"fallback_{tier}",
                html=base_html,
                html_bytes=len(base_html.encode("utf-8")),
                html_chars=len(base_html),
                html_lines=base_html.count("\n") + 1,
                history_text=_format_history_text(base_inst),
                ancestor_turns=len(base_inst),
            )
        )

    tier_order = {"short": 0, "medium": 1, "long": 2}
    sampled.sort(key=lambda item: tier_order.get(item.tier, 99))
    return sampled


def build_probe_cases(pages: list[SampledPage]) -> list[tuple[SampledPage, EditProbeCase]]:
    matrix: list[tuple[SampledPage, EditProbeCase]] = []
    for page in pages:
        for kind, expected_mode, desc, instruction in CASE_BLUEPRINTS:
            case = EditProbeCase(
                case_id=f"{page.tier}_{kind}",
                tier=page.tier,
                kind=kind,
                expected_primary_mode=expected_mode,
                description=f"[{page.tier}] {desc}",
                instruction=instruction,
            )
            matrix.append((page, case))
    return matrix


def build_continuation_messages(
    system_prompt: str,
    page: SampledPage,
    case: EditProbeCase,
) -> list[dict[str, str]]:
    """严格对齐第 2 步续写消息结构：system + user(历次需求) + assistant(上一版完整 HTML) + user(本轮续写提示词)。"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": page.history_text},
        {"role": "assistant", "content": page.html},
        {"role": "user", "content": build_edit_continue_prompt(case.instruction)},
    ]


# ---------------------------------------------------------------------------
# 流式模型调用与分类验证
# ---------------------------------------------------------------------------


def estimate_tokens_from_text(text: str) -> int:
    """当厂商流式未返回 usage 时的保守 token 估算（ASCII 约 4 字符/token，中日韩约 1.5 字符/token）。"""
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    non_ascii = len(text) - ascii_chars
    return max(1, int(round(ascii_chars / 3.8 + non_ascii / 1.5)))


async def call_model_stream_once(
    http_client: httpx.AsyncClient,
    model_cfg: LlmModelConfig,
    messages: list[dict[str, str]],
    chunk_idle_timeout_seconds: float,
    wall_timeout_seconds: float,
) -> dict[str, Any]:
    """执行一次与生产 OpenAICompatibleClient 同构的流式请求（分片空闲超时 + 单请求兜底总墙钟保护）。"""
    body: dict[str, Any] = {
        "model": model_cfg.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        **model_cfg.params,
        **model_cfg.extra_body,
    }
    headers = {
        "Authorization": f"Bearer {model_cfg.api_key}",
        "Content-Type": "application/json",
    }
    url = f"{model_cfg.base_url.rstrip('/')}/chat/completions"

    text_parts: list[str] = []
    reasoning_chars = 0
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    actual_model = model_cfg.model
    first_token_sec: float | None = None

    started = time.perf_counter()
    try:
        async with asyncio.timeout(wall_timeout_seconds):
            async with http_client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    err_bytes = await resp.aread()
                    err_text = err_bytes.decode("utf-8", errors="replace")[:240]
                    return {
                        "http_ok": False,
                        "status_code": resp.status_code,
                        "error": f"HTTP {resp.status_code}: {err_text}",
                        "elapsed_sec": round(time.perf_counter() - started, 2),
                        "first_token_sec": None,
                        "text": "",
                        "reasoning_chars": 0,
                        "finish_reason": None,
                        "input_tokens": None,
                        "output_tokens": None,
                        "reasoning_tokens": None,
                        "actual_model": actual_model,
                    }

                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line.removeprefix("data:").strip()
                    if not payload or payload == "[DONE]":
                        continue
                    chunk = json.loads(payload)
                    actual_model = chunk.get("model") or actual_model
                    choices = chunk.get("choices") or []
                    first_choice = choices[0] if choices else {}
                    if first_choice.get("finish_reason"):
                        finish_reason = str(first_choice["finish_reason"])
                    delta = first_choice.get("delta") or {}

                    r_piece = delta.get("reasoning_content")
                    if r_piece:
                        reasoning_chars += len(r_piece)

                    c_piece = delta.get("content")
                    if c_piece:
                        if first_token_sec is None:
                            first_token_sec = round(time.perf_counter() - started, 2)
                        text_parts.append(c_piece)

                    usage = chunk.get("usage")
                    if usage:
                        input_tokens = usage.get("prompt_tokens")
                        output_tokens = usage.get("completion_tokens")
                        comp_details = usage.get("completion_tokens_details") or {}
                        if comp_details.get("reasoning_tokens") is not None:
                            reasoning_tokens = comp_details.get("reasoning_tokens")
    except TimeoutError:
        return {
            "http_ok": False,
            "status_code": None,
            "error": f"Timeout > {wall_timeout_seconds:.0f}s",
            "elapsed_sec": round(time.perf_counter() - started, 2),
            "first_token_sec": first_token_sec,
            "text": "".join(text_parts),
            "reasoning_chars": reasoning_chars,
            "finish_reason": finish_reason,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "actual_model": actual_model,
        }
    except httpx.TimeoutException:
        return {
            "http_ok": False,
            "status_code": None,
            "error": f"Timeout (chunk idle > {chunk_idle_timeout_seconds:.0f}s)",
            "elapsed_sec": round(time.perf_counter() - started, 2),
            "first_token_sec": first_token_sec,
            "text": "".join(text_parts),
            "reasoning_chars": reasoning_chars,
            "finish_reason": finish_reason,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "actual_model": actual_model,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "http_ok": False,
            "status_code": None,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            "elapsed_sec": round(time.perf_counter() - started, 2),
            "first_token_sec": first_token_sec,
            "text": "".join(text_parts),
            "reasoning_chars": reasoning_chars,
            "finish_reason": finish_reason,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": reasoning_tokens,
            "actual_model": actual_model,
        }

    full_text = "".join(text_parts)
    return {
        "http_ok": True,
        "status_code": 200,
        "error": None,
        "elapsed_sec": round(time.perf_counter() - started, 2),
        "first_token_sec": first_token_sec,
        "text": full_text,
        "reasoning_chars": reasoning_chars,
        "finish_reason": finish_reason,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "actual_model": actual_model,
    }


def evaluate_single_run(
    model_cfg: LlmModelConfig,
    page: SampledPage,
    case: EditProbeCase,
    call_res: dict[str, Any],
) -> dict[str, Any]:
    """用 page_edit.classify_model_output 与 apply_page_edits 验证模型输出，不保留任何隐私文本。"""
    raw_text: str = call_res["text"] or ""
    output_chars = len(raw_text)
    output_bytes = len(raw_text.encode("utf-8"))

    # 区分思维链 token 与可见答案 token：
    # OpenAI 兼容接口中 completion_tokens 常包含 reasoning_tokens，因此同时统计总输出 token 与可见正文估算 token。
    reported_output_tokens = call_res["output_tokens"]
    reported_reasoning_tokens = call_res["reasoning_tokens"] or 0
    if reported_output_tokens is not None and reported_output_tokens >= reported_reasoning_tokens:
        visible_output_tokens = max(0, reported_output_tokens - reported_reasoning_tokens)
    else:
        visible_output_tokens = estimate_tokens_from_text(raw_text)

    total_output_tokens = (
        reported_output_tokens
        if reported_output_tokens is not None
        else visible_output_tokens + estimate_tokens_from_text("x" * call_res["reasoning_chars"])
    )
    base_html_est_tokens = estimate_tokens_from_text(page.html)

    if not call_res["http_ok"]:
        partial_kind = classify_model_output(raw_text).value if raw_text.strip() else "none"
        is_timeout = (call_res["error"] or "").startswith("Timeout")
        # 若流式超时前模型已明确走 FULL_HTML（以 <!doctype 或 <html 开头且无任何编辑块标记），
        # 记录为 full_html + timeout_full_html，避免把整页重写本身慢（>120s）漏统计在 full_html_count 外。
        if is_timeout and partial_kind == ModelOutputKind.FULL_HTML.value:
            effective_kind = ModelOutputKind.FULL_HTML.value
            fail_code = "timeout_full_html"
        elif is_timeout and partial_kind in {ModelOutputKind.EDIT_BLOCKS.value, ModelOutputKind.MALFORMED.value}:
            effective_kind = ModelOutputKind.MALFORMED.value
            fail_code = "timeout_incomplete_edit"
        else:
            effective_kind = "request_error"
            fail_code = "timeout_no_content" if is_timeout else "request_error"

        return {
            "model_key": model_cfg.key,
            "case_id": case.case_id,
            "tier": page.tier,
            "case_kind": case.kind,
            "base_html_bytes": page.html_bytes,
            "base_html_chars": page.html_chars,
            "base_html_est_tokens": base_html_est_tokens,
            "http_ok": False,
            "error": call_res["error"],
            "elapsed_sec": call_res["elapsed_sec"],
            "first_token_sec": call_res["first_token_sec"],
            "finish_reason": call_res["finish_reason"],
            "output_kind": effective_kind,
            "partial_kind_on_error": partial_kind,
            "usable_first_pass": False,
            "edit_apply_ok": None,
            "blocks_total": 0,
            "exact_blocks": 0,
            "line_stripped_blocks": 0,
            "failure_code": fail_code,
            "failed_block_index": None,
            "output_chars": output_chars,
            "output_bytes": output_bytes,
            "reasoning_chars": call_res["reasoning_chars"],
            "input_tokens": call_res["input_tokens"],
            "total_output_tokens": total_output_tokens,
            "reasoning_tokens": reported_reasoning_tokens,
            "visible_output_tokens": visible_output_tokens,
            "result_html_chars": None,
        }

    kind: ModelOutputKind = classify_model_output(raw_text)
    edit_res: PageEditResult | None = None
    exact_blocks = 0
    stripped_blocks = 0
    failure_code: str | None = None
    usable_first_pass = False
    result_html_chars: int | None = None

    if kind == ModelOutputKind.EDIT_BLOCKS:
        edit_res = apply_page_edits(page.html, raw_text)
        for detail in edit_res.applied_blocks:
            if detail.strategy == "exact":
                exact_blocks += 1
            elif detail.strategy == "line_stripped":
                stripped_blocks += 1
        if edit_res.ok and edit_res.html is not None:
            usable_first_pass = True
            result_html_chars = len(edit_res.html)
        else:
            failure_code = edit_res.error_code or "apply_failed"
    elif kind == ModelOutputKind.FULL_HTML:
        extracted = extract_html_document(raw_text)
        has_close = "</html>" in raw_text.lower() and call_res["finish_reason"] != "length"
        if extracted and has_close:
            usable_first_pass = True
            result_html_chars = len(extracted)
        else:
            failure_code = "truncated_full_html"
    else:
        # MALFORMED：进一步调用 apply_page_edits 提取精确错误码（如 malformed_markers / empty_output）
        parsed_probe = apply_page_edits(page.html, raw_text)
        failure_code = parsed_probe.error_code or "malformed_output"

    return {
        "model_key": model_cfg.key,
        "case_id": case.case_id,
        "tier": page.tier,
        "case_kind": case.kind,
        "base_html_bytes": page.html_bytes,
        "base_html_chars": page.html_chars,
        "base_html_est_tokens": base_html_est_tokens,
        "http_ok": True,
        "error": None,
        "elapsed_sec": call_res["elapsed_sec"],
        "first_token_sec": call_res["first_token_sec"],
        "finish_reason": call_res["finish_reason"],
        "output_kind": kind.value,
        "usable_first_pass": usable_first_pass,
        "edit_apply_ok": edit_res.ok if edit_res is not None else None,
        "blocks_total": edit_res.blocks_total if edit_res is not None else 0,
        "exact_blocks": exact_blocks,
        "line_stripped_blocks": stripped_blocks,
        "failure_code": failure_code,
        "failed_block_index": edit_res.failed_block_index if edit_res is not None else None,
        "output_chars": output_chars,
        "output_bytes": output_bytes,
        "reasoning_chars": call_res["reasoning_chars"],
        "input_tokens": call_res["input_tokens"],
        "total_output_tokens": total_output_tokens,
        "reasoning_tokens": reported_reasoning_tokens,
        "visible_output_tokens": visible_output_tokens,
        "result_html_chars": result_html_chars,
    }


# ---------------------------------------------------------------------------
# 聚合统计与 edit_mode 开关决策规则
# ---------------------------------------------------------------------------


def _mean(values: list[float | int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def summarize_model_runs(model_cfg: LlmModelConfig, runs: list[dict[str, Any]]) -> dict[str, Any]:
    total_cases = len(runs)
    edit_runs = [r for r in runs if r["output_kind"] == ModelOutputKind.EDIT_BLOCKS.value]
    full_runs = [r for r in runs if r["output_kind"] == ModelOutputKind.FULL_HTML.value]
    malformed_runs = [r for r in runs if r["output_kind"] == ModelOutputKind.MALFORMED.value]
    error_runs = [r for r in runs if r["output_kind"] == "request_error"]

    edit_ok_runs = [r for r in edit_runs if r["edit_apply_ok"] is True]
    edit_fail_runs = [r for r in edit_runs if r["edit_apply_ok"] is False]

    exact_blocks_sum = sum(int(r["exact_blocks"]) for r in edit_ok_runs)
    stripped_blocks_sum = sum(int(r["line_stripped_blocks"]) for r in edit_ok_runs)
    applied_blocks_sum = exact_blocks_sum + stripped_blocks_sum

    # 也在全部 EDIT_BLOCKS 尝试（含半途失败前已命中的块）上统计总块数
    all_exact_blocks = sum(int(r["exact_blocks"]) for r in edit_runs)
    all_stripped_blocks = sum(int(r["line_stripped_blocks"]) for r in edit_runs)
    all_declared_blocks = sum(int(r["blocks_total"]) for r in edit_runs)

    failure_counter: Counter[str] = Counter()
    for r in runs:
        if r["failure_code"]:
            failure_counter[str(r["failure_code"])] += 1

    # 局部修改专项用例（micro_copy + local_card_style，共 6 条）
    local_cases = [r for r in runs if r["case_kind"] in {"micro_copy", "local_card_style"}]
    local_edit_chosen = [r for r in local_cases if r["output_kind"] == ModelOutputKind.EDIT_BLOCKS.value]
    local_edit_applied = [
        r for r in local_cases if r["output_kind"] == ModelOutputKind.EDIT_BLOCKS.value and r["edit_apply_ok"] is True
    ]
    local_usable = [r for r in local_cases if r["usable_first_pass"]]

    # 大幅改版用例（full_redesign，共 3 条）
    redesign_cases = [r for r in runs if r["case_kind"] == "full_redesign"]
    redesign_usable = [r for r in redesign_cases if r["usable_first_pass"]]

    edit_apply_rate = (len(edit_ok_runs) / len(edit_runs)) if edit_runs else 0.0
    local_edit_success_rate = (len(local_edit_applied) / len(local_cases)) if local_cases else 0.0
    overall_first_pass_rate = (sum(1 for r in runs if r["usable_first_pass"]) / total_cases) if total_cases else 0.0

    # Token 与字符节省比例：对比该用例基准整页 HTML 的 token / 字符规模
    # 1) 可见答案 token 节省比例（直接反映流式输出正文缩短幅度）
    visible_savings_ratios: list[float] = []
    # 2) 总 completion_tokens（含思考）对比整页基准 HTML
    total_token_savings_ratios: list[float] = []
    char_savings_ratios: list[float] = []

    for r in edit_ok_runs:
        base_tok = max(1, int(r["base_html_est_tokens"]))
        vis_tok = int(r["visible_output_tokens"])
        tot_tok = int(r["total_output_tokens"])
        visible_savings_ratios.append(1.0 - (vis_tok / base_tok))
        total_token_savings_ratios.append(1.0 - (tot_tok / base_tok))
        char_savings_ratios.append(1.0 - (int(r["output_chars"]) / max(1, int(r["base_html_chars"]))))

    # 开关决策准则：
    # 1. 局部修改用例（micro_copy + local_card_style，共 6 条）一次成功应用 EDIT_BLOCKS 比例 >= 75%（即 >= 5/6）；
    # 2. 模型主动选 EDIT_BLOCKS 时的一次应用命中率 >= 75%；
    # 3. MALFORMED 畸形标记次数 == 0（或全矩阵 9 用例中 <= 1 次）；
    # 4. 大幅改版用例若因整页重写耗时超过单次探测超时（timeout 且已开始合法输出 <!doctype/<html），不误判为局部编辑能力缺陷，
    #    但若大幅改版产出了残缺/混合编辑块（partial_kind == malformed 或 edit_blocks 失败）仍计入失败。
    reasons: list[str] = []
    recommend_enable = True

    redesign_malformed = [
        r
        for r in redesign_cases
        if r["output_kind"] == ModelOutputKind.MALFORMED.value
        or (r["output_kind"] == ModelOutputKind.EDIT_BLOCKS.value and not r["edit_apply_ok"])
        or (r["output_kind"] == "request_error" and r.get("partial_kind_on_error") == ModelOutputKind.MALFORMED.value)
    ]

    if len(local_edit_chosen) == 0:
        recommend_enable = False
        reasons.append("局部修改用例均未采用 EDIT_BLOCKS（0 次）")
    elif local_edit_success_rate < 0.75:
        recommend_enable = False
        reasons.append(
            f"局部修改用例 EDIT_BLOCKS 一次成功率 {len(local_edit_applied)}/{len(local_cases)} "
            f"({local_edit_success_rate * 100:.1f}%) < 75%"
        )

    if edit_runs and edit_apply_rate < 0.75:
        recommend_enable = False
        reasons.append(
            f"EDIT_BLOCKS 一次应用命中率 {len(edit_ok_runs)}/{len(edit_runs)} ({edit_apply_rate * 100:.1f}%) < 75%"
        )

    if len(malformed_runs) >= 2:
        recommend_enable = False
        reasons.append(f"产出 MALFORMED 畸形标记偏高（{len(malformed_runs)}/{total_cases}）")

    if len(redesign_malformed) >= 2:
        recommend_enable = False
        reasons.append(f"大幅改版指令下混合/畸形输出偏高（{len(redesign_malformed)}/{len(redesign_cases)}）")

    if recommend_enable:
        reasons.append(
            f"局部修改 EDIT_BLOCKS 成功 {len(local_edit_applied)}/{len(local_cases)} "
            f"({local_edit_success_rate * 100:.1f}%)，EDIT_BLOCKS 命中率 {edit_apply_rate * 100:.1f}%，"
            f"MALFORMED {len(malformed_runs)} 次"
        )

    completed_http_runs = [r for r in runs if r["http_ok"]]
    completed_full_runs = [r for r in full_runs if r["usable_first_pass"]]

    avg_tot_edit = _mean([r["total_output_tokens"] for r in edit_ok_runs])
    avg_tot_full = _mean([r["total_output_tokens"] for r in completed_full_runs])
    avg_vis_edit = _mean([r["visible_output_tokens"] for r in edit_ok_runs])
    avg_vis_full = _mean([r["visible_output_tokens"] for r in completed_full_runs])

    total_token_savings_vs_full = (
        round(1.0 - (avg_tot_edit / avg_tot_full), 4)
        if avg_tot_edit is not None and avg_tot_full is not None and avg_tot_full > 0
        else None
    )
    visible_token_savings_vs_full = (
        round(1.0 - (avg_vis_edit / avg_vis_full), 4)
        if avg_vis_edit is not None and avg_vis_full is not None and avg_vis_full > 0
        else None
    )

    return {
        "model_key": model_cfg.key,
        "label": model_cfg.label,
        "provider": model_cfg.provider,
        "configured_model": model_cfg.model,
        "total_cases": total_cases,
        "edit_blocks_count": len(edit_runs),
        "full_html_count": len(full_runs),
        "malformed_count": len(malformed_runs),
        "request_error_count": len(error_runs),
        "edit_blocks_applied_ok": len(edit_ok_runs),
        "edit_blocks_applied_failed": len(edit_fail_runs),
        "edit_blocks_success_rate": round(edit_apply_rate, 4) if edit_runs else None,
        "local_cases_total": len(local_cases),
        "local_edit_chosen_count": len(local_edit_chosen),
        "local_edit_applied_ok": len(local_edit_applied),
        "local_edit_success_rate": round(local_edit_success_rate, 4),
        "local_usable_count": len(local_usable),
        "redesign_cases_total": len(redesign_cases),
        "redesign_usable_count": len(redesign_usable),
        "overall_first_pass_usable": sum(1 for r in runs if r["usable_first_pass"]),
        "overall_first_pass_rate": round(overall_first_pass_rate, 4),
        "blocks_declared_total": all_declared_blocks,
        "blocks_applied_in_ok_runs": applied_blocks_sum,
        "blocks_exact_in_ok_runs": exact_blocks_sum,
        "blocks_line_stripped_in_ok_runs": stripped_blocks_sum,
        "blocks_exact_all_attempts": all_exact_blocks,
        "blocks_line_stripped_all_attempts": all_stripped_blocks,
        "failure_reasons": dict(failure_counter),
        "avg_elapsed_sec_all": _mean([r["elapsed_sec"] for r in completed_http_runs]),
        "avg_elapsed_sec_edit_ok": _mean([r["elapsed_sec"] for r in edit_ok_runs]),
        "avg_elapsed_sec_micro_edit_ok": _mean(
            [r["elapsed_sec"] for r in edit_ok_runs if r["case_kind"] == "micro_copy"]
        ),
        "avg_elapsed_sec_card_edit_ok": _mean(
            [r["elapsed_sec"] for r in edit_ok_runs if r["case_kind"] == "local_card_style"]
        ),
        "avg_elapsed_sec_full_html": _mean([r["elapsed_sec"] for r in completed_full_runs]),
        "avg_total_output_tokens_all": _mean([r["total_output_tokens"] for r in completed_http_runs]),
        "avg_visible_output_tokens_all": _mean([r["visible_output_tokens"] for r in completed_http_runs]),
        "avg_total_output_tokens_edit_ok": avg_tot_edit,
        "avg_visible_output_tokens_edit_ok": avg_vis_edit,
        "avg_reasoning_tokens_edit_ok": _mean([r["reasoning_tokens"] for r in edit_ok_runs]),
        "avg_total_output_tokens_full_html": avg_tot_full,
        "avg_visible_output_tokens_full_html": avg_vis_full,
        "avg_visible_token_savings_vs_base_html": (
            round(sum(visible_savings_ratios) / len(visible_savings_ratios), 4) if visible_savings_ratios else None
        ),
        "avg_char_savings_vs_base_html": (
            round(sum(char_savings_ratios) / len(char_savings_ratios), 4) if char_savings_ratios else None
        ),
        "avg_total_token_savings_vs_base_html": (
            round(sum(total_token_savings_ratios) / len(total_token_savings_ratios), 4)
            if total_token_savings_ratios
            else None
        ),
        "total_token_savings_vs_full_html_runs": total_token_savings_vs_full,
        "visible_token_savings_vs_full_html_runs": visible_token_savings_vs_full,
        "recommended_edit_mode": recommend_enable,
        "recommendation_reason": "；".join(reasons),
    }


# ---------------------------------------------------------------------------
# 并发调度执行器
# ---------------------------------------------------------------------------


async def probe_single_model(
    model_cfg: LlmModelConfig,
    system_prompt: str,
    matrix: list[tuple[SampledPage, EditProbeCase]],
    per_model_concurrency: int,
    global_sem: asyncio.Semaphore,
    chunk_idle_timeout_seconds: float,
    wall_timeout_seconds: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    model_sem = asyncio.Semaphore(max(1, per_model_concurrency))
    limits = httpx.Limits(max_connections=max(4, per_model_concurrency * 2), max_keepalive_connections=per_model_concurrency)
    http_timeout = httpx.Timeout(connect=20.0, read=chunk_idle_timeout_seconds, write=30.0, pool=30.0)

    async with httpx.AsyncClient(limits=limits, timeout=http_timeout) as http_client:

        async def _run_one(page: SampledPage, case: EditProbeCase) -> dict[str, Any]:
            messages = build_continuation_messages(system_prompt, page, case)
            async with model_sem:
                async with global_sem:
                    call_res = await call_model_stream_once(
                        http_client=http_client,
                        model_cfg=model_cfg,
                        messages=messages,
                        chunk_idle_timeout_seconds=chunk_idle_timeout_seconds,
                        wall_timeout_seconds=wall_timeout_seconds,
                    )
            evaluated = evaluate_single_run(model_cfg, page, case, call_res)
            status_tag = (
                "EDIT_OK"
                if evaluated["edit_apply_ok"] is True
                else (
                    f"EDIT_FAIL({evaluated['failure_code']})"
                    if evaluated["output_kind"] == "edit_blocks"
                    else (
                        "FULL_OK"
                        if evaluated["output_kind"] == "full_html" and evaluated["usable_first_pass"]
                        else f"{evaluated['output_kind'].upper()}({evaluated['failure_code']})"
                    )
                )
            )
            print(
                f"  [{model_cfg.key:<20}] {case.case_id:<24} -> {status_tag:<30} | "
                f"blocks={evaluated['blocks_total']} (exact={evaluated['exact_blocks']}, strip={evaluated['line_stripped_blocks']}) | "
                f"{evaluated['elapsed_sec']:>5.1f}s | out_tok={evaluated['total_output_tokens']} (vis={evaluated['visible_output_tokens']})",
                flush=True,
            )
            return evaluated

        runs = await asyncio.gather(*(_run_one(page, case) for page, case in matrix))

    summary = summarize_model_runs(model_cfg, list(runs))
    return summary, list(runs)


def _fmt_pct(val: float | None) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "   -  "
    return f"{val * 100:5.1f}%"


def print_summary_table(summaries: list[dict[str, Any]], elapsed_wall: float) -> None:
    print("\n" + "=" * 132)
    print(f"星页局部编辑（SEARCH/REPLACE）9 模型实测汇总 · 总墙钟耗时 {elapsed_wall:.1f}s")
    print("=" * 132)
    header = (
        f"{'模型 Key':<20} | {'用例':>4} | {'EDIT':>4} | {'FULL':>4} | {'MALF':>4} | "
        f"{'EDIT一次命中':>12} | {'exact/strip':>11} | {'局部改成功':>10} | "
        f"{'均耗时(E/全)':>13} | {'可见tok省':>8} | {'建议开关':<10}"
    )
    print(header)
    print("-" * 132)
    for s in summaries:
        edit_hit_str = (
            f"{s['edit_blocks_applied_ok']}/{s['edit_blocks_count']} ({_fmt_pct(s['edit_blocks_success_rate']).strip()})"
            if s["edit_blocks_count"] > 0
            else "0/0 (-)"
        )
        exact_strip_str = f"{s['blocks_exact_in_ok_runs']}/{s['blocks_line_stripped_in_ok_runs']}"
        local_str = f"{s['local_edit_applied_ok']}/{s['local_cases_total']} ({_fmt_pct(s['local_edit_success_rate']).strip()})"
        t_edit = f"{s['avg_elapsed_sec_edit_ok']:.1f}s" if s["avg_elapsed_sec_edit_ok"] is not None else "-"
        t_all = f"{s['avg_elapsed_sec_all']:.1f}s" if s["avg_elapsed_sec_all"] is not None else "-"
        time_str = f"{t_edit}/{t_all}"
        sav_str = _fmt_pct(s["avg_visible_token_savings_vs_base_html"])
        flag_str = "edit_mode: true" if s["recommended_edit_mode"] else "edit_mode: false"
        print(
            f"{s['model_key']:<20} | {s['total_cases']:>4} | {s['edit_blocks_count']:>4} | "
            f"{s['full_html_count']:>4} | {s['malformed_count']:>4} | "
            f"{edit_hit_str:>12} | {exact_strip_str:>11} | {local_str:>10} | "
            f"{time_str:>13} | {sav_str:>8} | {flag_str}"
        )
    print("-" * 132)
    for s in summaries:
        fails = s["failure_reasons"]
        fail_str = ", ".join(f"{k}:{v}" for k, v in fails.items()) if fails else "无"
        print(f"  - {s['model_key']}: 失败分布=[{fail_str}] | 结论：{s['recommendation_reason']}")
    print("=" * 132)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="探测各可用 LLM 模型的局部编辑（SEARCH/REPLACE）能力与成功率")
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="仅测试指定模型 key（逗号分隔，例如 qwen,doubao）；默认测试全部 available 模型",
    )
    parser.add_argument(
        "--tiers",
        type=str,
        default="short,medium,long",
        help="测试页面复杂度分桶（逗号分隔：short,medium,long）",
    )
    parser.add_argument(
        "--per-model-concurrency",
        type=int,
        default=3,
        help="单个模型的最大并发请求数（默认 3）",
    )
    parser.add_argument(
        "--global-concurrency",
        type=int,
        default=27,
        help="全局最大并发请求数（默认 27）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="流式连接与单分片空闲读取超时秒数（与生产 httpx.Timeout 口径一致，默认 120s）",
    )
    parser.add_argument(
        "--wall-timeout",
        type=float,
        default=420.0,
        help="单次流式请求的总墙钟硬上限秒数（防止无限流式吐字挂死，默认 420s）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=f"结构化探测报告输出路径（默认 {DEFAULT_REPORT_PATH}）",
    )
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    os.chdir(BACKEND)
    get_settings.cache_clear()
    get_model_registry.cache_clear()

    registry: LlmModelRegistry = get_model_registry()
    available_models = registry.available_models()
    if args.only.strip():
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        target_models = [m for m in available_models if m.key in wanted]
    else:
        target_models = list(available_models)

    if not target_models:
        print("未找到任何可用的目标模型，请检查 config/llm.env 与 --only 参数。", file=sys.stderr)
        return 1

    system_prompt = read_html_page_system_prompt()
    print(f"[1/3] 正在从数据库纯只读抽样真实历史页面（default_transaction_read_only=on）...", flush=True)
    all_pages = await sample_readonly_pages_from_db()
    wanted_tiers = {item.strip() for item in args.tiers.split(",") if item.strip()}
    pages = [p for p in all_pages if p.tier in wanted_tiers]
    if not pages:
        print("未匹配到任何目标分桶页面，请检查 --tiers 参数。", file=sys.stderr)
        return 1

    for p in pages:
        print(
            f"  抽样页面 [{p.tier:<6}] id_hash={p.page_hash} source={p.source} "
            f"bytes={p.html_bytes} chars={p.html_chars} lines={p.html_lines} ancestor_turns={p.ancestor_turns}",
            flush=True,
        )

    matrix = build_probe_cases(pages)
    print(
        f"\n[2/3] 开始并发执行探测矩阵：{len(target_models)} 个模型 × {len(matrix)} 条用例 = "
        f"{len(target_models) * len(matrix)} 次调用 "
        f"(per_model_concurrency={args.per_model_concurrency}, idle_timeout={args.timeout:.0f}s, wall_timeout={args.wall_timeout:.0f}s)...\n",
        flush=True,
    )

    global_sem = asyncio.Semaphore(max(1, args.global_concurrency))
    wall_start = time.perf_counter()

    results = await asyncio.gather(
        *(
            probe_single_model(
                model_cfg=m,
                system_prompt=system_prompt,
                matrix=matrix,
                per_model_concurrency=args.per_model_concurrency,
                global_sem=global_sem,
                chunk_idle_timeout_seconds=args.timeout,
                wall_timeout_seconds=args.wall_timeout,
            )
            for m in target_models
        )
    )
    elapsed_wall = round(time.perf_counter() - wall_start, 2)

    summaries = [item[0] for item in results]
    all_runs = [run for _summary, runs in results for run in runs]

    print_summary_table(summaries, elapsed_wall)

    report_payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_wall_seconds": elapsed_wall,
        "config": {
            "per_model_concurrency": args.per_model_concurrency,
            "global_concurrency": args.global_concurrency,
            "chunk_idle_timeout_seconds": args.timeout,
            "wall_timeout_seconds": args.wall_timeout,
            "models_tested": [m.key for m in target_models],
            "cases_per_model": len(matrix),
        },
        "continuation_prompt_template": HTML_PAGE_EDIT_CONTINUE_PROMPT_TEMPLATE,
        "sampled_pages_redacted": [
            {
                "tier": p.tier,
                "source": p.source,
                "page_hash": p.page_hash,
                "html_bytes": p.html_bytes,
                "html_chars": p.html_chars,
                "html_lines": p.html_lines,
                "ancestor_turns": p.ancestor_turns,
            }
            for p in pages
        ],
        "cases_metadata": [
            {
                "case_id": case.case_id,
                "tier": case.tier,
                "kind": case.kind,
                "expected_primary_mode": case.expected_primary_mode,
                "description": case.description,
                "instruction": case.instruction,
            }
            for _page, case in matrix
        ],
        "model_summaries": summaries,
        "recommended_model_flags": {
            s["model_key"]: {"edit_mode": s["recommended_edit_mode"]} for s in summaries
        },
        "runs": all_runs,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[3/3] 脱敏探测报告已写入: {args.output}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
