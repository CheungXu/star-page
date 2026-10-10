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
    )
    return HTMLResponse(
        content=content,
        headers={
            "Content-Security-Policy": build_page_csp(),
            "X-Content-Type-Options": "nosniff",
        },
    )
