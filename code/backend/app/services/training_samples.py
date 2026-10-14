"""匿名结构样本：从生成页里只保留 HTML 结构、CSS 与交互代码。

正文、属性里的文字、链接、图片、脚本里的中文字符串、手机号、邮箱一律替换为占位，
样本不带用户 ID 与页面 ID，因此不再属于个人信息。必须在授权生成完成的同一流程里提取。
"""

from __future__ import annotations

import hashlib
import re
import uuid

from bs4 import BeautifulSoup, Comment, NavigableString
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import TrainingSample

EXTRACTOR_VERSION = "v1"

_TEXT_ATTRS = {"alt", "title", "placeholder", "aria-label", "aria-description", "value", "label", "content", "download"}
_URL_ATTRS = {"href", "src", "srcset", "action", "poster", "data", "cite", "background", "xlink:href"}
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_JS_STRING = re.compile(r"(?P<q>['\"`])(?P<body>(?:\\.|(?!(?P=q)).)*?)(?P=q)", re.S)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_LONG_DIGITS = re.compile(r"\d{6,}")
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(?!data:)[^)]*?\1\s*\)", re.I)
_CSS_CONTENT = re.compile(r"(content\s*:\s*)(['\"])(?:\\.|(?!\2).)*\2", re.I)


def _placeholder(text: str) -> str:
    """按原长度给占位文字，保留排版密度信息而不保留内容。"""
    stripped = text.strip()
    if not stripped:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    tail = text[len(text.rstrip()) :]
    size = len(stripped)
    filler = "文" if _CJK.search(stripped) else "x"
    return f"{lead}{filler * min(size, 200)}{tail}"


def _scrub_free_text(text: str) -> str:
    text = _EMAIL.sub("user@example.com", text)
    text = _PHONE.sub("13800000000", text)
    return _LONG_DIGITS.sub(lambda m: "0" * len(m.group(0)), text)


def _scrub_script(code: str) -> str:
    def replace(match: re.Match[str]) -> str:
        body = match.group("body")
        quote = match.group("q")
        if body.startswith(("http://", "https://", "//")):
            return f"{quote}#{quote}"
        if _CJK.search(body) or _EMAIL.search(body) or _PHONE.search(body):
            return f"{quote}{_placeholder(body)}{quote}"
        return match.group(0)

    return _scrub_free_text(_JS_STRING.sub(replace, code))


def _scrub_css(css: str) -> str:
    css = _CSS_URL.sub("url(#)", css)
    css = _CSS_CONTENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(2)}", css)
    return _scrub_free_text(css)


def extract_skeleton(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()

    for node in soup.find_all(string=True):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent.name if node.parent is not None else ""
        if parent == "script":
            node.replace_with(_scrub_script(str(node)))
        elif parent == "style":
            node.replace_with(_scrub_css(str(node)))
        else:
            node.replace_with(_placeholder(str(node)))

    for tag in soup.find_all(True):
        if tag.name == "meta" and str(tag.get("name", "")).lower() == "viewport":
            continue
        for attr in list(tag.attrs):
            name = attr.lower()
            value = tag.attrs[attr]
            if name in _URL_ATTRS:
                tag.attrs[attr] = "#"
            elif name in _TEXT_ATTRS or name.startswith("data-"):
                tag.attrs[attr] = _placeholder(value if isinstance(value, str) else " ".join(value))
            elif name == "style" and isinstance(value, str):
                tag.attrs[attr] = _scrub_css(value)
            elif name.startswith("on") and isinstance(value, str):
                tag.attrs[attr] = _scrub_script(value)

    return str(soup)


async def store_training_sample(
    session: AsyncSession, *, html: str, scene_key: str | None, model_key: str | None
) -> None:
    """提取并保存一条匿名结构样本，内容相同则跳过。"""
    skeleton = extract_skeleton(html)
    digest = hashlib.sha256(skeleton.encode("utf-8")).hexdigest()
    await session.execute(
        insert(TrainingSample)
        .values(
            id=uuid.uuid4(),
            scene_key=scene_key,
            model_key=model_key,
            html_skeleton=skeleton,
            content_hash=digest,
            extractor_version=EXTRACTOR_VERSION,
        )
        .on_conflict_do_nothing(index_elements=["content_hash"])
    )
