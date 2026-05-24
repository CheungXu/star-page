from __future__ import annotations

import re

from bs4 import BeautifulSoup, Doctype

BANNED_TAGS = {"script", "iframe", "form", "object", "embed", "base"}
URI_ATTRS = {"href", "src", "action", "formaction"}


def extract_html_document(raw_text: str) -> str:
    text = raw_text.strip()

    fenced = re.search(r"```(?:html)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    html_start = _first_existing_index(text.lower(), ["<!doctype", "<html"])
    if html_start > 0:
        text = text[html_start:]

    html_end = text.lower().rfind("</html>")
    if html_end != -1:
        text = text[: html_end + len("</html>")]

    if "<html" not in text.lower():
        text = f"<!doctype html><html><head><meta charset='utf-8'><title>生成页面</title></head><body>{text}</body></html>"

    return text


def sanitize_html(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")

    for node in soup.find_all(BANNED_TAGS):
        node.decompose()

    for meta in soup.find_all("meta"):
        http_equiv = str(meta.get("http-equiv", "")).lower()
        if http_equiv == "refresh":
            meta.decompose()

    for tag in soup.find_all(True):
        attrs_to_delete: list[str] = []
        for attr_name, attr_value in tag.attrs.items():
            lower_name = attr_name.lower()
            value = " ".join(attr_value) if isinstance(attr_value, list) else str(attr_value)
            lower_value = value.strip().lower()

            if lower_name.startswith("on"):
                attrs_to_delete.append(attr_name)
            elif lower_name in URI_ATTRS and lower_value.startswith("javascript:"):
                attrs_to_delete.append(attr_name)

        for attr_name in attrs_to_delete:
            del tag.attrs[attr_name]

    rendered = str(soup)
    has_doctype = any(isinstance(item, Doctype) for item in soup.contents)
    if not has_doctype and not rendered.lower().lstrip().startswith("<!doctype"):
        rendered = "<!doctype html>\n" + rendered

    return rendered


def _first_existing_index(text: str, needles: list[str]) -> int:
    indexes = [index for needle in needles if (index := text.find(needle)) != -1]
    return min(indexes) if indexes else -1
