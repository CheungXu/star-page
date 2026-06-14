from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_billing_config, get_model_registry, get_settings
from app.core.urls import build_page_url
from app.models.entities import (
    Conversation,
    GenerationBatch,
    GenerationEvent,
    GenerationTask,
    Page,
    PagePermission,
    PageVersion,
    User,
)
from app.services.anon_service import AnonService
from app.services.billing import BillingService
from app.services.html_sanitizer import extract_html_document, find_tailwind_runtime_violation, sanitize_html
from app.services.llm.client import create_llm_client
from app.services.llm.cost import cost_to_payload, estimate_llm_cost, merge_llm_usage, usage_to_payload
from app.services.llm.prompt import HTML_PAGE_SYSTEM_PROMPT, HTML_PAGE_TAILWIND_CORRECTION_PROMPT, build_skill_system_message
from app.services.llm.types import LlmCostBreakdown, LlmMessage, LlmUsage
from app.services.skills.registry import get_skill_registry
from app.services.skills.selector import SkillSelectionResult, select_skill_for_prompt
from app.services.sse import SseEvent
from app.services.storage.factory import create_storage_provider

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


@dataclass
class BatchRunRef:
    """批次内一个模型 run 的引用信息（供 API 组装响应）。"""

    task_id: uuid.UUID
    page_id: uuid.UUID
    model_key: str
    model_label: str
    page_url: str


@dataclass
class BatchCreation:
    conversation_id: uuid.UUID
    batch_id: uuid.UUID
    kind: str
    runs: list[BatchRunRef] = field(default_factory=list)


class GenerationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    def _resolve_model_keys(
        self, selected_model_keys: list[str] | None, allowed_keys: set[str] | None = None
    ) -> list[str]:
        registry = get_model_registry()
        available = {model.key for model in registry.available_models()}
        if allowed_keys is not None:
            available = available & allowed_keys

        requested = [key for key in (selected_model_keys or []) if key in available]
        if not requested:
            requested = [key for key in registry.default_model_keys if key in available]
        if not requested and available:
            requested = [next(iter(available))]
        if not requested:
            raise ValueError("没有可用模型，请检查 config/llm.models.json 与对应密钥")

        # 去重并保持顺序
        seen: set[str] = set()
        ordered: list[str] = []
        for key in requested:
            if key not in seen:
                seen.add(key)
                ordered.append(key)
        return ordered

    async def create_batch(
        self,
        *,
        prompt: str,
        selected_model_keys: list[str] | None = None,
        title_prompt: str | None = None,
        user_prompt: str | None = None,
        input_file_names: list[str] | None = None,
        extracted_file_text: str | None = None,
        compression_prompt: str | None = None,
        conversation_id: uuid.UUID | None = None,
        base_page_id: uuid.UUID | None = None,
        user: User,
        client_ip: str | None = None,
    ) -> BatchCreation:
        """创建一轮生成：会话(新建/复用) -> 批次 -> 每个模型一个 Page 节点 + Task。"""
        registry = get_model_registry()
        billing = BillingService(self.session)

        # 匿名：先按显式选择强校验模型白名单与数量（防绕过前端置灰），再把可用集合限定到允许范围。
        allowed_keys: set[str] | None = None
        if user.is_anonymous:
            billing.validate_anon_models(list(selected_model_keys or []))
            allowed_keys = set(billing.config.anon_allowed_models)

        model_keys = self._resolve_model_keys(selected_model_keys, allowed_keys=allowed_keys)

        # 额度/余额前置校验：匿名查免费次数与 IP 天花板，登录查积分余额。
        await billing.ensure_can_start_batch(user, model_keys)
        if user.is_anonymous:
            await AnonService(self.session).check_ip_free_generation_ceiling(client_ip)

        title = _build_page_title(title_prompt or prompt)

        # parent_for_model：每个模型的新节点接到哪个父节点。
        #  - 新会话(无 conversation_id)：根节点，父为空。
        #  - 续写且指定了基点 base_page_id：分支——所有模型都从该节点派生。
        #  - 续写但未指定基点：并行续写——每个模型各自接上自己在本会话的最新节点。
        parent_for_model: dict[str, uuid.UUID | None]
        if conversation_id is not None:
            conversation = await self.session.get(Conversation, conversation_id)
            if conversation is None or conversation.owner_user_id != user.id or conversation.deleted_at is not None:
                raise ValueError("会话不存在")
            conversation.updated_at = datetime.now(UTC)
            kind = "continue"

            if base_page_id is not None:
                base_page = await self.session.get(Page, base_page_id)
                if base_page is None or base_page.conversation_id != conversation.id:
                    raise ValueError("续写基点节点不存在或不属于该会话")
                parent_for_model = {key: base_page_id for key in model_keys}
            else:
                parent_for_model = await self._latest_node_per_model(conversation.id, model_keys)
        else:
            conversation = Conversation(owner_user_id=user.id, title=title, origin="new")
            self.session.add(conversation)
            await self.session.flush()
            kind = "create"
            parent_for_model = {key: None for key in model_keys}

        batch = GenerationBatch(
            conversation_id=conversation.id,
            base_page_id=base_page_id,
            kind=kind,
            selected_models=list(model_keys),
            prompt=prompt,
            user_prompt=user_prompt or title_prompt or prompt,
            input_file_names=input_file_names or [],
            extracted_file_text=extracted_file_text or None,
            compression_prompt=compression_prompt,
            status="pending",
        )
        self.session.add(batch)
        await self.session.flush()

        if kind == "create" and conversation.root_batch_id is None:
            conversation.root_batch_id = batch.id

        runs: list[BatchRunRef] = []
        for model_key in model_keys:
            model = registry.get(model_key)
            page = Page(
                owner_user_id=user.id,
                title=title,
                visibility="public",
                status="generating",
                conversation_id=conversation.id,
                batch_id=batch.id,
                parent_page_id=parent_for_model.get(model_key),
                model_key=model_key,
                model_provider=model.provider if model else None,
                model_name=model.model if model else None,
            )
            self.session.add(page)
            await self.session.flush()

            task = GenerationTask(
                page_id=page.id,
                batch_id=batch.id,
                requested_by_user_id=user.id,
                model_key=model_key,
                model_provider=model.provider if model else None,
                model_name=model.model if model else None,
                prompt=prompt,
                user_prompt=user_prompt or title_prompt or prompt,
                input_file_names=input_file_names or [],
                extracted_file_text=extracted_file_text or None,
                compression_prompt=compression_prompt,
                model_prompt=prompt,
                status="pending",
            )
            permission = PagePermission(page_id=page.id, user_id=user.id, role="owner")
            self.session.add_all([task, permission])
            await self.session.flush()

            runs.append(
                BatchRunRef(
                    task_id=task.id,
                    page_id=page.id,
                    model_key=model_key,
                    model_label=model.label if model else model_key,
                    page_url=self._page_url(page),
                )
            )

        # 匿名批次启动计一次免费额度（按批次计，非按模型）。
        await billing.record_anon_batch_started(user)

        await self.session.commit()
        return BatchCreation(
            conversation_id=conversation.id,
            batch_id=batch.id,
            kind=kind,
            runs=runs,
        )

    def _page_url(self, page: Page) -> str:
        return build_page_url(self.settings, page.conversation_id, page.id)

    async def _resolve_task_skill_key(self, *, page: Page, task: GenerationTask) -> SkillSelectionResult:
        """按 task 决定技能 key：续写延用 parent 链路；新建则用当前生成模型做 LLM 路由。"""
        if not self.settings.page_skills_enabled:
            return SkillSelectionResult(None, None)

        if page.parent_page_id is not None:
            inherited = await self._skill_key_from_chain(page.parent_page_id)
            return SkillSelectionResult(inherited, None)

        route_prompt = task.user_prompt or task.prompt
        if not task.model_key:
            return SkillSelectionResult(None, None)
        return await select_skill_for_prompt(route_prompt, router_model=task.model_key)

    async def _skill_key_from_chain(self, page_id: uuid.UUID) -> str | None:
        current_id: uuid.UUID | None = page_id
        visited: set[uuid.UUID] = set()
        while current_id is not None and current_id not in visited and len(visited) < 100:
            visited.add(current_id)
            node = await self.session.get(Page, current_id)
            if node is None:
                break
            if node.skill_key:
                return node.skill_key
            current_id = node.parent_page_id
        return None

    async def _latest_node_per_model(
        self, conversation_id: uuid.UUID, model_keys: list[str]
    ) -> dict[str, uuid.UUID | None]:
        """并行续写：取会话内每个模型最新的节点作为父节点；模型若是新加入则父为空。"""
        result = await self.session.execute(
            select(Page)
            .where(Page.conversation_id == conversation_id, Page.deleted_at.is_(None))
            .order_by(Page.created_at.desc())
        )
        latest_by_model: dict[str, uuid.UUID] = {}
        for page in result.scalars().all():
            if page.model_key and page.model_key not in latest_by_model:
                latest_by_model[page.model_key] = page.id
        return {key: latest_by_model.get(key) for key in model_keys}

    async def _build_llm_messages(self, task: GenerationTask, page: Page) -> list[LlmMessage]:
        """构造发给模型的消息。

        首轮：system(通用规则) + user(构造好的需求)。
        续写：system + user(历次全部需求清单) + assistant(最近一版完整 HTML) + user(本轮新指令)。
        策略：历次"指令"都带上（短、廉价，保留完整意图）；"答案"只保留最近一版 HTML
        （HTML 是完整快照，已包含此前全部改动，无需再带更早版本，避免上下文爆炸）。
        """
        system_messages = self._build_system_messages(task)

        if page.parent_page_id is None:
            return [*system_messages, LlmMessage(role="user", content=task.prompt)]

        parent_html = await self._load_node_html(page.parent_page_id)
        if not parent_html:
            # 拿不到上一版 HTML 时退化为普通生成，至少不报错。
            return [*system_messages, LlmMessage(role="user", content=task.prompt)]

        instructions = await self._collect_ancestor_instructions(page.parent_page_id)
        if instructions:
            history_lines = "\n".join(f"{index}. {text}" for index, text in enumerate(instructions, start=1))
            history_text = "这是该页面到目前为止的历次需求（按先后顺序）：\n" + history_lines
        else:
            history_text = "这是该页面到目前为止的需求。"
        continue_instruction = (
            "请在上面这版页面的基础上，按以下新的要求调整，并输出修改后的完整 HTML（未提到的部分尽量保持不变）：\n"
            + task.prompt
        )
        return [
            *system_messages,
            LlmMessage(role="user", content=history_text),
            LlmMessage(role="assistant", content=parent_html),
            LlmMessage(role="user", content=continue_instruction),
        ]

    def _build_system_messages(self, task: GenerationTask) -> list[LlmMessage]:
        """通用系统提示 + （如选中技能则）该技能的专项指南。"""
        messages = [LlmMessage(role="system", content=HTML_PAGE_SYSTEM_PROMPT)]
        if self.settings.page_skills_enabled and task.skill_key:
            skill = get_skill_registry().get(task.skill_key)
            if skill is not None:
                messages.append(LlmMessage(role="system", content=build_skill_system_message(skill)))
        return messages

    async def _collect_ancestor_instructions(self, parent_page_id: uuid.UUID) -> list[str]:
        """沿 parent_page_id 向上收集历次"指令"（每个祖先节点对应那一轮的用户需求），按时间正序返回。"""
        instructions: list[str] = []
        current_id: uuid.UUID | None = parent_page_id
        visited: set[uuid.UUID] = set()

        while current_id is not None and current_id not in visited and len(visited) < 100:
            visited.add(current_id)
            node = await self.session.get(Page, current_id)
            if node is None:
                break
            node_task = await self._latest_task_for_page(node.id)
            instruction = (node_task.user_prompt or node_task.prompt) if node_task else None
            if instruction:
                instructions.append(instruction)
            current_id = node.parent_page_id

        instructions.reverse()  # 根节点在前
        return instructions

    async def _latest_task_for_page(self, page_id: uuid.UUID) -> GenerationTask | None:
        result = await self.session.execute(
            select(GenerationTask)
            .where(GenerationTask.page_id == page_id)
            .order_by(GenerationTask.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _load_node_html(self, page_id: uuid.UUID) -> str | None:
        """取某节点的完整 HTML：优先用任务里记录的模型输出，回退到 OSS 的已发布版本。"""
        task = await self._latest_task_for_page(page_id)
        if task and task.model_output_text:
            extracted = extract_html_document(task.model_output_text)
            if extracted:
                return extracted

        page = await self.session.get(Page, page_id)
        if page and page.current_version_id:
            version = await self.session.get(PageVersion, page.current_version_id)
            if version is not None:
                try:
                    storage = create_storage_provider()
                    return await storage.get_text(version.storage_key)
                except Exception:
                    return None
        return None

    async def run_or_replay(self, task_id: uuid.UUID, user: User) -> AsyncIterator[SseEvent]:
        task = await self.session.get(GenerationTask, task_id)
        if task is None:
            yield SseEvent("failed", {"type": "failed", "message": "生成任务不存在"})
            return
        if task.requested_by_user_id != user.id:
            yield SseEvent("failed", {"type": "failed", "message": "无权访问该生成任务"})
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

        if task.input_file_names:
            yield await self._record_event(
                task.id,
                "progress",
                {
                    "type": "progress",
                    "step": "upload_file",
                    "status": "completed",
                    "text": "文件已上传到服务端",
                },
            )
            yield await self._record_event(
                task.id,
                "progress",
                {
                    "type": "progress",
                    "step": "parse_file",
                    "status": "completed",
                    "text": "文件内容已解析",
                },
            )

            compress_text = "长文本已压缩为页面生成简报" if task.compression_prompt else "内容未超过阈值，无需压缩"
            yield await self._record_event(
                task.id,
                "progress",
                {
                    "type": "progress",
                    "step": "compress_document",
                    "status": "completed",
                    "text": compress_text,
                },
            )

        yield await self._record_event(task.id, "status", {"type": "status", "text": "正在理解你的页面需求..."})

        skill_selection = await self._resolve_task_skill_key(page=page, task=task)
        task.skill_key = skill_selection.key
        page.skill_key = skill_selection.key
        router_usage = skill_selection.usage
        await self.session.commit()

        if skill_selection.key:
            skill = get_skill_registry().get(skill_selection.key)
            skill_payload: dict[str, Any] = {
                "type": "skill_selected",
                "skill_key": skill_selection.key,
                "skill_name": skill.name if skill else skill_selection.key,
                "model_key": task.model_key,
            }
            if router_usage is not None:
                skill_payload["router_usage"] = usage_to_payload(router_usage)
            yield await self._record_event(task.id, "skill_selected", skill_payload)

        yield await self._record_event(
            task.id,
            "progress",
            {
                "type": "progress",
                "step": "model_thinking",
                "status": "running",
                "text": "模型正在理解需求和资料",
            },
        )

        answer_parts: list[str] = []
        answer_started = False
        model_provider: str | None = None
        model_name: str | None = None
        generation_usage: LlmUsage | None = None
        cost: LlmCostBreakdown | None = None
        answer_text_length = 0
        reported_output_tokens = 0
        next_token_report = 50
        tailwind_retry_used = False

        try:
            llm_client = create_llm_client(task.model_key)
            messages = await self._build_llm_messages(task, page)

            llm_attempts = max(1, self.settings.llm_retry_attempts)
            llm_attempt = 1
            while True:
                try:
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
                                yield await self._record_event(
                                    task.id,
                                    "progress",
                                    {
                                        "type": "progress",
                                        "step": "model_thinking",
                                        "status": "completed",
                                        "text": "模型思考完成",
                                    },
                                )
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
                            generation_usage = chunk.usage

                    if answer_parts:
                        model_output_text = "".join(answer_parts)
                        html_document = extract_html_document(model_output_text)
                        violation = find_tailwind_runtime_violation(html_document)
                        if violation and not tailwind_retry_used:
                            tailwind_retry_used = True
                            messages = [
                                *messages,
                                LlmMessage(role="assistant", content=model_output_text),
                                LlmMessage(role="user", content=HTML_PAGE_TAILWIND_CORRECTION_PROMPT),
                            ]
                            answer_parts.clear()
                            answer_started = False
                            generation_usage = None
                            answer_text_length = 0
                            reported_output_tokens = 0
                            next_token_report = 50
                            yield await self._record_event(
                                task.id,
                                "status",
                                {"type": "status", "text": f"{violation.message}，正在要求模型改用普通内联 CSS..."},
                            )
                            yield await self._record_event(
                                task.id,
                                "progress",
                                {
                                    "type": "progress",
                                    "step": "model_output",
                                    "status": "running",
                                    "text": "正在重新输出不依赖 Tailwind 的自包含 HTML",
                                    "output_tokens": 0,
                                    "token_source": "estimated",
                                },
                            )
                            continue
                        if violation:
                            raise ValueError(violation.message)
                        break

                    if llm_attempt >= llm_attempts:
                        break

                    generation_usage = None
                    answer_text_length = 0
                    reported_output_tokens = 0
                    next_token_report = 50
                    yield await self._record_event(
                        task.id,
                        "status",
                        {"type": "status", "text": f"模型未返回正文，正在重试（{llm_attempt + 1}/{llm_attempts}）..."},
                    )
                    await asyncio.sleep(_retry_delay_seconds(self.settings, llm_attempt))
                    llm_attempt += 1
                except Exception:
                    if answer_started or llm_attempt >= llm_attempts:
                        raise

                    answer_parts.clear()
                    generation_usage = None
                    answer_text_length = 0
                    reported_output_tokens = 0
                    next_token_report = 50
                    yield await self._record_event(
                        task.id,
                        "status",
                        {"type": "status", "text": f"模型调用失败，正在重试（{llm_attempt + 1}/{llm_attempts}）..."},
                    )
                    await asyncio.sleep(_retry_delay_seconds(self.settings, llm_attempt))
                    llm_attempt += 1

            if not answer_parts:
                raise ValueError("模型未返回 HTML 正文")

            model_output_text = "".join(answer_parts)
            html_document = extract_html_document(model_output_text)

            usage = merge_llm_usage(router_usage, generation_usage)
            output_tokens = (
                usage.output_tokens
                if usage and usage.output_tokens is not None
                else _estimate_output_tokens(answer_text_length)
            )
            cost = estimate_llm_cost(task.model_key, usage) if usage else None
            progress_payload: dict[str, Any] = {
                "type": "progress",
                "step": "model_output",
                "status": "completed",
                "text": "模型答案输出完成",
                "output_tokens": output_tokens,
                "token_source": "actual" if usage and usage.output_tokens is not None else "estimated",
            }
            if usage and usage.input_tokens is not None:
                progress_payload["input_tokens"] = usage.input_tokens
            if usage and usage.total_tokens is not None:
                progress_payload["total_tokens"] = usage.total_tokens
            if usage and usage.cached_input_tokens is not None:
                progress_payload["cached_input_tokens"] = usage.cached_input_tokens
            if usage and usage.reasoning_tokens is not None:
                progress_payload["reasoning_tokens"] = usage.reasoning_tokens
            if router_usage is not None:
                progress_payload["router_usage"] = usage_to_payload(router_usage)
            if cost is not None:
                progress_payload["cost"] = cost_to_payload(cost)
            yield await self._record_event(task.id, "progress", progress_payload)

            task.model_output_text = model_output_text or None

            safe_html = sanitize_html(html_document)

            yield await self._record_event(
                task.id,
                "progress",
                {
                    "type": "progress",
                    "step": "deploy",
                    "status": "running",
                    "text": "正在上传 HTML 并更新数据库",
                },
            )
            version_id, storage_key = await self._upload_page_html(page, safe_html)
            task.output_html_storage_key = _build_storage_debug_link(storage_key, self.settings.object_storage_bucket)
            version = await self._create_page_version(
                page=page,
                task=task,
                version_id=version_id,
                storage_key=storage_key,
                model_provider=model_provider,
                model_name=model_name,
                usage=usage,
                cost=cost,
            )

            page.current_version_id = version.id
            page.status = "ready"
            task.status = "succeeded"
            task.finished_at = datetime.now(UTC)

            # 计费结算：按 page_version 幂等扣积分/记账（匿名仅记成本，登录扣积分确认收入）。
            await self._settle_generation_billing(task, version, cost)

            await self.session.commit()
            await self._recompute_batch_status(task.batch_id)
            yield await self._record_event(
                task.id,
                "progress",
                {
                    "type": "progress",
                    "step": "deploy",
                    "status": "completed",
                    "text": "HTML 已上传，数据库记录已更新",
                },
            )

            page_url = self._page_url(page)
            completed_payload: dict[str, Any] = {
                "type": "completed",
                "page_id": str(page.id),
                "url": page_url,
                "model_key": task.model_key,
            }
            if usage is not None:
                completed_payload["usage"] = usage_to_payload(usage)
            if cost is not None:
                completed_payload["cost"] = cost_to_payload(cost)
            yield await self._record_event(task.id, "completed", completed_payload)

        except asyncio.CancelledError:
            task.status = "cancelled"
            task.error_message = "客户端连接断开，生成已取消"
            task.finished_at = datetime.now(UTC)
            page.status = "failed"
            await self.session.commit()
            await self._recompute_batch_status(task.batch_id)
            raise
        except Exception as exc:
            if answer_parts and not task.model_output_text:
                task.model_output_text = "".join(answer_parts)
            task.status = "failed"
            task.error_message = str(exc)[:2000]
            task.finished_at = datetime.now(UTC)
            page.status = "failed"
            await self.session.commit()
            await self._recompute_batch_status(task.batch_id)
            yield await self._record_event(
                task.id,
                "failed",
                {"type": "failed", "message": "页面生成失败，请稍后重试。", "model_key": task.model_key},
            )

    async def _settle_generation_billing(
        self, task: GenerationTask, version: PageVersion, cost: LlmCostBreakdown | None
    ) -> None:
        """对一次成功生成结算计费，按 version_id 幂等。失败不阻断生成主流程。"""
        actor = await self.session.get(User, task.requested_by_user_id)
        if actor is None:
            return
        markup = get_billing_config().markup_for(task.model_key)
        raw_cost = cost.total_cost_cny if cost is not None else None
        try:
            await BillingService(self.session).settle_generation(
                user=actor,
                version_id=version.id,
                model_key=task.model_key,
                raw_cost_cny=raw_cost,
                markup=markup,
            )
        except Exception as exc:  # noqa: BLE001 - 结算失败仅告警，不影响已生成页面
            print(f"[billing] 结算失败 version={version.id}: {exc}")

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
        cost: LlmCostBreakdown | None = None,
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
            model_key=task.model_key,
            batch_id=task.batch_id,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            cached_input_tokens=usage.cached_input_tokens if usage else None,
            reasoning_tokens=usage.reasoning_tokens if usage else None,
            input_cost_cny=cost.input_cost_cny if cost else None,
            output_cost_cny=cost.output_cost_cny if cost else None,
            total_cost_cny=cost.total_cost_cny if cost else None,
        )
        self.session.add(version)
        await self.session.flush()
        return version

    async def _recompute_batch_status(self, batch_id: uuid.UUID | None) -> None:
        """按兄弟 run 的状态聚合批次状态：全成功=succeeded，部分成功=partial，全取消=cancelled，否则 failed；未完成=running。"""
        if batch_id is None:
            return

        batch = await self.session.get(GenerationBatch, batch_id)
        if batch is None:
            return

        result = await self.session.execute(
            select(GenerationTask.status).where(GenerationTask.batch_id == batch_id)
        )
        statuses = [row[0] for row in result.all()]
        if not statuses:
            return

        all_done = all(status in TERMINAL_STATUSES for status in statuses)
        if not all_done:
            new_status = "running"
        elif all(status == "succeeded" for status in statuses):
            new_status = "succeeded"
        elif any(status == "succeeded" for status in statuses):
            new_status = "partial"
        elif all(status == "cancelled" for status in statuses):
            new_status = "cancelled"
        else:
            new_status = "failed"

        batch.status = new_status
        if batch.started_at is None:
            batch.started_at = datetime.now(UTC)
        if all_done and batch.finished_at is None:
            batch.finished_at = datetime.now(UTC)

        conversation = await self.session.get(Conversation, batch.conversation_id)
        if conversation is not None:
            conversation.updated_at = datetime.now(UTC)

        await self.session.commit()

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


def _retry_delay_seconds(settings: Any, attempt: int) -> float:
    delay_ms = min(
        settings.llm_retry_max_delay_ms,
        settings.llm_retry_initial_delay_ms * (2 ** max(0, attempt - 1)),
    )
    return delay_ms / 1000


def _build_storage_debug_link(storage_key: str, bucket: str) -> str:
    if bucket:
        return f"oss://{bucket}/{storage_key}"
    return storage_key
