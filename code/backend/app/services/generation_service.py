from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.default_user import ensure_default_user
from app.models.entities import GenerationEvent, GenerationTask, Page, PagePermission, PageVersion
from app.services.html_sanitizer import extract_html_document, sanitize_html
from app.services.llm.client import create_llm_client
from app.services.llm.prompt import HTML_PAGE_SYSTEM_PROMPT
from app.services.llm.types import LlmMessage, LlmUsage
from app.services.sse import SseEvent
from app.services.storage.factory import create_storage_provider

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class GenerationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    async def create_generation(self, prompt: str) -> tuple[GenerationTask, Page]:
        user = await ensure_default_user(self.session)
        title = _build_page_title(prompt)

        page = Page(
            owner_user_id=user.id,
            title=title,
            visibility="public",
            status="generating",
        )
        self.session.add(page)
        await self.session.flush()

        task = GenerationTask(
            page_id=page.id,
            requested_by_user_id=user.id,
            prompt=prompt,
            status="pending",
        )
        permission = PagePermission(page_id=page.id, user_id=user.id, role="owner")
        self.session.add_all([task, permission])
        await self.session.commit()
        await self.session.refresh(task)
        await self.session.refresh(page)
        return task, page

    async def run_or_replay(self, task_id: uuid.UUID) -> AsyncIterator[SseEvent]:
        task = await self.session.get(GenerationTask, task_id)
        if task is None:
            yield SseEvent("failed", {"type": "failed", "message": "生成任务不存在"})
            return

        if task.status == "pending":
            async for event in self._run_task(task):
                yield event
            return

        async for event in self._stream_existing_events_until_done(task):
            yield event

    async def _run_task(self, task: GenerationTask) -> AsyncIterator[SseEvent]:
        page = await self.session.get(Page, task.page_id)
        if page is None:
            yield SseEvent("failed", {"type": "failed", "message": "页面记录不存在"})
            return

        task.status = "running"
        task.started_at = datetime.now(UTC)
        await self.session.commit()

        yield await self._record_event(task.id, "status", {"type": "status", "text": "正在理解你的页面需求..."})

        answer_parts: list[str] = []
        answer_started = False
        model_provider: str | None = None
        model_name: str | None = None
        usage: LlmUsage | None = None
        answer_text_length = 0
        reported_output_tokens = 0
        next_token_report = 50

        try:
            llm_client = create_llm_client()
            messages = [
                LlmMessage(role="system", content=HTML_PAGE_SYSTEM_PROMPT),
                LlmMessage(role="user", content=task.prompt),
            ]

            async for chunk in llm_client.stream_text(messages):
                model_provider = chunk.provider or model_provider
                model_name = chunk.model or model_name

                if chunk.type == "reasoning_delta" and chunk.reasoning_text:
                    yield await self._record_event(
                        task.id,
                        "reasoning_delta",
                        {"type": "reasoning_delta", "text": chunk.reasoning_text},
                    )

                if chunk.type == "text_delta" and chunk.text:
                    if not answer_started:
                        answer_started = True
                        yield await self._record_event(task.id, "answer_started", {"type": "answer_started"})
                        yield await self._record_event(
                            task.id,
                            "status",
                            {"type": "status", "text": "页面创建中..."},
                        )
                        yield await self._record_event(
                            task.id,
                            "progress",
                            {
                                "type": "progress",
                                "step": "model_output",
                                "status": "running",
                                "text": "模型正在输出页面 HTML",
                                "output_tokens": 0,
                                "token_source": "estimated",
                            },
                        )
                    answer_parts.append(chunk.text)
                    answer_text_length += len(chunk.text)
                    estimated_output_tokens = _estimate_output_tokens(answer_text_length)
                    if estimated_output_tokens >= next_token_report:
                        reported_output_tokens = estimated_output_tokens
                        next_token_report += 50
                        yield await self._record_event(
                            task.id,
                            "progress",
                            {
                                "type": "progress",
                                "step": "model_output",
                                "status": "running",
                                "text": "模型正在输出页面 HTML",
                                "output_tokens": reported_output_tokens,
                                "token_source": "estimated",
                            },
                        )

                if chunk.type == "done" and chunk.usage:
                    usage = chunk.usage

            output_tokens = usage.output_tokens if usage and usage.output_tokens is not None else _estimate_output_tokens(answer_text_length)
            yield await self._record_event(
                task.id,
                "progress",
                {
                    "type": "progress",
                    "step": "model_output",
                    "status": "completed",
                    "text": "模型答案输出完成",
                    "output_tokens": output_tokens,
                    "token_source": "actual" if usage and usage.output_tokens is not None else "estimated",
                },
            )

            html_document = extract_html_document("".join(answer_parts))
            safe_html = sanitize_html(html_document)

            yield await self._record_event(
                task.id,
                "progress",
                {
                    "type": "progress",
                    "step": "upload",
                    "status": "running",
                    "text": "正在上传 HTML 到 OSS",
                },
            )
            version_id, storage_key = await self._upload_page_html(page, safe_html)
            yield await self._record_event(
                task.id,
                "progress",
                {
                    "type": "progress",
                    "step": "upload",
                    "status": "completed",
                    "text": "HTML 文件已上传",
                },
            )

            yield await self._record_event(
                task.id,
                "progress",
                {
                    "type": "progress",
                    "step": "database",
                    "status": "running",
                    "text": "正在写入页面版本和任务状态",
                },
            )
            version = await self._create_page_version(
                page=page,
                task=task,
                version_id=version_id,
                storage_key=storage_key,
                model_provider=model_provider,
                model_name=model_name,
                usage=usage,
            )

            page.current_version_id = version.id
            page.status = "ready"
            task.status = "succeeded"
            task.finished_at = datetime.now(UTC)
            await self.session.commit()
            yield await self._record_event(
                task.id,
                "progress",
                {
                    "type": "progress",
                    "step": "database",
                    "status": "completed",
                    "text": "数据库记录已更新",
                },
            )

            page_url = f"{self.settings.public_base_url.rstrip('/')}/p/{page.id}"
            yield await self._record_event(
                task.id,
                "completed",
                {"type": "completed", "page_id": str(page.id), "url": page_url},
            )

        except asyncio.CancelledError:
            task.status = "cancelled"
            task.error_message = "客户端连接断开，生成已取消"
            task.finished_at = datetime.now(UTC)
            page.status = "failed"
            await self.session.commit()
            raise
        except Exception as exc:
            task.status = "failed"
            task.error_message = str(exc)[:2000]
            task.finished_at = datetime.now(UTC)
            page.status = "failed"
            await self.session.commit()
            yield await self._record_event(
                task.id,
                "failed",
                {"type": "failed", "message": "页面生成失败，请稍后重试。"},
            )

    async def _upload_page_html(self, page: Page, html: str) -> tuple[uuid.UUID, str]:
        version_id = uuid.uuid4()
        storage_key = f"pages/{page.id}/versions/{version_id}/index.html"

        storage = create_storage_provider()
        await storage.put_text(storage_key, html)
        return version_id, storage_key

    async def _create_page_version(
        self,
        page: Page,
        task: GenerationTask,
        version_id: uuid.UUID,
        storage_key: str,
        model_provider: str | None,
        model_name: str | None,
        usage: LlmUsage | None,
    ) -> PageVersion:
        result = await self.session.execute(
            select(func.count(PageVersion.id)).where(PageVersion.page_id == page.id)
        )
        version_number = int(result.scalar_one()) + 1

        version = PageVersion(
            id=version_id,
            page_id=page.id,
            version_number=version_number,
            prompt=task.prompt,
            storage_key=storage_key,
            status="ready",
            model_provider=model_provider,
            model_name=model_name,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
        )
        self.session.add(version)
        await self.session.flush()
        return version

    async def _record_event(self, task_id: uuid.UUID, event_type: str, payload: dict[str, Any]) -> SseEvent:
        event = GenerationEvent(task_id=task_id, event_type=event_type, payload=payload)
        self.session.add(event)
        await self.session.commit()
        return SseEvent(event_type, payload)

    async def _stream_existing_events_until_done(self, task: GenerationTask) -> AsyncIterator[SseEvent]:
        last_sequence = 0

        while True:
            result = await self.session.execute(
                select(GenerationEvent)
                .where(GenerationEvent.task_id == task.id, GenerationEvent.sequence > last_sequence)
                .order_by(GenerationEvent.sequence.asc())
            )
            events = result.scalars().all()

            for event in events:
                last_sequence = event.sequence
                yield SseEvent(event.event_type, event.payload)

            await self.session.refresh(task)
            if task.status in TERMINAL_STATUSES and not events:
                break

            await asyncio.sleep(1)


def _build_page_title(prompt: str) -> str:
    clean_prompt = " ".join(prompt.strip().split())
    if len(clean_prompt) <= 40:
        return clean_prompt or "生成页面"
    return clean_prompt[:40] + "..."


def _estimate_output_tokens(text_length: int) -> int:
    if text_length <= 0:
        return 0
    return max(1, round(text_length / 2))
