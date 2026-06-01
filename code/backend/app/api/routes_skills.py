from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas.generation import SkillInfo
from app.services.skills.registry import get_skill_registry

router = APIRouter(tags=["skills"])


@router.get("/api/skills", response_model=list[SkillInfo])
async def list_skills() -> list[SkillInfo]:
    """返回可用的网页制作技能列表；技能能力关闭时返回空列表（前端据此不展示选择器）。"""
    settings = get_settings()
    if not settings.page_skills_enabled:
        return []

    return [
        SkillInfo(key=skill.key, name=skill.name, description=skill.description)
        for skill in get_skill_registry().list_skills()
    ]
