from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

LlmMessageRole = Literal["system", "user", "assistant"]
LlmStreamChunkType = Literal["reasoning_delta", "text_delta", "done"]


@dataclass(frozen=True)
class LlmModelConfig:
    """单个模型的运行配置：由模型目录(defaults+params+extra_body 三层覆盖)与密钥解析得到。

    - params：已 resolved 的标准生成参数（仅保留非 None 值），直接拼进 OpenAI body。
    - extra_body：厂商专有透传字段（如 enable_thinking / reasoning_effort），合并在 body 最后一层。
    - available：密钥是否就绪；缺失则该模型在多选中不可用。
    - supports_vision：实测能读 image_url 内容块；为假时图片在适配层被丢弃。
    - extraction_extra_body：资料提取时关闭思考的写法，替换 extra_body 里的思考相关字段。
    """

    key: str
    label: str
    provider: str
    protocol: str
    base_url: str
    model: str
    api_key: str
    params: dict[str, Any] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    pricing: dict[str, Any] | None = None
    available: bool = False
    supports_vision: bool = False
    extraction_extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmImagePart:
    """发给模型看的一张图。data_url 只在内存里传，不落库。"""

    data_url: str
    label: str = ""


@dataclass(frozen=True)
class LlmMessage:
    role: LlmMessageRole
    content: str
    images: tuple[LlmImagePart, ...] = ()


@dataclass(frozen=True)
class LlmUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class LlmCostBreakdown:
    currency: str
    tier_label: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int
    input_per_million: float
    output_per_million: float
    cache_input_per_million: float | None
    input_cost_cny: float
    output_cost_cny: float
    total_cost_cny: float


@dataclass(frozen=True)
class LlmCompletionResult:
    text: str
    usage: LlmUsage | None = None


@dataclass(frozen=True)
class LlmStreamChunk:
    type: LlmStreamChunkType
    provider: str
    model: str
    text: str | None = None
    reasoning_text: str | None = None
    usage: LlmUsage | None = None
    raw_chunk: dict[str, Any] | None = None
