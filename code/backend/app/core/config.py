from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    object_storage_provider: str = Field(default="aliyun", alias="OBJECT_STORAGE_PROVIDER")
    object_storage_bucket: str = Field(default="", alias="OBJECT_STORAGE_BUCKET")
    object_storage_region: str = Field(default="", alias="OBJECT_STORAGE_REGION")
    object_storage_endpoint: str = Field(default="", alias="OBJECT_STORAGE_ENDPOINT")
    object_storage_access_key_id: str = Field(default="", alias="OBJECT_STORAGE_ACCESS_KEY_ID")
    object_storage_access_key_secret: str = Field(default="", alias="OBJECT_STORAGE_ACCESS_KEY_SECRET")
    local_storage_dir: str = Field(default="data/generated-pages", alias="LOCAL_STORAGE_DIR")

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
