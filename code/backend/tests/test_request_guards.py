"""访问者 IP 只信 Nginx 写入的 X-Real-IP；匿名身份超限在任何接口都回 402 而不是 500。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.core.auth import get_client_ip  # noqa: E402
from app.main import app  # noqa: E402
from app.services.billing.errors import AnonLimitError  # noqa: E402


def _request(headers: dict[str, str], client: tuple[str, int] | None = ("10.0.0.9", 5000)) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        "client": client,
    }
    return Request(scope)


def test_client_ip_ignores_forged_forwarded_for():
    request = _request({"X-Forwarded-For": "1.2.3.4, 203.0.113.7", "X-Real-IP": "203.0.113.7"})
    assert get_client_ip(request) == "203.0.113.7"


def test_client_ip_without_proxy_uses_peer_address():
    assert get_client_ip(_request({"X-Forwarded-For": "1.2.3.4"})) == "10.0.0.9"
    assert get_client_ip(_request({}, client=None)) is None


def test_anon_limit_maps_to_402_need_login():
    async def blocked() -> None:
        raise AnonLimitError("当前网络匿名访问过于频繁，请登录后继续")

    app.add_api_route("/__test__/anon-limit", blocked, methods=["GET"])
    response = TestClient(app).get("/__test__/anon-limit")
    assert response.status_code == 402
    detail = response.json()["detail"]
    assert detail["code"] == "anon_limit_reached" and detail["need_login"] is True
