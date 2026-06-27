from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings


def _find_file(raw_path: str) -> Path | None:
    """按相对路径从运行目录逐级向上查找凭据文件（与 billing/llm 配置查找一致）。"""
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    cwd = Path.cwd()
    for base in [cwd, *cwd.parents]:
        path = base / raw_path
        if path.exists():
            return path
    return None


def _read_text(raw_path: str) -> str:
    path = _find_file(raw_path)
    if path is None:
        return ""
    return path.read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class WechatPayConfig:
    """微信支付凭据（Native + 微信支付公钥模式）。

    - private_key / cert_serial_no：商户私钥与证书序列号，用于「请求签名」，永远必填。
    - public_key / public_key_id：微信支付公钥与公钥ID，用于「应答/回调验签」。
    - apiv3_key：回调内容 AES-256-GCM 解密。
    """

    mchid: str
    appid: str
    apiv3_key: str
    cert_serial_no: str
    private_key: str
    public_key: str
    public_key_id: str
    notify_url: str

    @property
    def configured(self) -> bool:
        return all(
            [
                self.mchid,
                self.appid,
                self.apiv3_key,
                self.cert_serial_no,
                self.private_key,
                self.public_key,
                self.public_key_id,
                self.notify_url,
            ]
        )

    def missing_fields(self) -> list[str]:
        checks = {
            "WECHATPAY_MCHID": self.mchid,
            "WECHATPAY_APPID": self.appid,
            "WECHATPAY_APIV3_KEY": self.apiv3_key,
            "WECHATPAY_CERT_SERIAL_NO": self.cert_serial_no,
            "商户私钥文件(WECHATPAY_PRIVATE_KEY_PATH)": self.private_key,
            "微信支付公钥文件(WECHATPAY_PUBLIC_KEY_PATH)": self.public_key,
            "WECHATPAY_PUBLIC_KEY_ID": self.public_key_id,
            "WECHATPAY_NOTIFY_URL": self.notify_url,
        }
        return [name for name, value in checks.items() if not value]


@lru_cache(maxsize=1)
def get_wechatpay_config() -> WechatPayConfig:
    """读取微信支付配置（含私钥/公钥文件内容）。缺失项不报错，由 configured 判断是否启用。"""
    settings = get_settings()
    return WechatPayConfig(
        mchid=settings.wechatpay_mchid.strip(),
        appid=settings.wechatpay_appid.strip(),
        apiv3_key=settings.wechatpay_apiv3_key.strip(),
        cert_serial_no=settings.wechatpay_cert_serial_no.strip(),
        private_key=_read_text(settings.wechatpay_private_key_path),
        public_key=_read_text(settings.wechatpay_public_key_path),
        public_key_id=settings.wechatpay_public_key_id.strip(),
        notify_url=settings.wechatpay_notify_url.strip(),
    )
