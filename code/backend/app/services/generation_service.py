from __future__ import annotations

import asyncio
from json import JSONDecodeError
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
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
from app.services.llm.prompt import (
    HTML_PAGE_EDIT_TRUNCATION_CONTINUE_PROMPT,
    HTML_PAGE_SYSTEM_PROMPT,
    HTML_PAGE_TAILWIND_CORRECTION_PROMPT,
    HTML_PAGE_TRUNCATION_CONTINUE_PROMPT,
    build_page_edit_continue_prompt,
    build_page_edit_fallback_prompt,
    build_skill_system_message,
)
from app.services.llm.types import LlmCostBreakdown, LlmImagePart, LlmMessage, LlmModelConfig, LlmUsage
from app.services.page_edit import (
    DIVIDER_MARKER,
    REPLACE_MARKER,
    SEARCH_MARKER,
    EditBlock,
    ModelOutputKind,
    apply_page_edits,
    classify_model_output,
    normalize_newlines,
    parse_edit_blocks,
)
from app.services.page_render_check import check_page_render
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
TRUNCATION_CONTINUE_STATUS_TEXT = "输出被截断，正在续写"
TRUNCATED_USER_MESSAGE = "页面内容过长，未能完整生成"
EDIT_FALLBACK_STATUS_TEXT = "局部修改未能精确匹配原文，正在改为生成完整页面..."
RENDER_CHECK_STATUS_TEXT = "正在检查页面渲染效果..."
RENDER_REPAIR_STATUS_TEXT = "检测到页面存在显示或脚本问题，正在自动修复..."
RENDER_RECHECK_STATUS_TEXT = "正在复检修复后的页面渲染效果..."
# 等待解析、提取时多久检查一次停止请求。
CANCEL_POLL_SECONDS = 0.5


class GenerationCancelled(Exception):
    """用户主动停止。在两个模型 chunk 之间检查到停止请求时抛出，走正常收尾写成 cancelled。"""


class GenerationTruncatedError(Exception):
    """模型输出被截断且用尽自动续写次数后仍未完成：标失败并全额退回预扣积分。"""

    def __init__(self, message: str = TRUNCATED_USER_MESSAGE) -> None:
        super().__init__(message)
        self.user_message = message


class TaskLost(Exception):
    """任务已不归本 worker（续期过期后被别的 worker 接手）。已回滚，什么都不再写。"""


# 渲染自动修复轮仅吞掉上游模型/截断类可恢复异常以保住修前已生成的页面；数据库异常与系统错误照常外抛。
_REPAIR_RECOVERABLE_ERRORS = (
    GenerationTruncatedError,
    httpx.HTTPError,
    JSONDecodeError,
    ValueError,
)


@dataclass
class RoundResult:
    """单轮模型流式调用的结果（流式期间实时更新，供中途取消时读取已收到的字符与用量）。"""

    answer_parts: list[str] = field(default_factory=list)
    reasoning_chars: int = 0
    answer_chars: int = 0
    usage: LlmUsage | None = None
    finish_reason: str | None = None
    model_provider: str | None = None
    model_name: str | None = None

    @property
    def text(self) -> str:
        return "".join(self.answer_parts)


@dataclass
class _GenerationTracker:
    """跨多轮调用（截断续写、Tailwind 重试及后续局部编辑回退/渲染修复）追踪正文与双账本用量。"""

    current_messages: list[LlmMessage] = field(default_factory=list)
    current_round: RoundResult = field(default_factory=RoundResult)
    accumulated_text: str = ""
    last_output_text: str = ""
    model_provider: str | None = None
    model_name: str | None = None
    # 当前候选尝试内已跑完各轮的 (真实 usage, 含字符估算兜底的 usage)
    attempt_rounds: list[tuple[LlmUsage | None, LlmUsage | None]] = field(default_factory=list)
    # 已确认计入用户账单的轮次 (真实 usage, 含字符估算兜底的 usage)
    billable_rounds: list[tuple[LlmUsage | None, LlmUsage | None]] = field(default_factory=list)
    # 平台承担成本的废弃轮次（如 Tailwind 违规重试的首轮）
    wasted_retry_usage: LlmUsage | None = None

    def record_round_finished(self, *, joined_text: str) -> None:
        round_result = self.current_round
        self.model_provider = round_result.model_provider or self.model_provider
        self.model_name = round_result.model_name or self.model_name
        estimated = round_result.usage or _estimate_stream_usage(
            self.current_messages, round_result.reasoning_chars, round_result.answer_chars
        )
        self.attempt_rounds.append((round_result.usage, estimated))
        self.accumulated_text = joined_text
        self.current_round = RoundResult()

    def commit_attempt_as_billable(self) -> None:
        self.billable_rounds.extend(self.attempt_rounds)
        self.attempt_rounds.clear()
        self.last_output_text = self.accumulated_text
        self.accumulated_text = ""

    def discard_attempt_as_wasted(self) -> None:
        for _actual, estimated in self.attempt_rounds:
            self.wasted_retry_usage = merge_llm_usage(self.wasted_retry_usage, estimated)
        self.attempt_rounds.clear()
        self.accumulated_text = ""

    def billable_usage(self) -> LlmUsage | None:
        if not self.billable_rounds:
            return None
        if not any(actual is not None for actual, _estimated in self.billable_rounds):
            return None
        merged: LlmUsage | None = None
        for actual, estimated in self.billable_rounds:
            merged = merge_llm_usage(merged, actual or estimated)
        return merged

    def prior_billable_estimated_usage(self) -> LlmUsage | None:
        merged: LlmUsage | None = None
        for _actual, estimated in (*self.billable_rounds, *self.attempt_rounds):
            merged = merge_llm_usage(merged, estimated)
        return merged

    def partial_output_text(self) -> str:
        cur = self.current_round.text
        if self.accumulated_text and cur:
            return join_continued_output(self.accumulated_text, cur)
        return self.accumulated_text or cur or self.last_output_text


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


@dataclass(frozen=True)
class _PreparedMessages:
    """组装发给模型的初始消息链，同时携带续写局部编辑所需的上一版原文基准与开关状态。"""

    messages: list[LlmMessage]
    parent_html: str | None = None
    use_edit_mode: bool = False
    history_summary: str | None = None


class GenerationService:
    def __init__(
        self, session: AsyncSession, cancel_check: Callable[[], bool] | None = None, owner: str | None = None
    ) -> None:
        self.session = session
        self.settings = get_settings()
        # 后台执行器传入：返回 True 表示用户已请求停止这个任务。
        self._cancel_check = cancel_check
        # 后台执行器传入本 worker 的 worker_id：写终态前确认任务仍归自己。
        self._owner = owner
        # 本任务还没落库的思考碎片。硬取消时直接丢掉，不在 finally 里刷。
        self._reasoning_parts: list[str] = []
        self._reasoning_chars = 0
        self._reasoning_flushed_at: float | None = None
        self._progress_flushed_at: float | None = None

    def _raise_if_cancelled(self) -> None:
        if self._cancel_check is not None and self._cancel_check():
            raise GenerationCancelled()

    async def _fence(self, task_id: uuid.UUID) -> None:
        """写终态前锁住任务行，确认仍是本 worker 在跑。锁到提交为止，接手和本次写入不会交错。

        不归自己了就回滚本事务里的所有修改（版本、结算、终态）并抛 TaskLost。
        """
        if self._owner is None:
            return
        row = (
            await self.session.execute(
                select(GenerationTask.status, GenerationTask.worker_id)
                .where(GenerationTask.id == task_id)
                .with_for_update()
            )
        ).first()
        if row is None or row.status != "running" or row.worker_id != self._owner:
            await self.session.rollback()
            raise TaskLost()

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
        # 同一事务里 now() 都一样；按选模型的顺序错开 1 微秒，认领时按这个先后排队。
        created_base = datetime.now(UTC)
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
                created_at=created_base + timedelta(microseconds=idx),
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
        *,
        model_config: LlmModelConfig | None = None,
    ) -> list[LlmMessage]:
        """构造发给模型的消息（保持返回 list[LlmMessage] 向后兼容）。"""
        prepared = await self._prepare_llm_messages(
            task,
            page,
            doc_context,
            images,
            model_config=model_config,
        )
        return prepared.messages

    async def _prepare_llm_messages(
        self,
        task: GenerationTask,
        page: Page,
        doc_context: DocumentContext | None = None,
        images: tuple[LlmImagePart, ...] = (),
        *,
        model_config: LlmModelConfig | None = None,
    ) -> _PreparedMessages:
        """构造发给模型的消息并返回上一版 HTML 基准与局部编辑启用状态。

        首轮：system(通用规则) + user(构造好的需求 + 本轮资料提取)。
        续写：system + user(历次全部需求清单) + assistant(最近一版完整 HTML) + user(本轮新指令 + 本轮资料提取)。
        策略：历次"指令"都带上（短、廉价，保留完整意图）；"答案"只保留最近一版 HTML
        （HTML 是完整快照，已包含此前全部改动，无需再带更早版本，避免上下文爆炸）。
        资料每轮重新提取，只放进本轮最后一条 user 消息；本轮要看的图片也挂在这条消息上。
        当满足续写条件且全局与模型均开启局部编辑时，使用 SEARCH/REPLACE 二选一续写指令，
        并将发给 assistant 的同一份 parent_html 原文作为后续编辑应用的唯一基准。
        """
        system_messages = self._build_system_messages(task)
        extra = doc_context.prompt_block() if doc_context is not None else ""

        def current(text: str) -> LlmMessage:
            return LlmMessage(role="user", content=f"{text}\n\n{extra}" if extra else text, images=images)

        if page.parent_page_id is None:
            return _PreparedMessages(messages=[*system_messages, current(task.prompt)])

        parent_html = await self._load_node_html(page.parent_page_id)
        if not parent_html:
            # 拿不到上一版 HTML 时退化为普通生成，至少不报错。
            return _PreparedMessages(messages=[*system_messages, current(task.prompt)])

        instructions = await self._collect_ancestor_instructions(page.parent_page_id)
        if instructions:
            history_lines = "\n".join(f"{index}. {text}" for index, text in enumerate(instructions, start=1))
            history_text = "这是该页面到目前为止的历次需求（按先后顺序）：\n" + history_lines
        else:
            history_text = "这是该页面到目前为止的需求。"

        resolved_cfg = model_config or get_model_registry().get(task.model_key or "")
        model_allows_edit = bool(getattr(resolved_cfg, "edit_mode", True)) if resolved_cfg is not None else True
        use_edit_mode = bool(self.settings.page_edit_mode_enabled and model_allows_edit)

        if use_edit_mode:
            continue_instruction = build_page_edit_continue_prompt(task.prompt)
        else:
            continue_instruction = (
                "请在上面这版页面的基础上，按以下新的要求调整，并输出修改后的完整 HTML（未提到的部分尽量保持不变）：\n"
                + task.prompt
            )

        messages = [
            *system_messages,
            LlmMessage(role="user", content=history_text),
            LlmMessage(role="assistant", content=parent_html),
            current(continue_instruction),
        ]
        return _PreparedMessages(
            messages=messages,
            parent_html=parent_html,
            use_edit_mode=use_edit_mode,
            history_summary=history_text,
        )

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
            await self._fence(task.id)
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
        tracker = _GenerationTracker()
        cost: LlmCostBreakdown | None = None
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
            prepared = await self._prepare_llm_messages(
                task, page, doc_context, images, model_config=model_config
            )
            messages = list(prepared.messages)
            system_messages = [m for m in messages if m.role == "system"]
            edit_base_html: str | None = prepared.parent_html if prepared.use_edit_mode else None
            tailwind_retry_base_html: str | None = None
            edit_fallback_used = False
            collected_edit_texts: list[str] = []

            tracker.current_messages = messages
            task.model_prompt = _model_prompt_record(messages, doc_context)
            await self.session.commit()

            final_html_document = ""
            final_model_output_text = ""

            while True:
                async for event in self._run_with_truncation_continue(
                    task_id=task.id,
                    llm_client=llm_client,
                    messages=messages,
                    tracker=tracker,
                ):
                    yield event

                raw_output = tracker.accumulated_text
                used_edit_this_attempt = False

                if edit_base_html is not None:
                    output_kind = classify_model_output(raw_output)
                    if output_kind == ModelOutputKind.EDIT_BLOCKS:
                        edit_result = apply_page_edits(edit_base_html, raw_output)
                        if edit_result.ok and edit_result.html is not None:
                            used_edit_this_attempt = True
                            candidate_output_text = edit_result.html
                            html_document = extract_html_document(candidate_output_text)
                        elif not edit_fallback_used:
                            edit_fallback_used = True
                            edit_base_html = None
                            # 局部编辑失败回退的模型调用计入用户账单。
                            tracker.commit_attempt_as_billable()
                            fallback_prompt = build_page_edit_fallback_prompt(edit_result.error_message)
                            messages = [
                                *messages,
                                LlmMessage(role="assistant", content=raw_output),
                                LlmMessage(role="user", content=fallback_prompt),
                            ]
                            yield await self._record_event(
                                task.id,
                                "status",
                                {"type": "status", "text": EDIT_FALLBACK_STATUS_TEXT},
                            )
                            self._note_progress_written()
                            yield await self._record_event(
                                task.id,
                                "progress",
                                {
                                    "type": "progress",
                                    "step": "model_output",
                                    "status": "running",
                                    "text": "正在改为生成完整页面 HTML",
                                    "output_tokens": 0,
                                    "token_source": "estimated",
                                },
                            )
                            continue
                        else:
                            raise ValueError(edit_result.error_message or "局部编辑应用失败")
                    elif output_kind == ModelOutputKind.FULL_HTML:
                        candidate_output_text = raw_output
                        html_document = extract_html_document(candidate_output_text)
                    else:
                        # MALFORMED（残缺编辑块标记或无 <html）：处于局部编辑且尚未回退过时同样计入账单并回退整页重写。
                        probe = apply_page_edits(edit_base_html, raw_output)
                        err_reason = probe.error_message or "模型输出格式异常，未包含有效编辑块或 HTML"
                        if not edit_fallback_used:
                            edit_fallback_used = True
                            edit_base_html = None
                            tracker.commit_attempt_as_billable()
                            fallback_prompt = build_page_edit_fallback_prompt(err_reason)
                            messages = [
                                *messages,
                                LlmMessage(role="assistant", content=raw_output),
                                LlmMessage(role="user", content=fallback_prompt),
                            ]
                            yield await self._record_event(
                                task.id,
                                "status",
                                {"type": "status", "text": EDIT_FALLBACK_STATUS_TEXT},
                            )
                            self._note_progress_written()
                            yield await self._record_event(
                                task.id,
                                "progress",
                                {
                                    "type": "progress",
                                    "step": "model_output",
                                    "status": "running",
                                    "text": "正在改为生成完整页面 HTML",
                                    "output_tokens": 0,
                                    "token_source": "estimated",
                                },
                            )
                            continue
                        raise ValueError(err_reason)
                elif tailwind_retry_base_html is not None:
                    output_kind = classify_model_output(raw_output)
                    if output_kind == ModelOutputKind.FULL_HTML:
                        candidate_output_text = raw_output
                        html_document = extract_html_document(candidate_output_text)
                    elif output_kind == ModelOutputKind.EDIT_BLOCKS:
                        tw_edit = apply_page_edits(tailwind_retry_base_html, raw_output)
                        if tw_edit.ok and tw_edit.html is not None:
                            used_edit_this_attempt = True
                            candidate_output_text = tw_edit.html
                            html_document = extract_html_document(candidate_output_text)
                        else:
                            raise ValueError(tw_edit.error_message or "Tailwind 修正轮局部编辑块应用失败")
                    else:
                        raise ValueError("Tailwind 修正轮模型未返回有效 HTML 文档")
                else:
                    output_kind = classify_model_output(raw_output)
                    if edit_fallback_used and output_kind != ModelOutputKind.FULL_HTML:
                        raise ValueError("回退整页重写后模型仍未返回有效 HTML 文档")
                    if output_kind == ModelOutputKind.EDIT_BLOCKS or (
                        parse_edit_blocks(raw_output).error_code == "malformed_markers"
                    ):
                        raise ValueError("模型返回了局部编辑块标记，但当前无可应用的基准页面")
                    if output_kind != ModelOutputKind.FULL_HTML and not _contains_html_element(raw_output):
                        raise ValueError("模型未返回有效 HTML 内容")
                    candidate_output_text = raw_output
                    html_document = extract_html_document(candidate_output_text)

                violation = await asyncio.to_thread(find_tailwind_runtime_violation, html_document)
                if violation and not tailwind_retry_used:
                    tailwind_retry_used = True
                    if used_edit_this_attempt:
                        collected_edit_texts.append(raw_output)
                    edit_base_html = None
                    tailwind_retry_base_html = html_document
                    # 保留首轮违规已消耗的 usage 用于 6001 COGS 真实记账，对用户计价只收成功轮。
                    tracker.discard_attempt_as_wasted()
                    messages = [
                        *messages,
                        LlmMessage(role="assistant", content=html_document),
                        LlmMessage(role="user", content=HTML_PAGE_TAILWIND_CORRECTION_PROMPT),
                    ]
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

                tracker.commit_attempt_as_billable()
                if used_edit_this_attempt:
                    collected_edit_texts.append(raw_output)
                    final_model_output_text = html_document
                else:
                    final_model_output_text = candidate_output_text
                final_html_document = html_document
                tracker.last_output_text = final_model_output_text
                break

            usage, billable_cost, cost = _summarize_usage_and_cost(
                task_model_key=task.model_key or "",
                tracker=tracker,
                extraction_usage=extraction_usage,
                router_usage=router_usage,
                router_model_key=router_model_key,
            )
            yield await self._record_event(
                task.id,
                "progress",
                _build_model_output_completed_payload(
                    usage=usage,
                    router_usage=router_usage,
                    answer_text_length=len(final_model_output_text),
                ),
            )

            safe_html = await asyncio.to_thread(sanitize_html, final_html_document)
            render_mode = self.settings.page_render_check_mode
            render_check_dict: dict[str, Any] | None = None
            render_issue_payload: dict[str, Any] | None = None

            if render_mode in {"observe", "repair"}:
                yield await self._record_event(
                    task.id,
                    "status",
                    {"type": "status", "text": RENDER_CHECK_STATUS_TEXT},
                )
                yield await self._record_event(
                    task.id,
                    "progress",
                    {
                        "type": "progress",
                        "step": "deploy",
                        "status": "running",
                        "text": "正在检查页面渲染效果",
                    },
                )
                await self._release_read_txn("渲染检查")
                first_check = await self._await_cancellable(check_page_render(safe_html))

                if render_mode == "observe":
                    # observe 模式：只检查并记录到 page_versions.render_check，不修复、不向前端打标记。
                    render_check_dict = first_check.to_dict(
                        repaired=False, mode="observe", expose_issue=False
                    )
                    render_issue_payload = None
                elif first_check.has_issues:
                    yield await self._record_event(
                        task.id,
                        "status",
                        {"type": "status", "text": RENDER_REPAIR_STATUS_TEXT},
                    )
                    self._note_progress_written()
                    yield await self._record_event(
                        task.id,
                        "progress",
                        {
                            "type": "progress",
                            "step": "model_output",
                            "status": "running",
                            "text": "正在自动修复页面显示或脚本问题",
                            "output_tokens": 0,
                            "token_source": "estimated",
                        },
                    )

                    model_allows_edit = (
                        bool(getattr(model_config, "edit_mode", True)) if model_config is not None else True
                    )
                    repair_allow_edit = bool(self.settings.page_edit_mode_enabled and model_allows_edit)
                    repair_prompt = first_check.format_for_repair_prompt(
                        prefer_edit_blocks=repair_allow_edit
                    )
                    repair_context_text = _build_repair_context_text(task, prepared.history_summary)
                    repair_messages = [
                        *system_messages,
                        LlmMessage(role="user", content=repair_context_text),
                        LlmMessage(role="assistant", content=final_html_document),
                        LlmMessage(role="user", content=repair_prompt),
                    ]

                    repair_applied = False
                    repair_raw = ""
                    try:
                        async for event in self._run_with_truncation_continue(
                            task_id=task.id,
                            llm_client=llm_client,
                            messages=repair_messages,
                            tracker=tracker,
                            initial_status_text="正在自动修复页面问题...",
                            output_progress_text="正在输出修复后的页面代码",
                        ):
                            yield event
                        repair_raw = tracker.accumulated_text
                        # 修复轮无论是否修好，一律并入用户计费账单。
                        tracker.commit_attempt_as_billable()
                        tracker.last_output_text = final_model_output_text
                    except GenerationCancelled:
                        raise
                    except _REPAIR_RECOVERABLE_ERRORS as repair_exc:
                        if not getattr(self.session, "is_active", True):
                            raise
                        print(f"[render-check] 自动修复轮模型异常 task={task.id}: {repair_exc}", flush=True)
                        if tracker.attempt_rounds:
                            tracker.commit_attempt_as_billable()
                        tracker.current_round = RoundResult()
                        tracker.accumulated_text = ""
                        tracker.last_output_text = final_model_output_text

                    if repair_raw:
                        repair_kind = classify_model_output(repair_raw)
                        candidate_repaired_html: str | None = None
                        if repair_kind == ModelOutputKind.EDIT_BLOCKS:
                            repair_edit = apply_page_edits(final_html_document, repair_raw)
                            if repair_edit.ok and repair_edit.html is not None:
                                candidate_repaired_html = extract_html_document(repair_edit.html)
                        elif repair_kind == ModelOutputKind.FULL_HTML:
                            candidate_repaired_html = extract_html_document(repair_raw)

                        if candidate_repaired_html is not None:
                            tw_violation = await asyncio.to_thread(
                                find_tailwind_runtime_violation, candidate_repaired_html
                            )
                            if tw_violation is None:
                                if repair_kind == ModelOutputKind.EDIT_BLOCKS:
                                    collected_edit_texts.append(repair_raw)
                                    final_model_output_text = candidate_repaired_html
                                else:
                                    final_model_output_text = repair_raw
                                final_html_document = candidate_repaired_html
                                tracker.last_output_text = final_model_output_text
                                safe_html = await asyncio.to_thread(sanitize_html, final_html_document)
                                repair_applied = True

                    # 重新汇总包含修复轮用量的账单，并刷新 model_output 完成事件
                    usage, billable_cost, cost = _summarize_usage_and_cost(
                        task_model_key=task.model_key or "",
                        tracker=tracker,
                        extraction_usage=extraction_usage,
                        router_usage=router_usage,
                        router_model_key=router_model_key,
                    )
                    yield await self._record_event(
                        task.id,
                        "progress",
                        _build_model_output_completed_payload(
                            usage=usage,
                            router_usage=router_usage,
                            answer_text_length=len(final_model_output_text),
                        ),
                    )

                    if repair_applied:
                        yield await self._record_event(
                            task.id,
                            "status",
                            {"type": "status", "text": RENDER_RECHECK_STATUS_TEXT},
                        )
                        yield await self._record_event(
                            task.id,
                            "progress",
                            {
                                "type": "progress",
                                "step": "deploy",
                                "status": "running",
                                "text": "正在复检修复后的页面渲染效果",
                            },
                        )
                        await self._release_read_txn("渲染复检")
                        final_check = await self._await_cancellable(check_page_render(safe_html))
                    else:
                        final_check = first_check

                    render_check_dict = final_check.to_dict(
                        repaired=True, mode="repair", expose_issue=True
                    )
                    if not repair_applied:
                        render_check_dict["repair_applied"] = False
                    render_issue_payload = final_check.to_render_issue()
                else:
                    render_check_dict = first_check.to_dict(
                        repaired=False, mode="repair", expose_issue=True
                    )
                    render_issue_payload = None

            # 模型调用与渲染检查已全部完成，不再响应停止：照常上传发布并结算。
            model_provider = tracker.model_provider
            model_name = tracker.model_name
            task.model_output_text = final_model_output_text or None
            if collected_edit_texts:
                task.model_edit_text = "\n\n".join(collected_edit_texts)

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
            version_kwargs: dict[str, Any] = {
                "page": page,
                "task": task,
                "version_id": version_id,
                "storage_key": storage_key,
                "model_provider": model_provider,
                "model_name": model_name,
                "usage": usage,
                "cost": cost,
            }
            if render_check_dict is not None:
                version_kwargs["render_check"] = render_check_dict
            version = await self._create_page_version(**version_kwargs)

            await self._fence(task.id)
            page.current_version_id = version.id
            page.status = "ready"
            task.status = "succeeded"
            task.finished_at = datetime.now(UTC)

            # 计费结算：向用户按 billable_cost 计价扣积分，总账 6001/1102 按含重试的真实 full_cost 入账。
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
                "render_issue": render_issue_payload,
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

        except TaskLost:
            raise
        except GenerationCancelled:
            if not getattr(self.session, "is_active", True):
                await self.session.rollback()
            await self._fence(task.id)
            partial_output = tracker.partial_output_text()
            if partial_output and not task.model_output_text:
                task.model_output_text = partial_output
            task.status = "cancelled"
            task.error_message = "用户停止了生成"
            task.finished_at = datetime.now(UTC)
            page.status = "failed"
            await self._settle_cancelled_generation(
                task=task,
                messages=tracker.current_messages,
                reasoning_chars=tracker.current_round.reasoning_chars,
                answer_chars=tracker.current_round.answer_chars,
                generation_usage=tracker.current_round.usage,
                prior_billable_usage=tracker.prior_billable_estimated_usage(),
                extraction_usage=extraction_usage,
                wasted_retry_usage=tracker.wasted_retry_usage,
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
            if not getattr(self.session, "is_active", True):
                await self.session.rollback()
            await self._fence(task.id)
            partial_output = tracker.partial_output_text()
            if partial_output and not task.model_output_text:
                task.model_output_text = partial_output
            document_stage = isinstance(exc, DocumentStageError)
            custom_user_message = getattr(exc, "user_message", None)
            user_message = str(custom_user_message) if custom_user_message else "页面生成失败，请稍后重试。"
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

    async def _run_with_truncation_continue(
        self,
        *,
        task_id: uuid.UUID,
        llm_client: Any,
        messages: list[LlmMessage],
        tracker: _GenerationTracker,
        initial_status_text: str = "页面创建中...",
        output_progress_text: str = "模型正在输出页面 HTML",
    ) -> AsyncIterator[SseEvent]:
        """调用模型生成完整输出；若被截断则按 GENERATION_TRUNCATION_CONTINUES 自动续写拼接，仍截断则抛 GenerationTruncatedError。"""
        base_messages = list(messages)
        max_continues = max(0, int(self.settings.generation_truncation_continues))
        continues_used = 0
        tracker.accumulated_text = ""
        tracker.attempt_rounds.clear()

        while True:
            if continues_used == 0:
                round_messages = base_messages
                status_text = initial_status_text
            else:
                continue_prompt = (
                    HTML_PAGE_EDIT_TRUNCATION_CONTINUE_PROMPT
                    if _looks_like_partial_edit_blocks(tracker.accumulated_text)
                    else HTML_PAGE_TRUNCATION_CONTINUE_PROMPT
                )
                round_messages = [
                    *base_messages,
                    LlmMessage(role="assistant", content=tracker.accumulated_text),
                    LlmMessage(role="user", content=continue_prompt),
                ]
                status_text = TRUNCATION_CONTINUE_STATUS_TEXT

            tracker.current_messages = round_messages
            tracker.current_round = RoundResult()
            async for event in self._stream_llm_round(
                task_id=task_id,
                llm_client=llm_client,
                messages=round_messages,
                result=tracker.current_round,
                prior_answer_chars=len(tracker.accumulated_text),
                output_status_text=status_text,
                output_progress_text=output_progress_text,
            ):
                yield event

            finish_reason = tracker.current_round.finish_reason
            joined_text = (
                join_continued_output(tracker.accumulated_text, tracker.current_round.text)
                if tracker.accumulated_text
                else tracker.current_round.text
            )
            tracker.record_round_finished(joined_text=joined_text)

            if not is_output_truncated(tracker.accumulated_text, finish_reason):
                return

            if continues_used >= max_continues:
                raise GenerationTruncatedError()

            continues_used += 1
            yield await self._record_event(
                task_id,
                "status",
                {"type": "status", "text": TRUNCATION_CONTINUE_STATUS_TEXT},
            )

    async def _stream_llm_round(
        self,
        *,
        task_id: uuid.UUID,
        llm_client: Any,
        messages: list[LlmMessage],
        result: RoundResult,
        prior_answer_chars: int = 0,
        output_status_text: str = "页面创建中...",
        output_progress_text: str = "模型正在输出页面 HTML",
    ) -> AsyncIterator[SseEvent]:
        """调用一轮模型，流式产出事件并实时把正文、思考字符、用量和结束原因写入 result。

        空正文重试与吐字前的异常重试都在本层处理；若重试耗尽仍无正文则抛 ValueError。
        """
        llm_attempts = max(1, self.settings.llm_retry_attempts)
        llm_attempt = 1
        initial_tokens = _estimate_output_tokens(prior_answer_chars)
        next_token_report = (initial_tokens // 50 + 1) * 50 if prior_answer_chars > 0 else 50

        while True:
            answer_started = False
            had_reasoning = False
            stream = llm_client.stream_text(messages)
            try:
                async for chunk in stream:
                    # 停止请求在两个 chunk 之间生效；finally 里关掉流，模型连接随之断开。
                    self._raise_if_cancelled()
                    result.model_provider = chunk.provider or result.model_provider
                    result.model_name = chunk.model or result.model_name
                    if chunk.finish_reason:
                        result.finish_reason = chunk.finish_reason

                    if chunk.type == "reasoning_delta" and chunk.reasoning_text:
                        had_reasoning = True
                        result.reasoning_chars += len(chunk.reasoning_text)
                        self._buffer_reasoning(chunk.reasoning_text)
                        if self._reasoning_due():
                            payload = self._take_reasoning_payload()
                            if payload is not None:
                                yield await self._record_event(task_id, "reasoning_delta", payload)

                    if chunk.type == "text_delta" and chunk.text:
                        if not answer_started:
                            answer_started = True
                            if prior_answer_chars == 0 or had_reasoning:
                                yield await self._record_event(
                                    task_id,
                                    "progress",
                                    {
                                        "type": "progress",
                                        "step": "model_thinking",
                                        "status": "completed",
                                        "text": "模型思考完成",
                                    },
                                )
                                yield await self._record_event(task_id, "answer_started", {"type": "answer_started"})
                                yield await self._record_event(
                                    task_id,
                                    "status",
                                    {"type": "status", "text": output_status_text},
                                )
                            if prior_answer_chars == 0:
                                self._note_progress_written()
                                yield await self._record_event(
                                    task_id,
                                    "progress",
                                    {
                                        "type": "progress",
                                        "step": "model_output",
                                        "status": "running",
                                        "text": output_progress_text,
                                        "output_tokens": 0,
                                        "token_source": "estimated",
                                    },
                                )
                        result.answer_parts.append(chunk.text)
                        result.answer_chars += len(chunk.text)
                        estimated_output_tokens = _estimate_output_tokens(prior_answer_chars + result.answer_chars)
                        if estimated_output_tokens >= next_token_report and self._running_progress_due():
                            next_token_report = estimated_output_tokens + 50
                            self._note_progress_written()
                            yield await self._record_event(
                                task_id,
                                "progress",
                                {
                                    "type": "progress",
                                    "step": "model_output",
                                    "status": "running",
                                    "text": output_progress_text,
                                    "output_tokens": estimated_output_tokens,
                                    "token_source": "estimated",
                                },
                            )

                    if chunk.type == "done" and chunk.usage:
                        result.usage = chunk.usage

                if result.answer_parts:
                    return

                if llm_attempt >= llm_attempts:
                    break

                result.usage = None
                result.finish_reason = None
                result.answer_chars = 0
                next_token_report = (initial_tokens // 50 + 1) * 50 if prior_answer_chars > 0 else 50
                yield await self._record_event(
                    task_id,
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

                result.answer_parts.clear()
                result.usage = None
                result.finish_reason = None
                result.answer_chars = 0
                next_token_report = (initial_tokens // 50 + 1) * 50 if prior_answer_chars > 0 else 50
                yield await self._record_event(
                    task_id,
                    "status",
                    {"type": "status", "text": f"模型调用失败，正在重试（{llm_attempt + 1}/{llm_attempts}）..."},
                )
                await asyncio.sleep(_retry_delay_seconds(self.settings, llm_attempt))
                self._raise_if_cancelled()
                llm_attempt += 1
            finally:
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    await aclose()

        raise ValueError("模型未返回 HTML 正文")

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
        prior_billable_usage: LlmUsage | None = None,
    ) -> None:
        """用户主动取消时的结算：把思考字符 + 正文字符（及已跑完的前序轮次用量）一并纳入输出 Token 估算。"""
        try:
            async with self.session.begin_nested():
                actor = await self.session.get(User, task.requested_by_user_id)
                if actor is None:
                    await BillingService(self.session).release_task_reservation(task)
                    return

                stream_usage = generation_usage or _estimate_stream_usage(messages, reasoning_chars, answer_chars)
                est_out_tokens = 0
                for usage_item in (stream_usage, prior_billable_usage, wasted_retry_usage):
                    if usage_item and usage_item.output_tokens:
                        est_out_tokens += int(usage_item.output_tokens)

                main_usage = merge_llm_usage(
                    merge_llm_usage(
                        merge_llm_usage(extraction_usage, prior_billable_usage),
                        stream_usage,
                    ),
                    wasted_retry_usage,
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
        render_check: dict[str, Any] | None = None,
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
            render_check=render_check,
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

    async def finish_unfinished_task(
        self,
        task_id: uuid.UUID,
        *,
        status: str,
        message: str,
        user_message: str,
        owner: str | None = None,
        stale_before: datetime | None = None,
    ) -> bool:
        """把仍未结束的任务直接写成终态（硬取消、执行器兜底、启动扫尾用）。已结束的不动，返回是否改了。

        owner：执行器兜底传本 worker 的 worker_id，任务已不归自己（被接手）时不动。
        stale_before：扫尾传续期时限，加锁后发现任务又续上了期（执行者还在）时不动。
        """
        # 行锁：停止接口和执行器兜底可能同时收尾同一个任务，只能有一方写终态和收尾事件。
        task = await self.session.get(GenerationTask, task_id, populate_existing=True, with_for_update=True)
        if task is None or task.status in TERMINAL_STATUSES:
            await self.session.rollback()
            return False
        if owner is not None and (task.status != "running" or task.worker_id != owner):
            await self.session.rollback()
            return False
        if stale_before is not None and task.status == "running" and not lease_expired(task, stale_before):
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

    async def request_cancel(self, task_id: uuid.UUID, *, message: str, user_message: str) -> str:
        """不在本进程跑生成时停止一个任务，返回 "cancelled"、"flagged" 或 "noop"。

        加行锁后按此刻的状态决定：还在排队（pending）就直接写成已停止；已被 worker 认领（running）
        只记下停止请求，由 worker 停下并写终态，避免两边同时写终态、重复结算。
        worker 先认领时这里看到的是 running；这里先锁住时，worker 的认领条件 status='pending' 不再成立。
        """
        task = await self.session.get(GenerationTask, task_id, populate_existing=True, with_for_update=True)
        if task is None or task.status in TERMINAL_STATUSES:
            await self.session.rollback()
            return "noop"
        if task.status == "pending":
            changed = await self.finish_unfinished_task(task_id, status="cancelled", message=message, user_message=user_message)
            return "cancelled" if changed else "noop"
        if task.cancel_requested_at is None:
            task.cancel_requested_at = datetime.now(UTC)
        await self.session.commit()
        return "flagged"


def lease_expired(task: GenerationTask, stale_before: datetime) -> bool:
    """running 任务的续期是否早于 stale_before。老行没有续期时间，按开始、创建时间算。"""
    last = task.heartbeat_at or task.started_at or task.created_at
    return last is None or last < stale_before


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


def _summarize_usage_and_cost(
    *,
    task_model_key: str,
    tracker: _GenerationTracker,
    extraction_usage: LlmUsage | None,
    router_usage: LlmUsage | None,
    router_model_key: str | None,
) -> tuple[LlmUsage | None, LlmCostBreakdown | None, LlmCostBreakdown | None]:
    """统一汇总总 usage、向用户收费的 billable_cost 与含平台重试的总账 cost。"""
    generation_usage = tracker.billable_usage()
    wasted_retry_usage = tracker.wasted_retry_usage
    # 技能路由单独按实际轻量模型（如 qwen-plus）单价核计，避免误按写码旗舰单价高估。
    router_cost = (
        estimate_llm_cost(router_model_key or task_model_key, router_usage)
        if router_usage is not None
        else None
    )
    billable_main_usage = merge_llm_usage(extraction_usage, generation_usage)
    billable_cost = merge_cost_breakdowns(
        router_cost, estimate_llm_cost(task_model_key, billable_main_usage)
    )
    full_main_usage = merge_llm_usage(billable_main_usage, wasted_retry_usage)
    full_cost = merge_cost_breakdowns(
        router_cost, estimate_llm_cost(task_model_key, full_main_usage)
    )
    usage = merge_llm_usage(router_usage, full_main_usage)
    cost = full_cost or billable_cost
    return usage, billable_cost, cost


def _build_model_output_completed_payload(
    *,
    usage: LlmUsage | None,
    router_usage: LlmUsage | None,
    answer_text_length: int,
) -> dict[str, Any]:
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
    return progress_payload


def _build_repair_context_text(task: GenerationTask, history_summary: str | None) -> str:
    """构造渲染修复轮的用户背景说明，保证上下文只有唯一一份待修 HTML，防止编辑块锚点串版本。"""
    current_req = (task.user_prompt or task.prompt or "").strip()
    if history_summary:
        return f"{history_summary}\n\n本轮页面要求：\n{current_req}"
    return f"该页面的制作要求：\n{current_req}"


def _looks_like_partial_edit_blocks(text: str) -> bool:
    """判断截断前的已输出前缀是否处于 SEARCH/REPLACE 局部编辑块形态。"""
    if not text:
        return False
    parsed = parse_edit_blocks(text)
    if parsed.blocks or parsed.error_code == "malformed_markers":
        return True
    return "<<<<<<<" in text and "<html" not in text.lower()


def is_output_truncated(text: str, finish_reason: str | None) -> bool:
    """判定模型输出是否被截断。

    - finish_reason == "length"：明确触及最大输出长度上限。
    - 厂商未给结束原因（None 或空串）：
      - 若已是完整闭合的 SEARCH/REPLACE 编辑块（ModelOutputKind.EDIT_BLOCKS），绝不算截断；
      - 若是完整 HTML 形态（ModelOutputKind.FULL_HTML），按正文是否含 </html>（忽略大小写）判定；
      - 其余情况（空串、未闭合的半截编辑块等 ModelOutputKind.MALFORMED），视为截断。
    - 正常结束（如 "stop"）即使漏写 </html> 也不算截断，浏览器可容错。
    """
    reason = (finish_reason or "").strip().lower()
    if reason == "length":
        return True
    if not reason:
        raw = text or ""
        kind = classify_model_output(raw)
        if kind == ModelOutputKind.EDIT_BLOCKS:
            return False
        if kind == ModelOutputKind.FULL_HTML:
            return "</html>" not in raw.lower()
        return True
    return False


_CONTINUATION_SEARCH_START_RE = re.compile(r"^[ \t]*<<<<<<<[ \t]+SEARCH[ \t]*$")
_HTML_ELEMENT_TAG_RE = re.compile(r"<[a-zA-Z][a-zA-Z0-9-]*[\s>/]")


def _contains_html_element(raw_text: str) -> bool:
    return bool(raw_text and _HTML_ELEMENT_TAG_RE.search(raw_text))


def _format_edit_blocks(blocks: tuple[EditBlock, ...]) -> str:
    return "\n".join(
        f"{SEARCH_MARKER}\n{b.search}\n{DIVIDER_MARKER}\n{b.replace}\n{REPLACE_MARKER}"
        for b in blocks
    )


def join_continued_output(prefix: str, continuation: str) -> str:
    """拼接截断前的半页与续写段。

    - 若续写段外误包了代码围栏（```html / ```diff / ``` 等）则先剥除；
    - 若模型忽略断点续写提示而从头重发了完整 HTML（以 <!doctype 或重复的 <html 开头），直接采用新完整文档，防止两份页面叠在一起；
    - 若续写段以 <<<<<<< SEARCH 开头：当前缀尚无闭合块或续写段从第 1 块重发时直接采用续写段；否则保留前缀已闭合块并换行衔接；
    - 其余正常断点续写保持起止字符原样拼接，支持标签/属性名/词中无缝拼合。
    """
    if not prefix:
        return continuation
    if not continuation:
        return prefix
    stripped = _strip_continuation_fence(continuation)
    lstripped = stripped.lstrip()
    lowered_cont = lstripped.lower()
    lowered_prefix = prefix.lower()

    if lowered_cont.startswith("<!doctype"):
        return lstripped
    if lowered_cont.startswith("<html") and ("<html" in lowered_prefix or "<!doctype" not in lowered_prefix):
        return lstripped

    first_line = lstripped.split("\n", 1)[0].rstrip("\r")
    if _CONTINUATION_SEARCH_START_RE.match(first_line):
        prefix_parsed = parse_edit_blocks(prefix)
        if not prefix_parsed.blocks:
            return lstripped
        cont_parsed = parse_edit_blocks(lstripped)
        if (
            cont_parsed.blocks
            and normalize_newlines(cont_parsed.blocks[0].search).strip()
            == normalize_newlines(prefix_parsed.blocks[0].search).strip()
        ):
            return lstripped
        return f"{_format_edit_blocks(prefix_parsed.blocks)}\n{lstripped}"

    return prefix + stripped


def _strip_continuation_fence(continuation: str) -> str:
    match = re.match(r"^\s*```[a-zA-Z0-9_-]*[ \t]*\r?\n", continuation)
    if not match:
        return continuation
    body = continuation[match.end() :]
    return re.sub(r"\r?\n?```\s*$", "", body)


def _build_storage_debug_link(storage_key: str, bucket: str) -> str:
    if bucket:
        return f"oss://{bucket}/{storage_key}"
    return storage_key
