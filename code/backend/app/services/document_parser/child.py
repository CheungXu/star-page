"""独立子进程执行单份文档解析：限制虚拟内存、降低 CPU 优先级，超时或取消时强杀并回收。

用法（由父进程通过 subprocess.Popen 拉起）：
    python -m app.services.document_parser.child <input_path> <output_path> <filename> <extension> <max_memory_mb>
"""

from __future__ import annotations

import asyncio
import os
import pickle
import resource
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from app.services.document_parser.errors import DocumentParseError

EXIT_OOM = 42
NICE_INCREMENT = 10
_OOM_KEYWORDS = ("MemoryError", "Cannot allocate memory", "Out of memory", "bad_alloc")
_UNPICKLE_IN_THREAD_BYTES = 256 * 1024

_semaphores: WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = WeakKeyDictionary()
_waiting_count = 0
_running_procs = 0
_last_rss_mb = 0.0
_last_duration_ms = 0.0
_timeout_count = 0
_oom_count = 0


def parse_subprocess_stats() -> dict[str, int | float]:
    """供 worker 的 [metrics] 日志读取的解析子进程运行指标。"""
    return {
        "parse_waiting": _waiting_count,
        "parse_procs": _running_procs,
        "parse_last_rss_mb": _last_rss_mb,
        "parse_last_ms": _last_duration_ms,
        "parse_timeouts": _timeout_count,
        "parse_ooms": _oom_count,
    }


def reset_parse_subprocess_stats() -> None:
    """测试用：清空累计计数。"""
    global _waiting_count, _running_procs, _last_rss_mb, _last_duration_ms, _timeout_count, _oom_count
    _waiting_count = 0
    _running_procs = 0
    _last_rss_mb = 0.0
    _last_duration_ms = 0.0
    _timeout_count = 0
    _oom_count = 0


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


async def _wait4_async(proc: subprocess.Popen[bytes]) -> tuple[int, int]:
    """用 pidfd 在事件循环上无阻塞等待子进程退出，再用 os.wait4 取退出码和峰值 RSS（KB）。"""
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


def _reap_killed(proc: subprocess.Popen[bytes]) -> int:
    """超时或取消时强杀并立即 wait4 回收，不留僵尸进程，同时返回已消耗的峰值 RSS（KB）。"""
    _kill_process_group(proc.pid)
    try:
        _, status, rusage = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        return int(rusage.ru_maxrss)
    except ChildProcessError:
        return 0


def _read_tail(path: Path, max_bytes: int = 1024) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > max_bytes:
        data = data[-max_bytes:]
    return data.decode("utf-8", errors="replace").strip()


def _load_pickle(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return pickle.load(handle)  # noqa: S301 - 仅读取本进程创建的私有临时文件


async def run_parse_subprocess(
    *,
    filename: str,
    extension: str,
    input_path: Path,
    timeout_seconds: float,
    max_memory_mb: int | None = None,
    concurrency: int | None = None,
    extra_args: list[str] | None = None,
):
    """在受限子进程里解析 input_path，返回 ParsedDocument；失败统一抛 DocumentParseError。"""
    global _waiting_count, _running_procs, _last_rss_mb, _last_duration_ms, _timeout_count, _oom_count
    from app.core.config import get_settings

    settings = get_settings()
    limit_concurrency = concurrency if concurrency is not None else settings.document_parse_concurrency
    limit_memory_mb = max_memory_mb if max_memory_mb is not None else settings.document_parse_max_memory_mb
    sem = _get_semaphore(limit_concurrency)

    _waiting_count += 1
    try:
        await sem.acquire()
    finally:
        _waiting_count -= 1
    try:
        with tempfile.TemporaryDirectory(prefix="sp-parse-ipc-") as ipc_dir_str:
            ipc_dir = Path(ipc_dir_str)
            output_path = ipc_dir / "result.pkl"
            stderr_path = ipc_dir / "stderr.log"
            cmd = [
                sys.executable,
                "-m",
                "app.services.document_parser.child",
                str(input_path),
                str(output_path),
                filename,
                extension,
                str(limit_memory_mb),
            ]
            if extra_args:
                cmd.extend(extra_args)

            started = time.perf_counter()
            _running_procs += 1
            proc: subprocess.Popen[bytes] | None = None
            try:
                with stderr_path.open("wb") as err_file:
                    proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=err_file,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                try:
                    exitcode, maxrss_kb = await asyncio.wait_for(_wait4_async(proc), timeout=timeout_seconds)
                except TimeoutError as exc:
                    maxrss_kb = _reap_killed(proc)
                    _last_rss_mb = round(maxrss_kb / 1024, 1)
                    _last_duration_ms = round((time.perf_counter() - started) * 1000, 1)
                    _timeout_count += 1
                    raise DocumentParseError(
                        f"{filename} 解析超时，请精简后再上传",
                        detail=f"超过 {timeout_seconds}s (rss={_last_rss_mb}MB)",
                    ) from exc
                except asyncio.CancelledError:
                    maxrss_kb = _reap_killed(proc)
                    _last_rss_mb = round(maxrss_kb / 1024, 1)
                    _last_duration_ms = round((time.perf_counter() - started) * 1000, 1)
                    raise
            finally:
                _running_procs -= 1

            _last_rss_mb = round(maxrss_kb / 1024, 1)
            _last_duration_ms = round((time.perf_counter() - started) * 1000, 1)
            stderr_tail = _read_tail(stderr_path)

            payload: dict[str, Any] | None = None
            if output_path.exists() and output_path.stat().st_size > 0:
                try:
                    if output_path.stat().st_size > _UNPICKLE_IN_THREAD_BYTES:
                        payload = await asyncio.to_thread(_load_pickle, output_path)
                    else:
                        payload = _load_pickle(output_path)
                except Exception as exc:  # noqa: BLE001
                    stderr_tail = f"unpickle failed: {type(exc).__name__}: {exc}; {stderr_tail}"

            succeeded = exitcode == 0 and payload is not None and bool(payload.get("ok"))
            if succeeded:
                return payload["doc"]

            is_oom = (
                exitcode == EXIT_OOM
                or exitcode in {-signal.SIGKILL, -signal.SIGABRT, -signal.SIGSEGV, 137}
                or (payload is not None and payload.get("kind") == "oom")
                or any(keyword in stderr_tail for keyword in _OOM_KEYWORDS)
            )
            if is_oom:
                _oom_count += 1
                detail = (payload or {}).get("detail") or stderr_tail or f"exitcode={exitcode}"
                raise DocumentParseError(
                    f"{filename} 文件过大或内容过于复杂，请精简或拆分后再上传",
                    detail=f"oom ({detail}, rss={_last_rss_mb}MB)",
                )

            detail = (payload or {}).get("detail") or stderr_tail or f"exitcode={exitcode}"
            hint = "；扫描件请确认每页都能正常打开" if extension.lower() == ".pdf" else ""
            raise DocumentParseError(
                f"{filename} 解析失败，请确认文件未加密且内容可读取{hint}",
                detail=detail,
            )
    finally:
        sem.release()


def _apply_child_limits(max_memory_mb: int) -> None:
    try:
        os.nice(NICE_INCREMENT)
    except OSError:
        pass
    if max_memory_mb <= 0:
        return
    limit_bytes = max_memory_mb * 1024 * 1024
    try:
        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit_bytes = min(limit_bytes, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, hard))
    except (OSError, ValueError):
        pass


def _write_result(output_path: Path, payload: dict[str, Any]) -> None:
    tmp_path = output_path.with_suffix(".tmp")
    with tmp_path.open("wb") as handle:
        pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
    tmp_path.replace(output_path)


def _child_main(argv: list[str]) -> int:
    if len(argv) < 6:
        print("usage: child.py <input_path> <output_path> <filename> <extension> <max_memory_mb>", file=sys.stderr)
        return 2
    input_path = Path(argv[1])
    output_path = Path(argv[2])
    filename = argv[3]
    extension = argv[4]
    max_memory_mb = int(argv[5])
    mode = argv[6] if len(argv) > 6 else ""

    _apply_child_limits(max_memory_mb)

    try:
        # 供隔离单测确定性模拟卡死与爆内存，不污染正常解析逻辑。
        if mode == "--simulate-hang":
            time.sleep(3600)
        elif mode == "--simulate-oom":
            _ = bytearray(max(max_memory_mb + 256, 512) * 1024 * 1024)
        elif mode == "--simulate-noisy-stderr":
            print("pdfium warning: MemoryError Cannot allocate memory (benign)", file=sys.stderr, flush=True)

        from app.services.document_parser.parse import parse_document

        document = parse_document(filename, extension, input_path)
        _write_result(output_path, {"ok": True, "doc": document})
        return 0
    except MemoryError as exc:
        try:
            _write_result(output_path, {"ok": False, "kind": "oom", "detail": f"MemoryError: {exc}"})
        except Exception:  # noqa: BLE001
            pass
        return EXIT_OOM
    except Exception as exc:  # noqa: BLE001
        try:
            _write_result(output_path, {"ok": False, "kind": "error", "detail": f"{type(exc).__name__}: {exc}"})
        except MemoryError:
            return EXIT_OOM
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(_child_main(sys.argv))
