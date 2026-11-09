"""把生产库的首页案例（scene_showcases 及其页面链路）只读复制到预览库。

用法（需先加载 config/db.env）：
    code/backend/.venv/bin/python script/sync_preview_showcases.py [--target stars_page_preview]

- 源库 stars_page 只读；目标库名必须以 _preview 结尾，防止误写生产。
- 复制链路：users → conversations → generation_batches → pages → page_versions → scene_showcases，
  按 id 幂等（已存在则跳过），可重复执行。
- 作者账号的手机号、邮箱、密码不复制，避免把个人信息带进预览库。
- 页面 HTML 存在共享 OSS，复制库表后预览环境即可直接打开。
"""

import argparse
import asyncio

import asyncpg

SOURCE_DB = "stars_page"

USER_OVERRIDES = {"phone": None, "email": None, "password_hash": None, "merged_into_user_id": None}


async def common_columns(src: asyncpg.Connection, dst: asyncpg.Connection, table: str) -> list[str]:
    sql = "select column_name from information_schema.columns where table_schema='public' and table_name=$1"
    src_cols = {r[0] for r in await src.fetch(sql, table)}
    dst_cols = [r[0] for r in await dst.fetch(sql + " order by ordinal_position", table)]
    return [c for c in dst_cols if c in src_cols]


async def copy_rows(
    src: asyncpg.Connection,
    dst: asyncpg.Connection,
    table: str,
    ids: set,
    overrides: dict | None = None,
) -> int:
    """按 id 复制行；overrides 中的列强制写成指定值（None 表示置空，后续再回填）。"""
    if not ids:
        return 0
    overrides = dict(overrides or {})
    cols = await common_columns(src, dst, table)
    dst_cols = {r[0] for r in await dst.fetch(
        "select column_name from information_schema.columns where table_schema='public' and table_name=$1", table)}
    if "training_consent" in dst_cols and "training_consent" not in cols:
        cols.append("training_consent")
        overrides["training_consent"] = "legacy"
    src_cols = [c for c in cols if c not in overrides or c in {"id"}]
    rows = await src.fetch(f"select {', '.join(src_cols)} from {table} where id = any($1::uuid[])", list(ids))
    placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
    sql = f"insert into {table} ({', '.join(cols)}) values ({placeholders}) on conflict (id) do nothing"
    inserted = 0
    for row in rows:
        values = [overrides[c] if c in overrides else row[c] for c in cols]
        status = await dst.execute(sql, *values)
        inserted += int(status.split()[-1])
    return inserted


async def main(target: str) -> None:
    if not target.endswith("_preview"):
        raise SystemExit(f"目标库 {target} 不是预览库，已拒绝执行")
    src = await asyncpg.connect(database=SOURCE_DB)
    dst = await asyncpg.connect(database=target)
    try:
        await src.execute("set default_transaction_read_only = on")
        showcases = await src.fetch("select id, page_id from scene_showcases")
        page_ids = {r["page_id"] for r in showcases}
        pages = await src.fetch(
            "select id, owner_user_id, conversation_id, batch_id, current_version_id, parent_page_id from pages where id = any($1::uuid[])",
            list(page_ids),
        )
        versions = await src.fetch("select id, batch_id from page_versions where page_id = any($1::uuid[])", list(page_ids))
        batch_ids = {r["batch_id"] for r in pages if r["batch_id"]} | {r["batch_id"] for r in versions if r["batch_id"]}
        batches = await src.fetch(
            "select id, conversation_id, base_page_id from generation_batches where id = any($1::uuid[])", list(batch_ids))
        conv_ids = {r["conversation_id"] for r in pages if r["conversation_id"]} | {r["conversation_id"] for r in batches}
        convs = await src.fetch("select id, owner_user_id, root_batch_id from conversations where id = any($1::uuid[])", list(conv_ids))
        user_ids = {r["owner_user_id"] for r in pages if r["owner_user_id"]} | {r["owner_user_id"] for r in convs if r["owner_user_id"]}

        async with dst.transaction():
            stats = {
                "users": await copy_rows(src, dst, "users", user_ids, USER_OVERRIDES),
                "conversations": await copy_rows(src, dst, "conversations", conv_ids, {"root_batch_id": None}),
                "generation_batches": await copy_rows(src, dst, "generation_batches", batch_ids, {"base_page_id": None}),
                "pages": await copy_rows(src, dst, "pages", page_ids, {"current_version_id": None, "parent_page_id": None}),
                "page_versions": await copy_rows(src, dst, "page_versions", {r["id"] for r in versions}),
                "scene_showcases": await copy_rows(src, dst, "scene_showcases", {r["id"] for r in showcases}),
            }
            # 回填循环引用字段（只回填引用目标已在预览库中的）
            for r in convs:
                if r["root_batch_id"] in batch_ids:
                    await dst.execute("update conversations set root_batch_id=$2 where id=$1 and root_batch_id is null", r["id"], r["root_batch_id"])
            for r in batches:
                if r["base_page_id"] in page_ids:
                    await dst.execute("update generation_batches set base_page_id=$2 where id=$1 and base_page_id is null", r["id"], r["base_page_id"])
            for r in pages:
                await dst.execute("update pages set current_version_id=$2 where id=$1 and current_version_id is null", r["id"], r["current_version_id"])
                if r["parent_page_id"] in page_ids:
                    await dst.execute("update pages set parent_page_id=$2 where id=$1 and parent_page_id is null", r["id"], r["parent_page_id"])
        for table, n in stats.items():
            print(f"{table}: 新增 {n} 行")
    finally:
        await src.close()
        await dst.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="复制生产库首页案例到预览库")
    parser.add_argument("--target", default="stars_page_preview")
    asyncio.run(main(parser.parse_args().target))
