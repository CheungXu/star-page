"""HTML 转 Markdown：去掉脚本样式，保留标题、列表、表格结构。txt / md 只解码。"""

from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString, Tag

from app.services.document_parser.text_utils import decode_text, markdown_table, normalize_text
from app.services.document_parser.types import ParsedDocument

_DROP = {"script", "style", "noscript", "template", "svg", "iframe", "head"}
_BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "aside", "nav", "blockquote", "figure", "figcaption", "pre"}


def parse_plain_text(filename: str, data: bytes, content_sha256: str, extension: str) -> ParsedDocument:
    return ParsedDocument(
        filename=filename,
        extension=extension.lstrip("."),
        content_sha256=content_sha256,
        text=normalize_text(decode_text(data)),
    )


def parse_html(filename: str, data: bytes, content_sha256: str, extension: str) -> ParsedDocument:
    soup = BeautifulSoup(decode_text(data), "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup.find_all(_DROP):
        tag.decompose()
    root = soup.body or soup
    blocks: list[str] = []
    _walk(root, blocks)
    text = "\n\n".join(block for block in blocks if block.strip())
    if title and title not in text[:200]:
        text = f"# {title}\n\n{text}"
    return ParsedDocument(filename=filename, extension=extension.lstrip("."), content_sha256=content_sha256, text=normalize_text(text))


def _walk(node: Tag, blocks: list[str]) -> None:
    inline: list[str] = []

    def flush() -> None:
        text = " ".join("".join(inline).split())
        if text:
            blocks.append(text)
        inline.clear()

    for child in node.children:
        if isinstance(child, NavigableString):
            inline.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            flush()
            text = child.get_text(" ", strip=True)
            if text:
                blocks.append("#" * int(name[1]) + " " + text)
        elif name in {"ul", "ol"}:
            flush()
            items = [item.get_text(" ", strip=True) for item in child.find_all("li", recursive=False)]
            blocks.append("\n".join(f"- {item}" for item in items if item))
        elif name == "table":
            flush()
            rows = [[cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])] for tr in child.find_all("tr")]
            table = markdown_table(rows)
            if table:
                blocks.append(table)
        elif name == "br":
            inline.append("\n")
        elif name in _BLOCK:
            flush()
            _walk(child, blocks)
        else:
            inline.append(child.get_text(" ", strip=False))
    flush()
