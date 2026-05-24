from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

LlmMessageRole = Literal["system", "user", "assistant"]
LlmStreamChunkType = Literal["reasoning_delta", "text_delta", "done"]


@dataclass(frozen=True)
class LlmMessage:
    role: LlmMessageRole
    content: str


@dataclass(frozen=True)
class LlmUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class LlmStreamChunk:
    type: LlmStreamChunkType
    provider: str
    model: str
    text: str | None = None
    reasoning_text: str | None = None
    usage: LlmUsage | None = None
    raw_chunk: dict[str, Any] | None = None
