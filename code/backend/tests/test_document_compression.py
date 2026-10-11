"""资料压缩：创建请求不阻塞，压缩模型关闭思考，并有总超时。"""

import asyncio
import uuid

from app.services.document_extractor import (
    COMPRESSION_CHUNK_CHARS,
    MAX_DOCUMENT_CONTEXT_CHARS,
    DocumentCompressionError,
    ExtractedDocument,
    assemble_prepared_input,
    build_prompt_with_document_context,
    compress_document_context,
)
from app.services.generation_service import _compression_gate, _compression_gates, _release_compression_gate
from app.services.llm.client import COMPRESSION_MAX_TOKENS, create_compression_llm_client
from app.services.scenes.catalog import format_image_block


def test_short_document_is_inlined_without_compression() -> None:
    prepared = assemble_prepared_input("请生成网页", [_document("短正文")])
    assert prepared.needs_compression is False
    assert prepared.compression_prompt is None
    assert "上传文件内容" in prepared.model_prompt
    assert "短正文" in prepared.model_prompt


def test_long_document_defers_compression() -> None:
    body = "甲" * (MAX_DOCUMENT_CONTEXT_CHARS + 50)
    prepared = assemble_prepared_input("请根据文件生成网页", [_document(body)])
    assert prepared.needs_compression is True
    assert prepared.compression_prompt is None
    assert prepared.model_prompt == "请根据文件生成网页"
    assert body in prepared.extracted_file_text
    assert "上传文件压缩简报" not in prepared.model_prompt


def test_compression_client_disables_thinking() -> None:
    client = create_compression_llm_client()
    assert client.model.key == "qwen-plus"
    assert client.model.extra_body.get("enable_thinking") is False
    assert "reasoning_effort" not in client.model.extra_body
    assert client.model.params.get("max_tokens") == COMPRESSION_MAX_TOKENS


def test_compression_has_total_timeout(monkeypatch) -> None:
    async def slow(*_args, **_kwargs) -> tuple[str, str]:
        await asyncio.sleep(1)
        return "简报", "log"

    monkeypatch.setattr("app.services.document_extractor._compress_document_context", slow)
    monkeypatch.setattr("app.services.document_extractor.COMPRESSION_TOTAL_TIMEOUT_SECONDS", 0.01)

    async def run() -> None:
        await compress_document_context("请生成网页", "甲" * 6000)

    try:
        asyncio.run(run())
    except DocumentCompressionError as exc:
        assert exc.user_message == "文件内容压缩超时，请缩短文件后重试"
        assert "超过" in exc.detail
        assert exc.detail in str(exc)
    else:
        raise AssertionError("压缩总超时未生效")


def test_chunks_compress_concurrently_and_keep_order(monkeypatch) -> None:
    state = {"current": 0, "peak": 0}

    async def fake(_user_prompt: str, _scope: str, document_text: str) -> str:
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        await asyncio.sleep(0.05)
        state["current"] -= 1
        return document_text[:1]

    monkeypatch.setattr("app.services.document_extractor._call_llm_for_document_compression", fake)
    text = "甲" * COMPRESSION_CHUNK_CHARS + "乙" * COMPRESSION_CHUNK_CHARS + "丙" * COMPRESSION_CHUNK_CHARS
    summary, _log = asyncio.run(compress_document_context("请生成网页", text))
    assert state["peak"] >= 2
    assert summary.index("甲") < summary.index("乙") < summary.index("丙")


def test_compressed_prompt_keeps_uploaded_images() -> None:
    image_block = format_image_block([("头像", "https://stars-page.com/assets/abc", "a.png")])
    existing_prompt = f"请做一份简历\n\n{image_block}"
    prompt = build_prompt_with_document_context(existing_prompt, "工作经历：林晓，产品经理", compressed=True)
    assert "https://stars-page.com/assets/abc" in prompt
    assert "必须用 <img> 引用" in prompt
    assert "工作经历：林晓" in prompt
    assert "# 上传文件压缩简报" in prompt


def test_compression_gate_is_removed_after_last_user() -> None:
    async def run() -> None:
        batch_id = uuid.uuid4()
        await _compression_gate(batch_id)
        await _compression_gate(batch_id)
        await _release_compression_gate(batch_id)
        assert batch_id in _compression_gates
        await _release_compression_gate(batch_id)
        assert batch_id not in _compression_gates

    asyncio.run(run())


def _document(content: str) -> ExtractedDocument:
    return ExtractedDocument(filename="资料.txt", extension="txt", char_count=len(content), content=content)
