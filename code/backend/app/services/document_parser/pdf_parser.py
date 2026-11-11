"""PDF：pdfplumber 按坐标还原阅读顺序和表格，pypdfium2 取配图、渲染整页快照。

两者都是宽松许可（MIT / BSD-Apache）。不用 PyMuPDF，它是 AGPL。
"""

from __future__ import annotations

import io
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

import pdfplumber
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from app.services.document_parser.images import normalize_pil
from app.services.document_parser.limits import BATCH_MAX_SNAPSHOTS, DOC_MAX_IMAGES, MIN_IMAGE_SIDE, PDF_MAX_PAGES, SNAPSHOT_MAX_EDGE
from app.services.document_parser.text_utils import join_fragments, markdown_table, normalize_text
from app.services.document_parser.types import IMAGE_PLACEHOLDER, SNAPSHOT_PLACEHOLDER, ParsedDocument, ParsedImage, ParsedSnapshot

# 少字页：有效字数低于它，且有大图或大量矢量图形。
LOW_TEXT_PAGE_CHARS = 60
LARGE_IMAGE_RATIO = 0.2
DENSE_VECTOR_COUNT = 15
# 整份文件字数低于它，按扫描件 / 设计稿处理，每页都考虑快照。
SCANNED_DOC_CHARS = 100
# 少字页上占版面七成以上的图就是扫描底图，交给整页快照，不当配图公开。
SCAN_IMAGE_RATIO = 0.7
HEADER_FOOTER_BAND = 0.08


@dataclass
class _Line:
    top: float
    bottom: float
    x0: float
    x1: float
    size: float
    text: str


@dataclass
class _Block:
    """一页里的一段输出单位：一行文字或一张表。column 为 L / R / S（跨栏）。"""

    top: float
    bottom: float
    column: str
    text: str
    size: float = 0.0
    is_table: bool = False


@dataclass
class _PageInfo:
    number: int
    width: float
    height: float
    blocks: list[_Block] = field(default_factory=list)
    chars: int = 0
    vectors: int = 0
    images: list[tuple[float, float, int]] = field(default_factory=list)  # (top, 占页面比例, 图片对象序号)


def parse_pdf(filename: str, data: bytes, content_sha256: str) -> ParsedDocument:
    pages = _read_layout(data)
    _drop_headers_and_footers(pages)
    body_size = _body_font_size(pages)
    total_chars = sum(page.chars for page in pages)

    images: list[ParsedImage] = []
    snapshots: list[ParsedSnapshot] = []
    page_texts: list[str] = []
    pdf = pdfium.PdfDocument(data)
    try:
        for info in pages:
            page = pdf[info.number - 1]
            try:
                snapshot = _maybe_snapshot(page, info, total_chars, len(snapshots))
                parts = [_page_text(info, body_size)]
                if snapshot is not None:
                    snapshots.append(snapshot)
                    parts.append(SNAPSHOT_PLACEHOLDER.format(index=len(snapshots) - 1))
                parts.extend(_page_images(page, info, filename, images, scanned=snapshot is not None))
                body = "\n\n".join(part for part in parts if part)
                if body:
                    page_texts.append(f"【第 {info.number} 页】\n{body}" if len(pages) > 1 else body)
            finally:
                page.close()
    finally:
        pdf.close()

    return ParsedDocument(
        filename=filename,
        extension="pdf",
        content_sha256=content_sha256,
        text=normalize_text("\n\n".join(page_texts)),
        images=images,
        snapshots=snapshots,
    )


def _read_layout(data: bytes) -> list[_PageInfo]:
    result: list[_PageInfo] = []
    pdf_images = _collect_pdfium_images(data)
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for index, page in enumerate(pdf.pages[:PDF_MAX_PAGES]):
            info = _PageInfo(number=index + 1, width=float(page.width), height=float(page.height))
            info.chars = len(re.sub(r"\s+", "", "".join(char.get("text", "") for char in page.chars)))
            info.vectors = len(page.curves) + len(page.rects) + len(page.lines)
            info.blocks = _page_blocks(page)
            info.images = pdf_images.get(index, [])
            result.append(info)
    return result


def _collect_pdfium_images(data: bytes) -> dict[int, list[tuple[float, float, int]]]:
    """先只记每页图片的位置和占比（第几个图片对象），真正取位图放到后面按需做。"""
    found: dict[int, list[tuple[float, float, int]]] = {}
    pdf = pdfium.PdfDocument(data)
    try:
        for index in range(min(len(pdf), PDF_MAX_PAGES)):
            page = pdf[index]
            try:
                width, height = page.get_size()
                area = max(width * height, 1.0)
                entries = []
                objects = page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=5)
                for order, obj in enumerate(objects):
                    try:
                        left, bottom, right, top = obj.get_bounds()
                    except Exception:  # noqa: BLE001 - 个别对象取不到位置，跳过
                        continue
                    ratio = max(0.0, (right - left) * (top - bottom)) / area
                    entries.append((height - top, min(ratio, 1.0), order))
                found[index] = entries
            finally:
                page.close()
    finally:
        pdf.close()
    return found


def _page_blocks(page) -> list[_Block]:
    tables = [table for table in page.find_tables() if _is_real_table(table)]
    table_boxes = [table.bbox for table in tables]
    words = [
        word
        for word in page.extract_words(keep_blank_chars=False, use_text_flow=False, extra_attrs=["size"])
        if not _inside_any(word, table_boxes)
    ]
    lines = _group_lines(words)
    gap = _column_gap(words, float(page.width))
    blocks: list[_Block] = []
    for line_words in lines:
        blocks.extend(_split_line(line_words, gap))
    for table in tables:
        rows = table.extract()
        text = markdown_table(rows)
        if not text:
            continue
        x0, top, x1, bottom = table.bbox
        column = "S"
        if gap is not None:
            column = "L" if x1 <= gap else "R" if x0 >= gap else "S"
        blocks.append(_Block(top=top, bottom=bottom, column=column, text=text, is_table=True))
    return blocks


def _is_real_table(table) -> bool:
    rows = table.extract()
    return len(rows) >= 2 and max((len(row) for row in rows), default=0) >= 2


def _inside_any(word: dict, boxes: list[tuple[float, float, float, float]]) -> bool:
    cx = (word["x0"] + word["x1"]) / 2
    cy = (word["top"] + word["bottom"]) / 2
    return any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)


def _group_lines(words: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda item: (round(item["top"], 1), item["x0"])):
        tolerance = max(2.0, 0.5 * float(word.get("size") or 10))
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= tolerance:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda item: item["x0"]) for line in lines]


def _column_gap(words: list[dict], width: float) -> float | None:
    """在页面中段找一条几乎没有文字经过的竖缝，找到就按双栏处理。"""
    if len(words) < 40 or width <= 0:
        return None
    bins = 100
    counts = [0] * bins
    for word in words:
        start = max(0, int(word["x0"] / width * bins))
        end = min(bins - 1, int(word["x1"] / width * bins))
        for index in range(start, end + 1):
            counts[index] += 1
    left_mass = sum(counts[:25])
    right_mass = sum(counts[75:])
    if left_mass == 0 or right_mass == 0:
        return None
    threshold = max(1, int(len(words) * 0.03))
    best: tuple[int, int] | None = None
    run_start: int | None = None
    for index in range(25, 76):
        quiet = counts[index] <= threshold
        if quiet and run_start is None:
            run_start = index
        if (not quiet or index == 75) and run_start is not None:
            run_end = index if quiet else index - 1
            if run_end - run_start + 1 >= 2 and (best is None or run_end - run_start > best[1] - best[0]):
                best = (run_start, run_end)
            run_start = None
    if best is None:
        return None
    return (best[0] + best[1] + 1) / 2 / bins * width


def _split_line(line: list[dict], gap: float | None) -> list[_Block]:
    size = statistics.median(float(word.get("size") or 10) for word in line)
    if gap is None:
        return [_make_block(line, "S", size)]
    left = [word for word in line if word["x1"] <= gap]
    right = [word for word in line if word["x0"] >= gap]
    crossing = len(left) + len(right) < len(line)
    if crossing or (left and right and right[0]["x0"] - left[-1]["x1"] < 1.5 * size):
        return [_make_block(line, "S", size)]
    blocks = []
    if left:
        blocks.append(_make_block(left, "L", size))
    if right:
        blocks.append(_make_block(right, "R", size))
    return blocks


def _make_block(words: list[dict], column: str, size: float) -> _Block:
    text = ""
    previous: dict | None = None
    for word in words:
        if previous is not None and word["x0"] - previous["x1"] > 0.25 * size:
            text = join_fragments(text, word["text"])
        else:
            text += word["text"]
        previous = word
    return _Block(
        top=min(word["top"] for word in words),
        bottom=max(word["bottom"] for word in words),
        column=column,
        text=text.strip(),
        size=size,
    )


def _drop_headers_and_footers(pages: list[_PageInfo]) -> None:
    """页眉页脚：页面上下 8% 区域里、半数以上页面都出现的行（数字归一后比较），以及孤立页码。"""
    if not pages:
        return

    def in_band(block: _Block, page: _PageInfo) -> bool:
        return block.top < page.height * HEADER_FOOTER_BAND or block.bottom > page.height * (1 - HEADER_FOOTER_BAND)

    def key(text: str) -> str:
        return re.sub(r"\d+", "#", re.sub(r"\s+", "", text))

    counter: Counter[str] = Counter()
    for page in pages:
        counter.update({key(block.text) for block in page.blocks if not block.is_table and in_band(block, page)})
    threshold = max(2, len(pages) // 2)
    page_number = re.compile(r"^[-–—\s]*(第\s*)?\d+(\s*页)?([/／]\s*\d+)?[-–—\s]*$|^page\s*\d+", re.IGNORECASE)
    for page in pages:
        kept = []
        for block in page.blocks:
            if not block.is_table and in_band(block, page):
                if (len(pages) >= 3 and counter[key(block.text)] >= threshold) or page_number.match(block.text):
                    continue
            kept.append(block)
        page.blocks = kept


def _body_font_size(pages: list[_PageInfo]) -> float:
    sizes = [block.size for page in pages for block in page.blocks if not block.is_table and block.size]
    return statistics.median(sizes) if sizes else 10.0


def _page_text(info: _PageInfo, body_size: float) -> str:
    """跨栏块切段；每段里先左栏后右栏，栏内按从上到下，相邻行拼成段落。"""
    ordered: list[_Block] = []
    pending: dict[str, list[_Block]] = {"L": [], "R": []}

    def flush() -> None:
        for column in ("L", "R"):
            ordered.extend(sorted(pending[column], key=lambda block: block.top))
            pending[column] = []

    for block in sorted(info.blocks, key=lambda item: item.top):
        if block.column == "S":
            flush()
            ordered.append(block)
        else:
            pending[block.column].append(block)
    flush()

    paragraphs: list[str] = []
    previous: _Block | None = None
    for block in ordered:
        if block.is_table:
            paragraphs.append(block.text)
            previous = None
            continue
        heading = _heading_prefix(block, body_size)
        if heading:
            paragraphs.append(heading + block.text)
            previous = None
            continue
        close = (
            previous is not None
            and previous.column == block.column
            and 0 <= block.top - previous.bottom <= 0.6 * max(block.size, 1)
            and not re.search(r"[。！？.!?:：]$", previous.text)
        )
        if close and paragraphs:
            paragraphs[-1] = join_fragments(paragraphs[-1], block.text)
        else:
            paragraphs.append(block.text)
        previous = block
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph.strip())


def _heading_prefix(block: _Block, body_size: float) -> str:
    if len(block.text) > 60 or not body_size:
        return ""
    ratio = block.size / body_size
    if ratio >= 1.6:
        return "# "
    if ratio >= 1.25:
        return "## "
    return ""


def _maybe_snapshot(page, info: _PageInfo, total_chars: int, taken: int) -> ParsedSnapshot | None:
    if taken >= BATCH_MAX_SNAPSHOTS:
        return None
    largest = max((ratio for _top, ratio, _order in info.images), default=0.0)
    reason = ""
    if total_chars < SCANNED_DOC_CHARS and (info.images or info.vectors or info.chars):
        reason = "扫描件或设计稿导出，文字层几乎为空"
    elif info.chars < LOW_TEXT_PAGE_CHARS and (largest >= LARGE_IMAGE_RATIO or info.vectors > DENSE_VECTOR_COUNT):
        reason = "少字大图页，主要信息在画面里"
    if not reason:
        return None
    scale = min(150 / 72, SNAPSHOT_MAX_EDGE / max(info.width, info.height, 1))
    bitmap = page.render(scale=scale)
    try:
        image = bitmap.to_pil().convert("RGB")
    finally:
        bitmap.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return ParsedSnapshot(data=buffer.getvalue(), width=image.width, height=image.height, page_no=info.number, reason=reason)


def _page_images(page, info: _PageInfo, filename: str, images: list[ParsedImage], *, scanned: bool) -> list[str]:
    """取这一页的配图，返回要插进正文的占位。扫描底图不取。"""
    if not info.images or len(images) >= DOC_MAX_IMAGES:
        return []
    placeholders: list[str] = []
    seen = {image.sha256 for image in images}
    low_text = info.chars < LOW_TEXT_PAGE_CHARS
    objects = list(page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=5))
    for _top, ratio, order in sorted(info.images, key=lambda item: item[0]):
        if len(images) >= DOC_MAX_IMAGES:
            break
        if scanned and ratio >= SCAN_IMAGE_RATIO:
            continue
        pil = _object_bitmap(objects, order)
        if pil is None or min(pil.size) < MIN_IMAGE_SIDE:
            continue
        normalized = normalize_pil(pil)
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
                label=f"{filename} 第 {info.number} 页",
                area_ratio=ratio,
                core=low_text and ratio >= 0.25,
            )
        )
        placeholders.append(IMAGE_PLACEHOLDER.format(index=len(images) - 1))
    return placeholders


def _object_bitmap(objects: list, order: int):
    if order >= len(objects):
        return None
    try:
        bitmap = objects[order].get_bitmap(render=True)
    except Exception:  # noqa: BLE001 - 个别编码（如 JBIG2 遮罩）取不出来，跳过
        return None
    try:
        return bitmap.to_pil().copy()
    finally:
        bitmap.close()
