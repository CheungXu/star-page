"""管理员手机号白名单管理脚本（数据库 admin_phones 表）。

管理员身份以数据库 `admin_phones` 表为准（按手机号白名单），可预授权尚未注册的手机号。

用法（使用后端 venv 运行，自动复用 config/db.env 连接配置）：
    code/backend/.venv/bin/python script/set_admin.py list
    code/backend/.venv/bin/python script/set_admin.py grant 13800138000 --note "运营同学"
    code/backend/.venv/bin/python script/set_admin.py revoke 13800138000

子命令：
    list                列出全部管理员手机号
    grant <手机号>      授权（新增/幂等）管理员，可选 --note 备注
    revoke <手机号>     撤销管理员
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 复用后端 ORM 与数据库会话，保持连接配置与服务一致。
BACKEND_DIR = Path(__file__).resolve().parents[1] / "code" / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import delete, select  # noqa: E402

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.models.entities import AdminPhone  # noqa: E402


def _normalize_phone(phone: str) -> str:
    phone = phone.strip()
    if not phone:
        raise SystemExit("手机号不能为空")
    return phone


async def cmd_list() -> None:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(AdminPhone).order_by(AdminPhone.created_at))).scalars().all()
    if not rows:
        print("（暂无管理员手机号）")
        return
    print(f"共 {len(rows)} 个管理员：")
    for row in rows:
        note = f"  备注：{row.note}" if row.note else ""
        print(f"  - {row.phone}{note}  （添加于 {row.created_at:%Y-%m-%d %H:%M}）")


async def cmd_grant(phone: str, note: str | None) -> None:
    phone = _normalize_phone(phone)
    async with AsyncSessionLocal() as session:
        existing = await session.get(AdminPhone, phone)
        if existing is not None:
            if note is not None:
                existing.note = note
                await session.commit()
                print(f"已更新管理员 {phone} 的备注")
            else:
                print(f"管理员 {phone} 已存在，无需重复授权")
            return
        session.add(AdminPhone(phone=phone, note=note))
        await session.commit()
        print(f"已授权管理员：{phone}")


async def cmd_revoke(phone: str) -> None:
    phone = _normalize_phone(phone)
    async with AsyncSessionLocal() as session:
        result = await session.execute(delete(AdminPhone).where(AdminPhone.phone == phone))
        await session.commit()
        if result.rowcount:
            print(f"已撤销管理员：{phone}")
        else:
            print(f"未找到管理员手机号：{phone}")


def main() -> None:
    parser = argparse.ArgumentParser(description="管理员手机号白名单管理（admin_phones 表）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出全部管理员手机号")

    p_grant = sub.add_parser("grant", help="授权管理员")
    p_grant.add_argument("phone", help="手机号")
    p_grant.add_argument("--note", default=None, help="备注（可选）")

    p_revoke = sub.add_parser("revoke", help="撤销管理员")
    p_revoke.add_argument("phone", help="手机号")

    args = parser.parse_args()

    if args.command == "list":
        asyncio.run(cmd_list())
    elif args.command == "grant":
        asyncio.run(cmd_grant(args.phone, args.note))
    elif args.command == "revoke":
        asyncio.run(cmd_revoke(args.phone))


if __name__ == "__main__":
    main()
