from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from app.core.config import Settings
from app.services.llm.types import LlmMessage, LlmStreamChunk, LlmUsage


class OpenAICompatibleClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.llm_base_url.rstrip("/")

    async def stream_text(self, messages: list[LlmMessage]) -> AsyncIterator[LlmStreamChunk]:
        body = {
            "model": self.settings.llm_model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            **self.settings.llm_extra_body,
        }

        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }

        timeout = httpx.Timeout(self.settings.llm_timeout_ms / 1000)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue

                    payload = line.removeprefix("data:").strip()
                    if not payload or payload == "[DONE]":
                        continue

                    raw_chunk = json.loads(payload)
                    model = raw_chunk.get("model") or self.settings.llm_model
                    delta = (raw_chunk.get("choices") or [{}])[0].get("delta") or {}

                    reasoning_text = delta.get("reasoning_content")
                    if reasoning_text:
                        yield LlmStreamChunk(
                            type="reasoning_delta",
                            provider=self.settings.llm_provider,
                            model=model,
                            reasoning_text=reasoning_text,
                            raw_chunk=raw_chunk,
                        )

                    text = delta.get("content")
                    if text:
                        yield LlmStreamChunk(
                            type="text_delta",
                            provider=self.settings.llm_provider,
                            model=model,
                            text=text,
                            raw_chunk=raw_chunk,
                        )

                    usage = raw_chunk.get("usage")
                    if usage:
                        yield LlmStreamChunk(
                            type="done",
                            provider=self.settings.llm_provider,
                            model=model,
                            usage=LlmUsage(
                                input_tokens=usage.get("prompt_tokens"),
                                output_tokens=usage.get("completion_tokens"),
                                total_tokens=usage.get("total_tokens"),
                            ),
                            raw_chunk=raw_chunk,
                        )

        yield LlmStreamChunk(
            type="done",
            provider=self.settings.llm_provider,
            model=self.settings.llm_model,
        )
