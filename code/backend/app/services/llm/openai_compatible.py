from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from json import JSONDecodeError

import httpx

from app.core.config import Settings
from app.services.llm.types import LlmCompletionResult, LlmMessage, LlmModelConfig, LlmStreamChunk, LlmUsage

RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


class OpenAICompatibleClient:
    def __init__(self, model: LlmModelConfig, settings: Settings) -> None:
        self.model = model
        self.settings = settings
        self.base_url = model.base_url.rstrip("/")

    async def stream_text(self, messages: list[LlmMessage]) -> AsyncIterator[LlmStreamChunk]:
        attempts = self._retry_attempts()
        images_dropped = False
        attempt = 1

        while True:
            yielded_chunk = False
            try:
                async for chunk in self._stream_text_once(messages):
                    yielded_chunk = True
                    yield chunk
                return
            except Exception as exc:
                # 带图请求在吐字前被拒（模型实际不收图、图片格式不被接受），去掉图片再试一次，不占重试次数。
                if not yielded_chunk and not images_dropped and _has_images(messages) and _is_bad_request(exc):
                    images_dropped = True
                    messages = drop_images(messages)
                    continue
                if yielded_chunk or attempt >= attempts or not _is_retryable_exception(exc):
                    raise
                await self._sleep_before_retry(attempt)
                attempt += 1

    async def complete_text(self, messages: list[LlmMessage], *, require_content: bool = True) -> LlmCompletionResult:
        attempts = self._retry_attempts()
        last_text = ""
        last_usage: LlmUsage | None = None

        for attempt in range(1, attempts + 1):
            parts: list[str] = []
            usage: LlmUsage | None = None
            try:
                async for chunk in self._stream_text_once(messages):
                    if chunk.type == "text_delta" and chunk.text:
                        parts.append(chunk.text)
                    if chunk.type == "done" and chunk.usage:
                        usage = chunk.usage
            except Exception as exc:
                if attempt >= attempts or not _is_retryable_exception(exc):
                    raise
                await self._sleep_before_retry(attempt)
                continue

            last_text = "".join(parts).strip()
            last_usage = usage
            if last_text or not require_content:
                return LlmCompletionResult(text=last_text, usage=last_usage)

            if attempt < attempts:
                await self._sleep_before_retry(attempt)

        if require_content:
            raise ValueError("LLM 返回空正文")
        return LlmCompletionResult(text=last_text, usage=last_usage)

    async def _stream_text_once(self, messages: list[LlmMessage]) -> AsyncIterator[LlmStreamChunk]:
        body = {
            "model": self.model.model,
            "messages": [_serialize_message(message, self.model.supports_vision) for message in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            # 参数三层覆盖：defaults+params 已 resolved（仅非 None），extra_body 厂商专有最后合并。
            **self.model.params,
            **self.model.extra_body,
        }

        headers = {
            "Authorization": f"Bearer {self.model.api_key}",
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
                    model = raw_chunk.get("model") or self.model.model
                    delta = (raw_chunk.get("choices") or [{}])[0].get("delta") or {}

                    reasoning_text = delta.get("reasoning_content")
                    if reasoning_text:
                        yield LlmStreamChunk(
                            type="reasoning_delta",
                            provider=self.model.provider,
                            model=model,
                            reasoning_text=reasoning_text,
                            raw_chunk=raw_chunk,
                        )

                    text = delta.get("content")
                    if text:
                        yield LlmStreamChunk(
                            type="text_delta",
                            provider=self.model.provider,
                            model=model,
                            text=text,
                            raw_chunk=raw_chunk,
                        )

                    usage = raw_chunk.get("usage")
                    if usage:
                        prompt_details = usage.get("prompt_tokens_details") or {}
                        completion_details = usage.get("completion_tokens_details") or {}
                        yield LlmStreamChunk(
                            type="done",
                            provider=self.model.provider,
                            model=model,
                            usage=LlmUsage(
                                input_tokens=usage.get("prompt_tokens"),
                                output_tokens=usage.get("completion_tokens"),
                                total_tokens=usage.get("total_tokens"),
                                cached_input_tokens=prompt_details.get("cached_tokens"),
                                reasoning_tokens=completion_details.get("reasoning_tokens"),
                            ),
                            raw_chunk=raw_chunk,
                        )

        yield LlmStreamChunk(
            type="done",
            provider=self.model.provider,
            model=self.model.model,
        )

    def _retry_attempts(self) -> int:
        return max(1, self.settings.llm_retry_attempts)

    async def _sleep_before_retry(self, attempt: int) -> None:
        delay_ms = min(
            self.settings.llm_retry_max_delay_ms,
            self.settings.llm_retry_initial_delay_ms * (2 ** max(0, attempt - 1)),
        )
        await asyncio.sleep(delay_ms / 1000)


def _serialize_message(message: LlmMessage, supports_vision: bool) -> dict:
    """有图且模型能读图时用 OpenAI 多模态内容块，否则只发文字。"""
    if not message.images or not supports_vision:
        return {"role": message.role, "content": message.content}
    parts: list[dict] = [{"type": "text", "text": message.content}]
    parts.extend({"type": "image_url", "image_url": {"url": image.data_url}} for image in message.images)
    return {"role": message.role, "content": parts}


def _has_images(messages: list[LlmMessage]) -> bool:
    return any(message.images for message in messages)


def drop_images(messages: list[LlmMessage]) -> list[LlmMessage]:
    return [replace(message, images=()) if message.images else message for message in messages]


def _is_bad_request(exc: Exception) -> bool:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 400


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES

    return isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            JSONDecodeError,
        ),
    )
