from __future__ import annotations

import html
import re

# 锚点格式见 pipeline.anchor_for_image / anchor_for_snapshot，统计有效字数时要先去掉。
ANCHOR_PATTERN = re.compile(r"\[(?:配图|核心内容大图|整页快照) [^\]]*\]")
PLACEHOLDER_PATTERN = re.compile(r"⟦(?:IMG|SNAP):\d+⟧")
# 解析器和流水线自己加的结构行（页码标记、文件头、无标题的页标题），不算资料里的字。
STRUCTURE_PATTERN = re.compile(
    r"【第 \d+ 页】|^## 文件 \d+: .*$|^- 文件类型：.*$|^## 第 \d+ 页$|未抽取到可用文本内容。",
    re.MULTILINE,
)
_CJK = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")


def decode_text(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def normalize_text(text: str) -> str:
    """统一换行、去行尾空白，连续空行最多留两行。"""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized: list[str] = []
    blank_count = 0
    for line in lines:
        if line.strip():
            blank_count = 0
            normalized.append(line)
            continue
        blank_count += 1
        if blank_count <= 2:
            normalized.append("")
    return "\n".join(normalized).strip()


def effective_char_count(text: str) -> int:
    """去掉图片锚点、占位、结构行和空白之后的字数，用来判断「少字」。"""
    stripped = STRUCTURE_PATTERN.sub("", PLACEHOLDER_PATTERN.sub("", ANCHOR_PATTERN.sub("", text or "")))
    return len(re.sub(r"\s+", "", stripped))


def join_fragments(left: str, right: str) -> str:
    """拼接两段文字：中文之间不加空格，西文之间加一个空格。"""
    if not left:
        return right
    if not right:
        return left
    if _CJK.match(left[-1]) or _CJK.match(right[0]):
        return left + right
    return left + " " + right


def format_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text.replace("|", "\\|")


def markdown_table(rows: list[list[object]]) -> str:
    """第一行当表头。列数按最宽的一行补齐。"""
    cleaned = [[format_cell(cell) for cell in row] for row in rows if any(format_cell(cell) for cell in row)]
    if not cleaned:
        return ""
    width = max(len(row) for row in cleaned)
    cleaned = [row + [""] * (width - len(row)) for row in cleaned]
    header, body = cleaned[0], cleaned[1:]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def html_table(rows: list[list[dict]]) -> str:
    """有合并单元格的表格输出精简 HTML，保留 rowspan / colspan。cell: {text, rowspan, colspan}。"""
    out = ["<table>"]
    for row in rows:
        cells = []
        for cell in row:
            attrs = ""
            if cell.get("rowspan", 1) > 1:
                attrs += f' rowspan="{cell["rowspan"]}"'
            if cell.get("colspan", 1) > 1:
                attrs += f' colspan="{cell["colspan"]}"'
            cells.append(f"<td{attrs}>{html.escape(cell.get('text', ''))}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</table>")
    return "\n".join(out)
