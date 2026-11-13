"""生成页发布前渲染检查（page_render_check）单测：纯逻辑 + 真实 Chromium 子进程隔离验证。"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.page_render_check import (  # noqa: E402
    CHROMIUM_PATH_ENV,
    RenderCheckResult,
    RenderIssue,
    build_blank_page_issue,
    build_hang_issue,
    build_js_error_issue,
    check_page_render,
    extract_allowed_cdn_hosts,
    render_check_stats,
    render_issue_from_stored,
    reset_render_check_stats,
    resolve_chromium_executable,
)

REAL_CHROMIUM = resolve_chromium_executable()
requires_chromium = pytest.mark.skipif(
    not REAL_CHROMIUM,
    reason="当前环境未安装无头 Chromium，跳过真实浏览器子进程测试",
)


def _live_render_check_children() -> list[tuple[int, str]]:
    """检查当前测试进程名下或全局是否还有残留的 page_render_check.child / 关联临时目录 Chromium 进程。"""
    my_pid = str(os.getpid())
    proc = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,args="],
        capture_output=True,
        text=True,
        check=True,
    )
    leaked: list[tuple[int, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_str, ppid_str, args = parts
        if ppid_str == my_pid and "app.services.page_render_check.child" in args:
            leaked.append((int(pid_str), args))
        elif "sp-render-ipc-" in args:
            leaked.append((int(pid_str), args))
    return leaked


# -------------------- 纯逻辑单元测试 --------------------


def test_build_js_error_issue_parses_runtime_line_and_syntax_error() -> None:
    runtime = build_js_error_issue(
        name="ReferenceError",
        message="renderHeroChart is not defined",
        stack="ReferenceError: renderHeroChart is not defined\n    at http://starpage.invalid/:27:9",
    )
    assert runtime.kind == "js_error"
    assert runtime.line == 27
    assert runtime.column == 9
    assert "第 27 行" in runtime.summary
    assert "ReferenceError: renderHeroChart is not defined" in runtime.summary
    assert "http://starpage.invalid/:27:9" in runtime.detail

    syntax = build_js_error_issue(
        name="SyntaxError",
        message="Unexpected token ';'",
        stack="",
    )
    assert syntax.kind == "js_error"
    assert syntax.line is None
    assert "SyntaxError: Unexpected token ';'" in syntax.summary


def test_result_conversions_for_db_frontend_and_repair_prompt() -> None:
    passed = RenderCheckResult(status="passed", duration_ms=420.4, rss_mb=132.6)
    assert passed.passed is True
    assert passed.has_issues is False
    assert passed.to_render_issue() is None
    assert passed.format_for_repair_prompt() == ""
    stored_passed = passed.to_dict()
    assert stored_passed == {
        "status": "passed",
        "issues": [],
        "repaired": False,
        "duration_ms": 420.4,
        "rss_mb": 132.6,
    }
    assert render_issue_from_stored(stored_passed) is None

    skipped = RenderCheckResult(status="skipped", skip_reason="chromium_not_found")
    assert skipped.skipped is True
    assert skipped.to_render_issue() is None
    assert skipped.to_dict()["skip_reason"] == "chromium_not_found"
    assert render_issue_from_stored(skipped.to_dict()) is None

    js_issue = build_js_error_issue(
        name="TypeError",
        message="Cannot read properties of null (reading 'addEventListener')",
        stack="TypeError: Cannot read properties of null\n    at http://starpage.invalid/:18:12",
    )
    blank_issue = build_blank_page_issue("DOM 含文本但因 opacity:0 不可见")
    hang_issue = build_hang_issue(15.0)
    failed = RenderCheckResult(
        status="issues_found",
        issues=[js_issue, blank_issue, hang_issue],
        duration_ms=1512.3,
        rss_mb=168.2,
    )
    assert failed.has_issues is True

    db_payload = failed.to_dict(repaired=True)
    assert db_payload["status"] == "issues_found"
    assert db_payload["repaired"] is True
    assert len(db_payload["issues"]) == 3
    restored = RenderCheckResult.from_dict(db_payload)
    assert restored.repaired is True
    assert [i.kind for i in restored.issues] == ["js_error", "blank_page", "hang"]
    assert restored.issues[0].line == 18

    fe_issue = failed.to_render_issue()
    assert fe_issue is not None
    assert "脚本运行出错" in fe_issue["summary"]
    assert "首屏疑似白屏" in fe_issue["summary"]
    assert isinstance(fe_issue["issues"], list)
    assert len(fe_issue["issues"]) == 3
    assert render_issue_from_stored(db_payload) == fe_issue

    prompt_edit = failed.format_for_repair_prompt(prefer_edit_blocks=True)
    assert "<<<<<<< SEARCH" in prompt_edit
    assert "第 18 行" in prompt_edit
    assert "opacity: 0" in prompt_edit
    assert "死循环" in prompt_edit

    prompt_full = failed.format_for_repair_prompt(prefer_edit_blocks=False)
    assert "<<<<<<< SEARCH" not in prompt_full
    assert "完整 HTML 文档" in prompt_full


def test_resolve_chromium_and_cdn_hosts_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_bin = tmp_path / "fake-chrome"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    # 1. 显式参数优先，且不存在时直接返回 None（不静默回退）
    assert resolve_chromium_executable("/definitely/not/installed/chrome") is None
    assert resolve_chromium_executable(str(fake_bin)) == str(fake_bin)

    # 2. Settings 上的动态属性（getattr 兼容尚未写入 config.py 的字段）
    fake_settings = SimpleNamespace(
        page_render_check_chromium_path=str(fake_bin),
        generated_page_cdn_sources=[
            "https://cdn.jsdelivr.net",
            "https://unpkg.com/path",
            "https://cdn.jsdelivr.net",
        ],
    )
    assert resolve_chromium_executable(settings=fake_settings) == str(fake_bin)
    assert extract_allowed_cdn_hosts(fake_settings) == ["cdn.jsdelivr.net", "unpkg.com"]

    missing_settings = SimpleNamespace(page_render_check_chromium_path="/missing/from/settings/chrome")
    assert resolve_chromium_executable(settings=missing_settings) is None

    # 3. 环境变量显式指向不存在路径时同样返回 None
    monkeypatch.setenv(CHROMIUM_PATH_ENV, "/missing/from/env/chrome")
    assert resolve_chromium_executable(settings=SimpleNamespace()) is None
    monkeypatch.delenv(CHROMIUM_PATH_ENV, raising=False)


# -------------------- 真实 Chromium 子进程测试 --------------------


@requires_chromium
def test_real_chromium_passes_rich_animated_svg_canvas_and_fixed_slide_pages() -> None:
    reset_render_check_stats()

    # 1. 含入场 CSS 动画与内联交互脚本的标准展示页
    rich_html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>季度业务汇报</title>
  <style>
    body { margin: 0; font-family: sans-serif; background: #0f172a; color: #f8fafc; }
    .hero { padding: 64px; opacity: 0; animation: enter 80ms ease-out forwards; }
    @keyframes enter { to { opacity: 1; } }
  </style>
</head>
<body>
  <main class="hero">
    <h1 id="headline">2026 Q3 核心增长指标</h1>
    <p>营收同比增长 42%，净留存率达 118%。</p>
  </main>
  <script>
    document.getElementById("headline").dataset.ready = "1";
  </script>
</body>
</html>"""

    # 2. 纯 SVG 视觉海报页（无普通 HTML 文本，仅靠 SVG 图形展示）
    svg_poster_html = """<!doctype html>
<html><body style="margin:0;background:#111">
  <svg viewBox="0 0 1200 800" style="width:100vw;height:100vh;display:block">
    <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#38bdf8"/><stop offset="100%" stop-color="#6366f1"/>
    </linearGradient></defs>
    <rect width="1200" height="800" fill="url(#g)"/>
    <circle cx="600" cy="400" r="220" fill="#ffffff" fill-opacity="0.2"/>
  </svg>
</body></html>"""

    # 3. 纯 Canvas 数据艺术页
    canvas_html = """<!doctype html>
<html><body style="margin:0">
  <canvas id="stage" width="1200" height="800" style="display:block"></canvas>
  <script>
    const ctx = document.getElementById("stage").getContext("2d");
    ctx.fillStyle = "#10b981";
    ctx.fillRect(0, 0, 1200, 800);
  </script>
</body></html>"""

    # 4. 全屏 fixed 定位幻灯片页（body 文档流高度为 0，但首屏定位容器可见）
    fixed_slide_html = """<!doctype html>
<html><body style="margin:0;overflow:hidden">
  <section style="position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:#fff">
    <h1>第一页：年度产品发布演示</h1>
  </section>
</body></html>"""

    async def run() -> None:
        for html in (rich_html, svg_poster_html, canvas_html, fixed_slide_html):
            res = await check_page_render(html, allow_cdn=False, settle_ms=180, timeout_seconds=10.0)
            assert res.status == "passed", f"unexpected issues: {res.issues} / skip={res.skip_reason}"
            assert res.issues == []
            assert res.rss_mb > 0
            assert res.duration_ms > 0

    asyncio.run(run())
    stats = render_check_stats()
    assert stats["render_check_procs"] == 0
    assert stats["render_check_waiting"] == 0
    assert stats["render_check_last_rss_mb"] > 0
    assert _live_render_check_children() == []


@requires_chromium
def test_real_chromium_catches_js_runtime_error_syntax_error_and_aborts_external_urls() -> None:
    reset_render_check_stats()

    # 同时包含一个会被立即 abort 的慢外链图片（验证不会拖满 15s）与第 12 行的未定义函数调用
    broken_js_html = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>报错页</title></head>
<body>
  <h1>业务看板</h1>
  <img src="https://unreachable-tracker.example.invalid/slow.png" width="200" height="120" alt="chart">
  <script>
    const data = [10, 20, 30];
    function boot() {
      renderUndefinedWidget(data);
    }
    boot();
  </script>
</body>
</html>"""

    syntax_err_html = """<!doctype html>
<html><body>
  <h1>语法错误测试</h1>
  <script>
    function broken( {
      return 1;
    }
  </script>
</body></html>"""

    # 验证线上同款 CSP sandbox 生效：无 allow-same-origin 时直接读 localStorage 会触发 SecurityError
    csp_sandbox_err_html = """<!doctype html>
<html><body>
  <h1>沙箱存储报错</h1>
  <script>
    window.localStorage.setItem("k", "v");
  </script>
</body></html>"""

    async def run() -> None:
        t0 = time.perf_counter()
        res_runtime = await check_page_render(broken_js_html, allow_cdn=False, settle_ms=100, timeout_seconds=10.0)
        elapsed = time.perf_counter() - t0
        assert elapsed < 5.0
        assert res_runtime.status == "issues_found"
        assert any(i.kind == "js_error" and "renderUndefinedWidget" in i.summary for i in res_runtime.issues)
        js_item = next(i for i in res_runtime.issues if i.kind == "js_error")
        assert js_item.line == 10

        res_syntax = await check_page_render(syntax_err_html, allow_cdn=False, settle_ms=100, timeout_seconds=10.0)
        assert res_syntax.status == "issues_found"
        assert any(i.kind == "js_error" and "SyntaxError" in i.summary for i in res_syntax.issues)

        res_sandbox = await check_page_render(csp_sandbox_err_html, allow_cdn=False, settle_ms=100, timeout_seconds=10.0)
        assert res_sandbox.status == "issues_found"
        assert any(i.kind == "js_error" and "localStorage" in i.detail for i in res_sandbox.issues)

    asyncio.run(run())
    assert _live_render_check_children() == []


@requires_chromium
def test_real_chromium_detects_true_blank_and_stuck_opacity_zero_pages() -> None:
    reset_render_check_stats()

    empty_bg_html = """<!doctype html>
<html><body style="margin:0;min-height:100vh;background:linear-gradient(135deg,#0f172a,#1e293b)">
  <div class="card" style="padding:40px"></div>
</body></html>"""

    stuck_reveal_html = """<!doctype html>
<html><head><style>
  .reveal { opacity: 0; transform: translateY(24px); }
</style></head>
<body>
  <div class="reveal">
    <h1>本该显现的年度战略标题</h1>
    <p>由于滚动监听未触发，首屏一直保持全透明白屏状态。</p>
  </div>
</body></html>"""

    zero_height_clipped_html = """<!doctype html>
<html><body>
  <div style="height:0;overflow:hidden">
    <h1>被折叠在零高容器里的正文</h1>
  </div>
</body></html>"""

    async def run() -> None:
        res_empty = await check_page_render(empty_bg_html, allow_cdn=False, settle_ms=120, timeout_seconds=10.0)
        assert res_empty.status == "issues_found"
        assert [i.kind for i in res_empty.issues] == ["blank_page"]

        res_stuck = await check_page_render(stuck_reveal_html, allow_cdn=False, settle_ms=120, timeout_seconds=10.0)
        assert res_stuck.status == "issues_found"
        assert [i.kind for i in res_stuck.issues] == ["blank_page"]
        assert "opacity:0" in res_stuck.issues[0].detail

        res_clipped = await check_page_render(zero_height_clipped_html, allow_cdn=False, settle_ms=120, timeout_seconds=10.0)
        assert res_clipped.status == "issues_found"
        assert [i.kind for i in res_clipped.issues] == ["blank_page"]

    asyncio.run(run())
    assert _live_render_check_children() == []


@requires_chromium
def test_real_chromium_infinite_loop_reports_hang_and_reaps_process_group() -> None:
    reset_render_check_stats()

    infinite_loop_html = """<!doctype html>
<html><body>
  <h1>开始计算</h1>
  <script>
    while (true) {}
  </script>
</body></html>"""

    async def run() -> None:
        started = time.perf_counter()
        res = await check_page_render(
            infinite_loop_html,
            allow_cdn=False,
            settle_ms=100,
            timeout_seconds=2.2,
        )
        elapsed = time.perf_counter() - started
        assert elapsed < 5.0
        assert res.status == "issues_found"
        assert any(i.kind == "hang" for i in res.issues)

    asyncio.run(run())
    assert render_check_stats()["render_check_procs"] == 0
    assert _live_render_check_children() == []


@requires_chromium
def test_cancellation_kills_chromium_process_group_immediately() -> None:
    reset_render_check_stats()

    tmp_root = Path("/tmp")
    before_entries = {
        p.name
        for p in tmp_root.iterdir()
        if p.name.startswith(("sp-render-ipc-", "playwright_chromiumdev_profile-", "playwright-artifacts-"))
    }
    hung_html = "<!doctype html><html><body><script>while(true){}</script></body></html>"

    async def run() -> None:
        task = asyncio.create_task(
            check_page_render(
                hung_html,
                allow_cdn=False,
                timeout_seconds=25.0,
                simulate_mode="hang_after_nav",
            )
        )
        deadline = time.perf_counter() + 8.0
        while render_check_stats()["render_check_procs"] == 0:
            assert time.perf_counter() < deadline
            await asyncio.sleep(0.02)
        # 给 Chromium 真正拉起并创建临时 profile 一点时间
        await asyncio.sleep(0.35)

        t0 = time.perf_counter()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert time.perf_counter() - t0 < 1.0

    asyncio.run(run())
    assert render_check_stats()["render_check_procs"] == 0
    assert _live_render_check_children() == []
    after_entries = {
        p.name
        for p in tmp_root.iterdir()
        if p.name.startswith(("sp-render-ipc-", "playwright_chromiumdev_profile-", "playwright-artifacts-"))
    }
    assert after_entries - before_entries == set()


def test_missing_browser_queue_overflow_and_child_errors_smoothly_skip(tmp_path: Path) -> None:
    reset_render_check_stats()

    fake_bin = tmp_path / "fake-chrome"
    fake_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bin.chmod(0o755)

    sample_html = "<!doctype html><html><body><h1>你好</h1></body></html>"

    async def run() -> None:
        # 1. 浏览器路径不存在 -> 立即返回 skipped，不抛错
        res_missing = await check_page_render(sample_html, chromium_path="/no/such/browser/binary")
        assert res_missing.status == "skipped"
        assert res_missing.skip_reason == "chromium_not_found"

        # 2. 子进程异常崩溃 -> 返回 skipped，不阻断发布
        res_crash = await check_page_render(
            sample_html,
            chromium_path=str(fake_bin),
            simulate_mode="crash",
            timeout_seconds=5.0,
        )
        assert res_crash.status == "skipped"
        assert res_crash.skip_reason is not None and "subprocess_error" in res_crash.skip_reason

        # 3. 浏览器尚未进入导航阶段就卡死超时 -> 记为 skipped 而非误判用户页面 hang
        res_boot_timeout = await check_page_render(
            sample_html,
            chromium_path=str(fake_bin),
            simulate_mode="hang_before_launch",
            timeout_seconds=0.35,
        )
        assert res_boot_timeout.status == "skipped"
        assert res_boot_timeout.skip_reason is not None and "browser_launch_timeout" in res_boot_timeout.skip_reason

        # 4. 并发信号量与排队上限熔断：concurrency=1, max_queue=1 时第 3 个请求立即 skipped
        holder = asyncio.create_task(
            check_page_render(
                sample_html,
                chromium_path=str(fake_bin),
                concurrency=1,
                max_queue=1,
                timeout_seconds=20.0,
                simulate_mode="hang_before_launch",
            )
        )
        deadline = time.perf_counter() + 5.0
        while render_check_stats()["render_check_procs"] == 0:
            assert time.perf_counter() < deadline
            await asyncio.sleep(0.01)

        waiter = asyncio.create_task(
            check_page_render(
                sample_html,
                chromium_path=str(fake_bin),
                concurrency=1,
                max_queue=1,
                timeout_seconds=20.0,
            )
        )
        while render_check_stats()["render_check_waiting"] == 0:
            assert time.perf_counter() < deadline
            await asyncio.sleep(0.01)

        t0 = time.perf_counter()
        overflow = await check_page_render(
            sample_html,
            chromium_path=str(fake_bin),
            concurrency=1,
            max_queue=1,
            timeout_seconds=20.0,
        )
        assert time.perf_counter() - t0 < 0.1
        assert overflow.status == "skipped"
        assert overflow.skip_reason == "queue_full"

        waiter.cancel()
        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        with pytest.raises(asyncio.CancelledError):
            await holder

    asyncio.run(run())
    stats = render_check_stats()
    assert stats["render_check_procs"] == 0
    assert stats["render_check_waiting"] == 0
    assert _live_render_check_children() == []


@requires_chromium
def test_real_chromium_slow_allowed_cdn_does_not_false_positive_as_hang(monkeypatch: pytest.MonkeyPatch) -> None:
    """当白名单 CDN 网络响应极慢拖住 window.onload、但主线程与首屏正文正常时，不应误判为脚本死循环 hang。"""
    import socketserver
    import threading
    from http.server import BaseHTTPRequestHandler

    from app.core.config import get_settings

    release_evt = threading.Event()

    class _SlowCdnHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            release_evt.wait(timeout=8.0)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.end_headers()
                self.wfile.write(b"/* delayed module */\n")
            except OSError:
                pass

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    class _ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with _ThreadedServer(("127.0.0.1", 0), _SlowCdnHandler) as srv:
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{port}"
            monkeypatch.setenv("GENERATED_PAGE_CDN_ALLOWLIST", origin)
            get_settings.cache_clear()
            csp = (
                f"sandbox allow-scripts; default-src 'none'; "
                f"script-src 'unsafe-inline' {origin}; style-src 'unsafe-inline'"
            )
            html = f"""<!doctype html>
<html><body>
  <h1>Three.js 粒子背景展示页</h1>
  <p>主体文案早已渲染完毕，仅外链装饰模块因跨境 CDN 缓慢未完成加载。</p>
  <script type="module" src="{origin}/slow-three-addon.js"></script>
</body></html>"""

            res = asyncio.run(
                check_page_render(
                    html,
                    allow_cdn=True,
                    csp=csp,
                    settle_ms=100,
                    timeout_seconds=3.2,
                )
            )
            assert res.status == "passed", f"unexpected issues: {res.issues} / skip={res.skip_reason}"
            assert res.issues == []
        finally:
            release_evt.set()
            srv.shutdown()
            get_settings.cache_clear()
    assert _live_render_check_children() == []

