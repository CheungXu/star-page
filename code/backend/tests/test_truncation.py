"""截断检测与自动续写单测：截断判定、续写拼接、OpenAI 兼容流 finish_reason 透传、run_task 编排与计费/退款/取消。

纯逻辑 + Mock，无需连接任何数据库。
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.models.entities import GenerationEvent, GenerationTask, Page, PageVersion, User  # noqa: E402
from app.services import generation_service as service_module  # noqa: E402
from app.services.billing.account import SettleResult  # noqa: E402
from app.services.generation_service import (  # noqa: E402
    TRUNCATED_USER_MESSAGE,
    TRUNCATION_CONTINUE_STATUS_TEXT,
    GenerationService,
    RoundResult,
    is_output_truncated,
    join_continued_output,
)
from app.services.html_sanitizer import extract_html_document  # noqa: E402
from app.services.llm.openai_compatible import OpenAICompatibleClient  # noqa: E402
from app.services.llm.prompt import HTML_PAGE_TRUNCATION_CONTINUE_PROMPT  # noqa: E402
from app.services.llm.types import LlmCostBreakdown, LlmMessage, LlmModelConfig, LlmStreamChunk, LlmUsage  # noqa: E402
from app.services.skills.selector import SkillSelectionResult  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 截断判定与续写拼接纯逻辑测试
# ---------------------------------------------------------------------------


def test_is_output_truncated_rules():
    complete = "<!DOCTYPE html><html><head><title>ok</title></head><body><p>hi</p></body></html>"
    incomplete = "<!DOCTYPE html><html><head><title>cut</title></head><body><div class='ca"
    missing_html_but_body_closed = "<!DOCTYPE html><html><body><p>done</p></body>"

    # finish_reason == "length" 一律判为截断（即使文本里碰巧出现过 </html> 字符串）
    assert is_output_truncated(incomplete, "length") is True
    assert is_output_truncated(complete, "length") is True
    assert is_output_truncated(incomplete, " LENGTH ") is True

    # 正常结束（finish_reason == "stop"）：即使模型漏写 </html> 也不算截断，浏览器可容错
    assert is_output_truncated(complete, "stop") is False
    assert is_output_truncated(missing_html_but_body_closed, "stop") is False

    # 厂商未返回结束原因（None / 空串，兼容现有测试 FakeClient）：按正文是否含 </html> 判定（忽略大小写）
    assert is_output_truncated(complete, None) is False
    assert is_output_truncated(complete.upper(), "") is False
    assert is_output_truncated(incomplete, None) is True
    assert is_output_truncated("", None) is True


def test_is_output_truncated_with_edit_blocks_interaction():
    """关键交互：局部编辑块本身不含 </html>，闭合编辑块在 finish_reason=None 时绝不能误判为截断；半截编辑块则须判为截断。"""
    closed_edit = (
        "<<<<<<< SEARCH\n"
        "  <h1>旧标题</h1>\n"
        "=======\n"
        "  <h1>新标题</h1>\n"
        ">>>>>>> REPLACE"
    )
    unclosed_edit = (
        "<<<<<<< SEARCH\n"
        "  <h1>旧标题</h1>\n"
        "=======\n"
        "  <h1>新标题但还没写完闭合行"
    )
    unclosed_edit_containing_html_close = (
        "<<<<<<< SEARCH\n"
        "</body>\n</html>\n"
        "=======\n"
        "<footer>新增页脚</footer>\n</body>"
    )

    # 1) 闭合的完整编辑块 + finish_reason 为 None / 空串 / stop：绝不判截断
    assert is_output_truncated(closed_edit, None) is False
    assert is_output_truncated(closed_edit, "") is False
    assert is_output_truncated(closed_edit, "stop") is False

    # 2) 闭合编辑块但明确触及 max_tokens (finish_reason == "length")：可能后续块被截断，判为截断
    assert is_output_truncated(closed_edit, "length") is True

    # 3) 未闭合的半截编辑块 + finish_reason 为 None / length：判为截断（即使内部 SEARCH 碰巧出现过 </html>）
    assert is_output_truncated(unclosed_edit, None) is True
    assert is_output_truncated(unclosed_edit, "length") is True
    assert is_output_truncated(unclosed_edit_containing_html_close, None) is True

    # 4) 未闭合编辑块但模型主动正常结束 (finish_reason == "stop")：不走截断续写，交外层 MALFORMED 回退整页重写
    assert is_output_truncated(unclosed_edit, "stop") is False


def test_join_continued_output_seamless_and_fence_stripping():
    # 标签属性名中间截断：不插换行、不吞前导字符，严丝合缝拼合
    part1 = '<!doctype html><html><body><section cla'
    part2 = 'ss="hero"><h1>标题</h1></section></body></html>'
    assert join_continued_output(part1, part2) == '<!doctype html><html><body><section class="hero"><h1>标题</h1></section></body></html>'

    # 保留续写段前导缩进空白
    assert join_continued_output("<ul>\n", "  <li>1</li>\n</ul></html>") == "<ul>\n  <li>1</li>\n</ul></html>"

    # 续写段被模型误包 ```html 围栏时自动剥除外层围栏
    fenced_cont = "```html\n  <p>后半段</p>\n</body>\n</html>\n```"
    joined = join_continued_output("<!doctype html><html><body>\n", fenced_cont)
    assert "```" not in joined
    assert extract_html_document(joined).endswith("</html>")
    assert "  <p>后半段</p>" in joined


# ---------------------------------------------------------------------------
# 2. OpenAICompatibleClient 流式透传 finish_reason 测试
# ---------------------------------------------------------------------------


def _make_model_config() -> LlmModelConfig:
    return LlmModelConfig(
        key="qwen",
        label="Qwen",
        provider="qwen",
        protocol="openai",
        base_url="https://mock.llm.local/v1",
        model="qwen3.8-max",
        api_key="sk-test",
        available=True,
    )


def _sse_response(chunks: list[dict[str, Any]]) -> httpx.Response:
    lines = [f"data: {json.dumps(item, ensure_ascii=False)}" for item in chunks]
    lines.append("data: [DONE]")
    body = ("\n\n".join(lines) + "\n\n").encode("utf-8")
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def test_openai_compatible_preserves_finish_reason_when_usage_arrives_later(monkeypatch):
    """finish_reason 在最后一个正文分片，usage 在 choices=[] 的后续分片时，done 分片必须带上该 finish_reason。"""
    sse_chunks = [
        {"model": "qwen3.8-max", "choices": [{"delta": {"reasoning_content": "先思考"}, "finish_reason": None}]},
        {"model": "qwen3.8-max", "choices": [{"delta": {"content": "<html><body>半截"}, "finish_reason": "length"}]},
        {
            "model": "qwen3.8-max",
            "choices": [],
            "usage": {"prompt_tokens": 120, "completion_tokens": 8192, "total_tokens": 8312},
        },
    ]
    transport = httpx.MockTransport(lambda _req: _sse_response(sse_chunks))
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: orig_client(transport=transport, **kw))

    client = OpenAICompatibleClient(_make_model_config(), Settings())

    async def collect() -> list[LlmStreamChunk]:
        return [chunk async for chunk in client.stream_text([LlmMessage(role="user", content="test")])]

    out = asyncio.run(collect())
    assert [c.type for c in out] == ["reasoning_delta", "text_delta", "done", "done"]
    usage_done = out[2]
    assert usage_done.finish_reason == "length"
    assert usage_done.usage == LlmUsage(input_tokens=120, output_tokens=8192, total_tokens=8312)
    assert out[3].finish_reason == "length"


def test_openai_compatible_emits_finish_reason_without_usage_chunk(monkeypatch):
    """上游未返回 usage 分片时，流末尾兜底 done 分片仍能携带记录到的 finish_reason。"""
    sse_chunks = [
        {"model": "qwen3.8-max", "choices": [{"delta": {"content": "<html></html>"}, "finish_reason": "stop"}]},
    ]
    transport = httpx.MockTransport(lambda _req: _sse_response(sse_chunks))
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: orig_client(transport=transport, **kw))

    client = OpenAICompatibleClient(_make_model_config(), Settings())

    async def collect() -> list[LlmStreamChunk]:
        return [chunk async for chunk in client.stream_text([LlmMessage(role="user", content="test")])]

    out = asyncio.run(collect())
    done_chunks = [c for c in out if c.type == "done"]
    assert len(done_chunks) == 1
    assert done_chunks[0].finish_reason == "stop"
    assert done_chunks[0].usage is None


# ---------------------------------------------------------------------------
# 3. GenerationService._stream_llm_round 与 run_task 编排 Mock 单测
# ---------------------------------------------------------------------------


class _FakeNestedTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeMemorySession:
    """不连任何数据库的轻量内存会话，支撑 GenerationService.run_task 完整编排测试。"""

    def __init__(self, *, user: User, page: Page, task: GenerationTask) -> None:
        self._store: dict[tuple[type, Any], Any] = {
            (User, user.id): user,
            (Page, page.id): page,
            (GenerationTask, task.id): task,
        }
        self.events: list[GenerationEvent] = []
        self.versions: list[PageVersion] = []
        self.new: set[Any] = set()
        self.dirty: set[Any] = set()
        self.deleted: set[Any] = set()
        self.commits = 0

    async def get(self, entity: type, ident: Any, **_kwargs: Any) -> Any:
        return self._store.get((entity, ident))

    def add(self, obj: Any) -> None:
        if isinstance(obj, GenerationEvent):
            obj.sequence = len(self.events) + 1
            self.events.append(obj)
        elif isinstance(obj, PageVersion):
            self.versions.append(obj)
            self._store[(PageVersion, obj.id)] = obj

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    def in_transaction(self) -> bool:
        return False

    def begin_nested(self) -> _FakeNestedTxn:
        return _FakeNestedTxn()


class ScriptedLlmClient:
    """按轮次依次回放预设的 chunk 列表，并记录每轮收到的 messages。"""

    def __init__(self, rounds: list[list[LlmStreamChunk] | Exception], *, on_chunk=None) -> None:
        self.rounds = list(rounds)
        self.calls: list[list[LlmMessage]] = []
        self.closed_streams = 0
        self._on_chunk = on_chunk

    def stream_text(self, messages: list[LlmMessage]):
        call_index = len(self.calls)
        self.calls.append(list(messages))
        spec = self.rounds[call_index]
        parent = self

        async def _gen():
            try:
                if isinstance(spec, Exception):
                    raise spec
                for idx, chunk in enumerate(spec):
                    if parent._on_chunk is not None:
                        parent._on_chunk(call_index, idx, chunk)
                    yield chunk
            finally:
                parent.closed_streams += 1

        return _gen()


def _setup_harness(monkeypatch, *, continues: int = 1, retry_attempts: int = 2):
    pricing = {
        "currency": "CNY",
        "tiers": [
            {
                "label": "默认",
                "max_input_tokens": None,
                "input_per_million": 10.0,
                "output_per_million": 30.0,
                "cache_input_per_million": 1.0,
            }
        ],
    }
    fake_model = LlmModelConfig(
        key="qwen",
        label="Qwen",
        provider="qwen",
        protocol="openai",
        base_url="https://mock.local/v1",
        model="qwen-mock",
        api_key="sk",
        pricing=pricing,
        available=True,
    )
    fake_registry = SimpleNamespace(get=lambda key: fake_model if key == "qwen" else None)
    monkeypatch.setattr(service_module, "get_model_registry", lambda: fake_registry)
    monkeypatch.setattr("app.services.llm.cost.get_model_registry", lambda: fake_registry)

    settings = Settings(
        PAGE_SKILLS_ENABLED=False,
        GENERATION_TRUNCATION_CONTINUES=continues,
        LLM_RETRY_ATTEMPTS=retry_attempts,
        LLM_RETRY_INITIAL_DELAY_MS=1,
        LLM_RETRY_MAX_DELAY_MS=5,
        GENERATION_REASONING_FLUSH_SECONDS=0.0,
        GENERATION_PROGRESS_FLUSH_SECONDS=0.0,
    )
    monkeypatch.setattr(service_module, "get_settings", lambda: settings)

    records: dict[str, Any] = {
        "uploaded_html": None,
        "settle_calls": [],
        "released_tasks": [],
        "cancelled_settles": [],
    }

    async def fake_no_skill(self, *, page, task):
        return SkillSelectionResult(None, None)

    async def fake_select_visuals(_session, _batch, _doc_context):
        return []

    async def fake_upload(self, page, html: str):
        records["uploaded_html"] = html
        return uuid.uuid4(), f"pages/{page.id}/versions/v1/index.html"

    async def fake_create_version(
        self,
        *,
        page,
        task,
        version_id,
        storage_key,
        model_provider,
        model_name,
        usage,
        cost=None,
        render_check=None,
    ):
        version = PageVersion(
            id=version_id,
            page_id=page.id,
            version_number=1,
            prompt=task.prompt,
            storage_key=storage_key,
            status="ready",
            model_provider=model_provider,
            model_name=model_name,
            model_key=task.model_key,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            total_cost_cny=cost.total_cost_cny if cost else None,
            render_check=render_check,
        )
        self.session.add(version)
        return version

    async def fake_settle_billing(self, task, version, cost: LlmCostBreakdown | None, *, ledger_cost: LlmCostBreakdown | None = None):
        records["settle_calls"].append({"billable_cost": cost, "ledger_cost": ledger_cost})
        return SettleResult(
            credits_charged=12,
            paid_used=0,
            gift_used=12,
            balance_after=38,
            discount_credits=0,
            reserved_credits=50,
            refunded_credits=38,
            debt_added=0,
        )

    async def fake_release_safe(self, task):
        records["released_tasks"].append(task.id)
        task.reservation_settled = True

    async def fake_recompute(self, _batch_id):
        return None

    async def fake_refund_trial(self, _batch_id):
        return None

    monkeypatch.setattr(GenerationService, "_resolve_task_skill_key", fake_no_skill)
    monkeypatch.setattr(service_module, "select_attachment_visuals", fake_select_visuals)
    monkeypatch.setattr(GenerationService, "_upload_page_html", fake_upload)
    monkeypatch.setattr(GenerationService, "_create_page_version", fake_create_version)
    monkeypatch.setattr(GenerationService, "_settle_generation_billing", fake_settle_billing)
    monkeypatch.setattr(GenerationService, "_release_task_reservation_safe", fake_release_safe)
    monkeypatch.setattr(GenerationService, "recompute_batch_status", fake_recompute)
    monkeypatch.setattr(GenerationService, "_refund_flagship_trial_if_needed", fake_refund_trial)

    user = User(id=uuid.uuid4(), username="u1", display_name="测试用户")
    page = Page(
        id=uuid.uuid4(),
        owner_user_id=user.id,
        title="测试页",
        visibility="public",
        status="generating",
        conversation_id=uuid.uuid4(),
        model_key="qwen",
    )
    task = GenerationTask(
        id=uuid.uuid4(),
        page_id=page.id,
        requested_by_user_id=user.id,
        model_key="qwen",
        prompt="做一个产品发布会邀请函页面",
        user_prompt="做一个产品发布会邀请函页面",
        status="running",
        started_at=datetime.now(UTC),
        reserved_gift_credits=50,
        reserved_paid_credits=0,
    )
    session = FakeMemorySession(user=user, page=page, task=task)
    return session, user, page, task, records


def test_single_round_normal_completion_and_fake_client_compat(monkeypatch):
    """未截断场景 & 无 done chunk 的旧式 FakeClient：单轮直出、不触发续写，事件序列保持不变。"""
    session, _user, page, task, records = _setup_harness(monkeypatch, continues=1)
    html = "<!DOCTYPE html><html><head><title>邀请函</title></head><body><h1>欢迎</h1></body></html>"
    # 模拟旧测试里的 FakeClient：只发 reasoning_delta + text_delta，不发 done chunk（finish_reason=None）
    llm = ScriptedLlmClient(
        [
            [
                LlmStreamChunk(type="reasoning_delta", provider="qwen", model="qwen-mock", reasoning_text="构思结构"),
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=html),
            ]
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _key: llm)

    async def run():
        service = GenerationService(session)
        return [ev async for ev in service.run_task(task)]

    events = asyncio.run(run())
    assert len(llm.calls) == 1
    assert llm.closed_streams == 1
    assert task.status == "succeeded"
    assert page.status == "ready"
    assert events[-1].event == "completed"
    statuses = [ev.data.get("text") for ev in events if ev.event == "status"]
    assert TRUNCATION_CONTINUE_STATUS_TEXT not in statuses
    assert len(records["settle_calls"]) == 1
    assert records["released_tasks"] == []


def test_truncated_once_then_continues_and_merges_billable_usage(monkeypatch):
    """首轮 finish_reason=length 截断 -> 发 status「输出被截断，正在续写」-> 续写拼接完整 HTML -> 两轮用量合并计费。"""
    session, _user, page, task, records = _setup_harness(monkeypatch, continues=1)
    part1 = '<!DOCTYPE html><html><head><title>长页</title></head><body><div cla'
    part2 = 'ss="banner">完整内容</div></body></html>'
    llm = ScriptedLlmClient(
        [
            [
                LlmStreamChunk(type="reasoning_delta", provider="qwen", model="qwen-mock", reasoning_text="先写前半页"),
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=part1),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=200, output_tokens=4000, total_tokens=4200),
                    finish_reason="length",
                ),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=part2),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=4300, output_tokens=500, total_tokens=4800),
                    finish_reason="stop",
                ),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _key: llm)

    async def run():
        service = GenerationService(session)
        return [ev async for ev in service.run_task(task)]

    events = asyncio.run(run())
    assert len(llm.calls) == 2
    # 第二轮请求末尾必须是 assistant(已输出的半页) + user(续写指令)
    second_msgs = llm.calls[1]
    assert second_msgs[-2] == LlmMessage(role="assistant", content=part1)
    assert second_msgs[-1] == LlmMessage(role="user", content=HTML_PAGE_TRUNCATION_CONTINUE_PROMPT)

    # 进度文案包含「输出被截断，正在续写」，且 answer_started 只发过一次（未重置卡片状态）
    statuses = [ev.data.get("text") for ev in events if ev.event == "status"]
    assert TRUNCATION_CONTINUE_STATUS_TEXT in statuses
    assert [ev.event for ev in events].count("answer_started") == 1

    # 拼接后的 HTML 完整发布
    assert task.status == "succeeded"
    assert page.status == "ready"
    assert '<div class="banner">完整内容</div>' in (task.model_output_text or "")
    assert '<div class="banner">完整内容</div>' in (records["uploaded_html"] or "")

    # 计费结算合并了两轮全部用量：input=200+4300=4500, output=4000+500=4500
    assert len(records["settle_calls"]) == 1
    billable = records["settle_calls"][0]["billable_cost"]
    ledger = records["settle_calls"][0]["ledger_cost"]
    assert billable is not None and ledger is not None
    assert billable.input_tokens == 4500
    assert billable.output_tokens == 4500
    assert ledger.total_cost_cny == billable.total_cost_cny
    completed = events[-1]
    assert completed.event == "completed"
    assert completed.data["usage"] == {"input_tokens": 4500, "output_tokens": 4500, "total_tokens": 9000}


def test_still_truncated_after_continuation_fails_and_refunds_reservation(monkeypatch):
    """续写一次后仍截断：走失败分支，向用户提示「页面内容过长，未能完整生成」，并全额退回预扣积分。"""
    session, _user, page, task, records = _setup_harness(monkeypatch, continues=1)
    llm = ScriptedLlmClient(
        [
            [
                LlmStreamChunk(
                    type="text_delta",
                    provider="qwen",
                    model="qwen-mock",
                    text="<!DOCTYPE html><html><body><section>第一段未完",
                ),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=100, output_tokens=4000, total_tokens=4100),
                    finish_reason="length",
                ),
            ],
            [
                LlmStreamChunk(
                    type="text_delta",
                    provider="qwen",
                    model="qwen-mock",
                    text="<section>第二段还是没写完",
                ),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=4150, output_tokens=4000, total_tokens=8150),
                    finish_reason="length",
                ),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _key: llm)

    async def run():
        service = GenerationService(session)
        return [ev async for ev in service.run_task(task)]

    events = asyncio.run(run())
    assert len(llm.calls) == 2
    assert task.status == "failed"
    assert page.status == "failed"
    # 保存拼接后的半成品供排障
    assert task.model_output_text == "<!DOCTYPE html><html><body><section>第一段未完<section>第二段还是没写完"
    # 终态事件文案准确，预扣积分已全额释放，未扣费
    assert events[-1].event == "failed"
    assert events[-1].data["message"] == TRUNCATED_USER_MESSAGE
    assert records["released_tasks"] == [task.id]
    assert records["settle_calls"] == []


def test_truncation_continues_zero_disables_continuation(monkeypatch):
    """GENERATION_TRUNCATION_CONTINUES=0 时，首轮截断不再发起续写，直接标失败并退款。"""
    session, _user, _page, task, records = _setup_harness(monkeypatch, continues=0)
    llm = ScriptedLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text="<html><body>半截"),
                LlmStreamChunk(type="done", provider="qwen", model="qwen-mock", finish_reason="length"),
            ]
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _key: llm)

    async def run():
        service = GenerationService(session)
        return [ev async for ev in service.run_task(task)]

    events = asyncio.run(run())
    assert len(llm.calls) == 1
    assert task.status == "failed"
    assert events[-1].event == "failed"
    assert events[-1].data["message"] == TRUNCATED_USER_MESSAGE
    assert records["released_tasks"] == [task.id]


def test_cancel_mid_continuation_preserves_partial_text_and_prior_round_usage(monkeypatch):
    """在续写轮中途用户主动停止：保留首轮+续写已吐出的正文，取消结算同时计入首轮真实用量与续写轮已吐字符。"""
    session, _user, page, task, _records = _setup_harness(monkeypatch, continues=1)
    cancel_flag = {"stop": False}
    cancelled_calls: list[dict[str, Any]] = []

    async def spy_settle_cancelled(self, **kwargs):
        cancelled_calls.append(kwargs)

    monkeypatch.setattr(GenerationService, "_settle_cancelled_generation", spy_settle_cancelled)

    def on_chunk(call_idx: int, chunk_idx: int, _chunk: LlmStreamChunk) -> None:
        # 第 2 轮吐完第 1 个 chunk 后触发停止，在读第 2 个 chunk 时抛 GenerationCancelled
        if call_idx == 1 and chunk_idx == 1:
            cancel_flag["stop"] = True

    llm = ScriptedLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text="<html><body>第一段"),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=100, output_tokens=500, total_tokens=600),
                    finish_reason="length",
                ),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text="续写前半"),
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text="这句在停止后处理前"),
            ],
        ],
        on_chunk=on_chunk,
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _key: llm)

    async def run():
        service = GenerationService(session, cancel_check=lambda: cancel_flag["stop"])
        return [ev async for ev in service.run_task(task)]

    events = asyncio.run(run())
    assert task.status == "cancelled"
    assert page.status == "failed"
    assert events[-1].event == "cancelled"
    assert task.model_output_text == "<html><body>第一段续写前半"
    assert len(cancelled_calls) == 1
    call_kw = cancelled_calls[0]
    assert call_kw["prior_billable_usage"] == LlmUsage(input_tokens=100, output_tokens=500, total_tokens=600)
    assert call_kw["answer_chars"] == len("续写前半")


def test_tailwind_retry_keeps_wasted_ledger_separate_from_billable(monkeypatch):
    """重构回归：Tailwind 违规重试仍记入 wasted_retry_usage（总账 COGS 承担，用户只付成功轮）。"""
    session, _user, _page, task, records = _setup_harness(monkeypatch, continues=1)
    bad_html = '<!DOCTYPE html><html><head><script src="https://cdn.tailwindcss.com"></script></head><body>x</body></html>'
    good_html = "<!DOCTYPE html><html><head><style>body{color:#111}</style></head><body>ok</body></html>"
    llm = ScriptedLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=bad_html),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=100, output_tokens=300, total_tokens=400),
                    finish_reason="stop",
                ),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=good_html),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=500, output_tokens=350, total_tokens=850),
                    finish_reason="stop",
                ),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _key: llm)

    async def run():
        service = GenerationService(session)
        return [ev async for ev in service.run_task(task)]

    events = asyncio.run(run())
    assert events[-1].event == "completed"
    assert len(records["settle_calls"]) == 1
    billable = records["settle_calls"][0]["billable_cost"]
    ledger = records["settle_calls"][0]["ledger_cost"]
    assert billable.input_tokens == 500 and billable.output_tokens == 350
    assert ledger.input_tokens == 600 and ledger.output_tokens == 650


def test_stream_llm_round_retries_empty_and_transient_error(monkeypatch):
    """单轮辅助方法 _stream_llm_round：首字前异常重试 + 空正文重试后成功写出 RoundResult。"""
    session, _user, _page, task, _records = _setup_harness(monkeypatch, continues=1, retry_attempts=3)
    llm = ScriptedLlmClient(
        [
            RuntimeError("upstream transient 502"),
            [LlmStreamChunk(type="done", provider="qwen", model="qwen-mock", finish_reason="stop")],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text="<html><body>ok</body></html>"),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=10, output_tokens=20, total_tokens=30),
                    finish_reason="stop",
                ),
            ],
        ]
    )

    async def run():
        service = GenerationService(session)
        res = RoundResult()
        evs = [
            ev
            async for ev in service._stream_llm_round(
                task_id=task.id,
                llm_client=llm,
                messages=[LlmMessage(role="user", content="hi")],
                result=res,
            )
        ]
        return res, evs

    result, events = asyncio.run(run())
    assert len(llm.calls) == 3
    assert result.text == "<html><body>ok</body></html>"
    assert result.finish_reason == "stop"
    assert result.usage == LlmUsage(input_tokens=10, output_tokens=20, total_tokens=30)
    status_texts = [ev.data.get("text") for ev in events if ev.event == "status"]
    assert any("模型调用失败，正在重试" in (t or "") for t in status_texts)
    assert any("模型未返回正文，正在重试" in (t or "") for t in status_texts)


def test_truncated_edit_block_continues_to_closure_and_applies_cleanly(monkeypatch):
    """续写局部编辑块在中途被截断：自动使用编辑块断点续写提示词拼接闭合，随后精确应用到上一版 HTML。"""
    from app.services.llm.prompt import HTML_PAGE_EDIT_TRUNCATION_CONTINUE_PROMPT

    session, _user, page, task, records = _setup_harness(monkeypatch, continues=1)
    parent_id = uuid.uuid4()
    page.parent_page_id = parent_id
    parent_html = "<!doctype html><html><head><title>旧</title></head><body><h1>原标题</h1></body></html>"

    async def fake_load_parent(self, pid):
        assert pid == parent_id
        return parent_html

    async def fake_collect_inst(self, _pid):
        return ["做一个首页"]

    monkeypatch.setattr(GenerationService, "_load_node_html", fake_load_parent)
    monkeypatch.setattr(GenerationService, "_collect_ancestor_instructions", fake_collect_inst)

    edit_part1 = "<<<<<<< SEARCH\n<h1>原标题</h1>\n=======\n<h1>续写拼合"
    edit_part2 = "的新标题</h1>\n>>>>>>> REPLACE"

    llm = ScriptedLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=edit_part1),
                # 故意无 finish_reason (None)，半截未闭合编辑块应触发截断续写
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=edit_part2),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _key: llm)

    async def run():
        service = GenerationService(session)
        return [ev async for ev in service.run_task(task)]

    events = asyncio.run(run())
    assert len(llm.calls) == 2
    assert llm.calls[1][-1] == LlmMessage(role="user", content=HTML_PAGE_EDIT_TRUNCATION_CONTINUE_PROMPT)
    assert events[-1].event == "completed"
    assert task.model_edit_text == edit_part1 + edit_part2
    assert "<h1>续写拼合的新标题</h1>" in (task.model_output_text or "")
    assert "<h1>续写拼合的新标题</h1>" in (records["uploaded_html"] or "")


def test_malformed_edit_output_with_stop_reason_fallbacks_and_bills_both(monkeypatch):
    """续写轮模型主动 stop 但输出了残缺畸形编辑标记（MALFORMED）：不死磕截断续写，直接计入账单并回退整页重写。"""
    session, _user, page, task, records = _setup_harness(monkeypatch, continues=1)
    parent_id = uuid.uuid4()
    page.parent_page_id = parent_id
    parent_html = "<!doctype html><html><head><title>旧</title></head><body><h1>原标题</h1></body></html>"

    monkeypatch.setattr(GenerationService, "_load_node_html", lambda self, _pid: asyncio.sleep(0, result=parent_html))
    monkeypatch.setattr(GenerationService, "_collect_ancestor_instructions", lambda self, _pid: asyncio.sleep(0, result=["初版"]))

    malformed_text = "<<<<<<< SEARCH\n<h1>原标题</h1>\n>>>>>>> REPLACE"  # 缺少 ======= 分隔行
    fallback_html = "<!doctype html><html><head><title>新</title></head><body><h1>回退重写成功</h1></body></html>"

    llm = ScriptedLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=malformed_text),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=300, output_tokens=80, total_tokens=380),
                    finish_reason="stop",
                ),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=fallback_html),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=600, output_tokens=220, total_tokens=820),
                    finish_reason="stop",
                ),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _key: llm)

    async def run():
        service = GenerationService(session)
        return [ev async for ev in service.run_task(task)]

    events = asyncio.run(run())
    assert len(llm.calls) == 2
    assert events[-1].event == "completed"
    assert task.model_edit_text is None
    assert "<h1>回退重写成功</h1>" in (task.model_output_text or "")
    billable = records["settle_calls"][0]["billable_cost"]
    assert billable.input_tokens == 900
    assert billable.output_tokens == 300


# ---------------------------------------------------------------------------
# 4. 代码审查问题回归测试（续写整页重发、Tailwind 修正返回编辑块、修复轮数据库异常透传）
# ---------------------------------------------------------------------------


def test_join_continued_output_handles_full_document_or_edit_restart():
    # 1) 续写段从 <!doctype html> 或 <html 重新输出完整页面时，丢弃半截前缀，绝不把两份页面粘在一起
    half_html = "<!doctype html><html><head><title>旧半截</title></head><body><div>未闭合半截"
    restarted_html = "```html\n<!DOCTYPE html><html><head><title>完整重发</title></head><body><p>ok</p></body></html>\n```"
    joined = join_continued_output(half_html, restarted_html)
    assert "未闭合半截" not in joined
    assert joined.lower().count("<html") == 1
    assert "<title>完整重发</title>" in joined

    restarted_no_doctype = "<html><body><p>重新从 html 写</p></body></html>"
    joined2 = join_continued_output("<html><body><div>半截", restarted_no_doctype)
    assert "<div>半截" not in joined2
    assert joined2 == restarted_no_doctype

    # 特例：前缀仅输出了 <!doctype html>\n 尚未出现 <html，续写从 <html 接着写属于正常断点衔接
    joined_after_doctype = join_continued_output("<!doctype html>\n", "<html lang='zh'><body>ok</body></html>")
    assert joined_after_doctype.startswith("<!doctype html>\n<html")

    # 2) 编辑块未闭合时模型从 <<<<<<< SEARCH 重发第 1 块：采用续写段而非嵌套进未闭合块
    unclosed_b1 = "<<<<<<< SEARCH\n<h1>旧</h1>\n=======\n<h1>半截"
    restarted_b1 = "```diff\n<<<<<<< SEARCH\n<h1>旧</h1>\n=======\n<h1>新</h1>\n>>>>>>> REPLACE\n```"
    joined_b1 = join_continued_output(unclosed_b1, restarted_b1)
    assert service_module.classify_model_output(joined_b1) == service_module.ModelOutputKind.EDIT_BLOCKS
    assert joined_b1.count("<<<<<<< SEARCH") == 1

    # 3) 第 1 块恰好在 >>>>>>> REPLACE 无尾部换行处触发 length 截断，续写以 <<<<<<< SEARCH 输出第 2 块：换行衔接不粘连
    closed_b1_no_nl = "<<<<<<< SEARCH\n<h1>A</h1>\n=======\n<h1>B</h1>\n>>>>>>> REPLACE"
    next_b2 = "<<<<<<< SEARCH\n<p>C</p>\n=======\n<p>D</p>\n>>>>>>> REPLACE"
    joined_two = join_continued_output(closed_b1_no_nl, next_b2)
    parsed_two = service_module.parse_edit_blocks(joined_two)
    assert parsed_two.ok is True
    assert len(parsed_two.blocks) == 2

    # 4) 第 1 块已闭合 + 第 2 块半截未闭合，续写重新从第 2 块的 <<<<<<< SEARCH 输出：保留第 1 块并衔接第 2 块
    b1_plus_unclosed_b2 = closed_b1_no_nl + "\n<<<<<<< SEARCH\n<p>C</p>\n=======\n<p>半"
    joined_salvaged = join_continued_output(b1_plus_unclosed_b2, next_b2)
    parsed_salvaged = service_module.parse_edit_blocks(joined_salvaged)
    assert parsed_salvaged.ok is True
    assert len(parsed_salvaged.blocks) == 2


def test_tailwind_retry_accepts_edit_blocks_instead_of_publishing_raw_markers(monkeypatch):
    """局部编辑产出含 Tailwind 的页面后触发 Tailwind 修正时，若模型仍返回 SEARCH/REPLACE 编辑块，应正确应用而非把标记原样包进 body 发布。"""
    session, _user, page, task, records = _setup_harness(monkeypatch, continues=1)
    parent_id = uuid.uuid4()
    page.parent_page_id = parent_id
    parent_html = (
        "<!doctype html><html><head><title>旧版</title></head>"
        "<body><h1>旧标题</h1></body></html>"
    )
    monkeypatch.setattr(GenerationService, "_load_node_html", lambda self, _pid: asyncio.sleep(0, result=parent_html))
    monkeypatch.setattr(GenerationService, "_collect_ancestor_instructions", lambda self, _pid: asyncio.sleep(0, result=["初版"]))

    # 第一轮：局部编辑块把 Tailwind CDN 引了进来
    edit_with_tw = (
        "<<<<<<< SEARCH\n"
        "<head><title>旧版</title></head>\n"
        "=======\n"
        "<head><title>新版</title><script src=\"https://cdn.tailwindcss.com\"></script></head>\n"
        ">>>>>>> REPLACE"
    )
    # 第二轮（Tailwind 修正轮）：模型再次用 SEARCH/REPLACE 编辑块把 Tailwind CDN 换成内联 style
    tw_fix_edit = (
        "<<<<<<< SEARCH\n"
        "<script src=\"https://cdn.tailwindcss.com\"></script>\n"
        "=======\n"
        "<style>h1{color:#111;}</style>\n"
        ">>>>>>> REPLACE"
    )

    llm = ScriptedLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=edit_with_tw),
                LlmStreamChunk(type="done", provider="qwen", model="qwen-mock", finish_reason="stop"),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=tw_fix_edit),
                LlmStreamChunk(type="done", provider="qwen", model="qwen-mock", finish_reason="stop"),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _key: llm)

    async def run():
        service = GenerationService(session)
        return [ev async for ev in service.run_task(task)]

    events = asyncio.run(run())
    assert events[-1].event == "completed"
    uploaded = records["uploaded_html"] or ""
    assert "<<<<<<< SEARCH" not in uploaded
    assert "cdn.tailwindcss.com" not in uploaded
    assert "<title>新版</title>" in uploaded
    assert "h1{color:#111;}" in uploaded
    assert task.model_edit_text is not None
    assert tw_fix_edit in task.model_edit_text


def test_first_round_rejects_plain_refusal_or_stray_edit_blocks(monkeypatch):
    """首轮无基准页面时，若模型正常 stop 但只输出了纯文本拒答或编辑块标记，必须判失败而非包进 <body> 发布。"""
    session, _user, _page, task, records = _setup_harness(monkeypatch, continues=1)
    llm = ScriptedLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text="抱歉，请补充更多信息后再生成页面。"),
                LlmStreamChunk(type="done", provider="qwen", model="qwen-mock", finish_reason="stop"),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _key: llm)

    async def run():
        service = GenerationService(session)
        return [ev async for ev in service.run_task(task)]

    events = asyncio.run(run())
    assert events[-1].event == "failed"
    assert task.status == "failed"
    assert records["uploaded_html"] is None
    assert records["released_tasks"] == [task.id]


def test_repair_round_swallows_llm_error_but_propagates_db_error(monkeypatch):
    """渲染自动修复轮：上游 LLM 报错时保住修前页面照常发布；数据库写事件报错时绝不吞异常，回滚会话后走失败收尾。"""
    from sqlalchemy.exc import SQLAlchemyError
    from app.services.page_render_check import RenderCheckResult, build_js_error_issue

    broken_issue = RenderCheckResult(
        status="issues_found",
        issues=[build_js_error_issue(name="ReferenceError", message="foo is not defined", stack="")],
    )

    async def fake_render_check(_html: str):
        return broken_issue

    # 场景 A：修复轮抛出 httpx.ConnectError -> 吞掉异常，发布修前 HTML 并带 render_issue
    session_a, _u_a, page_a, task_a, records_a = _setup_harness(monkeypatch, continues=1)
    settings_a = service_module.get_settings()
    settings_a.page_render_check_mode = "repair"
    monkeypatch.setattr(service_module, "check_page_render", fake_render_check)

    v1_html = "<!doctype html><html><head><title>初版</title></head><body><h1>可用初版</h1></body></html>"
    llm_a = ScriptedLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=v1_html),
                LlmStreamChunk(type="done", provider="qwen", model="qwen-mock", finish_reason="stop"),
            ],
            httpx.ConnectError("upstream connection refused"),
            httpx.ConnectError("upstream connection refused"),
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _key: llm_a)

    events_a = asyncio.run(_collect_events(GenerationService(session_a), task_a))
    assert events_a[-1].event == "completed"
    assert page_a.status == "ready"
    assert "可用初版" in (records_a["uploaded_html"] or "")
    assert events_a[-1].data["render_issue"] is not None

    # 场景 B：修复轮写事件时数据库 commit 抛 SQLAlchemyError（且会话置为 is_active=False） -> 不吞异常，先 rollback 再走 failed
    session_b, _u_b, page_b, task_b, records_b = _setup_harness(monkeypatch, continues=1)
    settings_b = service_module.get_settings()
    settings_b.page_render_check_mode = "repair"
    monkeypatch.setattr(service_module, "check_page_render", fake_render_check)

    session_b.is_active = True
    session_b.rollbacks = 0
    in_repair_stream = {"flag": False}
    orig_commit = session_b.commit

    async def flaky_commit():
        if in_repair_stream["flag"]:
            in_repair_stream["flag"] = False
            session_b.is_active = False
            raise SQLAlchemyError("simulated db commit failure during repair")
        if not session_b.is_active:
            raise SQLAlchemyError("PendingRollbackError: session was not rolled back!")
        await orig_commit()

    async def tracked_rollback():
        session_b.rollbacks += 1
        session_b.is_active = True

    session_b.commit = flaky_commit
    session_b.rollback = tracked_rollback

    def trigger_db_fail_on_repair_chunk(call_idx: int, chunk_idx: int, _chunk: LlmStreamChunk) -> None:
        if call_idx == 1 and chunk_idx == 0:
            in_repair_stream["flag"] = True

    llm_b = ScriptedLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=v1_html),
                LlmStreamChunk(type="done", provider="qwen", model="qwen-mock", finish_reason="stop"),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text="<!doctype html><html><body>修复"),
                LlmStreamChunk(type="done", provider="qwen", model="qwen-mock", finish_reason="stop"),
            ],
        ],
        on_chunk=trigger_db_fail_on_repair_chunk,
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _key: llm_b)

    events_b = asyncio.run(_collect_events(GenerationService(session_b), task_b))
    assert events_b[-1].event == "failed"
    assert task_b.status == "failed"
    assert page_b.status == "failed"
    assert session_b.rollbacks >= 1
    assert records_b["uploaded_html"] is None
    assert records_b["released_tasks"] == [task_b.id]


async def _collect_events(service: GenerationService, task: GenerationTask):
    return [ev async for ev in service.run_task(task)]
