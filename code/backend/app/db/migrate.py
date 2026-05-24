from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.default_user import ensure_default_user


async def run_migrations() -> None:
    migrations_dir = Path(__file__).resolve().parents[2] / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))
    settings = get_settings()

    connection = await asyncpg.connect(dsn=_to_asyncpg_dsn(settings.async_database_url))
    try:
        for sql_file in sql_files:
            sql = sql_file.read_text(encoding="utf-8")
            await connection.execute(sql)
            print(f"已执行迁移：{sql_file.name}")
    finally:
        await connection.close()

    async with AsyncSessionLocal() as session:
        user = await ensure_default_user(session)
        print(f"默认测试用户已就绪：{user.username}")


def _to_asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


if __name__ == "__main__":
    asyncio.run(run_migrations())
