from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.services.llm.types import LlmModelConfig


def _load_local_env_files() -> None:
    """加载本地开发常用 env；生产环境优先使用 Docker/Nginx 注入。"""
    cwd = Path.cwd()
    candidates: list[Path] = []

    for base in [cwd, *cwd.parents]:
        candidates.extend(
            [
                base / ".env",
                base / "config" / ".env",
                base / "config" / "db.env",
                base / "config" / "oss.env",
                base / "config" / "llm.env",
                base / "config" / "sms.env",
            ]
        )

    for path in candidates:
        if path.exists():
            load_dotenv(path, override=False)


_load_local_env_files()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    app_name: str = "Star Page"
    app_env: str = Field(default="development", alias="APP_ENV")
    public_base_url: str = Field(default="http://localhost:3000", alias="PUBLIC_BASE_URL")
    frontend_origin: str = Field(default="http://localhost:3000", alias="FRONTEND_ORIGIN")

    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    pghost: str | None = Field(default=None, alias="PGHOST")
    pgport: int = Field(default=5432, alias="PGPORT")
    pgdatabase: str | None = Field(default=None, alias="PGDATABASE")
    pguser: str | None = Field(default=None, alias="PGUSER")
    pgpassword: str | None = Field(default=None, alias="PGPASSWORD")

    default_user_name: str = Field(default="default_test", alias="DEFAULT_USER_NAME")
    default_user_email: str = Field(default="default_test@example.local", alias="DEFAULT_USER_EMAIL")
    default_user_display_name: str = Field(default="默认测试用户", alias="DEFAULT_USER_DISPLAY_NAME")

    auth_session_cookie_name: str = Field(default="sp_session", alias="AUTH_SESSION_COOKIE_NAME")
    auth_session_ttl_seconds: int = Field(default=60 * 60 * 24 * 30, alias="AUTH_SESSION_TTL_SECONDS")
    auth_cookie_secure: bool = Field(default=False, alias="AUTH_COOKIE_SECURE")
    auth_secret_key: str = Field(default="star-page-dev-secret-change-me", alias="AUTH_SECRET_KEY")

    sms_provider: str = Field(default="mock", alias="SMS_PROVIDER")
    sms_code_ttl_seconds: int = Field(default=300, alias="SMS_CODE_TTL_SECONDS")
    sms_send_cooldown_seconds: int = Field(default=60, alias="SMS_SEND_COOLDOWN_SECONDS")
    sms_daily_limit_per_phone: int = Field(default=20, alias="SMS_DAILY_LIMIT_PER_PHONE")
    sms_daily_limit_per_ip: int = Field(default=100, alias="SMS_DAILY_LIMIT_PER_IP")
    aliyun_sms_sign_name: str = Field(default="", alias="ALIYUN_SMS_SIGN_NAME")
    aliyun_sms_template_code: str = Field(default="", alias="ALIYUN_SMS_TEMPLATE_CODE")
    aliyun_sms_endpoint: str = Field(default="dysmsapi.aliyuncs.com", alias="ALIYUN_SMS_ENDPOINT")

    llm_provider: str = Field(default="qwen", alias="LLM_PROVIDER")
    llm_protocol: str = Field(default="openai", alias="LLM_PROTOCOL")
    llm_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1", alias="LLM_BASE_URL")
    llm_model: str = Field(default="", alias="LLM_MODEL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_timeout_ms: int = Field(default=60000, alias="LLM_TIMEOUT_MS")
    llm_enable_thinking: bool | None = Field(default=None, alias="LLM_ENABLE_THINKING")
    llm_openai_extra_body_json: str | None = Field(default=None, alias="LLM_OPENAI_EXTRA_BODY_JSON")
    llm_max_tokens: int = Field(default=8192, alias="LLM_MAX_TOKENS")
    llm_temperature: float = Field(default=0.7, alias="LLM_TEMPERATURE")
    llm_retry_attempts: int = Field(default=3, alias="LLM_RETRY_ATTEMPTS")
    llm_retry_initial_delay_ms: int = Field(default=800, alias="LLM_RETRY_INITIAL_DELAY_MS")
    llm_retry_max_delay_ms: int = Field(default=5000, alias="LLM_RETRY_MAX_DELAY_MS")

    object_storage_provider: str = Field(default="aliyun", alias="OBJECT_STORAGE_PROVIDER")
    object_storage_bucket: str = Field(default="", alias="OBJECT_STORAGE_BUCKET")
    object_storage_region: str = Field(default="", alias="OBJECT_STORAGE_REGION")
    object_storage_endpoint: str = Field(default="", alias="OBJECT_STORAGE_ENDPOINT")
    object_storage_access_key_id: str = Field(default="", alias="OBJECT_STORAGE_ACCESS_KEY_ID")
    object_storage_access_key_secret: str = Field(default="", alias="OBJECT_STORAGE_ACCESS_KEY_SECRET")
    local_storage_dir: str = Field(default="data/generated-pages", alias="LOCAL_STORAGE_DIR")

    # 生成页允许引用的可信 CDN（空格或逗号分隔），同时供 HTML 清洗与页面 CSP 复用。
    generated_page_cdn_allowlist: str = Field(
        default="https://cdn.jsdelivr.net https://unpkg.com",
        alias="GENERATED_PAGE_CDN_ALLOWLIST",
    )

    llm_models_file: str = Field(default="config/llm.models.json", alias="LLM_MODELS_FILE")
    llm_default_models: str | None = Field(default=None, alias="LLM_DEFAULT_MODELS")

    billing_config_file: str = Field(default="config/billing.json", alias="BILLING_CONFIG_FILE")
    anon_cookie_name: str = Field(default="sp_anon", alias="ANON_COOKIE_NAME")
    anon_cookie_ttl_seconds: int = Field(default=60 * 60 * 24 * 180, alias="ANON_COOKIE_TTL_SECONDS")

    # 网页制作技能（page-skills）：默认开启；各 task 按生成模型自动路由匹配技能并注入生成提示。
    page_skills_enabled: bool = Field(default=True, alias="PAGE_SKILLS_ENABLED")
    page_skills_dir: str = Field(default="skills/page-skills", alias="PAGE_SKILLS_DIR")

    @computed_field
    @property
    def async_database_url(self) -> str:
        if self.database_url:
            return self._normalize_database_url(self.database_url)

        missing = [
            name
            for name, value in {
                "PGHOST": self.pghost,
                "PGDATABASE": self.pgdatabase,
                "PGUSER": self.pguser,
                "PGPASSWORD": self.pgpassword,
            }.items()
            if not value
        ]

        if missing:
            raise ValueError(f"缺少数据库配置：{', '.join(missing)}")

        return (
            f"postgresql+asyncpg://{self.pguser}:{self.pgpassword}"
            f"@{self.pghost}:{self.pgport}/{self.pgdatabase}"
        )

    @property
    def generated_page_cdn_sources(self) -> list[str]:
        """把 CDN 白名单字符串解析为去重、保序的来源列表（如 https://cdn.jsdelivr.net）。"""
        seen: set[str] = set()
        sources: list[str] = []
        for entry in re.split(r"[\s,]+", self.generated_page_cdn_allowlist or ""):
            entry = entry.strip()
            if entry and entry not in seen:
                seen.add(entry)
                sources.append(entry)
        return sources

    @property
    def llm_extra_body(self) -> dict[str, Any]:
        extra_body: dict[str, Any] = {}

        if self.llm_openai_extra_body_json:
            parsed = json.loads(self.llm_openai_extra_body_json)
            if not isinstance(parsed, dict):
                raise ValueError("LLM_OPENAI_EXTRA_BODY_JSON 必须是 JSON 对象")
            extra_body.update(parsed)

        if self.llm_enable_thinking is not None:
            extra_body["enable_thinking"] = self.llm_enable_thinking

        return extra_body

    @staticmethod
    def _normalize_database_url(database_url: str) -> str:
        if database_url.startswith("postgres://"):
            database_url = "postgresql://" + database_url[len("postgres://") :]
        if database_url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + database_url[len("postgresql://") :]
        return database_url

    @field_validator("llm_enable_thinking", mode="before")
    @classmethod
    def parse_optional_bool(cls, value: object) -> object:
        if value is None or value == "":
            return None

        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False

        return value

    @field_validator("auth_cookie_secure", mode="before")
    @classmethod
    def parse_bool(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@dataclass(frozen=True)
class BillingConfig:
    """计费配置：模型倍率、匿名围栏、赠送与套餐等，来源 config/billing.json。"""

    default_markup: float
    model_markups: dict[str, float]
    free_trial_generations: int
    signup_bonus_credits: int
    anon_allowed_models: list[str]
    anon_max_models_per_gen: int
    anon_daily_id_limit_per_ip: int
    anon_daily_free_generation_limit_per_ip: int
    min_recharge_cny: float
    max_recharge_cny: float
    aliyun_llm_keywords: list[str]

    def markup_for(self, model_key: str | None) -> float:
        if model_key and model_key in self.model_markups:
            return float(self.model_markups[model_key])
        return float(self.default_markup)

    def is_anon_allowed_model(self, model_key: str) -> bool:
        return model_key in self.anon_allowed_models


def _find_billing_config_path(settings: Settings) -> Path | None:
    raw = settings.billing_config_file
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None
    cwd = Path.cwd()
    for base in [cwd, *cwd.parents]:
        path = base / raw
        if path.exists():
            return path
        fallback = base / "config" / "billing.json"
        if fallback.exists():
            return fallback
    return None


@lru_cache(maxsize=1)
def get_billing_config() -> BillingConfig:
    settings = get_settings()
    path = _find_billing_config_path(settings)
    data: dict[str, Any] = {}
    if path is not None:
        data = json.loads(path.read_text(encoding="utf-8"))

    return BillingConfig(
        default_markup=float(data.get("default_markup", 1.2)),
        model_markups={str(k): float(v) for k, v in (data.get("model_markups") or {}).items()},
        free_trial_generations=int(data.get("free_trial_generations", 2)),
        signup_bonus_credits=int(data.get("signup_bonus_credits", 1000)),
        anon_allowed_models=[str(item) for item in (data.get("anon_allowed_models") or [])],
        anon_max_models_per_gen=int(data.get("anon_max_models_per_gen", 2)),
        anon_daily_id_limit_per_ip=int(data.get("anon_daily_id_limit_per_ip", 3)),
        anon_daily_free_generation_limit_per_ip=int(data.get("anon_daily_free_generation_limit_per_ip", 5)),
        min_recharge_cny=float(data.get("min_recharge_cny", 1)),
        max_recharge_cny=float(data.get("max_recharge_cny", 100000)),
        aliyun_llm_keywords=[
            str(item)
            for item in (
                data.get("aliyun_llm_keywords")
                or ["百炼", "灵积", "通义", "模型", "dashscope", "bailian", "lingji"]
            )
        ],
    )


def update_billing_markups(default_markup: float, model_markups: dict[str, float]) -> BillingConfig:
    """更新 config/billing.json 的倍率配置并即时生效（清缓存）。保留其余字段不变。"""
    settings = get_settings()
    path = _find_billing_config_path(settings)
    if path is None:
        raise FileNotFoundError("未找到 billing 配置文件，无法保存倍率")

    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    data["default_markup"] = float(default_markup)
    data["model_markups"] = {str(k): float(v) for k, v in model_markups.items()}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    get_billing_config.cache_clear()
    return get_billing_config()


@dataclass(frozen=True)
class LlmModelRegistry:
    """多模型目录：key -> 模型配置，外加默认勾选键。"""

    models: dict[str, LlmModelConfig]
    default_model_keys: list[str]

    def get(self, key: str) -> LlmModelConfig | None:
        return self.models.get(key)

    def available_models(self) -> list[LlmModelConfig]:
        return [model for model in self.models.values() if model.available]


def _find_catalog_path(settings: Settings) -> Path | None:
    raw = settings.llm_models_file
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate if candidate.exists() else None

    cwd = Path.cwd()
    for base in [cwd, *cwd.parents]:
        path = base / raw
        if path.exists():
            return path
        fallback = base / "config" / "llm.models.json"
        if fallback.exists():
            return fallback
    return None


def _resolve_api_key(entry: dict[str, Any]) -> str:
    for env_name in (entry.get("api_key_env"), entry.get("api_key_fallback_env")):
        if not env_name:
            continue
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value
    return ""


def _resolve_params(defaults: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    merged = {**(defaults or {}), **(entry.get("params") or {})}
    omit = set(entry.get("omit") or [])
    # 值为 None 表示显式不发该字段；omit 批量丢弃。
    return {key: value for key, value in merged.items() if value is not None and key not in omit}


def _legacy_registry(settings: Settings) -> LlmModelRegistry:
    """无模型目录文件时，用旧 LLM_* 单模型配置合成一条，保证现有部署可用。"""
    key = settings.llm_provider or "default"
    params = {"temperature": settings.llm_temperature, "max_tokens": settings.llm_max_tokens}
    config = LlmModelConfig(
        key=key,
        label=key,
        provider=settings.llm_provider,
        protocol=settings.llm_protocol,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        params={k: v for k, v in params.items() if v is not None},
        extra_body=dict(settings.llm_extra_body),
        available=bool(settings.llm_model and settings.llm_api_key),
    )
    return LlmModelRegistry(models={key: config}, default_model_keys=[key])


@lru_cache(maxsize=1)
def get_model_registry() -> LlmModelRegistry:
    settings = get_settings()
    catalog_path = _find_catalog_path(settings)
    if catalog_path is None:
        return _legacy_registry(settings)

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    defaults: dict[str, Any] = catalog.get("defaults") or {}

    models: dict[str, LlmModelConfig] = {}
    for entry in catalog.get("models") or []:
        key = entry.get("key")
        if not key:
            continue
        api_key = _resolve_api_key(entry)
        models[key] = LlmModelConfig(
            key=key,
            label=entry.get("label") or key,
            provider=entry.get("provider") or key,
            protocol=entry.get("protocol") or "openai",
            base_url=(entry.get("base_url") or "").rstrip("/"),
            model=entry.get("model") or "",
            api_key=api_key,
            params=_resolve_params(defaults, entry),
            extra_body=dict(entry.get("extra_body") or {}),
            pricing=dict(entry.get("pricing") or {}) or None,
            available=bool(api_key and entry.get("model")),
        )

    if not models:
        return _legacy_registry(settings)

    default_keys = _resolve_default_keys(settings, catalog, models)
    return LlmModelRegistry(models=models, default_model_keys=default_keys)


def _resolve_default_keys(settings: Settings, catalog: dict[str, Any], models: dict[str, LlmModelConfig]) -> list[str]:
    # 优先 env 覆盖，其次目录 default_models，最后回退到首个可用模型。
    raw = settings.llm_default_models
    keys: list[str] = []
    if raw:
        keys = [item.strip() for item in raw.split(",") if item.strip()]
    if not keys:
        keys = [str(item) for item in (catalog.get("default_models") or [])]

    valid = [key for key in keys if key in models]
    if valid:
        return valid

    available = [model.key for model in models.values() if model.available]
    if available:
        return [available[0]]
    return [next(iter(models))]
