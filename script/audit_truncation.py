"""只读统计线上历史 generation_tasks.model_output_text 缺失 </html> / 疑似截断的比例。

安全约束：
- 连接级设置 default_transaction_read_only=on，并在只读事务中执行纯 SELECT 统计，绝不修改任何表数据。
- 所有文本扫描在 PostgreSQL 端聚合完成，不拉取大文本列到客户端内存。

用法（在仓库根目录）：
    code/backend/.venv/bin/python script/audit_truncation.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "code" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402


def _resolve_dsn() -> str:
    db_env = ROOT / "config" / "db.env"
    if db_env.exists():
        load_dotenv(db_env, override=False)

    from app.core.config import get_settings

    dsn = get_settings().async_database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    return dsn


STATUS_SUMMARY_SQL = """
SELECT
    status,
    COUNT(*) AS total_tasks,
    COUNT(*) FILTER (WHERE model_output_text IS NOT NULL AND btrim(model_output_text) <> '') AS with_output,
    COUNT(*) FILTER (
        WHERE model_output_text IS NOT NULL
          AND btrim(model_output_text) <> ''
          AND position('</html>' IN lower(model_output_text)) = 0
    ) AS missing_html_close,
    COUNT(*) FILTER (
        WHERE model_output_text IS NOT NULL
          AND btrim(model_output_text) <> ''
          AND position('</html>' IN lower(model_output_text)) = 0
          AND position('</body>' IN lower(model_output_text)) = 0
    ) AS missing_both_body_and_html
FROM generation_tasks
GROUP BY status
ORDER BY total_tasks DESC;
"""

BY_MODEL_SUCCEEDED_SQL = """
SELECT
    COALESCE(model_key, '(null)') AS model_key,
    COUNT(*) AS succeeded_with_output,
    COUNT(*) FILTER (WHERE position('</html>' IN lower(model_output_text)) = 0) AS missing_html_close,
    COUNT(*) FILTER (
        WHERE position('</html>' IN lower(model_output_text)) = 0
          AND position('</body>' IN lower(model_output_text)) = 0
    ) AS hard_truncated,
    ROUND(AVG(char_length(model_output_text))) AS avg_chars,
    MAX(char_length(model_output_text)) FILTER (
        WHERE position('</html>' IN lower(model_output_text)) = 0
    ) AS max_truncated_chars,
    ROUND(AVG(char_length(model_output_text)) FILTER (
        WHERE position('</html>' IN lower(model_output_text)) = 0
    )) AS avg_truncated_chars
FROM generation_tasks
WHERE status = 'succeeded'
  AND model_output_text IS NOT NULL
  AND btrim(model_output_text) <> ''
GROUP BY COALESCE(model_key, '(null)')
ORDER BY missing_html_close DESC, succeeded_with_output DESC;
"""

RECENT_SAMPLES_SQL = """
SELECT
    id,
    COALESCE(model_key, '(null)') AS model_key,
    created_at,
    char_length(model_output_text) AS chars,
    position('</body>' IN lower(model_output_text)) > 0 AS has_body_close,
    right(model_output_text, 80) AS tail_snippet
FROM generation_tasks
WHERE status = 'succeeded'
  AND model_output_text IS NOT NULL
  AND btrim(model_output_text) <> ''
  AND position('</html>' IN lower(model_output_text)) = 0
ORDER BY created_at DESC
LIMIT 10;
"""


def _pct(num: int, den: int) -> str:
    if den <= 0:
        return "0.00%"
    return f"{(num * 100.0 / den):.2f}%"


async def run_audit() -> None:
    import asyncpg

    os.chdir(BACKEND)
    dsn = _resolve_dsn()
    conn = await asyncpg.connect(
        dsn=dsn,
        server_settings={"default_transaction_read_only": "on"},
    )
    try:
        async with conn.transaction(readonly=True):
            status_rows = await conn.fetch(STATUS_SUMMARY_SQL)
            model_rows = await conn.fetch(BY_MODEL_SUCCEEDED_SQL)
            sample_rows = await conn.fetch(RECENT_SAMPLES_SQL)
    finally:
        await conn.close()

    print("=== 1. generation_tasks 按状态统计 ===")
    total_all = 0
    total_with_out = 0
    total_missing_html = 0
    for row in status_rows:
        total_all += int(row["total_tasks"])
        with_out = int(row["with_output"])
        miss_html = int(row["missing_html_close"])
        miss_both = int(row["missing_both_body_and_html"])
        total_with_out += with_out
        total_missing_html += miss_html
        print(
            f"  status={row['status']:<10} total={row['total_tasks']:>6} | "
            f"with_output={with_out:>6} | "
            f"缺</html>={miss_html:>5} ({_pct(miss_html, with_out)}) | "
            f"同时缺</body>与</html>={miss_both:>5} ({_pct(miss_both, with_out)})"
        )
    print(
        f"  [合计] total={total_all} | with_output={total_with_out} | "
        f"缺</html>={total_missing_html} ({_pct(total_missing_html, total_with_out)})"
    )

    print("\n=== 2. succeeded 任务按模型统计（仅含非空 model_output_text） ===")
    succ_total = sum(int(r["succeeded_with_output"]) for r in model_rows)
    succ_miss_html = sum(int(r["missing_html_close"]) for r in model_rows)
    succ_hard = sum(int(r["hard_truncated"]) for r in model_rows)
    succ_benign = succ_miss_html - succ_hard
    print(
        f"  成功且有输出总数={succ_total} | 缺</html>={succ_miss_html} ({_pct(succ_miss_html, succ_total)}) | "
        f"疑似硬截断(同时缺</body>与</html>)={succ_hard} ({_pct(succ_hard, succ_total)}) | "
        f"仅漏写</html>(含</body>)={succ_benign} ({_pct(succ_benign, succ_total)})"
    )
    for row in model_rows:
        cnt = int(row["succeeded_with_output"])
        miss = int(row["missing_html_close"])
        hard = int(row["hard_truncated"])
        avg_c = int(row["avg_chars"] or 0)
        avg_tc = int(row["avg_truncated_chars"] or 0)
        max_tc = int(row["max_truncated_chars"] or 0)
        print(
            f"  model={row['model_key']:<22} succeeded={cnt:>5} | "
            f"缺</html>={miss:>4} ({_pct(miss, cnt):>6}) | "
            f"硬截断={hard:>4} ({_pct(hard, cnt):>6}) | "
            f"均长={avg_c:>6} chars | 截断均长/最大={avg_tc}/{max_tc}"
        )

    print("\n=== 3. 最近缺 </html> 的 succeeded 样本（最多 10 条） ===")
    if not sample_rows:
        print("  （无缺失 </html> 的成功任务样本）")
    for row in sample_rows:
        snippet = repr(str(row["tail_snippet"] or ""))
        ts = row["created_at"].strftime("%Y-%m-%d %H:%M:%S") if row["created_at"] else "-"
        print(
            f"  {ts} | model={row['model_key']:<20} | chars={row['chars']:>6} | "
            f"has_</body>={bool(row['has_body_close'])} | tail={snippet}"
        )


if __name__ == "__main__":
    asyncio.run(run_audit())
