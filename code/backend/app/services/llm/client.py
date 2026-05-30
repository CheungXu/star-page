from __future__ import annotations

from app.core.config import get_model_registry, get_settings
from app.services.llm.openai_compatible import OpenAICompatibleClient


def create_llm_client(model_key: str | None = None) -> OpenAICompatibleClient:
    settings = get_settings()
    registry = get_model_registry()

    key = model_key or (registry.default_model_keys[0] if registry.default_model_keys else None)
    if not key:
        raise ValueError("未配置任何可用模型")

    model = registry.get(key)
    if model is None:
        raise ValueError(f"未知模型：{key}")

    if model.protocol != "openai":
        raise ValueError(f"当前 Python 后端暂只支持 OpenAI-compatible 协议：{model.protocol}（模型 {key}）")

    if not model.model or not model.api_key:
        raise ValueError(f"模型 {key} 缺少 model 或 API Key（请检查 config/llm.models.json 与对应密钥环境变量）")

    return OpenAICompatibleClient(model, settings)
