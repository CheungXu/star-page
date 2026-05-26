#!/usr/bin/env python3
"""三端口 logo 方案预览服务

- 3001 -> 方案一：简化并缩小（44px SVG 单星）
- 3002 -> 方案二：中央不放 Logo
- 3003 -> 方案三：原 Logo 转化为氛围水印

每个端口独立 http.server，根路径 / 默认返回对应方案 HTML，
其它静态文件（_styles.css / stars-page-logo.png）按相对路径访问，
方便用户直接通过浏览器打开三个端口快速对比。
"""
from __future__ import annotations

import http.server
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 端口与默认入口的映射
PORT_MAPPING = {
    3001: ("preview-a.html", "方案一 · 简化并缩小"),
    3002: ("preview-b.html", "方案二 · 中央不放 Logo"),
    3003: ("preview-c.html", "方案三 · 氛围水印"),
}


def make_handler(default_file: str):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def do_GET(self):  # noqa: N802
            if self.path in ("/", ""):
                self.path = "/" + default_file
            return super().do_GET()

        # 精简日志：避免噪音过多
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
        sys.stdout.flush()


def main() -> None:
    threads: list[threading.Thread] = []
    for port, (default_file, label) in PORT_MAPPING.items():
        t = threading.Thread(
            target=serve_port,
            args=(port, default_file, label),
            daemon=True,
            name=f"preview-{port}",
        )
        t.start()
        threads.append(t)

    print("=" * 60)
    print("三端口预览服务已启动，按 Ctrl+C 停止")
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
