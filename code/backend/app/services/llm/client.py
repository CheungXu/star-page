from __future__ import annotations

from app.core.config import get_settings
from app.services.llm.openai_compatible import OpenAICompatibleClient


def create_llm_client() -> OpenAICompatibleClient:
    settings = get_settings()

    if settings.llm_protocol != "openai":
        raise ValueError(f"当前 Python 后端暂只支持 OpenAI-compatible 协议：{settings.llm_protocol}")

    if not settings.llm_model or not settings.llm_api_key:
        raise ValueError("缺少 LLM_MODEL 或 LLM_API_KEY")

    return OpenAICompatibleClient(settings)
