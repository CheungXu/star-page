"""星页 StarPage · 最终成品真实视觉预览服务

端口 3001（以及 3002、3003 容错）：
直接提供与线上生产 100% 保持一致的最终成品首页界面（preview-final.html），
无任何方案切换栏、无任何模拟控制条或 Demo Badge，纯净还原实际产品。
不影响生产服务 (3000 / 8000)。
"""
from __future__ import annotations

import http.server
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PORT_MAPPING = {
    3001: ("preview-final.html", "星页 StarPage 最终成品体验（方案 5-A 落地版）"),
    3002: ("preview-final.html", "星页 StarPage 最终成品体验（镜像端口）"),
    3003: ("preview-final.html", "星页 StarPage 最终成品体验（镜像端口）"),
}


def make_handler(default_file: str):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def do_GET(self):  # noqa: N802
            if self.path in ("/", ""):
                self.path = "/" + default_file
            return super().do_GET()

        def log_message(self, format, *args):  # noqa: A002
            sys.stdout.write(
                f"[端口 {self.server.server_address[1]}] {self.address_string()} - {format % args}\n"
            )
            sys.stdout.flush()

    return Handler


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve_port(port: int, default_file: str, label: str) -> None:
    handler = make_handler(default_file)
    try:
        with ReusableThreadingTCPServer(("0.0.0.0", port), handler) as httpd:
            print(f"[启动] http://0.0.0.0:{port}/  ->  {default_file}  ({label})")
            sys.stdout.flush()
            httpd.serve_forever()
    except OSError as exc:
        print(f"[错误] 端口 {port} 启动失败：{exc}")


def main() -> None:
    threads = [
        threading.Thread(
            target=serve_port,
            args=(port, default_file, label),
            daemon=True,
            name=f"preview-{port}",
        )
        for port, (default_file, label) in PORT_MAPPING.items()
    ]
    for t in threads:
        t.start()

    print("=" * 60)
    print("最终成品预览服务已启动：")
    for port, (default_file, label) in PORT_MAPPING.items():
        print(f"  • http://localhost:{port}/  ->  {label}")
    print("=" * 60)
    sys.stdout.flush()

    try:
        while True:
            for t in threads:
                t.join(timeout=1)
    except KeyboardInterrupt:
        print("\n[停止] 收到中断信号，退出预览服务")


if __name__ == "__main__":
    main()
