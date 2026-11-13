"""会话详情纯逻辑单测：节点生成用时。

    .venv/bin/python -m pytest tests/test_conversation_detail.py
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes_conversations import _build_render_issue, _task_duration_ms  # noqa: E402
from app.core.config import Settings, get_model_registry  # noqa: E402
from app.services.page_render_check import (  # noqa: E402
    RenderCheckResult,
    build_blank_page_issue,
    build_js_error_issue,
    render_issue_from_stored,
)


def test_duration_from_task_timestamps():
    started = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    task = SimpleNamespace(started_at=started, finished_at=started + timedelta(seconds=83, milliseconds=250))
    assert _task_duration_ms(task) == 83_250


def test_duration_missing_when_unfinished():
    started = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    assert _task_duration_ms(None) is None
    assert _task_duration_ms(SimpleNamespace(started_at=None, finished_at=started)) is None
    assert _task_duration_ms(SimpleNamespace(started_at=started, finished_at=None)) is None


def test_duration_never_negative():
    started = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)
    task = SimpleNamespace(started_at=started, finished_at=started - timedelta(seconds=1))
    assert _task_duration_ms(task) == 0


def test_render_issue_hidden_for_observe_mode_and_exposed_for_repair_mode():
    """observe 模式记录的 render_check 绝不能在会话详情里漏标；仅 repair 模式的未解决 issues_found 才下发。"""
    assert _build_render_issue(None) is None
    assert _build_render_issue(SimpleNamespace(render_check=None)) is None

    issue = build_js_error_issue("ReferenceError", "foo is not defined", "at http://starpage.invalid/:12:5")
    blank = build_blank_page_issue()
    check_with_issues = RenderCheckResult(status="issues_found", issues=[issue, blank], duration_ms=520.0, rss_mb=120.0)

    # 1) observe 模式即使发现了问题，也不暴露给前端
    stored_observe = check_with_issues.to_dict(repaired=False, mode="observe", expose_issue=False)
    assert render_issue_from_stored(stored_observe) is None
    assert _build_render_issue(SimpleNamespace(render_check=stored_observe)) is None

    # 即使只有 mode="observe" 忘了写 expose_issue，同样不暴露
    assert render_issue_from_stored({"status": "issues_found", "mode": "observe", "issues": [issue.to_dict()]}) is None

    # 2) repair 模式下修好（passed）或跳过（skipped）返回 None
    passed_repair = RenderCheckResult(status="passed", duration_ms=300.0).to_dict(
        repaired=True, mode="repair", expose_issue=True
    )
    assert _build_render_issue(SimpleNamespace(render_check=passed_repair)) is None

    # 3) repair 模式下修复一次后仍有问题：准确返回 summary 与 issues 列表
    failed_repair = check_with_issues.to_dict(repaired=True, mode="repair", expose_issue=True)
    node_issue = _build_render_issue(SimpleNamespace(render_check=failed_repair))
    assert node_issue is not None
    assert "脚本运行出错" in node_issue.summary
    assert "首屏疑似白屏" in node_issue.summary
    assert len(node_issue.issues) == 2


def test_settings_render_check_and_model_edit_mode_catalog_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 默认 off，支持大小写规范化，非法值报错
    default_cfg = Settings(_env_file=None)
    assert default_cfg.page_edit_mode_enabled is True
    assert default_cfg.page_render_check_mode == "off"
    assert default_cfg.page_render_check_concurrency == 2
    assert default_cfg.page_render_check_timeout_seconds == 15.0
    assert default_cfg.page_render_check_max_queue == 8
    assert default_cfg.page_render_check_settle_ms == 1300
    assert default_cfg.page_render_check_chromium_path is None

    assert Settings(PAGE_RENDER_CHECK_MODE=" OBSERVE ").page_render_check_mode == "observe"
    assert Settings(PAGE_RENDER_CHECK_MODE="Repair").page_render_check_mode == "repair"
    assert Settings(PAGE_RENDER_CHECK_MODE="").page_render_check_mode == "off"
    with pytest.raises(ValueError, match="PAGE_RENDER_CHECK_MODE"):
        Settings(PAGE_RENDER_CHECK_MODE="invalid-mode")

    # 验证 llm.models.json 的 defaults.edit_mode 与模型级覆盖，且 edit_mode 绝不泄漏进上游请求 params
    catalog = {
        "defaults": {"temperature": 0.7, "max_tokens": 32768, "edit_mode": True},
        "default_models": ["m-on"],
        "models": [
            {
                "key": "m-on",
                "label": "默认开启模型",
                "provider": "qwen",
                "protocol": "openai",
                "base_url": "https://mock.local/v1",
                "model": "qwen-max",
                "api_key_env": "TEST_MOCK_KEY",
                "params": {},
            },
            {
                "key": "m-off",
                "label": "显式关闭模型",
                "provider": "doubao",
                "protocol": "openai",
                "base_url": "https://mock.local/v1",
                "model": "doubao-pro",
                "api_key_env": "TEST_MOCK_KEY",
                "edit_mode": False,
                "params": {},
            },
        ],
    }
    catalog_file = tmp_path / "llm.models.json"
    catalog_file.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setenv("TEST_MOCK_KEY", "sk-mock")
    monkeypatch.setenv("LLM_MODELS_FILE", str(catalog_file))
    from app.core.config import get_settings

    get_settings.cache_clear()
    get_model_registry.cache_clear()
    try:
        reg = get_model_registry()
        m_on = reg.get("m-on")
        m_off = reg.get("m-off")
        assert m_on is not None and m_on.edit_mode is True
        assert m_off is not None and m_off.edit_mode is False
        assert "edit_mode" not in m_on.params
        assert "edit_mode" not in m_off.params
        assert m_on.params == {"temperature": 0.7, "max_tokens": 32768}
    finally:
        get_settings.cache_clear()
        get_model_registry.cache_clear()
