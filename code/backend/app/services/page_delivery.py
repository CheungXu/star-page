"""生成页对外返回：统一 CSP，并补上分享卡片信息。"""

from __future__ import annotations

from urllib.parse import urlencode, urlparse

from fastapi.responses import HTMLResponse

from app.core.config import get_settings
from app.services.share_meta import inject_share_meta


def build_page_csp() -> str:
    """展示型 CSP。图片允许 https、data，以及站点自己的地址（本地 http 调试时托管图片也能显示）。"""
    settings = get_settings()
    cdn = " ".join(settings.generated_page_cdn_sources)
    script_src = ("'unsafe-inline' " + cdn).strip()
    style_src = ("'unsafe-inline' " + cdn).strip()
    image_src = "https: data:"
    origin = urlparse(settings.public_base_url)
    if origin.scheme and origin.netloc:
        image_src = f"{image_src} {origin.scheme}://{origin.netloc}"
    return "; ".join(
        [
            "sandbox allow-scripts allow-modals allow-popups allow-popups-to-escape-sandbox",
            "default-src 'none'",
            f"script-src {script_src}",
            f"style-src {style_src}",
            f"img-src {image_src}",
            "font-src https: data:",
            "media-src https: data:",
            "connect-src 'none'",
            "form-action 'none'",
            "base-uri 'none'",
            "frame-ancestors 'self'",
        ]
    )


def build_remix_url(scene_key: str | None, ref: str | None) -> str:
    """浮标「用星页做同款」的落点：带上场景和来源，首页据此选中场景、记录渠道。"""
    params = {"utm_source": "made_with"}
    if scene_key:
        params["scene"] = scene_key
    if ref:
        params["ref"] = ref[:64]
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/?{urlencode(params)}"


_BLOCKED_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>页面已下架 · 星页 StarPage</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#f7f8fa;
font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;color:#475569}
main{max-width:360px;padding:32px 28px;border-radius:16px;background:#fff;box-shadow:0 8px 30px -12px rgba(15,23,42,.18);text-align:center}
h1{margin:0 0 8px;font-size:19px;color:#0f172a}a{color:#3563e9;text-decoration:none}</style></head>
<body><main><h1>该页面因违规已下架</h1><p>该页面经举报审核违反《星页用户服务协议》，已停止访问。</p>
<p><a href="__HOME__">返回星页首页</a></p></main></body></html>"""


def render_blocked_page() -> HTMLResponse:
    """举报审核下架后的统一页面，451 = 因法律原因不可用。"""
    home = get_settings().public_base_url.rstrip("/") + "/"
    return HTMLResponse(
        content=_BLOCKED_HTML.replace("__HOME__", home),
        status_code=451,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


def build_report_url(page_id: str, slug: str | None = None) -> str:
    """浮标「举报」的落点：生成页 CSP 不允许直接请求接口，跳到平台举报页提交。"""
    params = {"page": page_id}
    if slug:
        params["slug"] = slug[:64]
    base = get_settings().public_base_url.rstrip("/")
    return f"{base}/report?{urlencode(params)}"


def render_page_html(
    html: str,
    *,
    title: str,
    description: str,
    image_url: str | None,
    page_url: str | None,
    print_after_load: bool,
    remix_url: str | None = None,
    poster_url: str | None = None,
    report_url: str | None = None,
) -> HTMLResponse:
    content = inject_share_meta(
        html,
        title=title,
        description=description,
        image_url=image_url,
        page_url=page_url,
        print_after_load=print_after_load,
        remix_url=remix_url,
        poster_url=poster_url,
        report_url=report_url,
    )
    return HTMLResponse(
        content=content,
        headers={
            "Content-Security-Policy": build_page_csp(),
            "X-Content-Type-Options": "nosniff",
        },
    )
