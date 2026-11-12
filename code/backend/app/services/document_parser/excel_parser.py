"""Excel（xlsx / xls）：python-calamine 按行迭代读取。小表整表给出，大表流式统计画像再附样本行。

大表不交给模型「压缩」，是因为模型摘要会改动数字；画像里的数都是代码算的。
按行迭代流式累计各列画像和首尾样本，不在内存里同时持有数百万单元格的多份拷贝。
"""

from __future__ import annotations

import array
import io
import statistics
from collections import Counter, defaultdict, deque
from datetime import date, datetime, time
from pathlib import Path

from python_calamine import CalamineWorkbook

from app.services.document_parser.limits import XLSX_FULL_TABLE_ROWS, XLSX_MAX_COLS, XLSX_MAX_ROWS, XLSX_MAX_SHEETS
from app.services.document_parser.text_utils import format_cell, markdown_table, normalize_text
from app.services.document_parser.types import ParsedDocument

HEAD_SAMPLE_ROWS = 15
TAIL_SAMPLE_ROWS = 5
TOP_CATEGORIES = 8
# 最多缓冲 2 行潜在表头 + XLSX_FULL_TABLE_ROWS 行正文，超出后切入流式累计。
_SMALL_SHEET_BUFFER = XLSX_FULL_TABLE_ROWS + 2


class _ColumnAccumulator:
    """单列的流式统计器：数值放紧凑的 C double 数组，日期只留首尾与词频，文本记 Counter。"""

    __slots__ = ("filled", "numbers", "moments_count", "min_moment", "max_moment", "moment_texts", "text_counts")

    def __init__(self) -> None:
        self.filled = 0
        self.numbers = array.array("d")
        self.moments_count = 0
        self.min_moment: tuple[str, object] | None = None
        self.max_moment: tuple[str, object] | None = None
        self.moment_texts: Counter[str] = Counter()
        self.text_counts: Counter[str] = Counter()

    def add(self, value: object) -> None:
        self.filled += 1
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self.numbers.append(float(value))
            return
        if isinstance(value, (datetime, date, time)):
            self.moments_count += 1
            key = (str(type(value)), value)
            if self.min_moment is None or key < self.min_moment:
                self.min_moment = key
            if self.max_moment is None or key > self.max_moment:
                self.max_moment = key
            self.moment_texts[format_cell(value)] += 1
            return
        self.text_counts[format_cell(value)] += 1

    def describe(self, total: int) -> str:
        filled_str = f"非空 {self.filled}/{total}"
        if self.filled == 0:
            return filled_str
        threshold = max(1, int(self.filled * 0.8))
        if len(self.numbers) >= threshold:
            return (
                f"数值，{filled_str}，合计 {_num(sum(self.numbers))}，平均 {_num(statistics.fmean(self.numbers))}，"
                f"中位数 {_num(statistics.median(self.numbers))}，最小 {_num(min(self.numbers))}，最大 {_num(max(self.numbers))}"
            )
        if self.moments_count >= threshold and self.min_moment is not None and self.max_moment is not None:
            return f"日期，{filled_str}，从 {self.min_moment[1]} 到 {self.max_moment[1]}"
        counts = Counter(self.text_counts)
        if self.moment_texts:
            counts.update(self.moment_texts)
        if self.numbers:
            for number in self.numbers:
                counts[format_cell(number)] += 1
        top = "、".join(f"{key}（{count}）" for key, count in counts.most_common(TOP_CATEGORIES))
        return f"文本，{filled_str}，不同取值 {len(counts)} 个，最常见：{top}"


def parse_excel(filename: str, source: bytes | Path, content_sha256: str, extension: str) -> ParsedDocument:
    workbook = (
        CalamineWorkbook.from_path(str(source))
        if isinstance(source, Path)
        else CalamineWorkbook.from_filelike(io.BytesIO(source))
    )
    try:
        sections: list[str] = []
        used = 0
        for name in workbook.sheet_names:
            if used >= XLSX_MAX_SHEETS:
                sections.append(f"（其余工作表未读取：最多读取前 {XLSX_MAX_SHEETS} 个非空工作表）")
                break
            sheet = workbook.get_sheet_by_name(name)
            section = _read_sheet_streaming(name, sheet)
            if section is None:
                continue
            used += 1
            sections.append(section)
    finally:
        if hasattr(workbook, "close"):
            workbook.close()
    return ParsedDocument(
        filename=filename,
        extension=extension.lstrip("."),
        content_sha256=content_sha256,
        text=normalize_text("\n\n".join(sections)),
    )


def _index_merges(ranges: list, origin: tuple[int, int]) -> dict[int, list[tuple[int, int, int]]]:
    """把合并单元格按相对起始行建索引：(结束行, 左列, 右列)。"""
    start_row, start_col = origin
    by_top: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for (r0, c0), (r1, c1) in ranges:
        top, left = r0 - start_row, c0 - start_col
        if top < 0 or left < 0 or top >= XLSX_MAX_ROWS or left >= XLSX_MAX_COLS:
            continue
        end_r = min(r1 - start_row, XLSX_MAX_ROWS - 1)
        right = min(c1 - start_col, XLSX_MAX_COLS - 1)
        by_top[top].append((end_r, left, right))
    return by_top


def _apply_row_merges(
    rel_r: int,
    row: list[object],
    merge_starts: dict[int, list[tuple[int, int, int]]],
    active: list[tuple[int, int, int, object]],
) -> None:
    """对当前行应用跨行合并值，并登记本行新开始的合并区域。"""
    for _end_r, left, right, value in active:
        limit = min(right, len(row) - 1)
        for col in range(left, limit + 1):
            if _is_blank(row[col]):
                row[col] = value
    for end_r, left, right in merge_starts.get(rel_r, ()):
        if left >= len(row):
            continue
        value = row[left]
        limit = min(right, len(row) - 1)
        for col in range(left, limit + 1):
            if _is_blank(row[col]):
                row[col] = value
        if end_r > rel_r:
            active.append((end_r, left, right, value))
    if active:
        active[:] = [item for item in active if item[0] > rel_r]


def _read_sheet_streaming(name: str, sheet) -> str | None:
    origin = sheet.start or (0, 0)
    start_row, start_col = origin
    merge_starts = _index_merges(sheet.merged_cell_ranges or [], origin)
    active_merges: list[tuple[int, int, int, object]] = []

    buffered: list[list[object]] = []
    streaming = False
    header_raw: list[list[object]] = []
    header_rows_count = 1
    head_sample: list[list[object]] = []
    tail_sample: deque[list[object]] = deque(maxlen=TAIL_SAMPLE_ROWS)
    accumulators: list[_ColumnAccumulator] = []
    non_blank_cols = [False] * XLSX_MAX_COLS
    max_width = 0
    body_count = 0
    truncated = False

    def feed_body_row(row: list[object]) -> None:
        nonlocal body_count
        body_count += 1
        if len(head_sample) < HEAD_SAMPLE_ROWS:
            head_sample.append(row)
        tail_sample.append(row)
        while len(accumulators) < len(row):
            accumulators.append(_ColumnAccumulator())
        for index, cell in enumerate(row):
            if not _is_blank(cell):
                accumulators[index].add(cell)

    for raw_idx, raw_row in enumerate(sheet.iter_rows()):
        if raw_idx < start_row:
            continue
        rel_r = raw_idx - start_row
        if rel_r >= XLSX_MAX_ROWS:
            truncated = True
            break
        row = list(raw_row[start_col : start_col + XLSX_MAX_COLS])
        _apply_row_merges(rel_r, row, merge_starts, active_merges)

        has_value = False
        for index, cell in enumerate(row):
            if not _is_blank(cell):
                has_value = True
                non_blank_cols[index] = True
        if not has_value:
            continue
        if len(row) > max_width:
            max_width = len(row)

        if not streaming:
            buffered.append(row)
            if len(buffered) > _SMALL_SHEET_BUFFER:
                streaming = True
                _, header_rows_count = _header(buffered[:3])
                header_raw = buffered[:header_rows_count]
                for body_row in buffered[header_rows_count:]:
                    feed_body_row(body_row)
                buffered.clear()
        else:
            feed_body_row(row)

    if max_width == 0:
        return None

    keep = [index for index in range(max_width) if non_blank_cols[index]]
    if not keep:
        return None

    def pick_cols(rows: list[list[object]]) -> list[list[object]]:
        return [[r[index] if index < len(r) else "" for index in keep] for r in rows]

    if not streaming:
        kept_rows = pick_cols(buffered)
        header, hdr_count = _header(kept_rows)
        body = kept_rows[hdr_count:]
        if len(body) <= XLSX_FULL_TABLE_ROWS:
            return f"## 工作表：{name}\n\n{markdown_table([header, *body])}"
        # 刚好在缓冲边界但只有 1 行表头（例如 61 行正文）时，同样输出统计画像。
        header_raw = buffered[:hdr_count]
        for body_row in buffered[hdr_count:]:
            feed_body_row(body_row)
        buffered.clear()

    header, _ = _header(pick_cols(header_raw))
    kept_head = pick_cols(head_sample)
    kept_tail = pick_cols(list(tail_sample))
    lines = [
        f"## 工作表：{name}",
        "",
        f"共 {body_count} 行数据、{len(header)} 列"
        + (f"（超过上限，只读取了前 {XLSX_MAX_ROWS} 行）" if truncated else "")
        + "。以下统计由程序精确计算：",
        "",
    ]
    for col_pos, orig_index in enumerate(keep):
        acc = accumulators[orig_index] if orig_index < len(accumulators) else _ColumnAccumulator()
        lines.append(f"- {header[col_pos]}：{acc.describe(body_count)}")
    lines += ["", f"前 {HEAD_SAMPLE_ROWS} 行：", markdown_table([header, *kept_head])]
    lines += ["", f"末 {TAIL_SAMPLE_ROWS} 行：", markdown_table([header, *kept_tail])]
    return "\n".join(lines)


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _header(rows: list[list[object]]) -> tuple[list[str], int]:
    """表头：第一行；若第二行也全是文字且第一行有合并（重复值），合成「上级/下级」两级表头。"""
    width = max((len(r) for r in rows), default=0)
    padded = [r + [""] * (width - len(r)) for r in rows]
    first = [format_cell(cell) for cell in padded[0]]
    if len(padded) > 2 and all(isinstance(cell, str) or _is_blank(cell) for cell in padded[1]):
        second = [format_cell(cell) for cell in padded[1]]
        repeated = len(set(filter(None, first))) < len([cell for cell in first if cell])
        if repeated and any(second):
            combined = [f"{top}/{sub}" if top and sub and top != sub else (top or sub) for top, sub in zip(first, second)]
            return combined, 2
    return [cell or f"列{index + 1}" for index, cell in enumerate(first)], 1


def _num(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.4g}" if abs(value) < 1 else f"{value:,.2f}"
