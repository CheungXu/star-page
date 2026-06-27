"""微信支付纯逻辑单测：商户订单号还原、金额换算、资金账单解析归类。

无需真实凭据与网络，可直接运行：
    .venv/bin/python tests/test_wechat_pay.py
也兼容 pytest。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.payment.wechat.client import _parse_fundflow_csv  # noqa: E402


def test_out_trade_no_roundtrip():
    # out_trade_no = 订单 UUID 的 hex（32位），回调时可逆向还原为同一 UUID。
    order_id = uuid.uuid4()
    out_trade_no = order_id.hex
    assert len(out_trade_no) == 32
    assert uuid.UUID(hex=out_trade_no) == order_id


def test_amount_to_fen():
    # 元转分：服务端权威金额，10.00 元 = 1000 分。
    assert int(round(10.00 * 100)) == 1000
    assert int(round(0.01 * 100)) == 1


_SAMPLE_CSV = """记账时间,微信支付业务单号,资金流水单号,业务名称,业务类型,收支类型,收支金额(元),账户结余(元),资金变更提交申请人,备注,业务凭证号
`2026-06-18 10:00:00,`4200001,`F001,`普通支付,`交易,`收入,`100.00,`100.00,`,`,`
`2026-06-18 10:01:00,`4200001,`F002,`手续费,`手续费,`支出,`0.60,`99.40,`,`,`
`2026-06-18 23:00:00,`,`F003,`提现,`提现,`支出,`99.40,`0.00,`,`,`
资金流水总笔数,3,收入笔数,1,收入金额,100.00,支出笔数,2,支出金额,100.00"""


def test_parse_fundflow_csv_classification():
    settlement, fee, income, rows, unknown = _parse_fundflow_csv(_SAMPLE_CSV)
    assert settlement == 99.40  # 提现/结算
    assert fee == 0.60  # 手续费
    assert income == 100.00  # 交易收入
    assert rows == 3  # 不含尾部汇总行
    assert unknown == set()


def test_parse_fundflow_csv_empty():
    settlement, fee, income, rows, unknown = _parse_fundflow_csv("")
    assert (settlement, fee, income, rows) == (0.0, 0.0, 0.0, 0)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
    if failures:
        print(f"\n{failures} 个用例失败")
        sys.exit(1)
    print("\n全部用例通过")
