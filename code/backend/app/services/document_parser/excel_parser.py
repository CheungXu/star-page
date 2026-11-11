"""Excel（xlsx / xls）：python-calamine 读取。小表整表给出，大表由代码算统计画像再附样本行。

大表不交给模型「压缩」，是因为模型摘要会改动数字；画像里的数都是代码算的。
"""

from __future__ import annotations

import io
import statistics
from collections import Counter
from datetime import date, datetime, time

from python_calamine import CalamineWorkbook

from app.services.document_parser.limits import XLSX_FULL_TABLE_ROWS, XLSX_MAX_COLS, XLSX_MAX_ROWS, XLSX_MAX_SHEETS
from app.services.document_parser.text_utils import format_cell, markdown_table, normalize_text
from app.services.document_parser.types import ParsedDocument

HEAD_SAMPLE_ROWS = 15
TAIL_SAMPLE_ROWS = 5
TOP_CATEGORIES = 8


def parse_excel(filename: str, data: bytes, content_sha256: str, extension: str) -> ParsedDocument:
    workbook = CalamineWorkbook.from_filelike(io.BytesIO(data))
    sections: list[str] = []
    used = 0
    for name in workbook.sheet_names:
        if used >= XLSX_MAX_SHEETS:
            sections.append(f"（其余工作表未读取：最多读取前 {XLSX_MAX_SHEETS} 个非空工作表）")
            break
        sheet = workbook.get_sheet_by_name(name)
        rows = sheet.to_python(skip_empty_area=True, nrows=XLSX_MAX_ROWS + 1)
        truncated = len(rows) > XLSX_MAX_ROWS
        rows = [list(row[:XLSX_MAX_COLS]) for row in rows[:XLSX_MAX_ROWS]]
        _fill_merged(rows, sheet.merged_cell_ranges or [], sheet.start or (0, 0))
        rows = _drop_empty(rows)
        if not rows:
            continue
        used += 1
        sections.append(_sheet_section(name, rows, truncated))
    return ParsedDocument(
        filename=filename,
        extension=extension.lstrip("."),
        content_sha256=content_sha256,
        text=normalize_text("\n\n".join(sections)),
    )


def _fill_merged(rows: list[list[object]], ranges: list, origin: tuple[int, int]) -> None:
    """合并单元格只有左上角有值，把值铺满整个合并区域，表头才对得上列。"""
    start_row, start_col = origin
    for (r0, c0), (r1, c1) in ranges:
        top, left = r0 - start_row, c0 - start_col
        if top < 0 or left < 0 or top >= len(rows) or left >= len(rows[top]):
            continue
        value = rows[top][left]
        for r in range(top, min(r1 - start_row, len(rows) - 1) + 1):
            for c in range(left, min(c1 - start_col, len(rows[r]) - 1) + 1):
                if _is_blank(rows[r][c]):
                    rows[r][c] = value


def _drop_empty(rows: list[list[object]]) -> list[list[object]]:
    rows = [row for row in rows if any(not _is_blank(cell) for cell in row)]
    if not rows:
        return []
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    keep = [index for index in range(width) if any(not _is_blank(row[index]) for row in rows)]
    return [[row[index] for index in keep] for row in rows]


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _header(rows: list[list[object]]) -> tuple[list[str], int]:
    """表头：第一行；若第二行也全是文字且第一行有合并（重复值），合成「上级/下级」两级表头。"""
    first = [format_cell(cell) for cell in rows[0]]
    if len(rows) > 2 and all(isinstance(cell, str) or _is_blank(cell) for cell in rows[1]):
        second = [format_cell(cell) for cell in rows[1]]
        repeated = len(set(filter(None, first))) < len([cell for cell in first if cell])
        if repeated and any(second):
            combined = [f"{top}/{sub}" if top and sub and top != sub else (top or sub) for top, sub in zip(first, second)]
            return combined, 2
    return [cell or f"列{index + 1}" for index, cell in enumerate(first)], 1


def _sheet_section(name: str, rows: list[list[object]], truncated: bool) -> str:
    header, header_rows = _header(rows)
    body = rows[header_rows:]
    title = f"## 工作表：{name}"
    if len(body) <= XLSX_FULL_TABLE_ROWS:
        return f"{title}\n\n{markdown_table([header, *body])}"

    lines = [title, "", f"共 {len(body)} 行数据、{len(header)} 列" + ("（超过上限，只读取了前 " + str(XLSX_MAX_ROWS) + " 行）" if truncated else "") + "。以下统计由程序精确计算：", ""]
    for index, column in enumerate(header):
        values = [row[index] for row in body if index < len(row) and not _is_blank(row[index])]
        lines.append(f"- {column}：{_profile(values, len(body))}")
    sample = [header, *body[:HEAD_SAMPLE_ROWS]]
    lines += ["", f"前 {HEAD_SAMPLE_ROWS} 行：", markdown_table(sample)]
    lines += ["", f"末 {TAIL_SAMPLE_ROWS} 行：", markdown_table([header, *body[-TAIL_SAMPLE_ROWS:]])]
    return "\n".join(lines)


def _profile(values: list[object], total: int) -> str:
    filled = f"非空 {len(values)}/{total}"
    if not values:
        return filled
    numbers = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if len(numbers) >= max(1, int(len(values) * 0.8)):
        return (
            f"数值，{filled}，合计 {_num(sum(numbers))}，平均 {_num(statistics.fmean(numbers))}，"
            f"中位数 {_num(statistics.median(numbers))}，最小 {_num(min(numbers))}，最大 {_num(max(numbers))}"
        )
    moments = [value for value in values if isinstance(value, (datetime, date, time))]
    if len(moments) >= max(1, int(len(values) * 0.8)):
        ordered = sorted(moments, key=lambda item: (str(type(item)), item))
        return f"日期，{filled}，从 {ordered[0]} 到 {ordered[-1]}"
    counts = Counter(format_cell(value) for value in values)
    top = "、".join(f"{key}（{count}）" for key, count in counts.most_common(TOP_CATEGORIES))
    return f"文本，{filled}，不同取值 {len(counts)} 个，最常见：{top}"


def _num(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.4g}" if abs(value) < 1 else f"{value:,.2f}"
