from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_billing_config, get_model_registry, get_settings
from app.core.urls import build_page_url
from app.models.entities import (
    Conversation,
    GenerationBatch,
    GenerationEvent,
    GenerationTask,
    Page,
    PageAsset,
    PagePermission,
    PageVersion,
    User,
)
from app.services.anon_service import AnonService
from app.services.billing import BillingService
from app.services.billing.account import SettleResult
from app.services.consent_service import CONSENT_GRANTED, training_consent_for
from app.services.conversation_tree import ForkSource, pick_fallback_parent, view_batches
from app.services.document_parser.attachments import (
    DocumentContext,
    build_document_context,
    materialize_attachments,
    select_attachment_visuals,
)
from app.services.document_parser.context import LineageMaterial, collect_lineage
from app.services.document_parser.errors import DocumentStageError
from app.services.document_parser.extractor_agent import extract_for_round
from app.services.document_parser.pipeline import batch_needs_parse, refund_free_trial_once, wait_for_batch_parse
from app.services.html_sanitizer import extract_html_document, find_tailwind_runtime_violation, sanitize_html
from app.services.image_assets import StoredImage
from app.services.llm.client import create_llm_client
from app.services.llm.cost import estimate_llm_cost, merge_cost_breakdowns, merge_llm_usage, usage_to_payload
from app.services.llm.prompt import HTML_PAGE_SYSTEM_PROMPT, HTML_PAGE_TAILWIND_CORRECTION_PROMPT, build_skill_system_message
from app.services.llm.types import LlmCostBreakdown, LlmImagePart, LlmMessage, LlmUsage
from app.services.scenes.catalog import get_scene
from app.services.skills.registry import get_skill_registry
from app.services.skills.selector import SkillSelectionResult, select_skill_for_prompt
from app.services.sse import SseEvent
from app.services.storage.factory import create_storage_provider
from app.services.training_samples import store_training_sample

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
# 每个任务以且仅以其中一种事件收尾，并和终态写在同一个事务里，事件流据此判断任务结束。
TERMINAL_EVENT_TYPES = {"completed", "failed", "cancelled"}
CANCELLED_USER_MESSAGE = "已停止生成"


VISION_SKIPPED_MESSAGE = "该模型仅支持文本，已跳过图片视觉读取"
# 等待解析、提取时多久检查一次停止请求。
CANCEL_POLL_SECONDS = 0.5


class GenerationCancelled(Exception):
    """用户主动停止。在两个模型 chunk 之间检查到停止请求时抛出，走正常收尾写成 cancelled。"""


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
    # 这一轮分叉出了新会话时，记下分叉点所属的来源会话和轮次。
    forked_from: ForkSource | None = None


class GenerationService:
    def __init__(self, session: AsyncSession, cancel_check: Callable[[], bool] | None = None) -> None:
        self.session = session
        self.settings = get_settings()
        # 后台执行器传入：返回 True 表示用户已请求停止这个任务。
        self._cancel_check = cancel_check
        # 本任务还没落库的思考碎片。硬取消时直接丢掉，不在 finally 里刷。
        self._reasoning_parts: list[str] = []
        self._reasoning_chars = 0
        self._reasoning_flushed_at: float | None = None
        self._progress_flushed_at: float | None = None

    def _raise_if_cancelled(self) -> None:
        if self._cancel_check is not None and self._cancel_check():
            raise GenerationCancelled()

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
        staged_uploads: list[dict[str, Any]] | None = None,
        stored_images: list[StoredImage] | None = None,
        conversation_id: uuid.UUID | None = None,
        base_page_id: uuid.UUID | None = None,
        scene_key: str | None = None,
        utm_source: str | None = None,
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

        title = _build_page_title(title_prompt or prompt)
        # 授权快照在建批次这一刻定下，之后改开关不影响这一轮的数据归属和折扣。
        consent = training_consent_for(user)
        training_granted = consent == CONSENT_GRANTED
        has_documents = bool(staged_uploads or extracted_file_text or input_file_names)

        # 额度校验与动态预授权冻结：匿名查免费次数与 IP 天花板；登录加行锁预冻结积分并校验首秀券/欠费。
        reservation = await billing.reserve_for_batch(
            user=user,
            model_keys=model_keys,
            training_granted=training_granted,
            has_documents=has_documents,
        )
        if user.is_anonymous:
            await AnonService(self.session).check_ip_free_generation_ceiling(client_ip)

        # parent_for_model：每个模型的新节点接到哪个父节点。
        #  - 新会话(无 conversation_id)：根节点，父为空。
        #  - 续写且指定了基点 base_page_id：所有模型都从该节点派生。基点在最后一轮就留在本会话；
        #    在更早的轮次则分叉成一条新会话，原会话保持不变。
        #  - 续写但未指定基点：并行续写——都从最近一轮接着改，见 _parallel_parents。
        parent_for_model: dict[str, uuid.UUID | None]
        forked_from: ForkSource | None = None
        if conversation_id is not None:
            conversation = await self.session.get(Conversation, conversation_id)
            if conversation is None or conversation.owner_user_id != user.id or conversation.deleted_at is not None:
                raise ValueError("会话不存在")
            kind = "continue"
            view = await view_batches(self.session, conversation)

            if base_page_id is not None:
                base_page = await self.session.get(Page, base_page_id)
                view_ids = [batch.id for batch in view]
                if base_page is None or base_page.deleted_at is not None or base_page.batch_id not in view_ids:
                    raise ValueError("续写基点节点不存在或不属于该会话")
                parent_for_model = {key: base_page_id for key in model_keys}
                base_position = view_ids.index(base_page.batch_id)
                if base_position < len(view) - 1:
                    source_batch = view[base_position]
                    source = (
                        conversation
                        if source_batch.conversation_id == conversation.id
                        else await self.session.get(Conversation, source_batch.conversation_id)
                    )
                    forked_from = ForkSource(
                        conversation_id=source_batch.conversation_id,
                        title=source.title if source else conversation.title,
                        round_number=base_position + 1,
                        available=source is not None and source.deleted_at is None,
                    )
                    conversation = Conversation(
                        owner_user_id=user.id,
                        title=title,
                        origin="fork",
                        scene_key=conversation.scene_key,
                        utm_source=conversation.utm_source,
                        forked_from_conversation_id=source_batch.conversation_id,
                        forked_from_batch_id=source_batch.id,
                        fork_base_page_id=base_page.id,
                    )
                    self.session.add(conversation)
                    await self.session.flush()
            else:
                parent_for_model = await self._parallel_parents(view, model_keys)
            conversation.updated_at = datetime.now(UTC)
        else:
            conversation = Conversation(
                owner_user_id=user.id,
                title=title,
                origin="new",
                scene_key=scene_key,
                utm_source=utm_source,
            )
            self.session.add(conversation)
            await self.session.flush()
            kind = "create"
            parent_for_model = {key: None for key in model_keys}

        # 续写沿用会话上的场景，避免同一棵树中途换场景导致技能和链接口径不一致。
        effective_scene_key = conversation.scene_key or scene_key
        scene = get_scene(effective_scene_key)
        forced_skill = scene.skill_key if scene else None
        extra_skills = list(scene.extra_skill_keys) if scene else []
        if scene and not conversation.scene_key:
            conversation.scene_key = scene.key

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
            staged_uploads=list(staged_uploads or []),
            parse_status="pending" if staged_uploads else "none",
            scene_key=scene.key if scene else None,
            status="pending",
            training_consent=consent,
            used_flagship_trial=reservation.used_flagship_trial,
        )
        self.session.add(batch)
        await self.session.flush()

        if kind == "create" and conversation.root_batch_id is None:
            conversation.root_batch_id = batch.id

        runs: list[BatchRunRef] = []
        allocs = reservation.allocations
        for idx, model_key in enumerate(model_keys):
            alloc = allocs[idx] if idx < len(allocs) else None
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
                scene_key=scene.key if scene else None,
                skill_key=forced_skill,
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
                skill_key=forced_skill,
                extra_skill_keys=extra_skills,
                scene_key=scene.key if scene else None,
                status="pending",
                training_consent=consent,
                reserved_gift_credits=alloc.reserved_gift if alloc else 0,
                reserved_paid_credits=alloc.reserved_paid if alloc else 0,
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

        # 上传的图片和批次同一个事务提交：不会出现任务已建好、图片记录却没写进去的情况。
        for image in stored_images or []:
            self.session.add(
                PageAsset(
                    id=image.asset_id,
                    owner_user_id=user.id,
                    batch_id=batch.id,
                    storage_key=image.storage_key,
                    content_type=image.content_type,
                    filename=image.filename,
                    role=image.role,
                    byte_size=image.byte_size,
                    training_consent=consent,
                    source_kind=image.source_kind,
                    width=image.width,
                    height=image.height,
                    source_label=image.filename,
                )
            )

        await self.session.commit()
        return BatchCreation(
            conversation_id=conversation.id,
            batch_id=batch.id,
            kind=kind,
            runs=runs,
            forked_from=forked_from,
        )

    def _page_url(self, page: Page) -> str:
        return build_page_url(self.settings, page.conversation_id, page.id)

    async def _resolve_task_skill_key(self, *, page: Page, task: GenerationTask) -> SkillSelectionResult:
        """按 task 决定技能 key：续写延用 parent 链路；新建则用当前生成模型做 LLM 路由。"""
        if not self.settings.page_skills_enabled:
            return SkillSelectionResult(None, None)

        # 场景在创建任务时已经写好技能，跳过路由。
        if task.skill_key:
            return SkillSelectionResult(task.skill_key, None)

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

    async def _parallel_parents(
        self, view: list[GenerationBatch], model_keys: list[str]
    ) -> dict[str, uuid.UUID | None]:
        """并行续写：都从最近一轮接着改，不会串到别的分支，也不会跳回更早的轮次。
        参与了最近一轮且已完成的模型接自己那一版；新加入、上一轮没选或上一轮失败的模型，
        接最近一轮第一个已完成的结果。每轮可以自由增减模型。"""
        if not view:
            return {key: None for key in model_keys}
        latest_batch = view[-1]
        result = await self.session.execute(
            select(Page).where(Page.batch_id == latest_batch.id, Page.deleted_at.is_(None))
        )
        pages = list(result.scalars().all())
        ready_by_model = {page.model_key: page.id for page in pages if page.model_key and page.status == "ready"}
        fallback = pick_fallback_parent(pages, list(latest_batch.selected_models or []))
        return {key: ready_by_model.get(key, fallback) for key in model_keys}

    async def _build_llm_messages(
        self,
        task: GenerationTask,
        page: Page,
        doc_context: DocumentContext | None = None,
        images: tuple[LlmImagePart, ...] = (),
    ) -> list[LlmMessage]:
        """构造发给模型的消息。

        首轮：system(通用规则) + user(构造好的需求 + 本轮资料提取)。
        续写：system + user(历次全部需求清单) + assistant(最近一版完整 HTML) + user(本轮新指令 + 本轮资料提取)。
        策略：历次"指令"都带上（短、廉价，保留完整意图）；"答案"只保留最近一版 HTML
        （HTML 是完整快照，已包含此前全部改动，无需再带更早版本，避免上下文爆炸）。
        资料每轮重新提取，只放进本轮最后一条 user 消息；本轮要看的图片也挂在这条消息上。
        """
        system_messages = self._build_system_messages(task)
        extra = doc_context.prompt_block() if doc_context is not None else ""

        def current(text: str) -> LlmMessage:
            return LlmMessage(role="user", content=f"{text}\n\n{extra}" if extra else text, images=images)

        if page.parent_page_id is None:
            return [*system_messages, current(task.prompt)]

        parent_html = await self._load_node_html(page.parent_page_id)
        if not parent_html:
            # 拿不到上一版 HTML 时退化为普通生成，至少不报错。
            return [*system_messages, current(task.prompt)]

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
            current(continue_instruction),
        ]

    def _build_system_messages(self, task: GenerationTask) -> list[LlmMessage]:
        """通用系统提示 + （如选中技能则）该技能的专项指南。"""
        messages = [LlmMessage(role="system", content=HTML_PAGE_SYSTEM_PROMPT)]
        if not self.settings.page_skills_enabled:
            return messages
        registry = get_skill_registry()
        keys: list[str] = []
        if task.skill_key:
            keys.append(task.skill_key)
        for key in task.extra_skill_keys or []:
            if key not in keys:
                keys.append(key)
        for key in keys:
            skill = registry.get(key)
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
                storage_key = version.storage_key
                await self._release_read_txn("读取上一版页面")
                try:
                    storage = create_storage_provider()
                    return await storage.get_text(storage_key)
                except Exception:
                    return None
        return None

    async def claim_task(self, task_id: uuid.UUID) -> GenerationTask | None:
        """原子地把 pending 任务改成 running。返回 None 表示已被别人抢走或不再是 pending。"""
        result = await self.session.execute(
            update(GenerationTask)
            .where(GenerationTask.id == task_id, GenerationTask.status == "pending")
            .values(status="running", started_at=func.now())
            .returning(GenerationTask.id)
        )
        claimed = result.scalar_one_or_none()
        await self.session.commit()
        if claimed is None:
            return None
        return await self.session.get(GenerationTask, task_id, populate_existing=True)

    async def run_task(self, task: GenerationTask) -> AsyncIterator[SseEvent]:
        """跑完一个已认领的任务，逐条产出已写入数据库的事件。由后台执行器驱动，不依赖任何浏览器连接。"""
        page = await self.session.get(Page, task.page_id)
        if page is None:
            task.status = "failed"
            task.error_message = "页面记录不存在"
            task.finished_at = datetime.now(UTC)
            await BillingService(self.session).release_task_reservation(task)
            failed = self._stage_event(
                task.id, "failed", {"type": "failed", "message": "页面记录不存在", "model_key": task.model_key}
            )
            await self.session.commit()
            await self.recompute_batch_status(task.batch_id)
            await self._refund_flagship_trial_if_needed(task.batch_id)
            yield failed
            return

        if task.started_at is None:
            task.started_at = datetime.now(UTC)
            await self.session.commit()

        batch = await self.session.get(GenerationBatch, task.batch_id) if task.batch_id else None
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

        doc_context = DocumentContext()
        extraction_usage: LlmUsage | None = None
        router_usage: LlmUsage | None = None
        router_model_key: str | None = None
        wasted_retry_usage: LlmUsage | None = None
        messages: list[LlmMessage] = []
        answer_parts: list[str] = []
        answer_started = False
        model_provider: str | None = None
        model_name: str | None = None
        generation_usage: LlmUsage | None = None
        cost: LlmCostBreakdown | None = None
        reasoning_total_chars = 0
        answer_text_length = 0
        reported_output_tokens = 0
        next_token_report = 50
        tailwind_retry_used = False

        try:
            self._raise_if_cancelled()
            if batch is not None and batch.parse_status != "none":
                if batch_needs_parse(batch):
                    yield await self._progress(task.id, "parse_file", "running", "正在解析文件内容")
                    await self._await_cancellable(wait_for_batch_parse(batch.id, task.id))
                    await self.session.refresh(batch)
                    await self.session.refresh(task)
                elif batch.parse_status == "failed":
                    await self._await_cancellable(wait_for_batch_parse(batch.id, task.id))
                yield await self._progress(task.id, "parse_file", "completed", "文件内容已解析")

            material = await collect_lineage(self.session, batch) if batch is not None else LineageMaterial()
            if material.has_documents:
                yield await self._progress(task.id, "compress_document", "running", "正在结合本轮需求提取资料里的关键信息")
                doc_context = await self._prepare_document_context(task, page, material)
                extraction_usage = doc_context.usage
                task.extracted_context = doc_context.text
                task.compression_prompt = doc_context.prompt_log or None
                await self.session.commit()
                yield await self._progress(task.id, "compress_document", "completed", _extraction_summary(doc_context))

            yield await self._record_event(task.id, "status", {"type": "status", "text": "正在理解你的页面需求..."})

            skill_selection = await self._resolve_task_skill_key(page=page, task=task)
            task.skill_key = skill_selection.key
            page.skill_key = skill_selection.key
            router_usage = skill_selection.usage
            router_model_key = skill_selection.router_model_key
            await self.session.commit()
            self._raise_if_cancelled()

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

            llm_client = create_llm_client(task.model_key)
            model_config = get_model_registry().get(task.model_key or "")
            visuals = await select_attachment_visuals(self.session, batch, doc_context)
            await self._release_read_txn("附图下载")
            images, vision_skipped = await materialize_attachments(
                visuals, doc_context, supports_vision=bool(model_config and model_config.supports_vision)
            )
            if vision_skipped:
                yield await self._record_event(task.id, "status", {"type": "status", "text": VISION_SKIPPED_MESSAGE})
            messages = await self._build_llm_messages(task, page, doc_context, images)
            task.model_prompt = _model_prompt_record(messages, doc_context)
            await self.session.commit()

            llm_attempts = max(1, self.settings.llm_retry_attempts)
            llm_attempt = 1
            while True:
                stream = llm_client.stream_text(messages)
                try:
                    async for chunk in stream:
                        # 停止请求在两个 chunk 之间生效；finally 里关掉流，模型连接随之断开。
                        self._raise_if_cancelled()
                        model_provider = chunk.provider or model_provider
                        model_name = chunk.model or model_name

                        if chunk.type == "reasoning_delta" and chunk.reasoning_text:
                            reasoning_total_chars += len(chunk.reasoning_text)
                            self._buffer_reasoning(chunk.reasoning_text)
                            if self._reasoning_due():
                                payload = self._take_reasoning_payload()
                                if payload is not None:
                                    yield await self._record_event(task.id, "reasoning_delta", payload)

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
                                self._note_progress_written()
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
                            if estimated_output_tokens >= next_token_report and self._running_progress_due():
                                reported_output_tokens = estimated_output_tokens
                                next_token_report = estimated_output_tokens + 50
                                self._note_progress_written()
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
                            # 保留首轮违规已消耗的 usage 用于 6001 COGS 真实记账，对用户计价只收成功轮。
                            first_round_usage = generation_usage or _estimate_stream_usage(
                                messages, reasoning_total_chars, answer_text_length
                            )
                            wasted_retry_usage = merge_llm_usage(wasted_retry_usage, first_round_usage)
                            messages = [
                                *messages,
                                LlmMessage(role="assistant", content=model_output_text),
                                LlmMessage(role="user", content=HTML_PAGE_TAILWIND_CORRECTION_PROMPT),
                            ]
                            answer_parts.clear()
                            answer_started = False
                            generation_usage = None
                            reasoning_total_chars = 0
                            answer_text_length = 0
                            reported_output_tokens = 0
                            next_token_report = 50
                            yield await self._record_event(
                                task.id,
                                "status",
                                {"type": "status", "text": f"{violation.message}，正在要求模型改用普通内联 CSS..."},
                            )
                            self._note_progress_written()
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
                    self._raise_if_cancelled()
                    llm_attempt += 1
                except GenerationCancelled:
                    raise
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
                    self._raise_if_cancelled()
                    llm_attempt += 1
                finally:
                    await stream.aclose()

            # 模型已完整输出后不再响应停止：页面马上就能用，丢掉反而浪费这次调用。
            if not answer_parts:
                raise ValueError("模型未返回 HTML 正文")

            model_output_text = "".join(answer_parts)
            html_document = extract_html_document(model_output_text)

            # 技能路由单独按实际轻量模型（如 qwen-plus）单价核计，避免误按写码旗舰单价高估。
            router_cost = (
                estimate_llm_cost(router_model_key or task.model_key or "", router_usage)
                if router_usage is not None
                else None
            )
            billable_main_usage = merge_llm_usage(extraction_usage, generation_usage)
            billable_cost = merge_cost_breakdowns(
                router_cost, estimate_llm_cost(task.model_key or "", billable_main_usage)
            )

            full_main_usage = merge_llm_usage(billable_main_usage, wasted_retry_usage)
            full_cost = merge_cost_breakdowns(
                router_cost, estimate_llm_cost(task.model_key or "", full_main_usage)
            )

            usage = merge_llm_usage(router_usage, full_main_usage)
            cost = full_cost or billable_cost
            output_tokens = (
                usage.output_tokens
                if usage and usage.output_tokens is not None
                else _estimate_output_tokens(answer_text_length)
            )
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

            # 计费结算：向用户按成功轮 billable_cost 计价扣积分，总账 6001/1102 按含重试的真实 full_cost 入账。
            settle = await self._settle_generation_billing(
                task, version, billable_cost or cost, ledger_cost=cost
            )
            # 授权生成当场提取匿名结构样本：等到事后再提，用户可能已撤回授权。
            if task.training_consent == CONSENT_GRANTED:
                await self._store_training_sample(task, safe_html)

            deploy_done = self._stage_event(
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
            completed_payload["billing"] = {
                "training_consent": task.training_consent,
                "credits_charged": settle.credits_charged if settle else 0,
                "discount_credits": settle.discount_credits if settle else 0,
                "reserved_credits": settle.reserved_credits if settle else 0,
                "refunded_credits": settle.refunded_credits if settle else 0,
            }
            # 终态、版本、结算和收尾事件同一个事务提交：事件流看到终态时一定也能读到 completed。
            completed = self._stage_event(task.id, "completed", completed_payload)
            await self.session.commit()
            await self.recompute_batch_status(task.batch_id)
            yield deploy_done
            yield completed

        except GenerationCancelled:
            if answer_parts and not task.model_output_text:
                task.model_output_text = "".join(answer_parts)
            task.status = "cancelled"
            task.error_message = "用户停止了生成"
            task.finished_at = datetime.now(UTC)
            page.status = "failed"
            await self._settle_cancelled_generation(
                task=task,
                messages=messages,
                reasoning_chars=reasoning_total_chars,
                answer_chars=answer_text_length,
                generation_usage=generation_usage,
                extraction_usage=extraction_usage,
                wasted_retry_usage=wasted_retry_usage,
                router_usage=router_usage,
                router_model_key=router_model_key,
            )
            cancelled = self._stage_event(
                task.id,
                "cancelled",
                {"type": "cancelled", "message": CANCELLED_USER_MESSAGE, "model_key": task.model_key},
            )
            await self.session.commit()
            await self.recompute_batch_status(task.batch_id)
            await self._refund_flagship_trial_if_needed(task.batch_id)
            yield cancelled
        except Exception as exc:
            if answer_parts and not task.model_output_text:
                task.model_output_text = "".join(answer_parts)
            document_stage = isinstance(exc, DocumentStageError)
            user_message = exc.user_message if document_stage else "页面生成失败，请稍后重试。"
            task.status = "failed"
            task.error_message = ((DocumentStageError.ERROR_PREFIX if document_stage else "") + str(exc))[:2000]
            task.finished_at = datetime.now(UTC)
            page.status = "failed"
            await self._release_task_reservation_safe(task)
            failed = self._stage_event(
                task.id,
                "failed",
                {"type": "failed", "message": user_message, "model_key": task.model_key},
            )
            await self.session.commit()
            await self.recompute_batch_status(task.batch_id)
            if document_stage:
                await self._refund_if_batch_failed_on_documents(task.batch_id)
            await self._refund_flagship_trial_if_needed(task.batch_id)
            yield failed

    async def _progress(self, task_id: uuid.UUID, step: str, status: str, text: str) -> SseEvent:
        return await self._record_event(task_id, "progress", {"type": "progress", "step": step, "status": status, "text": text})

    async def _release_read_txn(self, reason: str) -> None:
        """只读查完后把连接还回池。会话里还有没提交的修改时不动，避免把写入悄悄提交或丢掉。"""
        if self.session.new or self.session.dirty or self.session.deleted:
            print(f"[generation] 跳过结束只读事务（{reason}）：会话有未提交修改", flush=True)
            return
        if self.session.in_transaction():
            await self.session.commit()

    async def _await_cancellable(self, awaitable: Any) -> Any:
        """等解析、提取这类较长的步骤时也响应停止：每隔一会儿检查一次，停止时取消等待。"""
        waiter = asyncio.ensure_future(awaitable)
        try:
            while not waiter.done():
                await asyncio.wait({waiter}, timeout=CANCEL_POLL_SECONDS)
                if not waiter.done():
                    self._raise_if_cancelled()
            return waiter.result()
        except BaseException:
            if not waiter.done():
                waiter.cancel()
            raise

    async def _prepare_document_context(self, task: GenerationTask, page: Page, material: LineageMaterial) -> DocumentContext:
        """同模型、关闭思考、纯文本提取本轮用得上的资料。

        纯视觉资料（几乎没有文字、主要信息在快照或大图里）在它上传的那一轮跳过提取，直接把图交给写码模型。
        """
        if material.visual_only and material.current_batch_has_documents:
            return build_document_context(material, None, visual_direct=True)
        history = await self._collect_ancestor_instructions(page.parent_page_id) if page.parent_page_id else []
        await self._release_read_txn("资料提取")
        extraction = await self._await_cancellable(
            extract_for_round(
                model_key=task.model_key or "",
                document_text=material.text,
                history=history,
                current=task.user_prompt or task.prompt,
            )
        )
        return build_document_context(material, extraction, visual_direct=False)

    async def _refund_if_batch_failed_on_documents(self, batch_id: uuid.UUID | None) -> None:
        """整批都结束、且每一路都失败在资料阶段（解析或提取）时，退回一次匿名免费次数。"""
        if batch_id is None:
            return
        result = await self.session.execute(
            select(GenerationTask.status, GenerationTask.error_message).where(GenerationTask.batch_id == batch_id)
        )
        rows = result.all()
        if not rows or any(status != "failed" for status, _message in rows):
            return
        if all((message or "").startswith(DocumentStageError.ERROR_PREFIX) for _status, message in rows):
            await refund_free_trial_once(self.session, batch_id)

    async def _settle_generation_billing(
        self,
        task: GenerationTask,
        version: PageVersion,
        cost: LlmCostBreakdown | None,
        *,
        ledger_cost: LlmCostBreakdown | None = None,
    ) -> SettleResult | None:
        """对一次成功生成结算计费，按 version_id 幂等。失败不阻断生成主流程。"""
        actor = await self.session.get(User, task.requested_by_user_id)
        if actor is None:
            return None
        markup = get_billing_config().markup_for(task.model_key)
        raw_cost = cost.total_cost_cny if cost is not None else None
        ledger_cogs = ledger_cost.total_cost_cny if ledger_cost is not None else raw_cost
        try:
            async with self.session.begin_nested():
                return await BillingService(self.session).settle_generation(
                    user=actor,
                    version_id=version.id,
                    model_key=task.model_key,
                    raw_cost_cny=raw_cost,
                    markup=markup,
                    training_granted=task.training_consent == CONSENT_GRANTED,
                    task=task,
                    ledger_cogs_cny=ledger_cogs,
                )
        except Exception as exc:  # noqa: BLE001 - 结算失败仅告警，回退解冻不卡死资金
            print(f"[billing] 结算失败 version={version.id}: {exc}")
            await self._release_task_reservation_safe(task)
            return None

    async def _release_task_reservation_safe(self, task: GenerationTask) -> None:
        try:
            async with self.session.begin_nested():
                await BillingService(self.session).release_task_reservation(task)
        except Exception as exc:  # noqa: BLE001
            print(f"[billing] 释放任务预授权失败 task={task.id}: {exc}")

    async def _refund_flagship_trial_if_needed(self, batch_id: uuid.UUID | None) -> None:
        if batch_id is None:
            return
        try:
            await BillingService(self.session).refund_flagship_trial_if_batch_all_failed(batch_id)
            await self.session.commit()
        except Exception as exc:  # noqa: BLE001
            await self.session.rollback()
            print(f"[billing] 检查并退回首秀券失败 batch={batch_id}: {exc}")

    async def _settle_cancelled_generation(
        self,
        *,
        task: GenerationTask,
        messages: list[LlmMessage],
        reasoning_chars: int,
        answer_chars: int,
        generation_usage: LlmUsage | None,
        extraction_usage: LlmUsage | None,
        wasted_retry_usage: LlmUsage | None,
        router_usage: LlmUsage | None,
        router_model_key: str | None,
    ) -> None:
        """用户主动取消时的结算：把思考字符 + 正文字符一并纳入输出 Token 估算。"""
        try:
            async with self.session.begin_nested():
                actor = await self.session.get(User, task.requested_by_user_id)
                if actor is None:
                    await BillingService(self.session).release_task_reservation(task)
                    return

                stream_usage = generation_usage or _estimate_stream_usage(messages, reasoning_chars, answer_chars)
                est_out_tokens = 0
                if stream_usage and stream_usage.output_tokens:
                    est_out_tokens += int(stream_usage.output_tokens)
                if wasted_retry_usage and wasted_retry_usage.output_tokens:
                    est_out_tokens += int(wasted_retry_usage.output_tokens)

                main_usage = merge_llm_usage(
                    merge_llm_usage(extraction_usage, stream_usage), wasted_retry_usage
                )
                router_cost = (
                    estimate_llm_cost(router_model_key or task.model_key or "", router_usage)
                    if router_usage is not None
                    else None
                )
                total_cost = merge_cost_breakdowns(
                    router_cost, estimate_llm_cost(task.model_key or "", main_usage)
                )
                await BillingService(self.session).settle_cancelled_task(
                    task=task,
                    user=actor,
                    estimated_output_tokens=est_out_tokens,
                    raw_cost_cny=total_cost.total_cost_cny if total_cost else None,
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[billing] 取消任务结算失败 task={task.id}: {exc}")
            await self._release_task_reservation_safe(task)

    async def _store_training_sample(self, task: GenerationTask, html: str) -> None:
        """保存匿名结构样本。放在保存点里，失败只丢样本，不影响页面和计费。"""
        try:
            async with self.session.begin_nested():
                await store_training_sample(self.session, html=html, scene_key=task.scene_key, model_key=task.model_key)
        except Exception as exc:  # noqa: BLE001
            print(f"[training] 匿名结构样本提取失败 task={task.id}: {exc}")

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
            training_consent=task.training_consent,
        )
        self.session.add(version)
        await self.session.flush()
        return version

    async def recompute_batch_status(self, batch_id: uuid.UUID | None) -> None:
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

    def _buffer_reasoning(self, text: str) -> None:
        self._reasoning_parts.append(text)
        self._reasoning_chars += len(text)

    def _reasoning_due(self) -> bool:
        if not self._reasoning_parts:
            return False
        # 本任务第一段马上写，卡片据此切到思考中。之后只看间隔和字数，没有定时器。
        if self._reasoning_flushed_at is None:
            return True
        if time.monotonic() - self._reasoning_flushed_at >= self.settings.generation_reasoning_flush_seconds:
            return True
        return self._reasoning_chars >= self.settings.generation_reasoning_flush_chars

    def _take_reasoning_payload(self) -> dict[str, Any] | None:
        if not self._reasoning_parts:
            return None
        text = "".join(self._reasoning_parts)
        self._reasoning_parts.clear()
        self._reasoning_chars = 0
        self._reasoning_flushed_at = time.monotonic()
        return {"type": "reasoning_delta", "text": text}

    def _stage_buffered_reasoning(self, task_id: uuid.UUID) -> None:
        payload = self._take_reasoning_payload()
        if payload is None:
            return
        self.session.add(GenerationEvent(task_id=task_id, event_type="reasoning_delta", payload=payload))

    def _running_progress_due(self) -> bool:
        if self._progress_flushed_at is None:
            return True
        return time.monotonic() - self._progress_flushed_at >= self.settings.generation_progress_flush_seconds

    def _note_progress_written(self) -> None:
        self._progress_flushed_at = time.monotonic()

    async def _record_event(self, task_id: uuid.UUID, event_type: str, payload: dict[str, Any]) -> SseEvent:
        event = self._stage_event(task_id, event_type, payload)
        await self.session.commit()
        return event

    async def record_event(self, task_id: uuid.UUID, event_type: str, payload: dict[str, Any]) -> SseEvent:
        """执行器写排队、开始这类不属于生成流程本身的事件。"""
        return await self._record_event(task_id, event_type, payload)

    def _stage_event(self, task_id: uuid.UUID, event_type: str, payload: dict[str, Any]) -> SseEvent:
        """只加入会话不提交，用来和状态变更放进同一个事务。

        非思考事件先把本任务还没落库的思考放进同一事务，保证任务内序号顺序不变。
        """
        if event_type != "reasoning_delta":
            self._stage_buffered_reasoning(task_id)
        self.session.add(GenerationEvent(task_id=task_id, event_type=event_type, payload=payload))
        return SseEvent(event_type, payload)

    async def finish_unfinished_task(self, task_id: uuid.UUID, *, status: str, message: str, user_message: str) -> bool:
        """把仍未结束的任务直接写成终态（硬取消、执行器兜底、启动扫尾用）。已结束的不动，返回是否改了。"""
        # 行锁：停止接口和执行器兜底可能同时收尾同一个任务，只能有一方写终态和收尾事件。
        task = await self.session.get(GenerationTask, task_id, populate_existing=True, with_for_update=True)
        if task is None or task.status in TERMINAL_STATUSES:
            await self.session.rollback()
            return False
        task.status = status
        task.error_message = message
        task.finished_at = datetime.now(UTC)
        page = await self.session.get(Page, task.page_id)
        if page is not None and page.status == "generating":
            page.status = "failed"

        if status == "cancelled":
            # 若硬取消前已落库部分思考/正文，汇总估算后按取消规则结算；排队中未跑过的估算为 0 将直接全额解冻。
            ev_rows = (
                await self.session.execute(
                    select(GenerationEvent.payload).where(
                        GenerationEvent.task_id == task.id,
                        GenerationEvent.event_type == "reasoning_delta",
                    )
                )
            ).scalars().all()
            reasoning_chars = sum(len(str((p or {}).get("text") or "")) for p in ev_rows)
            answer_chars = len(task.model_output_text or "")
            prompt_msgs = [LlmMessage(role="user", content=task.model_prompt or task.prompt or "")]
            await self._settle_cancelled_generation(
                task=task,
                messages=prompt_msgs,
                reasoning_chars=reasoning_chars,
                answer_chars=answer_chars,
                generation_usage=None,
                extraction_usage=None,
                wasted_retry_usage=None,
                router_usage=None,
                router_model_key=None,
            )
        else:
            await self._release_task_reservation_safe(task)

        event_type = "cancelled" if status == "cancelled" else "failed"
        self._stage_event(task.id, event_type, {"type": event_type, "message": user_message, "model_key": task.model_key})
        await self.session.commit()
        await self.recompute_batch_status(task.batch_id)
        await self._refund_flagship_trial_if_needed(task.batch_id)
        return True


def _extraction_summary(context: DocumentContext) -> str:
    if context.mode == "visual":
        return "资料以图片为主，已直接交给模型查看"
    if context.mode == "empty":
        return "本轮需求与资料无关，未引用资料"
    return "已提取与本轮需求相关的资料"


def _model_prompt_record(messages: list[LlmMessage], context: DocumentContext) -> str:
    """调试记录：最后一条 user 消息的文字，附图只记说明，不存图片数据。"""
    text = messages[-1].content if messages else ""
    if context.attached_labels:
        text += "\n\n[本轮附图] " + "；".join(context.attached_labels)
    return text


def _build_page_title(prompt: str) -> str:
    clean_prompt = " ".join(prompt.strip().split())
    if len(clean_prompt) <= 40:
        return clean_prompt or "生成页面"
    return clean_prompt[:40] + "..."


def _estimate_output_tokens(text_length: int) -> int:
    if text_length <= 0:
        return 0
    return max(1, round(text_length / 2))


def _estimate_stream_usage(
    messages: list[LlmMessage], reasoning_chars: int, answer_chars: int
) -> LlmUsage | None:
    """当上游流在中途取消或未返回最终 usage 帧时，按已收到的思考 + 正文字符数估算 Token。"""
    total_out_chars = max(0, int(reasoning_chars)) + max(0, int(answer_chars))
    if total_out_chars <= 0:
        return None
    in_chars = sum(len(m.content or "") for m in messages)
    in_tokens = _estimate_output_tokens(in_chars)
    reason_tokens = _estimate_output_tokens(reasoning_chars) if reasoning_chars > 0 else 0
    out_tokens = _estimate_output_tokens(total_out_chars)
    return LlmUsage(
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        total_tokens=in_tokens + out_tokens,
        reasoning_tokens=reason_tokens or None,
    )


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
