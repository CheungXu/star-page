"""同模型资料提取、跨轮资料拼装、图片双通道与批次共享解析的单元测试（不需要数据库）。"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import get_model_registry
from app.services.document_parser import extractor_agent, pipeline
from app.services.document_parser.attachments import build_document_context
from app.services.document_parser.context import LineageMaterial, _fit
from app.services.document_parser.errors import DocumentExtractionError
from app.services.document_parser.extractor_agent import ExtractionResult, extract_for_round, parse_extraction_output
from app.services.llm.client import EXTRACTION_MAX_TOKENS, create_extraction_llm_client
from app.services.llm.openai_compatible import OpenAICompatibleClient, _serialize_message
from app.services.llm.types import LlmCompletionResult, LlmImagePart, LlmMessage, LlmStreamChunk, LlmUsage

ASSET_ID = "3f2b8c1e-1111-4222-8333-944455556666"


# ---------- 提取客户端 ----------


def _catalog_key(predicate) -> str:
    return next(model.key for model in get_model_registry().models.values() if predicate(model))


def test_extraction_client_uses_same_model_with_thinking_off() -> None:
    key = _catalog_key(lambda model: model.extra_body.get("enable_thinking") is True)
    client = create_extraction_llm_client(key)
    assert client.model.key == key
    assert client.model.extra_body.get("enable_thinking") is False
    assert client.model.params["max_tokens"] == EXTRACTION_MAX_TOKENS


def test_extraction_client_replaces_reasoning_effort_and_keeps_param_bans() -> None:
    key = _catalog_key(lambda model: model.extra_body.get("reasoning_effort") == "medium")
    client = create_extraction_llm_client(key)
    # 写码用的 medium 思考被整体换成目录里实测的关闭写法。
    assert client.model.extra_body.get("reasoning_effort") != "medium"
    assert client.model.extra_body == get_model_registry().get(key).extraction_extra_body
    # 目录里显式不发 temperature 的模型，提取时也不补回。
    assert "temperature" not in client.model.params


# ---------- 输出协议 ----------


def test_empty_marker_means_unrelated() -> None:
    assert parse_extraction_output("<EMPTY/>") == ("", [])
    assert parse_extraction_output("  <EMPTY/>\n") == ("", [])


def test_empty_marker_with_content_keeps_content() -> None:
    body, refs = parse_extraction_output("价格：99 元\n<EMPTY/>")
    assert body == "价格：99 元" and refs == []


def test_image_refs_are_parsed_and_removed() -> None:
    raw = f"## 核心信息\n收入 1.2 亿\nIMAGE_REFS: img:{ASSET_ID}, snap:a1b2c3d4-2，{ASSET_ID}"
    body, refs = parse_extraction_output(raw)
    assert body == "## 核心信息\n收入 1.2 亿"
    assert refs == [ASSET_ID, "a1b2c3d4-2"]


class _FakeExtractionClient:
    def __init__(self, replies, delay: float = 0.0) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.delay = delay
        self.active = 0
        self.peak = 0

    async def complete_text(self, messages, *, require_content=True):
        assert all(not message.images for message in messages), "提取阶段不附图"
        self.prompts.append(messages[-1].content)
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(self.delay)
        self.active -= 1
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return LlmCompletionResult(text=reply, usage=LlmUsage(input_tokens=100, output_tokens=10, total_tokens=110))


def test_extraction_prompt_carries_history_current_and_document(monkeypatch) -> None:
    fake = _FakeExtractionClient(["产品名：星页"])
    monkeypatch.setattr(extractor_agent, "create_extraction_llm_client", lambda key: fake)
    result = asyncio.run(
        extract_for_round(model_key="qwen", document_text="资料正文：星页", history=["做落地页"], current="把标题改成产品名")
    )
    assert result.text == "产品名：星页"
    assert result.usage.input_tokens == 100
    prompt = fake.prompts[0]
    assert "1. 做落地页" in prompt and "把标题改成产品名" in prompt and "资料正文：星页" in prompt
    assert "提取模型：qwen" in result.prompt_log


def test_long_document_is_chunked_concurrently_and_merged(monkeypatch) -> None:
    long_part = "要点" * (extractor_agent.MAX_EXTRACTED_CHARS // 2 + 10)
    fake = _FakeExtractionClient([long_part + f"\nIMAGE_REFS: {ASSET_ID}", long_part, "合并后的简报"], delay=0.05)
    monkeypatch.setattr(extractor_agent, "create_extraction_llm_client", lambda key: fake)
    text = "甲" * (extractor_agent.EXTRACTION_CHUNK_CHARS + 10)
    result = asyncio.run(extract_for_round(model_key="qwen", document_text=text, history=[], current="做网页"))
    assert fake.peak >= 2
    assert result.text == "合并后的简报"
    assert result.image_refs == [ASSET_ID]
    assert result.usage.input_tokens == 300


def test_extraction_has_total_timeout(monkeypatch) -> None:
    monkeypatch.setattr(extractor_agent, "create_extraction_llm_client", lambda key: _FakeExtractionClient(["x"], delay=1))
    monkeypatch.setattr(extractor_agent, "EXTRACTION_TOTAL_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(DocumentExtractionError) as caught:
        asyncio.run(extract_for_round(model_key="qwen", document_text="x", history=[], current="y"))
    assert caught.value.user_message == "资料提取超时，请精简资料后重试"


# ---------- 适配层：多模态与降级 ----------


def test_images_serialized_only_for_vision_models() -> None:
    message = LlmMessage(role="user", content="看图", images=(LlmImagePart(data_url="data:image/png;base64,AAA"),))
    assert _serialize_message(message, supports_vision=False) == {"role": "user", "content": "看图"}
    parts = _serialize_message(message, supports_vision=True)["content"]
    assert parts[0] == {"type": "text", "text": "看图"}
    assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}


def test_bad_request_with_images_retries_once_without_images(monkeypatch) -> None:
    model = SimpleNamespace(supports_vision=True, base_url="", provider="p", model="m", params={}, extra_body={}, api_key="k")
    settings = SimpleNamespace(llm_retry_attempts=1, llm_retry_initial_delay_ms=1, llm_retry_max_delay_ms=1, llm_timeout_ms=1000)
    client = OpenAICompatibleClient(model, settings)
    calls: list[bool] = []

    async def fake_once(messages):
        calls.append(any(message.images for message in messages))
        if calls[-1]:
            request = httpx.Request("POST", "http://x")
            raise httpx.HTTPStatusError("bad", request=request, response=httpx.Response(400, request=request))
        yield LlmStreamChunk(type="text_delta", provider="p", model="m", text="<html></html>")

    monkeypatch.setattr(client, "_stream_text_once", fake_once)
    message = LlmMessage(role="user", content="x", images=(LlmImagePart(data_url="data:,"),))

    async def collect():
        return [chunk.text async for chunk in client.stream_text([message])]

    assert asyncio.run(collect()) == ["<html></html>"]
    assert calls == [True, False]


# ---------- 跨轮资料与图片决策 ----------


def _asset(batch_id, *, core=False, area=(800, 600)):
    return SimpleNamespace(
        id=uuid.uuid4(),
        batch_id=batch_id,
        storage_key="k",
        filename="deck.pptx",
        source_label="deck.pptx 第 2 页" + ("｜核心内容大图" if core else ""),
        width=area[0],
        height=area[1],
    )


def _material(*, current_has_docs=True, text="## 文件 1: a.pdf\n正文" * 50) -> tuple[LineageMaterial, list]:
    old_batch, current_batch = uuid.uuid4(), uuid.uuid4()
    assets = [_asset(old_batch), _asset(current_batch, core=True)]
    material = LineageMaterial(
        text=text,
        current_batch_has_documents=current_has_docs,
        doc_assets=assets,
        snapshots=[{"snap_id": "abcd1234-1", "storage_key": "s1", "filename": "a.pdf", "page_no": 3, "batch_id": str(old_batch)}],
        current_batch_id=current_batch,
    )
    return material, assets


def test_empty_extraction_adds_nothing() -> None:
    material, _assets = _material()
    context = build_document_context(material, ExtractionResult(text=""), visual_direct=False)
    assert context.mode == "empty" and context.prompt_block() == "" and context.doc_visuals == []


def test_refs_without_text_still_count_as_related() -> None:
    """扫描件续写时提取模型没字可摘，只点名了快照：不能当成「与资料无关」。"""
    body, refs = parse_extraction_output("IMAGE_REFS: snap:abcd1234-1")
    extraction = ExtractionResult(text=body, image_refs=refs)
    assert not extraction.empty
    material, _assets = _material(current_has_docs=False)
    context = build_document_context(material, extraction, visual_direct=False)
    assert context.mode == "extracted"
    assert [visual.kind for visual in context.doc_visuals] == ["snapshot"]


def test_follow_up_round_lists_only_referenced_old_images() -> None:
    material, assets = _material(current_has_docs=False)
    extraction = ExtractionResult(text="架构说明", image_refs=[str(assets[0].id), "abcd1234-1"])
    context = build_document_context(material, extraction, visual_direct=False)
    assert context.listed_assets == [assets[0]]
    assert [visual.kind for visual in context.doc_visuals] == ["snapshot", "doc_image"]
    block = context.prompt_block()
    assert f"/assets/{assets[0].id}" in block
    assert "abcd1234-1" not in block.split("资料中的配图")[1]


def test_round_with_new_document_lists_all_its_images_but_attaches_only_refs() -> None:
    material, assets = _material()
    context = build_document_context(material, ExtractionResult(text="内容"), visual_direct=False)
    assert context.listed_assets == [assets[1]]
    assert context.doc_visuals == []


def test_visual_only_document_goes_straight_to_codegen() -> None:
    material, assets = _material(text="## 文件 1: deck.pptx\n- 文件类型：pptx\n\n## 第 1 页\n\n[核心内容大图 img:x｜p｜1×1]")
    material.snapshots[0]["batch_id"] = str(material.current_batch_id)
    assert material.visual_only
    context = build_document_context(material, None, visual_direct=True)
    assert context.mode == "visual"
    assert {visual.kind for visual in context.doc_visuals} == {"snapshot", "doc_image"}
    assert "以图片为主" in context.prompt_block()


def test_lineage_cap_keeps_current_round_first() -> None:
    current, old = uuid.uuid4(), uuid.uuid4()
    pieces = [(old, "旧" * 80_000), (current, "新" * 80_000)]
    text, truncated = _fit(pieces, current)
    assert truncated
    assert text.count("新") == 80_000
    assert text.index("旧") < text.index("新")
    assert len(text) <= 120_000 + 100


# ---------- 批次共享解析 ----------


def test_parse_job_shared_and_survives_one_waiter_cancel(monkeypatch) -> None:
    runs = {"count": 0, "finished": False}

    async def fake_run(batch_id):
        runs["count"] += 1
        await asyncio.sleep(0.2)
        runs["finished"] = True

    monkeypatch.setattr(pipeline, "_run_parse", fake_run)

    async def flow():
        batch_id = uuid.uuid4()
        first = asyncio.create_task(pipeline.wait_for_batch_parse(batch_id))
        second = asyncio.create_task(pipeline.wait_for_batch_parse(batch_id))
        await asyncio.sleep(0.05)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await second
        assert batch_id not in pipeline._jobs

    asyncio.run(flow())
    assert runs == {"count": 1, "finished": True}


def test_parse_job_cancelled_when_every_waiter_leaves(monkeypatch) -> None:
    state = {"cancelled": False}

    async def fake_run(batch_id):
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    monkeypatch.setattr(pipeline, "_run_parse", fake_run)

    async def no_live_tasks(batch_id, *, exclude):
        return False

    monkeypatch.setattr(pipeline, "_batch_has_live_tasks", no_live_tasks)

    async def flow():
        waiter = asyncio.create_task(pipeline.wait_for_batch_parse(uuid.uuid4()))
        await asyncio.sleep(0.05)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        await asyncio.sleep(0.05)

    asyncio.run(flow())
    assert state["cancelled"] is True


def test_parse_job_kept_when_batch_still_has_queued_tasks(monkeypatch) -> None:
    state = {"finished": False, "starts": 0}

    async def fake_run(batch_id):
        state["starts"] += 1
        await asyncio.sleep(0.2)
        state["finished"] = True

    async def still_live(batch_id, *, exclude):
        return True

    monkeypatch.setattr(pipeline, "_run_parse", fake_run)
    monkeypatch.setattr(pipeline, "_batch_has_live_tasks", still_live)

    async def flow():
        batch_id = uuid.uuid4()
        first = asyncio.create_task(pipeline.wait_for_batch_parse(batch_id, uuid.uuid4()))
        await asyncio.sleep(0.05)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        # 排队的兄弟任务后到，复用同一个解析任务。
        await pipeline.wait_for_batch_parse(batch_id, uuid.uuid4())
        assert batch_id not in pipeline._jobs

    asyncio.run(flow())
    assert state == {"finished": True, "starts": 1}


def test_catalog_capability_fields_are_loaded() -> None:
    registry = get_model_registry()
    flags = {key: model.supports_vision for key, model in registry.models.items()}
    assert any(flags.values()) and not all(flags.values())
    assert all(model.extraction_extra_body for model in registry.models.values())
    json.dumps({key: model.extraction_extra_body for key, model in registry.models.items()})
