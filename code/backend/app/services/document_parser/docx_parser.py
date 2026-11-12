"""DOCX：按正文里的原始顺序遍历段落和表格，带出浮动图、文本框；合并单元格的表格输出 HTML。"""

from __future__ import annotations

import io
import re
import statistics
from pathlib import Path

import docx
from lxml import etree
from docx.document import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.document_parser.images import normalize_image
from app.services.document_parser.limits import DOC_MAX_IMAGES
from app.services.document_parser.text_utils import html_table, markdown_table, normalize_text
from app.services.document_parser.types import IMAGE_PLACEHOLDER, ParsedDocument, ParsedImage

LOW_TEXT_DOC_CHARS = 100
CORE_IMAGE_RATIO = 0.25
_HEADING_STYLE = re.compile(r"(?:heading|标题)\s*(\d)", re.IGNORECASE)
_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "v": "urn:schemas-microsoft-com:vml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}


def _xp(element, expression: str) -> list:
    """python-docx 自带的 xpath 不认识 mc / v 前缀，改用 lxml 原生 xpath 并显式给命名空间。"""
    return etree._Element.xpath(element, expression, namespaces=_NS)


def parse_docx(filename: str, source: bytes | Path, content_sha256: str) -> ParsedDocument:
    docx_input = str(source) if isinstance(source, Path) else io.BytesIO(source)
    document: DocxDocument = docx.Document(docx_input)
    page_area = _page_area(document)
    body_size = _body_font_size(document)

    blocks: list[str] = []
    images: list[ParsedImage] = []
    seen: set[str] = set()
    heading = ""
    textbox_seen: set[str] = set()
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = _paragraph_text(item, body_size)
            if text:
                if text.startswith("#"):
                    heading = text.lstrip("# ").strip()[:30]
                blocks.append(text)
            for box in _textboxes(item):
                if box not in textbox_seen:
                    textbox_seen.add(box)
                    blocks.append(f"（文本框）{box}")
            # 有效配图收满上限后立即停止抽取后续图片，避免在内存里囤积整份文档的全部图片字节。
            if len(images) < DOC_MAX_IMAGES:
                for blob, ratio in _paragraph_images(document, item, page_area):
                    if len(images) >= DOC_MAX_IMAGES:
                        break
                    normalized = normalize_image(blob)
                    if normalized is None or normalized.sha256 in seen:
                        continue
                    seen.add(normalized.sha256)
                    images.append(
                        ParsedImage(
                            data=normalized.data,
                            content_type=normalized.content_type,
                            extension=normalized.extension,
                            width=normalized.width,
                            height=normalized.height,
                            sha256=normalized.sha256,
                            label=filename + (f"「{heading}」附近" if heading else ""),
                            area_ratio=ratio,
                            core=False,
                        )
                    )
                    blocks.append(IMAGE_PLACEHOLDER.format(index=len(images) - 1))
        elif isinstance(item, Table):
            table = _table_text(item)
            if table:
                blocks.append(table)

    char_count = len(re.sub(r"\s+", "", "".join(blocks)))
    low_text = char_count < LOW_TEXT_DOC_CHARS
    if low_text:
        for image in images:
            image.core = image.area_ratio >= CORE_IMAGE_RATIO

    return ParsedDocument(
        filename=filename,
        extension="docx",
        content_sha256=content_sha256,
        text=normalize_text("\n\n".join(block for block in blocks if block)),
        images=images,
    )


def _page_area(document: DocxDocument) -> int:
    try:
        section = document.sections[0]
        return max(int(section.page_width or 0) * int(section.page_height or 0), 1)
    except (IndexError, TypeError):
        return 7560310 * 10692130  # A4，单位 EMU


def _body_font_size(document: DocxDocument) -> float:
    sizes = [run.font.size.pt for paragraph in document.paragraphs for run in paragraph.runs if run.font.size and run.text.strip()]
    if sizes:
        return statistics.median(sizes)
    try:
        normal = document.styles["Normal"].font.size
        return normal.pt if normal else 10.5
    except KeyError:
        return 10.5


def _paragraph_text(paragraph: Paragraph, body_size: float) -> str:
    text = paragraph.text.strip()
    if not text:
        return ""
    style = paragraph.style.name if paragraph.style is not None else ""
    if style.lower() == "title":
        return f"# {text}"
    match = _HEADING_STYLE.search(style)
    if match:
        return "#" * min(int(match.group(1)) + 1, 6) + " " + text
    outline = paragraph._p.xpath("./w:pPr/w:outlineLvl/@w:val")
    if outline and outline[0].isdigit() and int(outline[0]) < 6:
        return "#" * (int(outline[0]) + 2) + " " + text
    runs = [run for run in paragraph.runs if run.text.strip()]
    if runs and len(text) <= 40:
        bold = all(run.bold for run in runs)
        sizes = [run.font.size.pt for run in runs if run.font.size]
        larger = bool(sizes) and min(sizes) >= body_size * 1.25
        if larger or (bold and not text.endswith(("。", ".", "；", ";", "，", ","))):
            return f"### {text}"
    numbered = paragraph._p.xpath("./w:pPr/w:numPr")
    if numbered or "list" in style.lower() or "列表" in style:
        level = paragraph._p.xpath("./w:pPr/w:numPr/w:ilvl/@w:val")
        depth = int(level[0]) if level and level[0].isdigit() else 0
        return "  " * depth + "- " + text
    return text


def _textboxes(paragraph: Paragraph) -> list[str]:
    """文本框正文。Word 会同时写一份 VML 兜底（mc:Fallback），只取主版本避免重复。"""
    boxes = []
    for content in _xp(paragraph._p, ".//w:txbxContent[not(ancestor::mc:Fallback)]"):
        lines = ["".join(_xp(node, ".//w:t/text()")).strip() for node in _xp(content, ".//w:p")]
        text = "\n".join(line for line in lines if line)
        if text:
            boxes.append(text)
    return boxes


def _paragraph_images(document: DocxDocument, paragraph: Paragraph, page_area: int) -> list[tuple[bytes, float]]:
    """段落里的嵌入图和浮动图（wp:inline / wp:anchor 都在段落的 run 里）。"""
    found: list[tuple[bytes, float]] = []
    related = document.part.related_parts
    for drawing in _xp(paragraph._p, ".//w:drawing[not(ancestor::mc:Fallback)]"):
        extent = _xp(drawing, ".//wp:extent")
        ratio = 0.0
        if extent:
            try:
                ratio = int(extent[0].get("cx")) * int(extent[0].get("cy")) / page_area
            except (TypeError, ValueError):
                ratio = 0.0
        for rel_id in _xp(drawing, ".//a:blip/@r:embed"):
            part = related.get(rel_id)
            if part is not None and hasattr(part, "blob"):
                found.append((part.blob, min(ratio, 1.0)))
    # 老版本 Word 的 VML 图片；新版的 VML 只出现在 mc:Fallback 里，前面已经按 drawing 取过。
    for rel_id in _xp(paragraph._p, ".//v:imagedata[not(ancestor::mc:Fallback)]/@r:id"):
        part = related.get(rel_id)
        if part is not None and hasattr(part, "blob"):
            found.append((part.blob, 0.0))
    return found


def _table_text(table: Table) -> str:
    """有合并单元格时按网格还原 rowspan / colspan 输出 HTML，否则输出 Markdown。"""
    tbl = table._tbl
    merged = bool(_xp(tbl, ".//w:gridSpan | .//w:vMerge"))
    grid_rows: list[list[dict]] = []
    owners: dict[tuple[int, int], dict] = {}
    for row_index, tr in enumerate(tbl.tr_lst):
        row: list[dict] = []
        column = 0
        for tc in tr.tc_lst:
            span = int(tc.grid_span or 1)
            text = "\n".join("".join(_xp(paragraph, ".//w:t/text()")).strip() for paragraph in _xp(tc, "./w:p")).strip()
            if tc.vMerge == "continue":
                above = owners.get((row_index - 1, column))
                if above is not None:
                    above["rowspan"] += 1
                    for offset in range(span):
                        owners[(row_index, column + offset)] = above
                column += span
                continue
            cell = {"text": text, "rowspan": 1, "colspan": span}
            row.append(cell)
            for offset in range(span):
                owners[(row_index, column + offset)] = cell
            column += span
        grid_rows.append(row)
    if not any(cell["text"] for row in grid_rows for cell in row):
        return ""
    if merged:
        return html_table(grid_rows)
    return markdown_table([[cell["text"] for cell in row] for row in grid_rows])
