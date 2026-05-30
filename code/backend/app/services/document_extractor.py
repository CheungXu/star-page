from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from fastapi import HTTPException, status
from markitdown import MarkItDown
from starlette.datastructures import UploadFile

from app.services.llm.client import create_llm_client
from app.services.llm.types import LlmMessage

SUPPORTED_EXTENSIONS = {".docx", ".pptx", ".xlsx", ".xls", ".pdf", ".txt", ".md", ".markdown", ".html", ".htm"}
SUPPORTED_FILE_TYPES_LABEL = "docx、pptx、xlsx、xls、pdf、txt、md 或 html"
MAX_FILE_COUNT = 3
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_FILE_SIZE_BYTES = 50 * 1024 * 1024
MAX_DOCUMENT_CONTEXT_CHARS = 5000
COMPRESSION_CHUNK_CHARS = 24000

DOCUMENT_COMPRESSION_SYSTEM_PROMPT = """你是一个面向网页生成任务的资料压缩专家。
你的目标不是泛泛总结，而是把用户上传资料压缩成足够短、结构清晰、可直接指导 HTML 页面生成的设计/内容简报。"""

DOCUMENT_COMPRESSION_USER_PROMPT = """请将以下资料压缩为“页面生成资料简报”，供后续大模型生成高质量 HTML 页面使用。

压缩目标：
1. 保留与用户页面需求直接相关的信息，包括主题、受众、业务目标、产品/项目亮点、关键数据、表格结构、章节层级、行动号召、品牌语气和任何约束。
2. 删除重复寒暄、页眉页脚、低信息密度长段落和与页面无关的细节。
3. 如果资料包含表格、幻灯片页标题、Markdown/HTML 标题层级，请保留其结构关系。
4. 输出应任务导向，方便直接转化为网页信息架构和视觉模块。
5. 不要编造资料中不存在的事实；不确定的信息请标记为“资料未明确”。

用户页面需求：
{user_prompt}

资料范围：
{scope}

输出格式：
- 页面主题与目标
- 目标受众
- 必须呈现的核心信息
- 建议页面结构
- 可用数据/表格/清单
- 视觉与文案倾向
- 风险、缺失信息或不确定点

待压缩资料：
{document_text}
"""


@dataclass(frozen=True)
class ExtractedDocument:
    filename: str
    extension: str
    char_count: int
    content: str
    truncated: bool = False


@dataclass(frozen=True)
class PreparedGenerationInput:
    user_prompt: str
    input_file_names: list[str]
    extracted_file_text: str
    compression_prompt: str | None
    model_prompt: str
    compressed: bool


async def prepare_generation_input(prompt: str, files: list[UploadFile]) -> PreparedGenerationInput:
    documents = await extract_uploaded_documents(files)
    if not documents:
        return PreparedGenerationInput(
            user_prompt=prompt,
            input_file_names=[],
            extracted_file_text="",
            compression_prompt=None,
            model_prompt=prompt,
            compressed=False,
        )

    extracted_file_text = _build_document_context(documents)
    document_context = extracted_file_text
    compression_prompt: str | None = None
    compressed = False

    if len(extracted_file_text) > MAX_DOCUMENT_CONTEXT_CHARS:
        document_context, compression_prompt = await compress_document_context(prompt, extracted_file_text)
        compressed = True

    return PreparedGenerationInput(
        user_prompt=prompt,
        input_file_names=[document.filename for document in documents],
        extracted_file_text=extracted_file_text,
        compression_prompt=compression_prompt,
        model_prompt=build_prompt_with_document_context(prompt, document_context, compressed=compressed),
        compressed=compressed,
    )


async def extract_uploaded_documents(files: list[UploadFile]) -> list[ExtractedDocument]:
    selected_files = [file for file in files if file.filename]
    if not selected_files:
        return []

    if len(selected_files) > MAX_FILE_COUNT:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"当前一次最多上传 {MAX_FILE_COUNT} 个文件",
        )

    extracted: list[ExtractedDocument] = []
    total_size = 0

    for file in selected_files:
        filename = Path(file.filename or "未命名文件").name
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{filename} 的格式暂不支持，请上传 {SUPPORTED_FILE_TYPES_LABEL} 文件",
            )

        raw_bytes = await file.read()
        size = len(raw_bytes)
        total_size += size
        if size > MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"{filename} 超过 50MB，请压缩或拆分后再上传",
            )
        if total_size > MAX_TOTAL_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="本次上传文件总大小超过 50MB，请减少文件大小",
            )

        content = await _extract_content(filename, extension, raw_bytes)
        content = _normalize_text(content)
        char_count = len(content)

        extracted.append(
            ExtractedDocument(
                filename=filename,
                extension=extension.lstrip("."),
                char_count=char_count,
                content=content,
                truncated=False,
            )
        )

    return extracted


def build_prompt_with_document_context(prompt: str, document_context: str, compressed: bool) -> str:
    if not document_context:
        return prompt

    context_title = "# 上传文件压缩简报" if compressed else "# 上传文件内容"
    return "\n\n".join(
        [
            "用户需求：",
            prompt,
            "",
            "以下是用户上传文件抽取出的资料。请结合这些资料理解页面主题、数据、层级和重点，再生成最终 HTML 页面。",
            "如果文件中包含表格、幻灯片页标题或 Markdown/HTML 结构，请优先保留这些结构信息用于页面内容组织。",
            "",
            context_title,
            document_context,
        ]
    )


def build_prompt_with_documents(prompt: str, documents: list[ExtractedDocument]) -> str:
    return build_prompt_with_document_context(prompt, _build_document_context(documents), compressed=False)


async def compress_document_context(user_prompt: str, extracted_file_text: str) -> tuple[str, str]:
    chunks = _chunk_text(extracted_file_text, COMPRESSION_CHUNK_CHARS)
    prompt_log = _build_compression_prompt_log(user_prompt, len(chunks), len(extracted_file_text))
    summaries: list[str] = []

    for index, chunk in enumerate(chunks, start=1):
        scope = f"第 {index}/{len(chunks)} 段，原始抽取文本共 {len(extracted_file_text)} 字符"
        summaries.append(await _call_llm_for_document_compression(user_prompt, scope, chunk))

    combined_summary = "\n\n".join(
        f"## 压缩片段 {index}\n{summary.strip()}" for index, summary in enumerate(summaries, start=1)
    ).strip()

    if len(combined_summary) > MAX_DOCUMENT_CONTEXT_CHARS:
        combined_summary = await _call_llm_for_document_compression(
            user_prompt,
            "二次压缩：将多个片段简报合并为最终页面生成资料简报",
            combined_summary,
        )

    return combined_summary.strip(), prompt_log


async def _call_llm_for_document_compression(user_prompt: str, scope: str, document_text: str) -> str:
    llm_client = create_llm_client()
    compression_user_prompt = DOCUMENT_COMPRESSION_USER_PROMPT.format(
        user_prompt=user_prompt,
        scope=scope,
        document_text=document_text,
    )
    try:
        return await llm_client.complete_text(
            [
                LlmMessage(role="system", content=DOCUMENT_COMPRESSION_SYSTEM_PROMPT),
                LlmMessage(role="user", content=compression_user_prompt),
            ],
            require_content=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="文件内容过长且压缩失败，请缩短文件内容后重试",
        ) from exc


def _build_compression_prompt_log(user_prompt: str, chunk_count: int, extracted_chars: int) -> str:
    return "\n\n".join(
        [
            "SYSTEM:",
            DOCUMENT_COMPRESSION_SYSTEM_PROMPT,
            "",
            "USER_PROMPT_TEMPLATE:",
            DOCUMENT_COMPRESSION_USER_PROMPT.replace("{document_text}", "{document_text 已单独记录在 extracted_file_text 字段}"),
            "",
            f"用户页面需求：{user_prompt}",
            f"抽取文本字符数：{extracted_chars}",
            f"压缩分段数：{chunk_count}",
            f"单段最大字符数：{COMPRESSION_CHUNK_CHARS}",
        ]
    )


def _build_document_context(documents: list[ExtractedDocument]) -> str:
    document_blocks = []
    for index, document in enumerate(documents, start=1):
        document_blocks.append(
            "\n".join(
                [
                    f"## 文件 {index}: {document.filename}",
                    f"- 文件类型：{document.extension}",
                    f"- 抽取字符数：{document.char_count}",
                    "",
                    document.content or "未抽取到可用文本内容。",
                ]
            ).strip()
        )
    return "\n\n".join(document_blocks)


def _chunk_text(text: str, chunk_size: int) -> list[str]:
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]


async def _extract_content(filename: str, extension: str, raw_bytes: bytes) -> str:
    if extension in {".txt", ".md", ".markdown"}:
        return _decode_text(raw_bytes)

    if extension in {".html", ".htm"}:
        try:
            return await asyncio.to_thread(_convert_file_to_markdown, filename, extension, raw_bytes)
        except HTTPException:
            return _extract_html_text(raw_bytes)

    return await asyncio.to_thread(_convert_file_to_markdown, filename, extension, raw_bytes)


def _decode_text(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def _extract_html_text(raw_bytes: bytes) -> str:
    source = _decode_text(raw_bytes)
    soup = BeautifulSoup(source, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    body_text = soup.get_text("\n", strip=True)
    if title and title not in body_text:
        return f"# {title}\n\n{body_text}"
    return body_text


def _convert_file_to_markdown(filename: str, extension: str, raw_bytes: bytes) -> str:
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temp_file:
            temp_file.write(raw_bytes)
            temp_path = temp_file.name

        result = MarkItDown().convert(temp_path)
        markdown = getattr(result, "markdown", None) or getattr(result, "text_content", None) or ""
        if not markdown.strip():
            raise ValueError("未能从文件中抽取到文本内容")
        return str(markdown)
    except Exception as exc:
        pdf_hint = "；扫描版图片 PDF 暂不支持，请先转换为可复制文本的 PDF 或其他文档格式" if extension == ".pdf" else ""
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{filename} 解析失败，请确认文件未加密且内容可读取{pdf_hint}",
        ) from exc
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def _normalize_text(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized_lines: list[str] = []
    blank_count = 0

    for line in lines:
        if line.strip():
            blank_count = 0
            normalized_lines.append(line)
            continue

        blank_count += 1
        if blank_count <= 2:
            normalized_lines.append("")

    return "\n".join(normalized_lines).strip()
