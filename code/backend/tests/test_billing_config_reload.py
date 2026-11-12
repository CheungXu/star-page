"""计费配置跨进程生效：一个进程改了 billing.json，其他进程按文件修改时间在几秒内读到。不需要数据库。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import config as config_module  # noqa: E402
from app.core.config import get_billing_config, get_settings  # noqa: E402


def test_billing_config_reloads_after_file_changes(tmp_path, monkeypatch):
    path = tmp_path / "billing.json"
    path.write_text(json.dumps({"default_markup": 1.5}), encoding="utf-8")
    monkeypatch.setattr(get_settings(), "billing_config_file", str(path))
    get_billing_config.cache_clear()
    try:
        assert get_billing_config().default_markup == 1.5

        # 另一个进程改了文件：在重新检查的间隔内仍用缓存，过了间隔读到新值。
        path.write_text(json.dumps({"default_markup": 2.0}), encoding="utf-8")
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        assert get_billing_config().default_markup == 1.5
        monkeypatch.setattr(config_module, "BILLING_CONFIG_RECHECK_SECONDS", 0)
        assert get_billing_config().default_markup == 2.0
    finally:
        get_billing_config.cache_clear()
