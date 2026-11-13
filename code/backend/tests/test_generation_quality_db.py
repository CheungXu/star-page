"""生成质量三步改造（截断续写、续写局部编辑、发布前渲染检查与自动修复）数据库全流程集成测试。

覆盖计划要求的全部场景（真实 PostgreSQL + 真实 BillingService 预冻结/结算/欠费 + HTTP 会话详情契约）：
1. 截断后续写成功：两轮用量合并计入用户账单，拼接完整 HTML 入库；
2. 续写后仍截断：标 failed（「页面内容过长，未能完整生成」）并全额退回预扣积分；
3. 续写轮返回 EDIT_BLOCKS 应用成功：model_edit_text 存编辑块、model_output_text 存合并后完整 HTML，
   下一轮续写 _load_node_html 准确取到合并后完整页面；同时验证开关关闭时退回整页续写指令；
4. 续写轮 EDIT_BLOCKS 对不上原文（及 MALFORMED）自动回退整页重写：失败编辑轮 + 回退重写轮用量均计入用户账单，
   含超出预扣时记欠费覆盖；
5. PAGE_RENDER_CHECK_MODE=off / observe：observe 记录 page_versions.render_check 但不修复，completed 事件与
   GET /api/conversations/{id} 的 render_issue 均为 None（且事后切到 repair 也不会误暴露历史 observe 记录）；
6. PAGE_RENDER_CHECK_MODE=repair（修复成功）：发现渲染问题 -> 调用模型用 EDIT_BLOCKS 修复一次 -> 复检通过 ->
   render_issue 为 None 且修复轮用量计入用户账单；
7. PAGE_RENDER_CHECK_MODE=repair（修复后仍有白屏/JS 报错）：照常发布为 ready、正常结算，completed 事件与
   GET /api/conversations/{id} 均准确返回 render_issue；
8. 渲染检查返回 skipped 时照常发布且无 render_issue；渲染检查期间用户停止立即取消；以及真实 Chromium 端到端闭环。

运行方式（严禁指向线上数据库）：
    TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55433/stars_page_test \
        .venv/bin/python -m pytest tests/test_generation_quality_db.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any
import uuid

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="未设置 TEST_DATABASE_URL")

if TEST_DATABASE_URL:
    from sqlalchemy import func, select, update  # noqa: E402
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

    from app.api import routes_conversations  # noqa: E402
    from app.core.config import get_billing_config, get_settings  # noqa: E402
    from app.main import app  # noqa: E402
    from app.models.entities import (  # noqa: E402
        CreditAccount,
        CreditTransaction,
        GenerationEvent,
        GenerationTask,
        Page,
        PageVersion,
        User,
    )
    from app.services import generation_service as service_module  # noqa: E402
    from app.services.billing.account import BillingService  # noqa: E402
    from app.services.generation_runner import GenerationRunner  # noqa: E402
    from app.services.generation_service import (  # noqa: E402
        EDIT_FALLBACK_STATUS_TEXT,
        RENDER_CHECK_STATUS_TEXT,
        RENDER_REPAIR_STATUS_TEXT,
        TRUNCATED_USER_MESSAGE,
        TRUNCATION_CONTINUE_STATUS_TEXT,
        GenerationService,
    )
    from app.services.llm.types import LlmMessage, LlmModelConfig, LlmStreamChunk, LlmUsage  # noqa: E402
    from app.services.page_render_check import (  # noqa: E402
        RenderCheckResult,
        build_blank_page_issue,
        build_js_error_issue,
        resolve_chromium_executable,
    )
    from app.services.skills.selector import SkillSelectionResult  # noqa: E402
    from idle_txn import assert_external_call_idle  # noqa: E402

    REAL_CHROMIUM = resolve_chromium_executable()
else:
    REAL_CHROMIUM = None


PRICING_SPEC = {
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


class MemoryObjectStorage:
    """内存对象存储：每次读写断言调用方数据库会话不处于事务中。"""

    def __init__(self) -> None:
        self.objects: dict[str, str] = {}

    async def put_text(self, key: str, content: str, content_type: str = "text/html; charset=utf-8") -> None:
        assert_external_call_idle(required=False)
        self.objects[key] = content

    async def get_text(self, key: str) -> str:
        assert_external_call_idle(required=True)
        if key not in self.objects:
            raise FileNotFoundError(key)
        return self.objects[key]


class QueueLlmClient:
    """按预设轮次队列回放流式 chunk，同时校验调用期间数据库会话处于空闲无事务状态。"""

    def __init__(self, rounds: list[list[LlmStreamChunk]]) -> None:
        self.rounds = list(rounds)
        self.calls: list[list[LlmMessage]] = []

    async def stream_text(self, messages: list[LlmMessage]):
        assert_external_call_idle(required=True)
        idx = len(self.calls)
        self.calls.append(list(messages))
        assert idx < len(self.rounds), f"LLM 调用超出预期轮数（第 {idx + 1} 轮）"
        for chunk in self.rounds[idx]:
            await asyncio.sleep(0)
            yield chunk


def _make_fake_registry(*, edit_mode: bool = True):
    cfg = LlmModelConfig(
        key="qwen-plus",
        label="通义千问 Flash",
        provider="qwen",
        protocol="openai",
        base_url="https://mock.llm.local/v1",
        model="qwen-mock",
        api_key="sk-mock",
        pricing=PRICING_SPEC,
        available=True,
        supports_vision=True,
        edit_mode=edit_mode,
    )
    models = {"qwen-plus": cfg}
    return SimpleNamespace(
        models=models,
        default_model_keys=["qwen-plus"],
        get=lambda key: models.get(key),
        available_models=lambda: [cfg],
    )


async def _no_skill(self, *, page, task):
    return SkillSelectionResult(None, None)


@pytest.fixture
def quality_env(monkeypatch):
    storage = MemoryObjectStorage()
    settings = get_settings()
    monkeypatch.setattr(settings, "page_skills_enabled", False)
    monkeypatch.setattr(settings, "generation_truncation_continues", 1)
    monkeypatch.setattr(settings, "page_edit_mode_enabled", True)
    monkeypatch.setattr(settings, "page_render_check_mode", "off")
    monkeypatch.setattr(settings, "generation_reasoning_flush_seconds", 0.0)
    monkeypatch.setattr(settings, "generation_progress_flush_seconds", 0.0)

    registry = _make_fake_registry(edit_mode=True)
    monkeypatch.setattr(service_module, "get_model_registry", lambda: registry)
    monkeypatch.setattr("app.services.llm.cost.get_model_registry", lambda: registry)
    monkeypatch.setattr(routes_conversations, "get_model_registry", lambda: registry)
    monkeypatch.setattr(GenerationService, "_resolve_task_skill_key", _no_skill)
    monkeypatch.setattr(service_module, "create_storage_provider", lambda: storage)

    return SimpleNamespace(storage=storage, settings=settings)


def _run_db(flow) -> None:
    async def wrapper() -> None:
        engine = create_async_engine(TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1))
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        runner = GenerationRunner(session_factory=factory)
        await _cleanup_unfinished(factory)
        try:
            await flow(factory, runner)
        finally:
            await runner.shutdown()
            await _cleanup_unfinished(factory)
            await engine.dispose()

    asyncio.run(wrapper())


async def _cleanup_unfinished(factory) -> None:
    async with factory() as session:
        await session.execute(
            update(GenerationTask)
            .where(GenerationTask.status.in_(["pending", "running"]))
            .values(status="failed", finished_at=func.now())
        )
        await session.commit()


async def _create_funded_user(
    factory,
    *,
    gift_credits: int = 500,
    paid_credits: int = 0,
    allow_training: bool = False,
) -> User:
    """创建登录用户并精确设置积分钱包（默认关闭授权折扣以便心算精确积分额）。"""
    async with factory() as session:
        user = User(
            username=f"qtest-{uuid.uuid4().hex[:10]}",
            display_name="质量改造测试用户",
            is_anonymous=False,
            allow_training=allow_training,
            policy_notice_seen_at=datetime.now(UTC),
        )
        session.add(user)
        await session.flush()
        account = CreditAccount(
            user_id=user.id,
            paid_balance=paid_credits,
            gift_balance=gift_credits,
            signup_bonus_granted=True,
        )
        session.add(account)
        await session.commit()
        return user


async def _submit_and_claim_run(
    factory,
    *,
    user_id: uuid.UUID,
    prompt: str,
    conversation_id: uuid.UUID | None = None,
    base_page_id: uuid.UUID | None = None,
    cancel_check=None,
) -> tuple[Any, list[Any]]:
    async with factory() as session:
        user = await session.get(User, user_id)
        creation = await GenerationService(session).create_batch(
            prompt=prompt,
            selected_model_keys=["qwen-plus"],
            conversation_id=conversation_id,
            base_page_id=base_page_id,
            user=user,
        )
    task_id = creation.runs[0].task_id
    async with factory() as session:
        service = GenerationService(session, cancel_check=cancel_check)
        claimed = await service.claim_task(task_id)
        assert claimed is not None
        events = [ev async for ev in service.run_task(claimed)]
    return creation, events


async def _fetch_conversation_detail_http(
    monkeypatch,
    factory,
    user: User,
    conversation_id: uuid.UUID,
) -> dict[str, Any]:
    async def fake_actor(_session, _request):
        return user

    monkeypatch.setattr(routes_conversations, "AsyncSessionLocal", factory)
    monkeypatch.setattr(routes_conversations, "get_optional_actor", fake_actor)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/conversations/{conversation_id}")
        assert resp.status_code == 200, resp.text
        return resp.json()


# ---------------------------------------------------------------------------
# 1. 截断后续写成功：两轮用量合并计费，拼接完整 HTML 入库
# ---------------------------------------------------------------------------


def test_db_truncation_continue_success_and_merged_billing(monkeypatch, quality_env):
    part1 = '<!DOCTYPE html><html lang="zh-CN"><head><title>长文报告</title></head><body><section cla'
    part2 = 'ss="hero"><h1>完整拼接标题</h1><p>核心数据</p></section></body></html>'
    llm = QueueLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=part1),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=1000, output_tokens=3000, total_tokens=4000),
                    finish_reason="length",
                ),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=part2),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=4200, output_tokens=1000, total_tokens=5200),
                    finish_reason="stop",
                ),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _k: llm)

    async def flow(factory, _runner):
        user = await _create_funded_user(factory, gift_credits=500)
        creation, events = await _submit_and_claim_run(factory, user_id=user.id, prompt="生成一份年度数据长文报告")
        run_ref = creation.runs[0]

        assert len(llm.calls) == 2
        status_texts = [ev.data.get("text") for ev in events if ev.event == "status"]
        assert TRUNCATION_CONTINUE_STATUS_TEXT in status_texts
        completed = events[-1]
        assert completed.event == "completed"
        assert completed.data["render_issue"] is None
        assert completed.data["usage"] == {"input_tokens": 5200, "output_tokens": 4000, "total_tokens": 9200}

        async with factory() as session:
            task = await session.get(GenerationTask, run_ref.task_id)
            page = await session.get(Page, run_ref.page_id)
            version = await session.get(PageVersion, page.current_version_id)
            account = await session.get(CreditAccount, user.id)
            txn = (
                await session.execute(
                    select(CreditTransaction).where(
                        CreditTransaction.user_id == user.id,
                        CreditTransaction.type == "consume",
                    )
                )
            ).scalar_one()

        assert task.status == "succeeded"
        assert page.status == "ready"
        assert '<section class="hero"><h1>完整拼接标题</h1>' in (task.model_output_text or "")
        assert '<section class="hero"><h1>完整拼接标题</h1>' in quality_env.storage.objects[version.storage_key]
        assert (version.input_tokens, version.output_tokens, version.total_tokens) == (5200, 4000, 9200)
        # 成本：5200 * 10/1e6 + 4000 * 30/1e6 = 0.052 + 0.12 = 0.172 CNY > 0，冻结已全部解冻结算
        assert abs(float(version.total_cost_cny) - 0.172) < 1e-6
        assert account.frozen_gift_balance == 0 and account.frozen_paid_balance == 0
        charged = completed.data["billing"]["credits_charged"]
        assert charged > 0
        assert -int(txn.credits_delta) == charged
        assert account.gift_balance == 500 - charged

    _run_db(flow)


# ---------------------------------------------------------------------------
# 2. 续写后仍截断：判失败并全额退回预扣积分
# ---------------------------------------------------------------------------


def test_db_truncation_exhausted_fails_and_refunds_full_reservation(monkeypatch, quality_env):
    llm = QueueLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text="<!DOCTYPE html><html><body>第一段"),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=500, output_tokens=4000, total_tokens=4500),
                    finish_reason="length",
                ),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text="<p>第二段依然超长未闭合"),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=4600, output_tokens=4000, total_tokens=8600),
                    finish_reason="length",
                ),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _k: llm)

    async def flow(factory, _runner):
        user = await _create_funded_user(factory, gift_credits=300)
        creation, events = await _submit_and_claim_run(factory, user_id=user.id, prompt="生成一个超长页面")
        run_ref = creation.runs[0]

        assert len(llm.calls) == 2
        assert events[-1].event == "failed"
        assert events[-1].data["message"] == TRUNCATED_USER_MESSAGE

        async with factory() as session:
            task = await session.get(GenerationTask, run_ref.task_id)
            page = await session.get(Page, run_ref.page_id)
            account = await session.get(CreditAccount, user.id)
            txns = (
                await session.execute(select(CreditTransaction).where(CreditTransaction.user_id == user.id))
            ).scalars().all()

        assert task.status == "failed"
        assert page.status == "failed"
        assert task.reservation_settled is True
        # 预扣积分 100% 原路退回，余额分文不少，无任何消费流水
        assert account.gift_balance == 300
        assert account.frozen_gift_balance == 0
        assert account.debt_credits == 0
        assert txns == []

    _run_db(flow)


# ---------------------------------------------------------------------------
# 3. 续写轮返回 EDIT_BLOCKS 应用成功 + 三轮接力验证 _load_node_html 与开关覆盖
# ---------------------------------------------------------------------------


def test_db_continuation_edit_blocks_success_and_next_round_loads_full_html(monkeypatch, quality_env):
    v1_html = (
        "<!doctype html>\n"
        "<html lang=\"zh-CN\">\n"
        "<head><meta charset=\"utf-8\"><title>产品发布 V1</title></head>\n"
        "<body>\n"
        "  <header><h1>星页 1.0 正式上线</h1></header>\n"
        "  <p class=\"lead\">支持多模型并行生成展示页。</p>\n"
        "</body>\n"
        "</html>"
    )
    # 故意不传 finish_reason (None)，验证闭合 EDIT_BLOCKS 绝不会因缺 </html> 被误判为截断！
    r2_edit_blocks = (
        "<<<<<<< SEARCH\n"
        "  <header><h1>星页 1.0 正式上线</h1></header>\n"
        "=======\n"
        "  <header><h1>星页 2.0 极速局部编辑版</h1></header>\n"
        ">>>>>>> REPLACE"
    )
    # 第 3 轮只命中第 2 轮合并后的新标题，若第 2 轮未存完整 HTML 将无法匹配！
    r3_edit_blocks = (
        "<<<<<<< SEARCH\n"
        "  <header><h1>星页 2.0 极速局部编辑版</h1></header>\n"
        "  <p class=\"lead\">支持多模型并行生成展示页。</p>\n"
        "=======\n"
        "  <header><h1>星页 2.0 极速局部编辑版</h1></header>\n"
        "  <p class=\"lead\">支持 9 模型局部编辑与无头浏览器自检。</p>\n"
        ">>>>>>> REPLACE"
    )
    r4_full_html = (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>V4</title></head>"
        "<body><h1>开关关闭后整页重写</h1></body></html>"
    )

    llm = QueueLlmClient(
        [
            [LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=v1_html)],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=r2_edit_blocks),
                # 故意无 finish_reason，测试截断检测×编辑块兼容
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=800, output_tokens=120, total_tokens=920),
                    finish_reason=None,
                ),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=r3_edit_blocks),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=900, output_tokens=150, total_tokens=1050),
                    finish_reason="stop",
                ),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=r4_full_html),
                LlmStreamChunk(type="done", provider="qwen", model="qwen-mock", finish_reason="stop"),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _k: llm)

    async def flow(factory, _runner):
        user = await _create_funded_user(factory, gift_credits=800)

        # 第 1 轮：新建
        c1, _ = await _submit_and_claim_run(factory, user_id=user.id, prompt="做一个星页 1.0 上线页")
        conv_id = c1.conversation_id
        p1_id = c1.runs[0].page_id

        # 第 2 轮：续写 -> 走局部编辑指令，模型输出 EDIT_BLOCKS（finish_reason=None）
        c2, ev2 = await _submit_and_claim_run(
            factory,
            user_id=user.id,
            prompt="把主标题改成 2.0 极速局部编辑版",
            conversation_id=conv_id,
            base_page_id=p1_id,
        )
        assert ev2[-1].event == "completed"
        # 验证第 2 轮提示词使用了 HTML_PAGE_EDIT_CONTINUE_PROMPT_TEMPLATE
        assert "<<<<<<< SEARCH" in llm.calls[1][-1].content
        assert llm.calls[1][-2].role == "assistant"
        assert "星页 1.0 正式上线" in llm.calls[1][-2].content

        async with factory() as session:
            t2 = await session.get(GenerationTask, c2.runs[0].task_id)
        assert t2.model_edit_text == r2_edit_blocks
        assert t2.model_output_text is not None
        assert "<!doctype html>" in t2.model_output_text.lower()
        assert "星页 2.0 极速局部编辑版" in t2.model_output_text
        assert "<<<<<<< SEARCH" not in t2.model_output_text

        # 第 3 轮：再续写 -> 验证 _load_node_html 拿到第 2 轮合并后的完整 HTML
        p2_id = c2.runs[0].page_id
        c3, ev3 = await _submit_and_claim_run(
            factory,
            user_id=user.id,
            prompt="更新副标题说明支持 9 模型局部编辑",
            conversation_id=conv_id,
            base_page_id=p2_id,
        )
        assert ev3[-1].event == "completed"
        assert llm.calls[2][-2].role == "assistant"
        assert "星页 2.0 极速局部编辑版" in llm.calls[2][-2].content

        async with factory() as session:
            t3 = await session.get(GenerationTask, c3.runs[0].task_id)
        assert t3.model_edit_text == r3_edit_blocks
        assert "支持 9 模型局部编辑与无头浏览器自检。" in (t3.model_output_text or "")

        # 第 4 轮：模型级 edit_mode=False 时，自动切回传统整页续写提示词
        disabled_reg = _make_fake_registry(edit_mode=False)
        monkeypatch.setattr(service_module, "get_model_registry", lambda: disabled_reg)
        p3_id = c3.runs[0].page_id
        c4, ev4 = await _submit_and_claim_run(
            factory,
            user_id=user.id,
            prompt="重做第四版",
            conversation_id=conv_id,
            base_page_id=p3_id,
        )
        assert ev4[-1].event == "completed"
        assert "<<<<<<< SEARCH" not in llm.calls[3][-1].content
        assert "输出修改后的完整 HTML" in llm.calls[3][-1].content
        async with factory() as session:
            t4 = await session.get(GenerationTask, c4.runs[0].task_id)
        assert t4.model_edit_text is None
        assert len(llm.calls) == 4

    _run_db(flow)


# ---------------------------------------------------------------------------
# 4. 续写轮 EDIT_BLOCKS 对不上原文回退整页重写 + 两轮全部计费 + 超预扣记欠费
# ---------------------------------------------------------------------------


def test_db_edit_blocks_mismatch_fallbacks_to_full_html_and_bills_both_with_debt(monkeypatch, quality_env):
    base_html = "<!doctype html><html><head><title>原页</title></head><body><h1>真实原标题</h1></body></html>"
    bad_edit = (
        "<<<<<<< SEARCH\n"
        "<h1>根本不存在于上一版里的幻觉标题</h1>\n"
        "=======\n"
        "<h1>新标题</h1>\n"
        ">>>>>>> REPLACE"
    )
    fallback_full_html = (
        "<!doctype html><html><head><title>回退整页</title></head>"
        "<body><h1>回退整页重写后的新标题</h1></body></html>"
    )

    # 故意设置两轮较大 token 用量，使总计费积分超过 qwen-plus 的预冻结额，验证超出预扣时记欠费
    llm = QueueLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=base_html),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=100, output_tokens=100, total_tokens=200),
                    finish_reason="stop",
                ),
            ],
            # 续写第 1 次尝试：EDIT_BLOCKS 未命中原文（消耗 10,000 in + 8,000 out -> 成本 0.10 + 0.24 = 0.34 元）
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=bad_edit),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=10_000, output_tokens=8_000, total_tokens=18_000),
                    finish_reason="stop",
                ),
            ],
            # 续写第 2 次尝试（回退整页）：消耗 15,000 in + 12,000 out -> 成本 0.15 + 0.36 = 0.51 元
            # 两轮合计成本 = 0.85 元，按倍率（>=1.65）应扣 > 140 积分，远超预扣额！
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=fallback_full_html),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=15_000, output_tokens=12_000, total_tokens=27_000),
                    finish_reason="stop",
                ),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _k: llm)

    async def flow(factory, _runner):
        hold_for_qwen_plus = get_billing_config().hold_credits_for("qwen-plus")
        # 给用户配置恰好够首轮 + 次轮预扣的积分，使次轮结算超出余额触发记欠费
        initial_gift = hold_for_qwen_plus + 20
        user = await _create_funded_user(factory, gift_credits=initial_gift)

        c1, ev1 = await _submit_and_claim_run(factory, user_id=user.id, prompt="首轮小页面")
        first_charged = ev1[-1].data["billing"]["credits_charged"]

        # 补齐到恰好只剩 hold_for_qwen_plus 积分（不多一分富余）
        async with factory() as session:
            acc = await session.get(CreditAccount, user.id)
            acc.gift_balance = hold_for_qwen_plus
            await session.commit()

        c2, ev2 = await _submit_and_claim_run(
            factory,
            user_id=user.id,
            prompt="修改标题",
            conversation_id=c1.conversation_id,
            base_page_id=c1.runs[0].page_id,
        )
        assert len(llm.calls) == 3
        # 验证回退轮消息包含了失败编辑输出与禁止 SEARCH 标记的回退提示词
        fallback_msgs = llm.calls[2]
        assert fallback_msgs[-2] == LlmMessage(role="assistant", content=bad_edit)
        assert "未找到匹配的原文片段" in fallback_msgs[-1].content
        assert "严禁再输出任何 `<<<<<<< SEARCH`" in fallback_msgs[-1].content

        statuses = [ev.data.get("text") for ev in ev2 if ev.event == "status"]
        assert EDIT_FALLBACK_STATUS_TEXT in statuses

        completed = ev2[-1]
        assert completed.event == "completed"
        # 验证失败编辑轮（10k/8k）+ 回退整页轮（15k/12k）全部并入了用户计费用量！
        assert completed.data["usage"] == {
            "input_tokens": 25_000,
            "output_tokens": 20_000,
            "total_tokens": 45_000,
        }

        async with factory() as session:
            t2 = await session.get(GenerationTask, c2.runs[0].task_id)
            p2 = await session.get(Page, c2.runs[0].page_id)
            v2 = await session.get(PageVersion, p2.current_version_id)
            acc_after = await session.get(CreditAccount, user.id)

        assert t2.status == "succeeded"
        assert t2.model_edit_text is None
        assert "回退整页重写后的新标题" in (t2.model_output_text or "")
        assert (v2.input_tokens, v2.output_tokens) == (25_000, 20_000)
        assert abs(float(v2.total_cost_cny) - 0.85) < 1e-6
        # 验证钱包扣空且超额部分记入 debt_credits（受单任务欠费上限保护）
        assert acc_after.gift_balance == 0
        assert acc_after.frozen_gift_balance == 0
        assert 0 < acc_after.debt_credits <= get_billing_config().max_debt_credits_per_task
        assert first_charged >= 0

        # HTTP 会话详情里扣费金额与 completed 事件一致
        detail = await _fetch_conversation_detail_http(monkeypatch, factory, user, c1.conversation_id)
        node2 = detail["batches"][1]["nodes"][0]
        assert node2["usage"]["input_tokens"] == 25_000
        assert node2["usage"]["output_tokens"] == 20_000
        assert node2["billing"]["credits_charged"] == completed.data["billing"]["credits_charged"]

    _run_db(flow)


# ---------------------------------------------------------------------------
# 5. PAGE_RENDER_CHECK_MODE=off 与 observe：observe 落库但不修复、绝不向前端漏标
# ---------------------------------------------------------------------------


def test_db_render_check_off_and_observe_modes_never_expose_render_issue(monkeypatch, quality_env):
    broken_html = "<!doctype html><html><head><title>观察页</title></head><body><script>badCall();</script></body></html>"
    llm = QueueLlmClient(
        [
            [LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=broken_html)],
            [LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=broken_html)],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _k: llm)

    check_calls: list[str] = []

    async def fake_check_render(html: str) -> RenderCheckResult:
        assert_external_call_idle(required=True)
        check_calls.append(html)
        return RenderCheckResult(
            status="issues_found",
            issues=[build_js_error_issue("ReferenceError", "badCall is not defined", "at http://starpage.invalid/:1:80")],
            duration_ms=380.0,
            rss_mb=140.0,
        )

    monkeypatch.setattr(service_module, "check_page_render", fake_check_render)

    async def flow(factory, _runner):
        user = await _create_funded_user(factory, gift_credits=600)

        # (A) mode == "off"：根本不调用 check_page_render，version.render_check 为 None
        quality_env.settings.page_render_check_mode = "off"
        c_off, ev_off = await _submit_and_claim_run(factory, user_id=user.id, prompt="关闭检查模式页面")
        assert check_calls == []
        assert ev_off[-1].event == "completed"
        assert ev_off[-1].data["render_issue"] is None
        async with factory() as session:
            p_off = await session.get(Page, c_off.runs[0].page_id)
            v_off = await session.get(PageVersion, p_off.current_version_id)
        assert v_off.render_check is None

        # (B) mode == "observe"：调用 1 次检查并落库，但绝不调用第 2 轮模型修复，也不暴露 render_issue
        quality_env.settings.page_render_check_mode = "observe"
        c_obs, ev_obs = await _submit_and_claim_run(
            factory,
            user_id=user.id,
            prompt="观察模式页面",
            conversation_id=c_off.conversation_id,
            base_page_id=c_off.runs[0].page_id,
        )
        assert len(check_calls) == 1
        assert len(llm.calls) == 2  # 每轮只调了 1 次模型，无修复轮！
        statuses = [ev.data.get("text") for ev in ev_obs if ev.event == "status"]
        assert RENDER_CHECK_STATUS_TEXT in statuses
        assert RENDER_REPAIR_STATUS_TEXT not in statuses
        assert ev_obs[-1].event == "completed"
        assert ev_obs[-1].data["render_issue"] is None

        async with factory() as session:
            p_obs = await session.get(Page, c_obs.runs[0].page_id)
            v_obs = await session.get(PageVersion, p_obs.current_version_id)
        assert v_obs.render_check is not None
        assert v_obs.render_check["status"] == "issues_found"
        assert v_obs.render_check["mode"] == "observe"
        assert v_obs.render_check["expose_issue"] is False
        assert v_obs.render_check["repaired"] is False

        # 关键防漏标验证：即使后来线上配置切到了 repair，读取历史会话时 observe 阶段记录依然不显示徽标！
        quality_env.settings.page_render_check_mode = "repair"
        detail = await _fetch_conversation_detail_http(monkeypatch, factory, user, c_off.conversation_id)
        assert detail["batches"][0]["nodes"][0]["render_issue"] is None
        assert detail["batches"][1]["nodes"][0]["render_issue"] is None

    _run_db(flow)


# ---------------------------------------------------------------------------
# 6. PAGE_RENDER_CHECK_MODE=repair：发现问题 -> 用 EDIT_BLOCKS 修复成功 -> 复检通过 & 计费合并
# ---------------------------------------------------------------------------


def test_db_render_check_repair_succeeds_with_edit_blocks_and_bills_repair_round(monkeypatch, quality_env):
    quality_env.settings.page_render_check_mode = "repair"

    first_html = (
        "<!doctype html>\n"
        "<html><head><title>看板</title></head>\n"
        "<body>\n"
        "  <h1>销售看板</h1>\n"
        "  <script>\n"
        "    renderMissingChart();\n"
        "  </script>\n"
        "</body></html>"
    )
    repair_edit = (
        "<<<<<<< SEARCH\n"
        "    renderMissingChart();\n"
        "=======\n"
        "    document.querySelector('h1').dataset.ok = '1';\n"
        ">>>>>>> REPLACE"
    )

    llm = QueueLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=first_html),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=1000, output_tokens=2000, total_tokens=3000),
                    finish_reason="stop",
                ),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=repair_edit),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=2200, output_tokens=300, total_tokens=2500),
                    finish_reason="stop",
                ),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _k: llm)

    checked_htmls: list[str] = []

    async def fake_check_render(html: str) -> RenderCheckResult:
        assert_external_call_idle(required=True)
        checked_htmls.append(html)
        if "renderMissingChart" in html:
            return RenderCheckResult(
                status="issues_found",
                issues=[
                    build_js_error_issue(
                        "ReferenceError",
                        "renderMissingChart is not defined",
                        "at http://starpage.invalid/:6:5",
                    )
                ],
                duration_ms=410.0,
                rss_mb=135.0,
            )
        return RenderCheckResult(status="passed", duration_ms=350.0, rss_mb=130.0)

    monkeypatch.setattr(service_module, "check_page_render", fake_check_render)

    async def flow(factory, _runner):
        user = await _create_funded_user(factory, gift_credits=600)
        creation, events = await _submit_and_claim_run(factory, user_id=user.id, prompt="做一个销售看板页面")
        run_ref = creation.runs[0]

        assert len(llm.calls) == 2
        assert len(checked_htmls) == 2
        # 验证修复轮 assistant 消息正是首轮生成的 HTML，且修复 prompt 包含行号与异常信息
        repair_msgs = llm.calls[1]
        assert repair_msgs[-2] == LlmMessage(role="assistant", content=first_html)
        assert "renderMissingChart" in repair_msgs[-1].content
        assert "<<<<<<< SEARCH" in repair_msgs[-1].content

        statuses = [ev.data.get("text") for ev in events if ev.event == "status"]
        assert RENDER_CHECK_STATUS_TEXT in statuses
        assert RENDER_REPAIR_STATUS_TEXT in statuses

        completed = events[-1]
        assert completed.event == "completed"
        assert completed.data["render_issue"] is None
        # 首轮 (1000/2000) + 修复轮 (2200/300) = (3200/2300) 合并计费
        assert completed.data["usage"] == {"input_tokens": 3200, "output_tokens": 2300, "total_tokens": 5500}

        async with factory() as session:
            task = await session.get(GenerationTask, run_ref.task_id)
            page = await session.get(Page, run_ref.page_id)
            version = await session.get(PageVersion, page.current_version_id)

        assert task.status == "succeeded"
        assert page.status == "ready"
        assert task.model_edit_text == repair_edit
        assert "renderMissingChart" not in (task.model_output_text or "")
        assert "dataset.ok" in (task.model_output_text or "")
        assert "dataset.ok" in quality_env.storage.objects[version.storage_key]
        assert version.render_check is not None
        assert version.render_check["status"] == "passed"
        assert version.render_check["repaired"] is True
        assert version.render_check["mode"] == "repair"
        assert (version.input_tokens, version.output_tokens) == (3200, 2300)

        detail = await _fetch_conversation_detail_http(monkeypatch, factory, user, creation.conversation_id)
        node = detail["batches"][0]["nodes"][0]
        assert node["render_issue"] is None
        assert node["usage"]["input_tokens"] == 3200
        assert node["usage"]["output_tokens"] == 2300
        assert node["billing"]["credits_charged"] == completed.data["billing"]["credits_charged"]

    _run_db(flow)


# ---------------------------------------------------------------------------
# 7. PAGE_RENDER_CHECK_MODE=repair：修复一次后仍有白屏/JS 报错 -> 照常发布为 ready 并下发 render_issue
# ---------------------------------------------------------------------------


def test_db_render_check_repair_still_failing_always_publishes_with_render_issue(monkeypatch, quality_env):
    quality_env.settings.page_render_check_mode = "repair"

    broken_v1 = "<!doctype html><html><body><div style='opacity:0'><h1>隐藏标题</h1></div><script>boom1();</script></body></html>"
    broken_v2 = "<!doctype html><html><body><div style='opacity:0'><h1>仍隐藏</h1></div><script>boom2();</script></body></html>"

    llm = QueueLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=broken_v1),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=800, output_tokens=1500, total_tokens=2300),
                    finish_reason="stop",
                ),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=broken_v2),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=2500, output_tokens=1600, total_tokens=4100),
                    finish_reason="stop",
                ),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _k: llm)

    call_idx = {"n": 0}

    async def fake_check_render(_html: str) -> RenderCheckResult:
        assert_external_call_idle(required=True)
        call_idx["n"] += 1
        fn_name = "boom1" if call_idx["n"] == 1 else "boom2"
        return RenderCheckResult(
            status="issues_found",
            issues=[
                build_blank_page_issue("首屏元素保持 opacity:0"),
                build_js_error_issue("ReferenceError", f"{fn_name} is not defined", "at http://starpage.invalid/:1:90"),
            ],
            duration_ms=480.0,
            rss_mb=145.0,
        )

    monkeypatch.setattr(service_module, "check_page_render", fake_check_render)

    async def flow(factory, _runner):
        user = await _create_funded_user(factory, gift_credits=600)
        creation, events = await _submit_and_claim_run(factory, user_id=user.id, prompt="做一页带入场动画的海报")
        run_ref = creation.runs[0]

        # 最多只修 1 次（总计 2 次模型调用、2 次渲染检查），绝不死循环
        assert len(llm.calls) == 2
        assert call_idx["n"] == 2

        completed = events[-1]
        assert completed.event == "completed"
        render_issue = completed.data["render_issue"]
        assert render_issue is not None
        assert "首屏疑似白屏" in render_issue["summary"]
        assert "脚本运行出错" in render_issue["summary"]
        assert any("boom2" in item for item in render_issue["issues"])
        assert completed.data["usage"] == {"input_tokens": 3300, "output_tokens": 3100, "total_tokens": 6400}
        assert completed.data["billing"]["credits_charged"] > 0

        async with factory() as session:
            task = await session.get(GenerationTask, run_ref.task_id)
            page = await session.get(Page, run_ref.page_id)
            version = await session.get(PageVersion, page.current_version_id)
            acc = await session.get(CreditAccount, user.id)

        # 策略 always_publish：一律照常发布为 ready 并完成扣费结算
        assert task.status == "succeeded"
        assert page.status == "ready"
        assert version.status == "ready"
        assert version.render_check["status"] == "issues_found"
        assert version.render_check["repaired"] is True
        assert version.render_check["expose_issue"] is True
        assert acc.frozen_gift_balance == 0
        assert acc.gift_balance == 600 - completed.data["billing"]["credits_charged"]

        # GET /api/conversations/{id} 同样返回一致的 render_issue
        detail = await _fetch_conversation_detail_http(monkeypatch, factory, user, creation.conversation_id)
        node = detail["batches"][0]["nodes"][0]
        assert node["page_status"] == "ready"
        assert node["generation_status"] == "succeeded"
        assert node["render_issue"] == render_issue

    _run_db(flow)


# ---------------------------------------------------------------------------
# 8. 渲染检查返回 skipped 时照常发布无标记 + 渲染检查期间停止立即取消
# ---------------------------------------------------------------------------


def test_db_render_check_skipped_and_cancel_during_render_check(monkeypatch, quality_env):
    quality_env.settings.page_render_check_mode = "repair"
    good_html = "<!doctype html><html><head><title>正常页</title></head><body><h1>内容正常</h1></body></html>"

    llm = QueueLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=good_html),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=400, output_tokens=500, total_tokens=900),
                    finish_reason="stop",
                ),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=good_html),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=400, output_tokens=500, total_tokens=900),
                    finish_reason="stop",
                ),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _k: llm)

    async def skipped_check(_html: str) -> RenderCheckResult:
        assert_external_call_idle(required=True)
        return RenderCheckResult(status="skipped", skip_reason="chromium_not_found")

    monkeypatch.setattr(service_module, "check_page_render", skipped_check)

    async def flow(factory, _runner):
        user = await _create_funded_user(factory, gift_credits=600)

        # (A) skipped 照常发布且无 render_issue
        c1, ev1 = await _submit_and_claim_run(factory, user_id=user.id, prompt="无浏览器环境发布")
        assert ev1[-1].event == "completed"
        assert ev1[-1].data["render_issue"] is None
        async with factory() as session:
            p1 = await session.get(Page, c1.runs[0].page_id)
            v1 = await session.get(PageVersion, p1.current_version_id)
        assert p1.status == "ready"
        assert v1.render_check["status"] == "skipped"
        assert v1.render_check["skip_reason"] == "chromium_not_found"

        detail = await _fetch_conversation_detail_http(monkeypatch, factory, user, c1.conversation_id)
        assert detail["batches"][0]["nodes"][0]["render_issue"] is None

        # (B) 在渲染检查等待期间触发取消：立即取消检查协程并走 cancelled 收尾
        stop_flag = {"cancelled": False}
        check_cancelled_flag = {"triggered": False}

        async def slow_cancellable_check(_html: str) -> RenderCheckResult:
            stop_flag["cancelled"] = True
            try:
                await asyncio.sleep(30.0)
            except asyncio.CancelledError:
                check_cancelled_flag["triggered"] = True
                raise
            return RenderCheckResult(status="passed")

        monkeypatch.setattr(service_module, "check_page_render", slow_cancellable_check)
        monkeypatch.setattr(service_module, "CANCEL_POLL_SECONDS", 0.05)

        c2, ev2 = await _submit_and_claim_run(
            factory,
            user_id=user.id,
            prompt="检查中点取消",
            cancel_check=lambda: stop_flag["cancelled"],
        )
        assert check_cancelled_flag["triggered"] is True
        assert ev2[-1].event == "cancelled"
        async with factory() as session:
            t2 = await session.get(GenerationTask, c2.runs[0].task_id)
            p2 = await session.get(Page, c2.runs[0].page_id)
        assert t2.status == "cancelled"
        assert p2.status == "failed"

    _run_db(flow)


# ---------------------------------------------------------------------------
# 9. 真实 Chromium + 真实 PostgreSQL 端到端自动修复黄金路径验证
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_CHROMIUM, reason="当前环境未安装无头 Chromium")
def test_db_real_chromium_end_to_end_repair_flow(monkeypatch, quality_env):
    quality_env.settings.page_render_check_mode = "repair"
    quality_env.settings.page_render_check_settle_ms = 120
    quality_env.settings.page_render_check_timeout_seconds = 10.0

    first_broken_html = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>端到端真实检查</title></head>
<body>
  <h1 id="title">季度核心营收看板</h1>
  <script>
    undefinedChartBootstrap();
  </script>
</body>
</html>"""

    repair_edit_block = """<<<<<<< SEARCH
    undefinedChartBootstrap();
=======
    document.getElementById("title").dataset.rendered = "yes";
>>>>>>> REPLACE"""

    llm = QueueLlmClient(
        [
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=first_broken_html),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=600, output_tokens=800, total_tokens=1400),
                    finish_reason="stop",
                ),
            ],
            [
                LlmStreamChunk(type="text_delta", provider="qwen", model="qwen-mock", text=repair_edit_block),
                LlmStreamChunk(
                    type="done",
                    provider="qwen",
                    model="qwen-mock",
                    usage=LlmUsage(input_tokens=1500, output_tokens=200, total_tokens=1700),
                    finish_reason="stop",
                ),
            ],
        ]
    )
    monkeypatch.setattr(service_module, "create_llm_client", lambda _k: llm)

    async def flow(factory, _runner):
        user = await _create_funded_user(factory, gift_credits=600)
        creation, events = await _submit_and_claim_run(factory, user_id=user.id, prompt="真实无头浏览器自检并修复")
        run_ref = creation.runs[0]

        assert len(llm.calls) == 2
        assert "undefinedChartBootstrap" in llm.calls[1][-1].content
        assert events[-1].event == "completed"
        assert events[-1].data["render_issue"] is None

        async with factory() as session:
            page = await session.get(Page, run_ref.page_id)
            version = await session.get(PageVersion, page.current_version_id)
        assert page.status == "ready"
        assert version.render_check["status"] == "passed"
        assert version.render_check["repaired"] is True
        assert version.render_check["rss_mb"] > 0

    _run_db(flow)
