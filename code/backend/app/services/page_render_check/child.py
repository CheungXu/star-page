"""独立子进程内用无头 Chromium + Playwright 对清洗后的 HTML 做发布前渲染检查。

用法（由父进程 `runner.py` 通过 `subprocess.Popen` 拉起）：
    python -m app.services.page_render_check.child <input_html_path> <config_json_path> <output_json_path>

注意：
- 严禁在本进程设置 `RLIMIT_AS`！V8 / Chromium 会预留数十 GB 的虚拟地址空间（PROT_NONE），
  一旦设 `RLIMIT_AS` 浏览器必定无法启动；内存由 `--js-flags=--max-old-space-size=256`、
  严格超时强杀进程组以及宿主机 worker 的 systemd `MemoryMax` 兜底。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.services.page_render_check.types import (
    MAIN_DOCUMENT_URL,
    RenderIssue,
    build_blank_page_issue,
    build_hang_issue,
    build_js_error_issue,
)

NICE_INCREMENT = 10
MAX_JS_ISSUES = 5
DEFAULT_VIEWPORT_WIDTH = 1200
DEFAULT_VIEWPORT_HEIGHT = 800
DEFAULT_NAVIGATION_TIMEOUT_MS = 13500
DEFAULT_SETTLE_MS = 1300

CHROMIUM_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--js-flags=--max-old-space-size=256",
    "--disable-background-networking",
    "--disable-breakpad",
    "--disable-crash-reporter",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-sync",
    "--mute-audio",
    "--no-first-run",
]

# 在浏览器页面上下文中评估是否白屏：
# - 识别 display:none / visibility:hidden / 祖先链累积 opacity 接近 0 / 零尺寸 overflow:hidden 剪裁；
# - 同时识别非零尺寸可见 svg / canvas / img / video，避免误伤纯 SVG/Canvas/海报视觉页；
# - 兼顾全屏幻灯片（position: fixed / absolute 脱离文档流导致 body 高度为 0 但画面正常可见）。
_BLANK_PAGE_EVAL_SCRIPT = """() => {
  const root = document.documentElement;
  const body = document.body;
  if (!root || !body) {
    return { isBlank: true, reason: "缺少 html 或 body 节点" };
  }

  const visCache = new WeakMap();
  const posCache = new WeakMap();

  function checkElementChain(el) {
    if (!el || el.nodeType !== 1) {
      return { visible: false, outOfFlow: false };
    }
    const cached = visCache.get(el);
    if (cached !== undefined) {
      return { visible: cached, outOfFlow: posCache.get(el) || false };
    }

    let cur = el;
    let opacity = 1.0;
    let outOfFlow = false;
    let escapedToViewport = false;
    let escapedNormalFlow = false;
    let visible = true;

    while (cur && cur.nodeType === 1) {
      const style = window.getComputedStyle(cur);
      if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") {
        visible = false;
        break;
      }
      const op = parseFloat(style.opacity);
      if (!Number.isNaN(op)) {
        opacity *= op;
        if (opacity < 0.02) {
          visible = false;
          break;
        }
      }
      // 普通容器（非 html/body，因 html/body 的 overflow 传播到视口而非 0 高盒）若开启隐藏溢出且尺寸接近 0，会裁掉未脱离其包含块的子元素
      if (cur !== el && cur !== body && cur !== root && !escapedToViewport) {
        const canClip = !escapedNormalFlow || style.position !== "static";
        if (canClip) {
          const ovX = style.overflowX;
          const ovY = style.overflowY;
          const clipsX = ovX === "hidden" || ovX === "clip";
          const clipsY = ovY === "hidden" || ovY === "clip";
          if (clipsX || clipsY) {
            const r = cur.getBoundingClientRect();
            if ((clipsX && r.width < 2) || (clipsY && r.height < 2)) {
              visible = false;
              break;
            }
          }
        }
      }
      if (style.position === "fixed") {
        outOfFlow = true;
        escapedToViewport = true;
      } else if (style.position === "absolute") {
        outOfFlow = true;
        escapedNormalFlow = true;
      }
      cur = cur.parentElement;
    }

    visCache.set(el, visible);
    posCache.set(el, outOfFlow);
    return { visible, outOfFlow };
  }

  const SKIP_TEXT_PARENTS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "HEAD", "TITLE", "META"]);
  let visibleTextChars = 0;
  let visibleOutOfFlowContent = false;
  const range = document.createRange();

  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    const raw = node.nodeValue || "";
    const trimmed = raw.trim();
    if (trimmed.length > 0) {
      const parent = node.parentElement;
      if (parent && !SKIP_TEXT_PARENTS.has(parent.tagName)) {
        const info = checkElementChain(parent);
        if (info.visible) {
          range.selectNodeContents(node);
          const rect = range.getBoundingClientRect();
          if (rect.width >= 1 && rect.height >= 1) {
            visibleTextChars += trimmed.length;
            if (info.outOfFlow) {
              visibleOutOfFlowContent = true;
            }
            if (visibleTextChars >= 64 && visibleOutOfFlowContent) {
              break;
            }
          }
        }
      }
    }
    node = walker.nextNode();
  }

  let visibleMediaCount = 0;
  const mediaNodes = body.querySelectorAll("svg, canvas, img, video");
  for (let i = 0; i < mediaNodes.length; i++) {
    const el = mediaNodes[i];
    const tag = el.tagName;
    if (tag === "IMG") {
      const src = (el.getAttribute("src") || "").trim();
      const srcset = (el.getAttribute("srcset") || "").trim();
      if (!src && !srcset) {
        continue;
      }
    }
    const info = checkElementChain(el);
    if (!info.visible) {
      continue;
    }
    const rect = el.getBoundingClientRect();
    if (rect.width >= 4 && rect.height >= 4) {
      visibleMediaCount += 1;
      if (info.outOfFlow) {
        visibleOutOfFlowContent = true;
      }
    }
  }

  const bodyRect = body.getBoundingClientRect();
  const rootRect = root.getBoundingClientRect();
  const maxFlowHeight = Math.max(bodyRect.height || 0, rootRect.height || 0);

  if (!visibleOutOfFlowContent && maxFlowHeight < 10) {
    // 兜底扫描是否存在尺寸可观的可见 fixed/absolute 视觉容器
    const allEls = body.querySelectorAll("*");
    const limit = Math.min(allEls.length, 1500);
    for (let i = 0; i < limit; i++) {
      const el = allEls[i];
      const st = window.getComputedStyle(el);
      if (st.position === "fixed" || st.position === "absolute") {
        const info = checkElementChain(el);
        if (info.visible) {
          const r = el.getBoundingClientRect();
          if (r.width >= 20 && r.height >= 20 && (visibleTextChars > 0 || visibleMediaCount > 0)) {
            visibleOutOfFlowContent = true;
            break;
          }
        }
      }
    }
  }

  const rawTextLen = (body.textContent || "").replace(/\\s+/g, "").length;
  const hasVisibleContent = visibleTextChars > 0 || visibleMediaCount > 0;
  const collapsedHeight = maxFlowHeight < 10 && !visibleOutOfFlowContent;

  if (!hasVisibleContent) {
    let reason = "页面 body 内未检测到可见文字，也无非零尺寸的可见 svg/canvas/img/video 元素";
    if (rawTextLen > 0) {
      reason += `（DOM 含约 ${rawTextLen} 个字符，但因样式 opacity:0 / visibility:hidden / display:none 或零尺寸剪裁而不可见）`;
    }
    return {
      isBlank: true,
      reason,
      visibleTextChars,
      visibleMediaCount,
      maxFlowHeight: Math.round(maxFlowHeight),
    };
  }

  if (collapsedHeight) {
    return {
      isBlank: true,
      reason: `页面 body/documentElement 渲染高度接近 0（${Math.round(maxFlowHeight)}px）且无可见固定/绝对定位内容`,
      visibleTextChars,
      visibleMediaCount,
      maxFlowHeight: Math.round(maxFlowHeight),
    };
  }

  return {
    isBlank: false,
    reason: "",
    visibleTextChars,
    visibleMediaCount,
    maxFlowHeight: Math.round(maxFlowHeight),
  };
}"""


def _apply_child_priority() -> None:
    """仅降低子进程 CPU 调度优先级；切勿对 Chromium 子进程设置 RLIMIT_AS。"""
    try:
        os.nice(NICE_INCREMENT)
    except OSError:
        pass


def _write_json_atomic(output_path: Path, payload: dict[str, Any]) -> None:
    tmp_path = output_path.with_name(f"{output_path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, output_path)


def _write_stage(stage_path_str: str | None, stage: str) -> None:
    if not stage_path_str:
        return
    try:
        Path(stage_path_str).write_text(stage, encoding="utf-8")
    except OSError:
        pass


def _resolve_csp(config: dict[str, Any]) -> str:
    csp = str(config.get("csp") or "").strip()
    if csp:
        return csp
    from app.services.page_delivery import build_page_csp

    return build_page_csp()


def _run_playwright_check(
    html_bytes: bytes,
    config: dict[str, Any],
    *,
    child_started: float | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    started_mono = child_started if child_started is not None else time.monotonic()
    chromium_path = str(config["chromium_path"])
    csp_header = _resolve_csp(config)
    allow_cdn = bool(config.get("allow_cdn", True))
    allowed_hosts = {str(h).strip().lower() for h in (config.get("allowed_cdn_hosts") or []) if str(h).strip()}
    viewport_width = int(config.get("viewport_width") or DEFAULT_VIEWPORT_WIDTH)
    viewport_height = int(config.get("viewport_height") or DEFAULT_VIEWPORT_HEIGHT)
    navigation_timeout_ms = max(200, int(config.get("navigation_timeout_ms") or DEFAULT_NAVIGATION_TIMEOUT_MS))
    settle_ms = max(0, int(config.get("settle_ms") if config.get("settle_ms") is not None else DEFAULT_SETTLE_MS))
    timeout_seconds = float(config.get("timeout_seconds") or round(navigation_timeout_ms / 1000, 1))
    total_budget_ms = max(250, int(timeout_seconds * 1000))
    stage_path = config.get("stage_path")
    simulate_mode = str(config.get("simulate_mode") or "")

    if simulate_mode == "hang_before_launch":
        time.sleep(3600)
    elif simulate_mode == "crash":
        raise RuntimeError("simulated child crash")

    js_issues: list[RenderIssue] = []
    seen_js_keys: set[tuple[str, int | None, int | None]] = set()
    suppressed_js_count = 0
    pending_cdn_urls: set[str] = set()

    def on_page_error(exc: Any) -> None:
        nonlocal suppressed_js_count
        name = str(getattr(exc, "name", "") or "")
        message = str(getattr(exc, "message", "") or str(exc) or "")
        stack = str(getattr(exc, "stack", "") or "")
        issue = build_js_error_issue(name=name, message=message, stack=stack)
        dedup_key = (issue.summary, issue.line, issue.column)
        if dedup_key in seen_js_keys:
            return
        seen_js_keys.add(dedup_key)
        if len(js_issues) < MAX_JS_ISSUES:
            js_issues.append(issue)
        else:
            suppressed_js_count += 1

    def handle_route(route: Any) -> None:
        request = route.request
        url = request.url
        if url == MAIN_DOCUMENT_URL:
            route.fulfill(
                status=200,
                headers={
                    "Content-Type": "text/html; charset=utf-8",
                    "Content-Security-Policy": csp_header,
                    "X-Content-Type-Options": "nosniff",
                },
                body=html_bytes,
            )
            return

        if allow_cdn and allowed_hosts:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if parsed.scheme in {"https", "http"} and host in allowed_hosts:
                pending_cdn_urls.add(url)
                route.continue_()
                return

        route.abort()

    def _collect_js_and_blank_issues(blank_eval: Any) -> None:
        if suppressed_js_count > 0 and js_issues:
            last = js_issues[-1]
            js_issues[-1] = RenderIssue(
                kind=last.kind,
                summary=last.summary,
                detail=f"{last.detail}\n（另有 {suppressed_js_count} 条未捕获异常已省略）".strip(),
                line=last.line,
                column=last.column,
            )
        issues.extend(js_issues)
        if isinstance(blank_eval, dict) and blank_eval.get("isBlank"):
            issues.append(build_blank_page_issue(str(blank_eval.get("reason") or "")))

    issues: list[RenderIssue] = []
    result_payload: dict[str, Any] | None = None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=chromium_path,
            headless=True,
            args=CHROMIUM_LAUNCH_ARGS,
        )
        try:
            context = browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
            )
            context.route("**/*", handle_route)
            page = context.new_page()
            page.on("pageerror", on_page_error)
            page.on("dialog", lambda dialog: dialog.dismiss())
            page.on("requestfinished", lambda req: pending_cdn_urls.discard(req.url))
            page.on("requestfailed", lambda req: pending_cdn_urls.discard(req.url))

            _write_stage(stage_path, "navigating")
            if simulate_mode == "hang_after_nav":
                time.sleep(3600)

            # 扣除浏览器冷启动已消耗的时间，确保 goto + settle + evaluate 在父进程硬超时前留足余量
            elapsed_before_nav_ms = int((time.monotonic() - started_mono) * 1000)
            if total_budget_ms >= 6000:
                post_nav_reserve_ms = min(settle_ms + 3000, int(total_budget_ms * 0.45))
            else:
                post_nav_reserve_ms = min(400 + settle_ms // 2, int(total_budget_ms * 0.3))
            effective_nav_ms = max(
                250,
                min(navigation_timeout_ms, total_budget_ms - elapsed_before_nav_ms - post_nav_reserve_ms),
            )

            timed_out = False
            try:
                page.goto(MAIN_DOCUMENT_URL, wait_until="load", timeout=effective_nav_ms)
            except PlaywrightTimeoutError:
                timed_out = True
            except PlaywrightError as exc:
                err_text = str(exc)
                if "Timeout" in err_text or "timed out" in err_text.lower():
                    timed_out = True
                else:
                    raise

            _write_stage(stage_path, "evaluating")
            if timed_out:
                # 若存在未完成的白名单 CDN 外部请求，先探测页面主线程是否仍健康响应：
                # 避免跨境 CDN 网络偶发缓慢拖住 window.onload 时，把主线程正常的页面误判成死循环 hang。
                recovered_blank_eval: Any | None = None
                if pending_cdn_urls:
                    try:
                        page.set_default_timeout(1200)
                        recovered_blank_eval = page.evaluate(_BLANK_PAGE_EVAL_SCRIPT)
                        if isinstance(recovered_blank_eval, dict) and recovered_blank_eval.get("isBlank"):
                            elapsed_ms = int((time.monotonic() - started_mono) * 1000)
                            extra_settle_ms = max(0, min(settle_ms, total_budget_ms - elapsed_ms - 1800))
                            if extra_settle_ms > 0:
                                page.wait_for_timeout(extra_settle_ms)
                                recovered_blank_eval = page.evaluate(_BLANK_PAGE_EVAL_SCRIPT)
                    except (PlaywrightTimeoutError, PlaywrightError):
                        recovered_blank_eval = None

                if recovered_blank_eval is not None:
                    _collect_js_and_blank_issues(recovered_blank_eval)
                else:
                    issues.extend(js_issues)
                    extra = ""
                    if pending_cdn_urls:
                        sample_url = next(iter(pending_cdn_urls))
                        extra = f"页面在 {timeout_seconds:g} 秒内未完成加载（存在未完成外部资源 {sample_url} 且主线程无响应）"
                    issues.append(build_hang_issue(timeout_seconds=timeout_seconds, detail=extra))
            else:
                elapsed_after_nav_ms = int((time.monotonic() - started_mono) * 1000)
                eval_reserve_ms = 2800 if total_budget_ms >= 6000 else min(900, int(total_budget_ms * 0.35))
                effective_settle_ms = max(0, min(settle_ms, total_budget_ms - elapsed_after_nav_ms - eval_reserve_ms))
                if effective_settle_ms > 0:
                    page.wait_for_timeout(effective_settle_ms)

                try:
                    page.set_default_timeout(1500)
                    blank_eval = page.evaluate(_BLANK_PAGE_EVAL_SCRIPT)
                except PlaywrightTimeoutError:
                    issues.extend(js_issues)
                    issues.append(
                        build_hang_issue(
                            timeout_seconds=timeout_seconds,
                            detail="页面加载后主线程被死循环或长任务锁死，无法响应首屏检查",
                        )
                    )
                else:
                    _collect_js_and_blank_issues(blank_eval)

            status = "issues_found" if issues else "passed"
            result_payload = {
                "ok": True,
                "status": status,
                "issues": [issue.to_dict() for issue in issues],
            }
            _write_stage(stage_path, "closing")
            if output_path is not None:
                _write_json_atomic(output_path, result_payload)
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass

    assert result_payload is not None
    return result_payload


def _child_main(argv: list[str]) -> int:
    child_started = time.monotonic()
    if len(argv) < 4:
        print(
            "usage: python -m app.services.page_render_check.child <input_html_path> <config_json_path> <output_json_path>",
            file=sys.stderr,
        )
        return 2

    input_path = Path(argv[1])
    config_path = Path(argv[2])
    output_path = Path(argv[3])

    _apply_child_priority()

    try:
        html_bytes = input_path.read_bytes()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        payload = _run_playwright_check(
            html_bytes,
            config,
            child_started=child_started,
            output_path=output_path,
        )
        if not output_path.exists():
            _write_json_atomic(output_path, payload)
        return 0
    except Exception as exc:  # noqa: BLE001
        try:
            _write_json_atomic(
                output_path,
                {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        except Exception:  # noqa: BLE001
            pass
        print(f"[page_render_check.child] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv))
