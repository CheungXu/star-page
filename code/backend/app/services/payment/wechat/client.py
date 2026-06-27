from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, field

from app.services.payment.wechat.config import WechatPayConfig, get_wechatpay_config

# 资金账单中按「业务类型」归类的关键词（微信账单列含中文业务类型）。
_SETTLEMENT_KEYWORDS = ("提现", "结算")
_FEE_KEYWORDS = ("手续费",)


class WechatPayError(Exception):
    """微信支付调用错误（含微信返回的 code/message）。"""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class WechatPayNotConfiguredError(WechatPayError):
    def __init__(self, message: str = "微信支付未配置或凭据缺失") -> None:
        super().__init__(message, code="NOT_CONFIGURED")


# 懒加载单例：首次需要时构造，避免未配置时在导入期报错。
_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    config = get_wechatpay_config()
    if not config.configured:
        raise WechatPayNotConfiguredError(
            "微信支付未配置，缺少：" + "、".join(config.missing_fields())
        )
    from wechatpayv3 import WeChatPay, WeChatPayType

    _client = WeChatPay(
        wechatpay_type=WeChatPayType.NATIVE,
        mchid=config.mchid,
        private_key=config.private_key,
        cert_serial_no=config.cert_serial_no,
        apiv3_key=config.apiv3_key,
        appid=config.appid,
        notify_url=config.notify_url,
        public_key=config.public_key,
        public_key_id=config.public_key_id,
    )
    return _client


def is_configured() -> bool:
    return get_wechatpay_config().configured


def _parse_message(message: str | None) -> dict:
    if not message:
        return {}
    try:
        return json.loads(message)
    except (ValueError, TypeError):
        return {}


def native_prepay(*, out_trade_no: str, description: str, amount_fen: int, attach: str | None = None) -> str:
    """Native 下单，返回二维码链接 code_url。失败抛 WechatPayError。同步调用，需放线程池。"""
    from wechatpayv3 import WeChatPayType

    client = _get_client()
    code, message = client.pay(
        description=description,
        out_trade_no=out_trade_no,
        amount={"total": amount_fen, "currency": "CNY"},
        attach=attach,
        pay_type=WeChatPayType.NATIVE,
    )
    data = _parse_message(message)
    if str(code) != "200":
        raise WechatPayError(
            data.get("message") or f"微信下单失败（HTTP {code}）", code=data.get("code")
        )
    code_url = data.get("code_url")
    if not code_url:
        raise WechatPayError("微信下单未返回 code_url")
    return code_url


def query_order(out_trade_no: str) -> dict:
    """按商户订单号查单，返回微信订单信息字典（含 trade_state/transaction_id/amount）。同步调用。"""
    client = _get_client()
    code, message = client.query(out_trade_no=out_trade_no)
    data = _parse_message(message)
    if str(code) != "200":
        raise WechatPayError(
            data.get("message") or f"微信查单失败（HTTP {code}）", code=data.get("code")
        )
    return data


def verify_and_parse_callback(headers: dict, body: bytes) -> dict | None:
    """验签 + 解密回调。验签失败返回 None；成功返回含 event_type 与已解密 resource 的字典。同步调用。"""
    client = _get_client()
    return client.callback(headers, body)


@dataclass(frozen=True)
class WechatFundflowSummary:
    """某日资金账单的归类汇总（用于结算/手续费入账对账）。金额单位：元。"""

    bill_date: str
    configured: bool
    settlement_cny: float = 0.0  # 结算/提现到银行（支出，冲减应收第三方）
    fee_cny: float = 0.0  # 手续费（支出）
    income_cny: float = 0.0  # 交易收入（仅供参考）
    row_count: int = 0
    unknown_types: list[str] = field(default_factory=list)
    error: str | None = None
    note: str | None = None


def _strip_cell(cell: str) -> str:
    # 微信账单为防 Excel 格式化，每个单元格前缀反引号，需要去掉。
    return cell.strip().lstrip("`").strip()


def _to_amount(cell: str) -> float:
    try:
        return float(_strip_cell(cell).replace(",", ""))
    except ValueError:
        return 0.0


def _classify(business_type: str) -> str:
    if any(k in business_type for k in _SETTLEMENT_KEYWORDS):
        return "settlement"
    if any(k in business_type for k in _FEE_KEYWORDS):
        return "fee"
    return "other"


def _parse_fundflow_csv(text: str) -> tuple[float, float, float, int, set[str]]:
    """解析资金账单 CSV，按业务类型汇总结算/手续费/收入金额。"""
    settlement = fee = income = 0.0
    rows = 0
    unknown: set[str] = set()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return settlement, fee, income, rows, unknown

    header = [_strip_cell(c) for c in lines[0].split(",")]

    def col_index(*names: str) -> int | None:
        for i, name in enumerate(header):
            if any(n in name for n in names):
                return i
        return None

    idx_type = col_index("业务类型")
    idx_inout = col_index("收支类型")
    idx_amount = col_index("收支金额")
    if idx_type is None or idx_amount is None:
        return settlement, fee, income, rows, unknown

    for line in lines[1:]:
        cells = line.split(",")
        if len(cells) <= idx_amount:
            continue
        inout = _strip_cell(cells[idx_inout]) if idx_inout is not None else ""
        # 账单尾部为「资金流水总笔数/收入金额…」等汇总行，其「收支类型」列非 收入/支出，据此过滤。
        if idx_inout is not None and inout not in ("收入", "支出"):
            continue
        business_type = _strip_cell(cells[idx_type])
        amount = _to_amount(cells[idx_amount])
        rows += 1
        kind = _classify(business_type)
        if kind == "settlement":
            settlement += amount
        elif kind == "fee":
            fee += amount
        elif inout == "收入":
            income += amount
        else:
            unknown.add(business_type)
    return round(settlement, 2), round(fee, 2), round(income, 2), rows, unknown


def fetch_fundflow_summary(bill_date: str) -> WechatFundflowSummary:
    """拉取某日（YYYY-MM-DD）基本账户资金账单并归类汇总。同步调用，需放线程池。"""
    if not is_configured():
        return WechatFundflowSummary(
            bill_date=bill_date, configured=False, note="微信支付未配置"
        )
    try:
        client = _get_client()
        # 不压缩，直接拿可解析的 CSV 文本。
        code, message = client.fundflow_bill(bill_date=bill_date, account_type="BASIC", tar_type=None)
        data = _parse_message(message)
        if str(code) != "200":
            return WechatFundflowSummary(
                bill_date=bill_date,
                configured=True,
                error=data.get("message") or f"申请资金账单失败（HTTP {code}）",
            )
        download_url = data.get("download_url")
        if not download_url:
            return WechatFundflowSummary(bill_date=bill_date, configured=True, error="未返回账单下载地址")
        dl_code, dl_message = client.download_bill(download_url)
        if str(dl_code) != "200":
            return WechatFundflowSummary(
                bill_date=bill_date, configured=True, error=f"下载账单失败（HTTP {dl_code}）"
            )
        text = _maybe_gunzip(dl_message)
        settlement, fee, income, rows, unknown = _parse_fundflow_csv(text)
        return WechatFundflowSummary(
            bill_date=bill_date,
            configured=True,
            settlement_cny=settlement,
            fee_cny=fee,
            income_cny=income,
            row_count=rows,
            unknown_types=sorted(unknown),
        )
    except WechatPayError as exc:
        return WechatFundflowSummary(bill_date=bill_date, configured=True, error=exc.message[:300])
    except Exception as exc:  # noqa: BLE001 - 第三方异常多样，统一兜底为可展示错误
        return WechatFundflowSummary(bill_date=bill_date, configured=True, error=str(exc)[:300])


def _maybe_gunzip(message) -> str:
    """download_bill 在 tar_type=None 时返回明文 CSV；兼容偶发 gzip 字节。"""
    if isinstance(message, bytes):
        if message[:2] == b"\x1f\x8b":
            return gzip.decompress(message).decode("utf-8", errors="replace")
        return message.decode("utf-8", errors="replace")
    return str(message)
