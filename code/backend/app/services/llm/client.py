from __future__ import annotations

from dataclasses import replace

from app.core.config import get_model_registry, get_settings
from app.services.llm.openai_compatible import OpenAICompatibleClient

# 资料压缩用快模型，不走页面生成那条「深度思考」链路。
COMPRESSION_MODEL_KEY = "qwen-plus"
COMPRESSION_MAX_TOKENS = 4096


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


def create_compression_llm_client() -> OpenAICompatibleClient:
    """资料压缩客户端：优先通义 Flash，强制关闭思考并限制输出长度。"""
    settings = get_settings()
    registry = get_model_registry()
    base = registry.get(COMPRESSION_MODEL_KEY)
    if base is None or not base.available:
        base = next((item for item in registry.models.values() if item.available), None)
    if base is None:
        raise ValueError("没有可用的资料压缩模型")

    params = dict(base.params)
    params["max_tokens"] = COMPRESSION_MAX_TOKENS
    params["temperature"] = 0.2
    extra_body = {key: value for key, value in base.extra_body.items() if key != "reasoning_effort"}
    extra_body["enable_thinking"] = False
    model = replace(base, params=params, extra_body=extra_body)
    return OpenAICompatibleClient(model, settings)
