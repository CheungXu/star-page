from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import get_settings

# 匹配开头的 YAML frontmatter：--- 包裹的元数据 + 其后正文。
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


@dataclass(frozen=True)
class SkillDefinition:
    """单个网页制作技能：由 SKILL.md 的 frontmatter(元数据) 与正文(注入提示)解析得到。"""

    key: str
    name: str
    description: str
    body: str
    triggers: tuple[str, ...] = ()
    enabled: bool = True


@dataclass(frozen=True)
class SkillRegistry:
    """技能目录：key -> 技能定义。仅暴露 enabled 的技能。"""

    skills: dict[str, SkillDefinition] = field(default_factory=dict)

    def list_skills(self) -> list[SkillDefinition]:
        return [skill for skill in self.skills.values() if skill.enabled]

    def get(self, key: str | None) -> SkillDefinition | None:
        if not key:
            return None
        skill = self.skills.get(key)
        if skill is None or not skill.enabled:
            return None
        return skill


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """把 SKILL.md 拆成 (元数据 dict, 正文)。无合法 frontmatter 时元数据为空。"""
    stripped = text.lstrip("\ufeff")  # 去掉可能的 BOM
    match = _FRONTMATTER_RE.match(stripped)
    if not match:
        return {}, stripped.strip()

    meta_raw, body = match.group(1), match.group(2)
    try:
        meta = yaml.safe_load(meta_raw) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, body.strip()


def _normalize_triggers(raw: object) -> tuple[str, ...]:
    if isinstance(raw, str):
        items = [part.strip() for part in re.split(r"[,，\s]+", raw)]
    elif isinstance(raw, (list, tuple)):
        items = [str(part).strip() for part in raw]
    else:
        return ()
    return tuple(item for item in items if item)


def _parse_skill_file(skill_md: Path) -> SkillDefinition | None:
    """解析单个 SKILL.md；缺少必填字段(key/name/description)或正文为空则跳过。"""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None

    meta, body = _split_frontmatter(text)
    # key 缺省时回退到所在目录名，便于"目录名即技能名"的约定。
    key = str(meta.get("key") or skill_md.parent.name).strip()
    name = str(meta.get("name") or key).strip()
    description = str(meta.get("description") or "").strip()
    enabled = meta.get("enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in {"0", "false", "no", "off"}

    if not key or not description or not body:
        return None

    return SkillDefinition(
        key=key,
        name=name,
        description=description,
        body=body,
        triggers=_normalize_triggers(meta.get("triggers")),
        enabled=bool(enabled),
    )


def _find_skills_dir(raw: str) -> Path | None:
    """定位技能目录：绝对路径直接用；相对路径从 cwd 逐级向上查找(兼容 code/backend 作为 cwd)。"""
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate if candidate.is_dir() else None

    cwd = Path.cwd()
    for base in [cwd, *cwd.parents]:
        path = base / raw
        if path.is_dir():
            return path
    return None


def load_skill_registry(skills_dir: str) -> SkillRegistry:
    """扫描技能目录下每个子目录的 SKILL.md，构建技能目录。后 key 覆盖先 key(按目录名排序稳定)。"""
    base = _find_skills_dir(skills_dir)
    if base is None:
        return SkillRegistry(skills={})

    skills: dict[str, SkillDefinition] = {}
    for skill_md in sorted(base.glob("*/SKILL.md")):
        skill = _parse_skill_file(skill_md)
        if skill is not None:
            skills[skill.key] = skill
    return SkillRegistry(skills=skills)


@lru_cache(maxsize=1)
def get_skill_registry() -> SkillRegistry:
    """进程级缓存的技能目录。技能文件更新需重启后端生效。"""
    settings = get_settings()
    return load_skill_registry(settings.page_skills_dir)
