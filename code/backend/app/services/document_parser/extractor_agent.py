"""同模型、关闭思考、纯文本的资料信息提取。替代原来的「长文压缩」。

每一轮都结合「全部资料原文 + 历次需求 + 本轮需求」重新提取，只留下本轮用得上的内容。
本轮需求和资料无关时模型输出 <EMPTY/>，这一轮就不往写码模型里塞资料。
提取阶段不附图：图片以锚点文字出现在原文里，模型用 IMAGE_REFS 指出本轮写码需要看或要用的图。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from app.services.document_parser.errors import DocumentExtractionError
from app.services.llm.client import create_extraction_llm_client
from app.services.llm.cost import merge_llm_usage
from app.services.llm.types import LlmMessage, LlmUsage

EXTRACTION_CHUNK_CHARS = 60_000
EXTRACTION_CHUNK_CONCURRENCY = 4
EXTRACTION_TOTAL_TIMEOUT_SECONDS = 120
MAX_EXTRACTED_CHARS = 8_000
EMPTY_MARKER = "<EMPTY/>"
_IMAGE_REFS = re.compile(r"^\s*IMAGE_REFS\s*[:：]\s*(.*)$", re.MULTILINE)
_REF_ID = re.compile(r"(?:img:)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})|(?:snap:)?([0-9a-f]{8}-\d+)", re.IGNORECASE)

EXTRACTION_SYSTEM_PROMPT = """你是网页生成前的资料提取助手。用户上传了资料，并在多轮对话里不断提出网页需求。
你的任务：只从资料里提取「本轮需求」用得上的信息，交给后续的网页设计模型。你不写网页，也不评价需求。"""

EXTRACTION_USER_PROMPT = """## 历次需求（按先后顺序，最后一条是本轮之前的上一轮）
{history}

## 本轮需求
{current}

## 资料范围
{scope}

## 提取要求
1. 只提取与本轮需求直接相关的内容：事实、关键原句、精确数字、表格（保留 Markdown 或 HTML 表格结构）、章节层级、行动号召、品牌语气和约束。数字和专有名词照抄原文，不要改写、不要估算。
2. 首轮生成或本轮要求补充、替换、核对内容时，要完整覆盖页面需要的信息；只是局部修改时，只提取与修改点有关的部分。
3. 资料里形如 [配图 img:…]、[核心内容大图 img:…]、[整页快照 snap:…] 的是图片锚点，图片本身你看不到。
   如果本轮写网页需要使用某张配图，或需要看某张快照/核心大图才能理解内容，在全部输出的最后单独一行写：
   IMAGE_REFS: id1, id2
   id 写锚点里 img: 或 snap: 后面的那一串。不需要图片就不写这一行。
4. 本轮需求和资料完全无关（例如只改配色、字号、间距、动效、布局细节），只输出 <EMPTY/>，不要输出别的。
5. 不要编造资料里没有的信息；资料没写清楚的地方标注「资料未明确」。
6. 用简洁的 Markdown 分节输出，不写开场白和总结。

## 资料
{document}
"""

MERGE_SCOPE = "多段资料各自的提取结果，请合并去重成一份，保留全部 IMAGE_REFS"


@dataclass
class ExtractionResult:
    text: str
    image_refs: list[str] = field(default_factory=list)
    usage: LlmUsage | None = None
    prompt_log: str = ""

    @property
    def empty(self) -> bool:
        # 只点名了图片、没有正文（如扫描件续写时没字可摘）也算与资料相关。
        return not self.text.strip() and not self.image_refs


def parse_extraction_output(raw: str) -> tuple[str, list[str]]:
    """拆出正文和 IMAGE_REFS。去掉 <EMPTY/> 后只剩空白才算「与资料无关」。"""
    refs: list[str] = []
    for match in _IMAGE_REFS.finditer(raw or ""):
        for ref in _REF_ID.finditer(match.group(1)):
            value = (ref.group(1) or ref.group(2) or "").lower()
            if value and value not in refs:
                refs.append(value)
    body = _IMAGE_REFS.sub("", raw or "").replace(EMPTY_MARKER, "").strip()
    return body, refs


async def extract_for_round(
    *,
    model_key: str,
    document_text: str,
    history: list[str],
    current: str,
) -> ExtractionResult:
    try:
        return await asyncio.wait_for(
            _extract(model_key, document_text, history, current),
            timeout=EXTRACTION_TOTAL_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError as exc:
        raise DocumentExtractionError("资料提取超时，请精简资料后重试", detail=f"超过 {EXTRACTION_TOTAL_TIMEOUT_SECONDS}s") from exc
    except DocumentExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DocumentExtractionError("资料提取失败，请稍后重试", detail=f"{type(exc).__name__}: {exc}") from exc


async def _extract(model_key: str, document_text: str, history: list[str], current: str) -> ExtractionResult:
    client = create_extraction_llm_client(model_key)
    history_text = "\n".join(f"{index}. {item}" for index, item in enumerate(history, start=1)) or "（无，这是首轮）"
    chunks = [document_text[index : index + EXTRACTION_CHUNK_CHARS] for index in range(0, len(document_text), EXTRACTION_CHUNK_CHARS)] or [""]
    semaphore = asyncio.Semaphore(EXTRACTION_CHUNK_CONCURRENCY)

    async def run(scope: str, document: str) -> tuple[str, LlmUsage | None]:
        async with semaphore:
            prompt = EXTRACTION_USER_PROMPT.format(history=history_text, current=current, scope=scope, document=document)
            result = await client.complete_text(
                [LlmMessage(role="system", content=EXTRACTION_SYSTEM_PROMPT), LlmMessage(role="user", content=prompt)],
                require_content=True,
            )
            return result.text, result.usage

    total = len(document_text)
    if len(chunks) == 1:
        outputs = [await run(f"全部资料，共 {total} 字", chunks[0])]
    else:
        outputs = list(
            await asyncio.gather(
                *(run(f"第 {index}/{len(chunks)} 段，资料共 {total} 字", chunk) for index, chunk in enumerate(chunks, start=1))
            )
        )
    usage: LlmUsage | None = None
    for _text, part_usage in outputs:
        usage = merge_llm_usage(usage, part_usage)

    if len(outputs) == 1:
        body, refs = parse_extraction_output(outputs[0][0])
    else:
        parts = [parse_extraction_output(text) for text, _u in outputs]
        refs = [ref for _body, part_refs in parts for ref in part_refs]
        bodies = [body for body, _r in parts if body]
        body = "\n\n".join(bodies)
        if len(body) > MAX_EXTRACTED_CHARS:
            merged_raw, merge_usage = await run(MERGE_SCOPE, body + ("\n\nIMAGE_REFS: " + ", ".join(refs) if refs else ""))
            usage = merge_llm_usage(usage, merge_usage)
            body, merged_refs = parse_extraction_output(merged_raw)
            refs = list(dict.fromkeys([*refs, *merged_refs]))
    if len(body) > MAX_EXTRACTED_CHARS:
        body = body[:MAX_EXTRACTED_CHARS] + "\n（提取内容过长，已截断）"
    return ExtractionResult(
        text=body,
        image_refs=list(dict.fromkeys(refs)),
        usage=usage,
        prompt_log=_prompt_log(model_key, history_text, current, total, len(chunks)),
    )


def _prompt_log(model_key: str, history: str, current: str, total: int, chunk_count: int) -> str:
    template = EXTRACTION_USER_PROMPT.replace("{document}", "{资料原文已单独记录在 extracted_file_text 字段}")
    return "\n\n".join(
        [
            f"提取模型：{model_key}（关闭思考，纯文本）",
            "SYSTEM:",
            EXTRACTION_SYSTEM_PROMPT,
            "USER_PROMPT_TEMPLATE:",
            template.replace("{history}", history).replace("{current}", current).replace("{scope}", f"共 {total} 字，分 {chunk_count} 段"),
        ]
    )
