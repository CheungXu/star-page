"""生成页发布前无头浏览器渲染检查模块。"""

from __future__ import annotations

from app.services.page_render_check.runner import (
    CHROMIUM_PATH_ENV,
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_QUEUE,
    DEFAULT_SETTLE_MS,
    DEFAULT_TIMEOUT_SECONDS,
    check_page_render,
    extract_allowed_cdn_hosts,
    render_check_stats,
    reset_render_check_stats,
    resolve_chromium_executable,
)
from app.services.page_render_check.types import (
    MAIN_DOCUMENT_URL,
    RenderCheckResult,
    RenderCheckStatus,
    RenderIssue,
    RenderIssueKind,
    build_blank_page_issue,
    build_hang_issue,
    build_js_error_issue,
    render_issue_from_stored,
)

__all__ = [
    "CHROMIUM_PATH_ENV",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_MAX_QUEUE",
    "DEFAULT_SETTLE_MS",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAIN_DOCUMENT_URL",
    "RenderCheckResult",
    "RenderCheckStatus",
    "RenderIssue",
    "RenderIssueKind",
    "build_blank_page_issue",
    "build_hang_issue",
    "build_js_error_issue",
    "check_page_render",
    "extract_allowed_cdn_hosts",
    "render_check_stats",
    "render_issue_from_stored",
    "reset_render_check_stats",
    "resolve_chromium_executable",
]
