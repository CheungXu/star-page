"""资料流程端到端：后台解析一次供全批共用、每轮同模型提取、图片双通道、解析失败退回免费次数、停一个模型不打断解析。

真实数据库 + 本地存储 + 真实解析器，模型调用和提取换成假的。需要一个跑过迁移的空库：

    TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/stars_page_test \
        .venv/bin/python -m pytest tests/test_document_flow_db.py

未设置 TEST_DATABASE_URL 时跳过。
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="未设置 TEST_DATABASE_URL")

if TEST_DATABASE_URL:
    from sqlalchemy import select  # noqa: E402
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

    from app.models.entities import CreditAccount, GenerationBatch, GenerationEvent, GenerationTask, PageAsset, User  # noqa: E402
    from app.services import generation_service as service_module  # noqa: E402
    from app.services.billing.account import BillingService  # noqa: E402
    from app.services.document_parser import attachments as attachments_module  # noqa: E402
    from app.services.document_parser import pipeline  # noqa: E402
    from app.services.document_parser.extractor_agent import ExtractionResult  # noqa: E402
    from app.services.document_parser.precheck import precheck_uploads  # noqa: E402
    from app.services.generation_runner import GenerationRunner  # noqa: E402
    from app.services.generation_service import VISION_SKIPPED_MESSAGE, GenerationService  # noqa: E402
    from app.services.image_assets import store_general_images  # noqa: E402
    from app.services.llm.types import LlmStreamChunk, LlmUsage  # noqa: E402
    from app.services.skills.selector import SkillSelectionResult  # noqa: E402
    from app.services.storage.local import LocalStorageProvider  # noqa: E402

    from doc_fixtures import build_pdf, jpeg_bytes, png_bytes  # noqa: E402

HTML = "<!DOCTYPE html><html><head><title>t</title></head><body>ok</body></html>"


class Recorder:
    def __init__(self) -> None:
        self.messages: dict[str, list] = {}
        self.extractions: list[dict] = []
        self.parses = 0
        self.extraction_reply: ExtractionResult | None = None


class FakeClient:
    def __init__(self, model_key: str, recorder: Recorder) -> None:
        self.model_key = model_key
        self.recorder = recorder

    async def stream_text(self, messages):
        self.recorder.messages.setdefault(self.model_key, []).append(messages)
        if self.model_key.startswith("slow"):
            await asyncio.sleep(3600)
        yield LlmStreamChunk(type="text_delta", provider="fake", model="fake", text=HTML)


class FakeRegistry:
    def get(self, key: str):
        return SimpleNamespace(key=key, label=key, provider="fake", model=key, supports_vision=key.startswith("vis"))


async def _noop(*_args, **_kwargs):
    return None


async def _no_skill(self, *, page, task):
    return SkillSelectionResult(None, None)


async def _fake_upload(self, page, html):
    return uuid.uuid4(), f"pages/{page.id}/test/index.html"


@pytest.fixture
def env(monkeypatch, tmp_path):
    recorder = Recorder()
    storage = LocalStorageProvider(str(tmp_path))
    for module in (pipeline, attachments_module):
        monkeypatch.setattr(module, "create_storage_provider", lambda: storage)
    monkeypatch.setattr("app.services.image_assets.create_storage_provider", lambda: storage)
    monkeypatch.setattr(BillingService, "ensure_can_start_batch", _noop)
    monkeypatch.setattr(
        BillingService,
        "reserve_for_batch",
        lambda self, *, model_keys, **_kw: asyncio.sleep(
            0,
            result=__import__("app.services.billing.account", fromlist=["BatchReservation"]).BatchReservation(),
        ),
    )
    monkeypatch.setattr(BillingService, "record_anon_batch_started", _noop)
    monkeypatch.setattr(BillingService, "validate_anon_models", lambda self, keys: None)
    monkeypatch.setattr("app.services.anon_service.AnonService.check_ip_free_generation_ceiling", _noop)
    monkeypatch.setattr(GenerationService, "_resolve_model_keys", lambda self, keys, allowed_keys=None: list(keys))
    monkeypatch.setattr(GenerationService, "_resolve_task_skill_key", _no_skill)
    monkeypatch.setattr(GenerationService, "_upload_page_html", _fake_upload)
    monkeypatch.setattr(GenerationService, "_settle_generation_billing", _noop)
    monkeypatch.setattr(service_module, "create_llm_client", lambda key: FakeClient(key, recorder))
    monkeypatch.setattr(service_module, "get_model_registry", lambda: FakeRegistry())

    real_parse = pipeline.parse_document

    def counting_parse(filename, extension, data):
        recorder.parses += 1
        if filename.startswith("slow"):
            import time

            time.sleep(1.0)
        return real_parse(filename, extension, data)

    monkeypatch.setattr(pipeline, "parse_document", counting_parse)

    async def fake_extract(*, model_key, document_text, history, current):
        recorder.extractions.append({"model": model_key, "history": history, "current": current, "text": document_text})
        if recorder.extraction_reply is not None:
            return recorder.extraction_reply
        snap_ids = [line.split("snap:")[1].split("｜")[0] for line in document_text.splitlines() if "snap:" in line]
        return ExtractionResult(
            text="提取：Annual Report",
            image_refs=snap_ids[:1],
            usage=LlmUsage(input_tokens=50, output_tokens=5, total_tokens=55),
            prompt_log="log",
        )

    monkeypatch.setattr(service_module, "extract_for_round", fake_extract)
    return SimpleNamespace(recorder=recorder, storage=storage, root=tmp_path)


def _run(flow) -> None:
    async def wrapper() -> None:
        engine = create_async_engine(TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1))
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        pipeline.configure_session_factory(factory)
        runner = GenerationRunner(session_factory=factory)
        try:
            await flow(factory, runner)
        finally:
            await runner.shutdown()
            pipeline.configure_session_factory(None)
            await engine.dispose()

    asyncio.run(wrapper())


def _report_pdf() -> bytes:
    return build_pdf(
        [
            {"texts": [(60, 800, 22, "Annual Report"), (50, 760, 10, "Revenue grew 30 percent " * 6)], "image": (60, 300, 200, 120, jpeg_bytes(400, 240))},
            {"texts": [(50, 780, 10, "Architecture")], "image": (50, 200, 500, 500, jpeg_bytes(500, 500, (10, 200, 10)))},
        ]
    )


async def _user(factory, *, anonymous: bool = False) -> User:
    async with factory() as session:
        user = User(username=f"doc-{uuid.uuid4().hex[:12]}", display_name="资料测试", is_anonymous=anonymous)
        session.add(user)
        await session.commit()
        return user


async def _create(factory, user, models, files, *, conversation_id=None, prompt="做一个年报网页"):
    documents, images = precheck_uploads(files)
    staged = await pipeline.stage_documents(documents)
    stored = await store_general_images(images)
    async with factory() as session:
        creation = await GenerationService(session).create_batch(
            prompt=prompt,
            selected_model_keys=models,
            user_prompt=prompt,
            input_file_names=[name for name, _data in files],
            staged_uploads=staged,
            stored_images=stored,
            conversation_id=conversation_id,
            user=user,
        )
    return creation, staged


async def _wait_idle(runner, timeout: float = 20.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while runner._tasks:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("等待超时")
        await asyncio.sleep(0.05)


async def _events(factory, task_id) -> list[tuple[str, dict]]:
    async with factory() as session:
        result = await session.execute(
            select(GenerationEvent.event_type, GenerationEvent.payload).where(GenerationEvent.task_id == task_id).order_by(GenerationEvent.sequence)
        )
        return list(result.all())


def test_first_round_parses_once_and_feeds_both_channels(env):
    async def flow(factory, runner):
        user = await _user(factory)
        creation, staged = await _create(factory, user, ["vis-a", "txt-b"], [("report.pdf", _report_pdf()), ("logo.png", png_bytes())])
        ids = {run.model_key: run.task_id for run in creation.runs}
        runner.start(list(ids.values()), creation.batch_id, user_id=user.id, user_limit=10)
        await _wait_idle(runner)

        assert env.recorder.parses == 1
        assert len(env.recorder.extractions) == 2
        assert {item["model"] for item in env.recorder.extractions} == {"vis-a", "txt-b"}
        async with factory() as session:
            batch = await session.get(GenerationBatch, creation.batch_id)
            tasks = {key: await session.get(GenerationTask, task_id) for key, task_id in ids.items()}
            assets = list((await session.execute(select(PageAsset).where(PageAsset.batch_id == batch.id))).scalars())
        assert batch.parse_status == "succeeded" and batch.staged_uploads == []
        assert not (env.root / staged[0]["storage_key"]).exists()
        assert {asset.source_kind for asset in assets} == {"doc_embedded", "user_upload"}
        # 快照只在私有键里，不进 page_assets。
        assert len(batch.visual_snapshots) == 1
        assert batch.visual_snapshots[0]["storage_key"].startswith("batch-private/")
        assert (env.root / batch.visual_snapshots[0]["storage_key"]).exists()
        assert "[整页快照 snap:" in batch.extracted_file_text and "[核心内容大图 img:" in batch.extracted_file_text
        for task in tasks.values():
            assert task.status == "succeeded"
            assert task.extracted_context == "提取：Annual Report"
            assert "data:image" not in (task.model_prompt or "")

        vision_messages = env.recorder.messages["vis-a"][0]
        last = vision_messages[-1]
        assert "# 资料提取内容" in last.content and "/assets/" in last.content
        # 用户上传图 + 提取点名的快照
        assert len(last.images) == 2
        assert "只用于阅读内容" in last.content
        text_last = env.recorder.messages["txt-b"][0][-1]
        assert text_last.images == () and "随本条消息附上" not in text_last.content
        statuses = [payload.get("text") for kind, payload in await _events(factory, ids["txt-b"]) if kind == "status"]
        assert VISION_SKIPPED_MESSAGE in statuses
        steps = [(payload["step"], payload["status"]) for kind, payload in await _events(factory, ids["vis-a"]) if kind == "progress"]
        assert steps[:5] == [
            ("upload_file", "completed"),
            ("parse_file", "running"),
            ("parse_file", "completed"),
            ("compress_document", "running"),
            ("compress_document", "completed"),
        ]

        # 第二轮：没传新文件，仍然结合资料重新提取；本轮与资料无关时不塞资料、不重复附图。
        env.recorder.extraction_reply = ExtractionResult(text="", prompt_log="log")
        second, _staged = await _create(factory, user, ["vis-a"], [], conversation_id=creation.conversation_id, prompt="把主色改成墨绿")
        runner.start([second.runs[0].task_id], second.batch_id, user_id=user.id, user_limit=10)
        await _wait_idle(runner)
        follow = env.recorder.extractions[-1]
        assert follow["history"] == ["做一个年报网页"] and follow["current"] == "把主色改成墨绿"
        assert "Annual Report" in follow["text"]
        last = env.recorder.messages["vis-a"][-1][-1]
        assert "# 资料提取内容" not in last.content and last.images == ()
        progress = [payload for kind, payload in await _events(factory, second.runs[0].task_id) if kind == "progress"]
        assert any(item["step"] == "compress_document" and item["text"] == "本轮需求与资料无关，未引用资料" for item in progress)
        assert not any(item["step"] == "parse_file" for item in progress)

    _run(flow)


def test_same_image_in_two_documents_stored_once_and_anchored_in_both(env):
    async def flow(factory, runner):
        user = await _user(factory)
        logo = jpeg_bytes(400, 240, (200, 30, 30))
        first = build_pdf([{"texts": [(50, 780, 10, "First document body " * 8)], "image": (60, 300, 200, 120, logo)}])
        second = build_pdf([{"texts": [(50, 780, 10, "Second document body " * 8)], "image": (60, 300, 200, 120, logo)}])
        creation, _staged = await _create(factory, user, ["vis-a"], [("a.pdf", first), ("b.pdf", second)])
        runner.start([creation.runs[0].task_id], creation.batch_id, user_id=user.id, user_limit=10)
        await _wait_idle(runner)
        async with factory() as session:
            batch = await session.get(GenerationBatch, creation.batch_id)
            assets = list((await session.execute(select(PageAsset).where(PageAsset.batch_id == batch.id))).scalars())
        assert len(assets) == 1
        anchor = f"img:{assets[0].id}"
        documents = batch.parsed_documents
        assert anchor in batch.extracted_file_text[documents[0]["start"] : documents[0]["end"]]
        assert anchor in batch.extracted_file_text[documents[1]["start"] : documents[1]["end"]]

    _run(flow)


def test_parse_failure_fails_batch_and_refunds_free_trial_once(env):
    async def flow(factory, runner):
        user = await _user(factory, anonymous=True)
        async with factory() as session:
            session.add(CreditAccount(user_id=user.id, free_generations_used=1))
            await session.commit()
        # 绕过预检放进一个坏 PDF，模拟后台解析失败。
        staged = await pipeline.stage_documents([pipeline.StagedDocument(filename="broken.pdf", extension=".pdf", data=b"%PDF-1.4 garbage")])
        async with factory() as session:
            creation = await GenerationService(session).create_batch(
                prompt="做网页", selected_model_keys=["vis-a", "txt-b"], input_file_names=["broken.pdf"], staged_uploads=staged, user=user
            )
        runner.start([run.task_id for run in creation.runs], creation.batch_id, user_id=user.id, user_limit=10)
        await _wait_idle(runner)

        async with factory() as session:
            batch = await session.get(GenerationBatch, creation.batch_id)
            tasks = [await session.get(GenerationTask, run.task_id) for run in creation.runs]
            account = await session.get(CreditAccount, user.id)
        assert batch.parse_status == "failed" and batch.free_trial_refunded is True
        assert all(task.status == "failed" and task.error_message.startswith("document_stage: ") for task in tasks)
        assert account.free_generations_used == 0
        assert env.recorder.parses == 1
        assert not (env.root / staged[0]["storage_key"]).exists()
        failed = [payload for kind, payload in await _events(factory, tasks[0].id) if kind == "failed"]
        assert "broken.pdf 解析失败" in failed[0]["message"]

    _run(flow)


def test_stopping_running_model_keeps_parse_for_queued_sibling(env):
    """个人上限为 1：A 在解析时被停，B 还在排队。解析不能被取消，B 轮到后照常完成。"""

    async def flow(factory, runner):
        user = await _user(factory)
        creation, staged = await _create(factory, user, ["vis-a", "vis-b"], [("slow-report.pdf", _report_pdf())])
        ids = {run.model_key: run.task_id for run in creation.runs}
        runner.start([ids["vis-a"], ids["vis-b"]], creation.batch_id, user_id=user.id, user_limit=1)

        async def parsing() -> bool:
            return any(kind == "progress" and payload["step"] == "parse_file" for kind, payload in await _events(factory, ids["vis-a"]))

        deadline = asyncio.get_running_loop().time() + 10
        while not await parsing():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.02)
        assert await runner.cancel(ids["vis-a"]) is True
        await _wait_idle(runner)

        async with factory() as session:
            batch = await session.get(GenerationBatch, creation.batch_id)
            stopped = await session.get(GenerationTask, ids["vis-a"])
            queued = await session.get(GenerationTask, ids["vis-b"])
        assert stopped.status == "cancelled"
        assert queued.status == "succeeded", queued.error_message
        assert batch.parse_status == "succeeded"
        assert env.recorder.parses == 1
        assert not (env.root / staged[0]["storage_key"]).exists()

    _run(flow)


def test_parse_cancelled_when_whole_batch_stopped(env):
    async def flow(factory, runner):
        user = await _user(factory)
        creation, staged = await _create(factory, user, ["vis-a"], [("slow-report.pdf", _report_pdf())])
        task_id = creation.runs[0].task_id
        runner.start([task_id], creation.batch_id, user_id=user.id, user_limit=10)

        async def parsing() -> bool:
            return any(kind == "progress" and payload["step"] == "parse_file" for kind, payload in await _events(factory, task_id))

        deadline = asyncio.get_running_loop().time() + 10
        while not await parsing():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.02)
        assert await runner.cancel(task_id) is True
        await _wait_idle(runner)
        deadline = asyncio.get_running_loop().time() + 5
        while creation.batch_id in pipeline._jobs:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.05)

        async with factory() as session:
            batch = await session.get(GenerationBatch, creation.batch_id)
        assert batch.parse_status == "failed"
        assert not (env.root / staged[0]["storage_key"]).exists()

    _run(flow)


def test_stopping_one_model_does_not_interrupt_shared_parse(env):
    async def flow(factory, runner):
        user = await _user(factory)
        creation, _staged = await _create(factory, user, ["vis-a", "vis-b"], [("slow-report.pdf", _report_pdf())])
        ids = {run.model_key: run.task_id for run in creation.runs}
        runner.start(list(ids.values()), creation.batch_id, user_id=user.id, user_limit=10)

        async def parsing() -> bool:
            return any(kind == "progress" and payload["step"] == "parse_file" for kind, payload in await _events(factory, ids["vis-a"]))

        deadline = asyncio.get_running_loop().time() + 10
        while not await parsing():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.02)
        assert await runner.cancel(ids["vis-a"]) is True
        await _wait_idle(runner)

        async with factory() as session:
            batch = await session.get(GenerationBatch, creation.batch_id)
            stopped = await session.get(GenerationTask, ids["vis-a"])
            other = await session.get(GenerationTask, ids["vis-b"])
        assert stopped.status == "cancelled"
        assert other.status == "succeeded"
        assert batch.parse_status == "succeeded"
        assert env.recorder.parses == 1

    _run(flow)
