"""训练授权纯逻辑单测：授权折扣取整、授权快照、匿名结构样本脱敏。

    .venv/bin/python -m pytest tests/test_training_consent.py
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.billing.pricing import apply_training_discount  # noqa: E402
from app.services.consent_service import needs_policy_notice, training_consent_for  # noqa: E402
from app.services.training_samples import extract_skeleton  # noqa: E402


def _user(**kwargs):
    base = {"allow_training": True, "is_anonymous": False, "policy_notice_seen_at": datetime.now(UTC)}
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_discount_ceil_and_floor():
    assert apply_training_discount(100, 0.9) == 90
    # 小额生成也要真的打折：6 × 0.9 = 5.4 → 5，授权价不高于九折
    assert apply_training_discount(6, 0.9) == 5
    assert apply_training_discount(11, 0.9) == 9
    assert apply_training_discount(1, 0.9) == 1
    assert apply_training_discount(50, 1.0) == 50


def test_consent_snapshot():
    assert training_consent_for(None) == "granted"
    assert training_consent_for(_user()) == "granted"
    assert training_consent_for(_user(allow_training=False)) == "denied"
    assert training_consent_for(_user(is_anonymous=True, policy_notice_seen_at=None)) == "granted"
    assert training_consent_for(_user(is_anonymous=True, allow_training=False)) == "denied"
    # 老用户还没看到更新告知：原始数据先标 legacy
    assert training_consent_for(_user(policy_notice_seen_at=None)) == "legacy"


def test_needs_policy_notice():
    assert needs_policy_notice(_user(policy_notice_seen_at=None)) is True
    assert needs_policy_notice(_user()) is False
    assert needs_policy_notice(_user(is_anonymous=True, policy_notice_seen_at=None)) is False


def test_skeleton_strips_personal_content():
    html = """<!doctype html><html><head><meta name="viewport" content="width=device-width">
    <title>张三的简历</title><meta name="description" content="张三 前端工程师">
    <style>.hero{background:url(https://cdn.example.com/a.png);color:#333}.tag::before{content:"电话"}</style></head>
    <body><!-- 作者：张三 --><section class="hero" style="background-image:url('/assets/abc')">
    <h1>张三</h1><p>手机 13812345678，邮箱 zhangsan@example.com，身份证 440300199001011234</p>
    <a href="https://github.com/zhangsan" title="张三的主页">主页</a><img src="/assets/123" alt="张三头像">
    <button onclick="alert('你好张三')">联系</button></section>
    <script>const profile = {name: "张三", phone: "13812345678", url: "https://zhangsan.dev"}; console.log('ready');</script>
    </body></html>"""
    out = extract_skeleton(html)
    for secret in ["张三", "13812345678", "zhangsan", "440300199001011234", "cdn.example.com", "/assets/", "电话", "作者"]:
        assert secret not in out, secret
    # 结构、样式、交互代码保留
    for kept in ['class="hero"', "<h1>", "color:#333", "console.log('ready')", "width=device-width", "<button"]:
        assert kept in out, kept


if __name__ == "__main__":
    test_discount_ceil_and_floor()
    test_consent_snapshot()
    test_needs_policy_notice()
    test_skeleton_strips_personal_content()
    print("全部通过")
