from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Doctype

from app.core.config import get_settings

# 展示页用不到、且属于高风险/插件/跳转能力的标签，一律移除。
# 注意：<script>/<form> 不在此列——本产品已用 CSP sandbox + connect-src none
# 把页面关进无凭证、无外部网络的沙箱，故放行展示型 JS 与纯前端表单控件。
BANNED_TAGS = {"iframe", "object", "embed", "base"}
TAILWIND_PLAY_CDN_HOST = "cdn.tailwindcss.com"


@dataclass(frozen=True)
class GeneratedHtmlPolicyViolation:
    code: str
    message: str


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

    # 移除会自动跳转的 meta refresh。
    for meta in soup.find_all("meta"):
        http_equiv = str(meta.get("http-equiv", "")).lower()
        if http_equiv == "refresh":
            meta.decompose()

    # 外链脚本仅允许可信 CDN，其余 <script src> 丢弃；内联脚本（无 src）保留。
    allowed_hosts = _allowed_script_hosts()
    for script in soup.find_all("script"):
        src = script.get("src")
        if src is not None and not _is_allowed_script_src(str(src), allowed_hosts):
            script.decompose()

    rendered = str(soup)
    has_doctype = any(isinstance(item, Doctype) for item in soup.contents)
    if not has_doctype and not rendered.lower().lstrip().startswith("<!doctype"):
        rendered = "<!doctype html>\n" + rendered

    return rendered


def find_tailwind_runtime_violation(raw_html: str) -> GeneratedHtmlPolicyViolation | None:
    """检测当前不支持的 Tailwind 运行时依赖，避免发布后样式在沙箱里失效。"""
    soup = BeautifulSoup(raw_html, "html.parser")

    for script in soup.find_all("script"):
        src = str(script.get("src") or "").strip().lower()
        if TAILWIND_PLAY_CDN_HOST in src:
            return GeneratedHtmlPolicyViolation(
                code="tailwind_play_cdn",
                message="生成结果引用了平台当前不支持的 Tailwind Play CDN",
            )

    for style in soup.find_all("style"):
        style_type = str(style.get("type") or "").strip().lower()
        if style_type == "text/tailwindcss":
            return GeneratedHtmlPolicyViolation(
                code="tailwind_style_block",
                message="生成结果使用了平台当前不支持的 Tailwind 专用样式块",
            )

    return None


def _allowed_script_hosts() -> set[str]:
    """从 CDN 白名单配置解析出允许的脚本来源主机名（小写）。"""
    hosts: set[str] = set()
    for entry in get_settings().generated_page_cdn_sources:
        parsed = urlparse(entry if "//" in entry else f"https://{entry}")
        if parsed.netloc:
            hosts.add(parsed.netloc.lower())
    return hosts


def _is_allowed_script_src(src: str, allowed_hosts: set[str]) -> bool:
    src = src.strip()
    if not src:
        return True  # 内联脚本无 src
    parsed = urlparse(src if not src.startswith("//") else f"https:{src}")
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return False  # 拒绝 data:/javascript: 等作为外链脚本来源
    if not parsed.netloc:
        return False  # 自包含页不接受相对路径外链脚本
    return parsed.netloc.lower() in allowed_hosts


def _first_existing_index(text: str, needles: list[str]) -> int:
    indexes = [index for needle in needles if (index := text.find(needle)) != -1]
    return min(indexes) if indexes else -1
