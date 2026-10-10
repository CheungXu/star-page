"""场景目录：从 config/scenes.json 读取 P0/P1 场景，并拼装发给模型的需求文本。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings

_UTM_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class SceneField:
    key: str
    label: str
    placeholder: str


@dataclass(frozen=True)
class SceneStyle:
    key: str
    label: str
    hint: str


@dataclass(frozen=True)
class ImageSlot:
    key: str
    label: str


@dataclass(frozen=True)
class SceneDefinition:
    key: str
    name: str
    emoji: str
    priority: str
    skill_key: str
    extra_skill_keys: tuple[str, ...]
    tagline: str
    description: str
    seo_title: str
    seo_description: str
    prompt_template: str
    guide_fields: tuple[SceneField, ...]
    styles: tuple[SceneStyle, ...]
    mask_contact: bool
    accepts_documents: bool
    accepts_images: bool
    image_slots: tuple[ImageSlot, ...]
    features: tuple[str, ...]

    def style(self, key: str | None) -> SceneStyle | None:
        if not key:
            return None
        for item in self.styles:
            if item.key == key:
                return item
        return None

    def public_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "emoji": self.emoji,
            "priority": self.priority,
            "skill_key": self.skill_key,
            "tagline": self.tagline,
            "description": self.description,
            "seo_title": self.seo_title,
            "seo_description": self.seo_description,
            "prompt_template": self.prompt_template,
            "guide_fields": [
                {"key": item.key, "label": item.label, "placeholder": item.placeholder} for item in self.guide_fields
            ],
            "styles": [{"key": item.key, "label": item.label, "hint": item.hint} for item in self.styles],
            "accepts_documents": self.accepts_documents,
            "accepts_images": self.accepts_images,
            "image_slots": [{"key": item.key, "label": item.label} for item in self.image_slots],
            "features": list(self.features),
        }


def _find_scenes_path() -> Path | None:
    settings = get_settings()
    raw = getattr(settings, "scenes_file", None) or "config/scenes.json"
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    cwd = Path.cwd()
    for base in [cwd, *cwd.parents]:
        path = base / raw
        if path.exists():
            return path
        fallback = base / "config" / "scenes.json"
        if fallback.exists():
            return fallback
    return None


def _as_fields(raw: object) -> tuple[SceneField, ...]:
    if not isinstance(raw, list):
        return ()
    items: list[SceneField] = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        items.append(
            SceneField(
                key=str(entry["key"]),
                label=str(entry.get("label") or entry["key"]),
                placeholder=str(entry.get("placeholder") or ""),
            )
        )
    return tuple(items)


def _as_styles(raw: object) -> tuple[SceneStyle, ...]:
    if not isinstance(raw, list):
        return ()
    items: list[SceneStyle] = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        items.append(
            SceneStyle(
                key=str(entry["key"]),
                label=str(entry.get("label") or entry["key"]),
                hint=str(entry.get("hint") or ""),
            )
        )
    return tuple(items)


def _as_slots(raw: object) -> tuple[ImageSlot, ...]:
    if not isinstance(raw, list):
        return ()
    items: list[ImageSlot] = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("key"):
            continue
        items.append(ImageSlot(key=str(entry["key"]), label=str(entry.get("label") or entry["key"])))
    return tuple(items)


def _parse_scene(entry: dict[str, Any]) -> SceneDefinition | None:
    key = str(entry.get("key") or "").strip()
    skill_key = str(entry.get("skill_key") or "").strip()
    if not key or not skill_key:
        return None
    extras = entry.get("extra_skill_keys") or []
    features = entry.get("features") or []
    return SceneDefinition(
        key=key,
        name=str(entry.get("name") or key),
        emoji=str(entry.get("emoji") or ""),
        priority=str(entry.get("priority") or ""),
        skill_key=skill_key,
        extra_skill_keys=tuple(str(item) for item in extras if str(item).strip()),
        tagline=str(entry.get("tagline") or ""),
        description=str(entry.get("description") or ""),
        seo_title=str(entry.get("seo_title") or entry.get("name") or key),
        seo_description=str(entry.get("seo_description") or entry.get("tagline") or ""),
        prompt_template=str(entry.get("prompt_template") or "请根据我的资料生成页面。"),
        guide_fields=_as_fields(entry.get("guide_fields")),
        styles=_as_styles(entry.get("styles")),
        mask_contact=bool(entry.get("mask_contact")),
        accepts_documents=bool(entry.get("accepts_documents", True)),
        accepts_images=bool(entry.get("accepts_images")),
        image_slots=_as_slots(entry.get("image_slots")),
        features=tuple(str(item) for item in features if str(item).strip()),
    )


@lru_cache(maxsize=1)
def get_scene_catalog() -> dict[str, SceneDefinition]:
    path = _find_scenes_path()
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    scenes: dict[str, SceneDefinition] = {}
    for entry in data.get("scenes") or []:
        if not isinstance(entry, dict):
            continue
        scene = _parse_scene(entry)
        if scene is not None:
            scenes[scene.key] = scene
    return scenes


def get_scene(key: str | None) -> SceneDefinition | None:
    if not key:
        return None
    return get_scene_catalog().get(key)


def list_scenes() -> list[SceneDefinition]:
    return list(get_scene_catalog().values())


def normalize_utm_source(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    if not raw or not _UTM_RE.fullmatch(raw):
        return None
    return raw


def parse_guide(raw: str | None) -> dict[str, str]:
    """解析前端传来的引导字段 JSON。非法或非对象时返回空字典。"""
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    guide: dict[str, str] = {}
    for key, value in parsed.items():
        text = str(value).strip()
        if text:
            guide[str(key)[:40]] = text[:500]
    return guide


def compose_scene_prompt(
    scene: SceneDefinition,
    user_prompt: str,
    guide: dict[str, str],
    style_key: str | None,
    *,
    reveal_contact: bool = False,
) -> str:
    """把场景模板、风格和引导字段拼成一条发给模型的需求。结果截断到 4000 字。"""
    text = (user_prompt or "").strip()
    if len(text) < 3:
        text = scene.prompt_template.strip()

    extras: list[str] = []
    style = scene.style(style_key)
    if style and style.hint and style.hint not in text:
        extras.append(style.hint)

    lines: list[str] = []
    known = {field.key: field for field in scene.guide_fields}
    for key, value in guide.items():
        field = known.get(key)
        label = field.label if field else key
        line = f"- {label}：{value}"
        if line not in text:
            lines.append(line)
    if lines:
        extras.append("补充信息：\n" + "\n".join(lines))

    if scene.mask_contact and not reveal_contact:
        extras.append("联系方式处理：默认部分打码。手机号只保留后四位，邮箱只保留域名。只有需求里明确写了要公开时，才写完整联系方式。")

    if "「」" in text:
        extras.append("需求里留空的「」表示用户没有提供这一项。相关内容省略或写得笼统一些，不要编造具体名字、时间和地点。")

    if scene.key == "invitation":
        extras.append("报名不要做成会提交到服务器的表单。用外部链接按钮，或使用下方给出的二维码图片。")

    extra_text = "\n\n".join(extras)
    if extra_text:
        # 先截原文，再接场景约束，避免姓名、打码说明被 4000 字上限切掉。
        budget = max(200, 4000 - len(extra_text) - 2)
        text = text[:budget] + "\n\n" + extra_text
    return text[:4000]


def format_image_block(items: list[tuple[str, str, str]]) -> str:
    """把托管图片地址写成模型必须引用的说明。items 为 (角色名, 绝对地址, 文件名)。"""
    if not items:
        return ""
    lines = [
        "平台托管图片（这是通用规则里“少用外链图片”的明确例外，必须用 <img> 引用下列绝对地址，不要改写、不要换成占位图）："
    ]
    for label, url, _filename in items:
        lines.append(f"- {label}：{url}")
    return "\n".join(lines)
