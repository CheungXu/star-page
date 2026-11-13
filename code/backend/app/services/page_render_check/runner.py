"""父进程异步调度器：信号量限流、排队上限熔断、独立子进程执行、pidfd + wait4 无阻塞回收。

设计要点（对齐 `document_parser/child.py` 范式）：
- 单 worker 默认最多同时跑 2 个 Chromium 检查子进程；当等待队列已满时立即返回 `status="skipped"`，绝不阻塞或抛错。
- 通过私有临时目录传递输入 HTML、配置 JSON 与原子输出 JSON，子进程日志重定向至临时文件避免管道死锁。
- 用 `subprocess.Popen(..., start_new_session=True)` 启动独立会话/进程组，借由 `os.pidfd_open` + `loop.add_reader`
  在事件循环上零线程等待退出，再以 `os.wait4` 采集真实峰值内存 `ru_maxrss`。
- 超时或收到 `asyncio.CancelledError` 时立即 `os.killpg(proc.pid, signal.SIGKILL)` 强杀整个进程组并 `wait4` 回收，
  杜绝残留 Chromium 子进程。
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from weakref import WeakKeyDictionary

from app.core.config import get_settings
from app.services.page_delivery import build_page_csp
from app.services.page_render_check.types import (
    RenderCheckResult,
    RenderIssue,
    build_hang_issue,
)

DEFAULT_CONCURRENCY = 2
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_QUEUE = 8
DEFAULT_SETTLE_MS = 1300
CHROMIUM_PATH_ENV = "PAGE_RENDER_CHECK_CHROMIUM_PATH"

_BACKEND_ROOT = str(Path(__file__).resolve().parents[3])
_CHROMIUM_SYSTEM_BINARIES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")

_semaphores: WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = WeakKeyDictionary()
_waiting_count = 0
_running_procs = 0
_last_rss_mb = 0.0
_last_duration_ms = 0.0
_timeout_count = 0
_skipped_count = 0
_issues_count = 0


def render_check_stats() -> dict[str, int | float]:
    """供监控日志或测试读取的渲染检查运行指标。"""
    return {
        "render_check_waiting": _waiting_count,
        "render_check_procs": _running_procs,
        "render_check_last_rss_mb": _last_rss_mb,
        "render_check_last_ms": _last_duration_ms,
        "render_check_timeouts": _timeout_count,
        "render_check_skipped": _skipped_count,
        "render_check_issues": _issues_count,
    }


def reset_render_check_stats() -> None:
    """测试用：重置累计指标。"""
    global _waiting_count, _running_procs, _last_rss_mb, _last_duration_ms
    global _timeout_count, _skipped_count, _issues_count
    _waiting_count = 0
    _running_procs = 0
    _last_rss_mb = 0.0
    _last_duration_ms = 0.0
    _timeout_count = 0
    _skipped_count = 0
    _issues_count = 0


def _is_executable_file(path_str: str) -> bool:
    try:
        return os.path.isfile(path_str) and os.access(path_str, os.X_OK)
    except OSError:
        return False


def _resolve_explicit_binary(candidate: str) -> str | None:
    cleaned = candidate.strip()
    if not cleaned:
        return None
    if os.path.sep in cleaned or cleaned.startswith("."):
        expanded = os.path.expanduser(cleaned)
        return expanded if _is_executable_file(expanded) else None
    found = shutil.which(cleaned)
    return found if found and _is_executable_file(found) else None


def _chromium_revision_sort_key(path_str: str) -> tuple[int, str]:
    match = re.search(r"chromium-(\d+)", path_str)
    rev = int(match.group(1)) if match else 0
    return (rev, path_str)


def resolve_chromium_executable(
    chromium_path: str | None = None,
    *,
    settings: Any | None = None,
) -> str | None:
    """探测可用的 Chromium 可执行文件路径。

    - 若调用方入参、Settings 配置项或环境变量 `PAGE_RENDER_CHECK_CHROMIUM_PATH` 显式给出了具体路径（非空且非 `auto`），
      则严格校验该路径，不存在时直接返回 None（不会静默回退到其他浏览器，便于确定性测试与排障）。
    - 默认（未配置或设为 `auto`）依次探测 Playwright 缓存目录 `/root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome`
      与系统安装的 `chromium` / `google-chrome`。
    """
    if chromium_path is not None and chromium_path.strip().lower() not in {"", "auto"}:
        return _resolve_explicit_binary(chromium_path)

    cfg = settings if settings is not None else get_settings()
    configured = getattr(cfg, "page_render_check_chromium_path", None)
    if isinstance(configured, str) and configured.strip().lower() not in {"", "auto"}:
        return _resolve_explicit_binary(configured)

    env_val = os.environ.get(CHROMIUM_PATH_ENV)
    if env_val is not None and env_val.strip().lower() not in {"", "auto"}:
        return _resolve_explicit_binary(env_val)

    cache_roots: list[Path] = []
    pw_browsers = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if pw_browsers and pw_browsers != "0":
        cache_roots.append(Path(pw_browsers))
    cache_roots.append(Path("/root/.cache/ms-playwright"))
    home_cache = Path.home() / ".cache" / "ms-playwright"
    if home_cache not in cache_roots:
        cache_roots.append(home_cache)

    sub_patterns = (
        "chromium-*/chrome-linux64/chrome",
        "chromium-*/chrome-linux/chrome",
        "chromium_headless_shell-*/chrome-linux64/chrome-headless-shell",
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
    )
    for root in cache_roots:
        if not root.is_dir():
            continue
        matches: list[str] = []
        for pattern in sub_patterns:
            matches.extend(glob.glob(str(root / pattern)))
        for candidate in sorted(set(matches), key=_chromium_revision_sort_key, reverse=True):
            if _is_executable_file(candidate):
                return candidate

    for binary_name in _CHROMIUM_SYSTEM_BINARIES:
        found = shutil.which(binary_name)
        if found and _is_executable_file(found):
            return found

    return None


def extract_allowed_cdn_hosts(settings: Any | None = None) -> list[str]:
    """从配置 `generated_page_cdn_sources` 提取小写主机名列表。"""
    cfg = settings if settings is not None else get_settings()
    sources = getattr(cfg, "generated_page_cdn_sources", ()) or ()
    hosts: list[str] = []
    seen: set[str] = set()
    for entry in sources:
        raw = str(entry or "").strip()
        if not raw:
            continue
        parsed = urlparse(raw if "//" in raw else f"https://{raw}")
        host = (parsed.hostname or "").lower()
        if host and host not in seen:
            seen.add(host)
            hosts.append(host)
    return hosts


def _get_semaphore(concurrency: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    limit = max(1, concurrency)
    cached = _semaphores.get(loop)
    if cached is None or cached[0] != limit:
        sem = asyncio.Semaphore(limit)
        _semaphores[loop] = (limit, sem)
        return sem
    return cached[1]


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return


def _read_vmhwm_kb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("VmHWM:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1])
    except OSError:
        pass
    return 0


def _kill_render_tree(root_pid: int, ipc_token: str) -> int:
    """强杀 child.py 进程组以及 Playwright 以 detached/setsid 独立拉起的 Chromium 进程组，返回观测到的最大 VmHWM（KB）。"""
    pgids: set[int] = {root_pid}
    pids: set[int] = {root_pid}
    max_hwm_kb = _read_vmhwm_kb(root_pid)
    token_bytes = ipc_token.encode("utf-8", errors="ignore")
    if token_bytes and os.path.isdir("/proc"):
        try:
            for entry in os.scandir("/proc"):
                if not entry.name.isdigit():
                    continue
                pid = int(entry.name)
                try:
                    cmdline = Path(f"/proc/{entry.name}/cmdline").read_bytes()
                    if token_bytes not in cmdline:
                        continue
                    pids.add(pid)
                    pgids.add(os.getpgid(pid))
                    max_hwm_kb = max(max_hwm_kb, _read_vmhwm_kb(pid))
                except OSError:
                    continue
        except OSError:
            pass

    for pgid in pgids:
        if pgid > 1:
            _kill_process_group(pgid)
    for pid in pids:
        if pid > 1:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                continue
    return max_hwm_kb


async def _wait4_async(proc: subprocess.Popen[bytes]) -> tuple[int, int]:
    """用 pidfd + loop.add_reader 无阻塞等待子进程退出，再用 os.wait4 取退出码和峰值 RSS（KB）。"""
    loop = asyncio.get_running_loop()
    pid = proc.pid
    pidfd: int | None = None
    try:
        pidfd = os.pidfd_open(pid, 0)
    except (AttributeError, OSError):
        pidfd = None

    if pidfd is not None:
        done: asyncio.Future[None] = loop.create_future()
        loop.add_reader(pidfd, lambda: None if done.done() else done.set_result(None))
        try:
            await done
        finally:
            loop.remove_reader(pidfd)
            os.close(pidfd)
        _, status, rusage = os.wait4(pid, 0)
    else:
        _, status, rusage = await asyncio.to_thread(os.wait4, pid, 0)

    exitcode = os.waitstatus_to_exitcode(status)
    proc.returncode = exitcode
    return exitcode, int(rusage.ru_maxrss)


def _reap_killed(proc: subprocess.Popen[bytes], ipc_token: str) -> int:
    """超时或取消时立即强杀整个进程树（含 Chromium 独立进程组）并 wait4 回收，返回峰值 RSS（KB）。"""
    tree_hwm_kb = _kill_render_tree(proc.pid, ipc_token)
    try:
        _, status, rusage = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        return max(int(rusage.ru_maxrss), tree_hwm_kb)
    except ChildProcessError:
        return tree_hwm_kb


def _read_tail(path: Path, max_bytes: int = 1024) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    return data.decode("utf-8", errors="replace").strip()


def _read_stage(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _cleanup_ipc_dir(ipc_dir: Path) -> None:
    """删除本次检查的私有 IPC 目录；强杀进程组后内核回收 Chromium 打开句柄可能有数毫秒尾延迟，做短暂重试。"""
    for attempt in range(4):
        shutil.rmtree(ipc_dir, ignore_errors=True)
        if not ipc_dir.exists():
            return
        if attempt < 3:
            time.sleep(0.02)


def _build_child_env(tmp_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "").strip()
    if existing:
        parts = existing.split(os.pathsep)
        if _BACKEND_ROOT not in parts:
            env["PYTHONPATH"] = f"{_BACKEND_ROOT}{os.pathsep}{existing}"
    else:
        env["PYTHONPATH"] = _BACKEND_ROOT
    # 将子进程与 Playwright/Chromium 的 TMPDIR 收敛进本次私有 IPC 目录，SIGKILL 强杀后也能由父进程一并清空，不泄漏 /tmp profile。
    tmp_str = str(tmp_dir)
    env["TMPDIR"] = tmp_str
    env["TMP"] = tmp_str
    env["TEMP"] = tmp_str
    return env


def _compute_navigation_timeout_ms(timeout_seconds: float, settle_ms: int) -> int:
    """为子进程 `page.goto` 预留略短于父进程硬超时的导航预算，使常见死循环能先写出详细诊断。"""
    total_ms = max(250, int(timeout_seconds * 1000))
    # 给浏览器冷启动与关闭留一定余量，但至少保留总时长的 65%
    reserve_ms = min(1500 + settle_ms // 2, int(total_ms * 0.35))
    return max(200, total_ms - reserve_ms)


async def check_page_render(
    html: str,
    *,
    allow_cdn: bool = True,
    timeout_seconds: float | None = None,
    settle_ms: int | None = None,
    concurrency: int | None = None,
    max_queue: int | None = None,
    chromium_path: str | None = None,
    csp: str | None = None,
    simulate_mode: str | None = None,
) -> RenderCheckResult:
    """在独立受限子进程中对清洗后的 HTML 执行无头浏览器渲染检查。

    绝不向调用方抛出业务异常（`asyncio.CancelledError` 除外）：
    浏览器缺失、排队超限或子进程意外报错时一律平滑返回 `status="skipped"`。
    """
    global _waiting_count, _running_procs, _last_rss_mb, _last_duration_ms
    global _timeout_count, _skipped_count, _issues_count

    try:
        settings = get_settings()
        limit_concurrency = max(
            1,
            int(
                concurrency
                if concurrency is not None
                else getattr(settings, "page_render_check_concurrency", DEFAULT_CONCURRENCY)
            ),
        )
        limit_timeout = max(
            0.2,
            float(
                timeout_seconds
                if timeout_seconds is not None
                else getattr(settings, "page_render_check_timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
            ),
        )
        limit_max_queue = max(
            0,
            int(
                max_queue
                if max_queue is not None
                else getattr(settings, "page_render_check_max_queue", DEFAULT_MAX_QUEUE)
            ),
        )
        effective_settle_ms = max(
            0,
            int(
                settle_ms
                if settle_ms is not None
                else getattr(settings, "page_render_check_settle_ms", DEFAULT_SETTLE_MS)
            ),
        )

        resolved_chromium = resolve_chromium_executable(chromium_path, settings=settings)
        if not resolved_chromium:
            _skipped_count += 1
            return RenderCheckResult(status="skipped", skip_reason="chromium_not_found")

        sem = _get_semaphore(limit_concurrency)
        if sem.locked() and _waiting_count >= limit_max_queue:
            _skipped_count += 1
            return RenderCheckResult(status="skipped", skip_reason="queue_full")

        _waiting_count += 1
        try:
            await sem.acquire()
        finally:
            _waiting_count -= 1

        try:
            csp_header = csp if csp is not None else build_page_csp()
            cdn_hosts = extract_allowed_cdn_hosts(settings) if allow_cdn else []
            nav_timeout_ms = _compute_navigation_timeout_ms(limit_timeout, effective_settle_ms)

            ipc_dir = Path(tempfile.mkdtemp(prefix="sp-render-ipc-"))
            try:
                child_tmp_dir = ipc_dir / "tmp"
                child_tmp_dir.mkdir(parents=True, exist_ok=True)
                input_path = ipc_dir / "input.html"
                config_path = ipc_dir / "config.json"
                output_path = ipc_dir / "result.json"
                stage_path = ipc_dir / "stage.txt"
                stderr_path = ipc_dir / "stderr.log"

                input_path.write_text(html, encoding="utf-8")
                config_payload: dict[str, Any] = {
                    "chromium_path": resolved_chromium,
                    "csp": csp_header,
                    "allow_cdn": bool(allow_cdn),
                    "allowed_cdn_hosts": cdn_hosts,
                    "viewport_width": 1200,
                    "viewport_height": 800,
                    "navigation_timeout_ms": nav_timeout_ms,
                    "settle_ms": effective_settle_ms,
                    "timeout_seconds": limit_timeout,
                    "stage_path": str(stage_path),
                }
                if simulate_mode:
                    config_payload["simulate_mode"] = simulate_mode
                config_path.write_text(json.dumps(config_payload, ensure_ascii=False), encoding="utf-8")

                cmd = [
                    sys.executable,
                    "-m",
                    "app.services.page_render_check.child",
                    str(input_path),
                    str(config_path),
                    str(output_path),
                ]

                started = time.perf_counter()
                _running_procs += 1
                proc: subprocess.Popen[bytes] | None = None
                timed_out = False
                exitcode = -1
                maxrss_kb = 0
                try:
                    with stderr_path.open("wb") as err_file:
                        proc = subprocess.Popen(
                            cmd,
                            stdin=subprocess.DEVNULL,
                            stdout=err_file,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                            env=_build_child_env(child_tmp_dir),
                        )
                    try:
                        exitcode, maxrss_kb = await asyncio.wait_for(
                            _wait4_async(proc),
                            timeout=limit_timeout,
                        )
                    except TimeoutError:
                        timed_out = True
                        maxrss_kb = _reap_killed(proc, ipc_dir.name)
                        _timeout_count += 1
                    except asyncio.CancelledError:
                        maxrss_kb = _reap_killed(proc, ipc_dir.name)
                        _last_rss_mb = round(maxrss_kb / 1024, 1)
                        _last_duration_ms = round((time.perf_counter() - started) * 1000, 1)
                        raise
                    else:
                        # 子进程正常退出后仍清扫一次关联进程树，确保任何 Chromium 辅助子进程 100% 不残留。
                        _kill_render_tree(proc.pid, ipc_dir.name)
                finally:
                    _running_procs -= 1

                duration_ms = round((time.perf_counter() - started) * 1000, 1)
                rss_mb = round(maxrss_kb / 1024, 1)
                _last_duration_ms = duration_ms
                _last_rss_mb = rss_mb

                payload: dict[str, Any] | None = None
                if output_path.exists() and output_path.stat().st_size > 0:
                    try:
                        payload = json.loads(output_path.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        payload = None

                if payload is not None and payload.get("ok") is True:
                    issues = [
                        RenderIssue.from_dict(item)
                        for item in (payload.get("issues") or [])
                        if isinstance(item, dict)
                    ]
                    status = "issues_found" if issues else "passed"
                    if issues:
                        _issues_count += 1
                    return RenderCheckResult(
                        status=status,
                        issues=issues,
                        duration_ms=duration_ms,
                        rss_mb=rss_mb,
                    )

                if timed_out:
                    stage = _read_stage(stage_path)
                    if stage == "navigating":
                        _issues_count += 1
                        return RenderCheckResult(
                            status="issues_found",
                            issues=[build_hang_issue(timeout_seconds=limit_timeout)],
                            duration_ms=duration_ms,
                            rss_mb=rss_mb,
                        )
                    _skipped_count += 1
                    return RenderCheckResult(
                        status="skipped",
                        skip_reason=f"browser_launch_timeout ({limit_timeout:g}s)",
                        duration_ms=duration_ms,
                        rss_mb=rss_mb,
                    )

                detail = (
                    (payload or {}).get("error")
                    or _read_tail(stderr_path, max_bytes=400)
                    or f"exitcode={exitcode}"
                )
                _skipped_count += 1
                return RenderCheckResult(
                    status="skipped",
                    skip_reason=f"subprocess_error: {detail}",
                    duration_ms=duration_ms,
                    rss_mb=rss_mb,
                )
            finally:
                _cleanup_ipc_dir(ipc_dir)
        finally:
            sem.release()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 检查器自身故障绝不阻断页面发布
        _skipped_count += 1
        return RenderCheckResult(
            status="skipped",
            skip_reason=f"runner_error: {type(exc).__name__}: {exc}",
        )
