from __future__ import annotations

from dataclasses import replace

from app.core.config import get_model_registry, get_settings
from app.services.llm.openai_compatible import OpenAICompatibleClient

EXTRACTION_MAX_TOKENS = 8192
EXTRACTION_TEMPERATURE = 0.2
ROUTER_MODEL_KEY = "qwen-plus"
ROUTER_MAX_TOKENS = 16
# 思考相关的厂商字段，提取时整体换成目录里的 extraction_extra_body。
THINKING_KEYS = {"enable_thinking", "reasoning_effort", "thinking"}


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


def create_extraction_llm_client(model_key: str) -> OpenAICompatibleClient:
    """资料提取客户端：和写码用同一个模型，按目录里实测的写法关闭思考，限制输出长度。

    目录里 params 显式置空的参数（如 Kimi 的 temperature）在解析时已经去掉，这里也不补回。
    """
    base = create_llm_client(model_key).model
    params = dict(base.params)
    params["max_tokens"] = EXTRACTION_MAX_TOKENS
    if "temperature" in params:
        params["temperature"] = EXTRACTION_TEMPERATURE
    extra_body = {key: value for key, value in base.extra_body.items() if key not in THINKING_KEYS}
    extra_body.update(base.extraction_extra_body)
    model = replace(base, params=params, extra_body=extra_body)
    return OpenAICompatibleClient(model, get_settings())


def create_router_llm_client(fallback_model_key: str | None = None) -> tuple[OpenAICompatibleClient, str]:
    """技能分类路由专用轻量客户端：优先固定走 qwen-plus，强制关闭深度思考且限制 max_tokens=16。

    返回 (client, actual_model_key)，便于按实际调用的轻量模型单价单独核计成本。
    """
    registry = get_model_registry()
    chosen_key = ROUTER_MODEL_KEY
    candidate = registry.get(ROUTER_MODEL_KEY)
    if candidate is None or not candidate.available:
        if fallback_model_key and registry.get(fallback_model_key) and registry.get(fallback_model_key).available:
            chosen_key = fallback_model_key
        else:
            avail = registry.available_models()
            if not avail:
                raise ValueError("没有可用于技能路由的模型")
            chosen_key = avail[0].key
    base = create_llm_client(chosen_key).model
    params = dict(base.params)
    params["max_tokens"] = ROUTER_MAX_TOKENS
    if "temperature" in params:
        params["temperature"] = 0.0
    extra_body = {key: value for key, value in base.extra_body.items() if key not in THINKING_KEYS}
    extra_body.update(base.extraction_extra_body)
    model = replace(base, params=params, extra_body=extra_body)
    return OpenAICompatibleClient(model, get_settings()), chosen_key
