#!/usr/bin/env python3
"""只读抽检已发布页面的无头浏览器渲染健康度（上线前/观察期评估误报率与真实问题率）。

安全约束：
1. 数据库纯只读：连接级设置 `default_transaction_read_only=on`，并在 `readonly=True` 事务中仅执行 SELECT 抽样；
   抽样读取完成后立即关闭数据库连接，绝不在无头浏览器运行期间持有数据库连接或写任何表。
2. 隐私脱敏：终端与默认 JSON 报告仅记录页面短哈希、模型标识、字节规模、耗时、内存与问题摘要，不输出用户 prompt 或 HTML 原文。
3. 与线上完全同构：对抽样 HTML 先执行 `extract_html_document` + `sanitize_html`，再调用 `check_page_render`（同款沙箱 CSP 与可信 CDN 白名单）。

用法示例（仓库根目录）：
    code/backend/.venv/bin/python script/audit_render_check.py --limit=200 --concurrency=2
    code/backend/.venv/bin/python script/audit_render_check.py --limit=50 --output=/tmp/render_check_audit.json
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "code" / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

DEFAULT_REPORT_PATH = Path("/tmp/render_check_audit_report.json")
ENV_FILES = (
    ROOT / "config" / ".env",
    ROOT / "config" / "db.env",
    ROOT / "config" / "oss.env",
)


def _load_envs() -> None:
    for env_path in ENV_FILES:
        if env_path.exists():
            load_dotenv(env_path, override=False)


def _resolve_dsn() -> str:
    _load_envs()
    from app.core.config import get_settings

    dsn = get_settings().async_database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    return dsn


SAMPLE_PUBLISHED_PAGES_SQL = """
SELECT
    p.id AS page_id,
    COALESCE(p.model_key, pv.model_key, '(unknown)') AS model_key,
    p.skill_key,
    p.scene_key,
    p.created_at,
    pv.storage_key,
    t.model_output_text
FROM pages p
LEFT JOIN page_versions pv ON pv.id = p.current_version_id
LEFT JOIN LATERAL (
    SELECT gt.model_output_text
    FROM generation_tasks gt
    WHERE gt.page_id = p.id
      AND gt.status = 'succeeded'
      AND gt.model_output_text IS NOT NULL
      AND btrim(gt.model_output_text) <> ''
    ORDER BY gt.created_at DESC
    LIMIT 1
) t ON TRUE
WHERE p.status = 'ready'
  AND p.deleted_at IS NULL
  AND p.moderation_status = 'ok'
  AND (t.model_output_text IS NOT NULL OR pv.storage_key IS NOT NULL)
ORDER BY p.created_at DESC
LIMIT $1;
"""


async def fetch_readonly_pages(limit: int) -> list[dict[str, Any]]:
    """纯只读拉取最近已发布页面的 HTML，完成后立即关闭数据库连接。"""
    import asyncpg

    from app.services.html_sanitizer import extract_html_document, sanitize_html
    from app.services.storage.factory import create_storage_provider

    dsn = _resolve_dsn()
    conn = await asyncpg.connect(
        dsn=dsn,
        server_settings={"default_transaction_read_only": "on"},
        timeout=15,
    )
    try:
        async with conn.transaction(readonly=True):
            rows = await conn.fetch(SAMPLE_PUBLISHED_PAGES_SQL, max(1, int(limit)))
    finally:
        await conn.close()

    storage = None
    items: list[dict[str, Any]] = []
    for row in rows:
        raw_html: str | None = None
        source = "db_model_output"
        if row["model_output_text"]:
            raw_html = extract_html_document(str(row["model_output_text"]))
        elif row["storage_key"]:
            if storage is None:
                storage = create_storage_provider()
            try:
                fetched = await storage.get_text(str(row["storage_key"]))
                raw_html = extract_html_document(fetched) if fetched else None
                source = "oss_version"
            except Exception:  # noqa: BLE001
                raw_html = None

        if not raw_html:
            continue

        safe_html = sanitize_html(raw_html)
        page_id_str = str(row["page_id"])
        page_hash = "page_" + hashlib.sha256(page_id_str.encode("utf-8")).hexdigest()[:10]
        items.append(
            {
                "page_id": page_id_str,
                "page_hash": page_hash,
                "model_key": str(row["model_key"]),
                "skill_key": row["skill_key"],
                "scene_key": row["scene_key"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "source": source,
                "html": safe_html,
                "html_bytes": len(safe_html.encode("utf-8")),
            }
        )
    return items


def _percentile(sorted_vals: list[float], pct: float) -> float | None:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, math.ceil(len(sorted_vals) * pct) - 1))
    return round(sorted_vals[idx], 1)


def summarize_audit_results(
    records: list[dict[str, Any]],
    *,
    elapsed_wall_sec: float,
) -> dict[str, Any]:
    total = len(records)
    status_counter: Counter[str] = Counter(r["status"] for r in records)
    issue_kind_counter: Counter[str] = Counter()
    skip_reason_counter: Counter[str] = Counter()
    by_model: dict[str, Counter[str]] = defaultdict(Counter)

    durations = sorted(float(r["duration_ms"]) for r in records if r["duration_ms"] > 0)
    rss_values = [float(r["rss_mb"]) for r in records if r["rss_mb"] > 0]

    for rec in records:
        mkey = rec["model_key"]
        by_model[mkey]["total"] += 1
        by_model[mkey][rec["status"]] += 1
        if rec.get("skip_reason"):
            skip_reason_counter[str(rec["skip_reason"])] += 1
        seen_kinds_in_page: set[str] = set()
        for issue in rec.get("issues") or []:
            kind = str(issue.get("kind") or "unknown")
            seen_kinds_in_page.add(kind)
        for kind in seen_kinds_in_page:
            issue_kind_counter[kind] += 1
            by_model[mkey][f"kind_{kind}"] += 1

    evaluated = status_counter["passed"] + status_counter["issues_found"]
    issue_rate = round(status_counter["issues_found"] / evaluated, 4) if evaluated > 0 else None

    model_breakdown = []
    for mkey, cnt in sorted(by_model.items(), key=lambda kv: (-kv[1]["total"], kv[0])):
        m_eval = cnt["passed"] + cnt["issues_found"]
        model_breakdown.append(
            {
                "model_key": mkey,
                "total": cnt["total"],
                "passed": cnt["passed"],
                "issues_found": cnt["issues_found"],
                "skipped": cnt["skipped"],
                "issue_rate": round(cnt["issues_found"] / m_eval, 4) if m_eval > 0 else None,
                "js_error_pages": cnt["kind_js_error"],
                "blank_page_pages": cnt["kind_blank_page"],
                "hang_pages": cnt["kind_hang"],
            }
        )

    return {
        "total_pages": total,
        "evaluated_pages": evaluated,
        "passed_count": status_counter["passed"],
        "issues_found_count": status_counter["issues_found"],
        "skipped_count": status_counter["skipped"],
        "issue_rate": issue_rate,
        "pages_by_issue_kind": dict(issue_kind_counter),
        "skip_reasons": dict(skip_reason_counter),
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
        "p95_duration_ms": _percentile(durations, 0.95),
        "max_rss_mb": round(max(rss_values), 1) if rss_values else None,
        "elapsed_wall_seconds": round(elapsed_wall_sec, 2),
        "by_model": model_breakdown,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只读抽样已发布页面并运行无头浏览器渲染检查统计")
    parser.add_argument("--limit", type=int, default=200, help="抽样最近已发布页面数量上限（默认 200）")
    parser.add_argument("--concurrency", type=int, default=2, help="并发渲染检查子进程数（默认 2）")
    parser.add_argument("--timeout", type=float, default=15.0, help="单页检查硬超时秒数（默认 15.0）")
    parser.add_argument("--settle-ms", type=int, default=1300, help="加载完成后额外观察毫秒数（默认 1300）")
    parser.add_argument(
        "--allow-cdn",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否放行平台白名单可信 CDN 脚本（默认开启）",
    )
    parser.add_argument(
        "--include-page-id",
        action="store_true",
        default=False,
        help="在输出 JSON 中附带真实 page_id 以便本地排障（默认仅保留脱敏哈希）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=f"JSON 统计报告输出路径（默认 {DEFAULT_REPORT_PATH}）",
    )
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.chdir(BACKEND)
    _load_envs()

    from app.services.page_render_check import check_page_render, resolve_chromium_executable

    chromium = resolve_chromium_executable()
    if not chromium:
        print("未检测到可用的无头 Chromium，请检查 PAGE_RENDER_CHECK_CHROMIUM_PATH 或 Playwright 浏览器缓存。", file=sys.stderr)
        return 1

    print(f"[1/3] 正在纯只读抽样最近 {args.limit} 个已发布页面（default_transaction_read_only=on）...", flush=True)
    pages = await fetch_readonly_pages(args.limit)
    if not pages:
        print("未查询到符合条件的已发布页面样本。")
        return 0

    print(
        f"[2/3] 共获取 {len(pages)} 个页面样本，开始并发执行无头渲染检查 "
        f"(chromium={chromium}, concurrency={args.concurrency}, timeout={args.timeout:g}s, settle={args.settle_ms}ms)...",
        flush=True,
    )

    worker_sem = asyncio.Semaphore(max(1, args.concurrency))
    max_queue = max(len(pages) + 4, 16)
    wall_start = time.perf_counter()

    async def _inspect_one(idx: int, item: dict[str, Any]) -> dict[str, Any]:
        async with worker_sem:
            res = await check_page_render(
                item["html"],
                allow_cdn=args.allow_cdn,
                timeout_seconds=args.timeout,
                settle_ms=args.settle_ms,
                concurrency=args.concurrency,
                max_queue=max_queue,
            )
        rec: dict[str, Any] = {
            "index": idx,
            "page_hash": item["page_hash"],
            "model_key": item["model_key"],
            "skill_key": item["skill_key"],
            "scene_key": item["scene_key"],
            "created_at": item["created_at"],
            "source": item["source"],
            "html_bytes": item["html_bytes"],
            "status": res.status,
            "duration_ms": round(res.duration_ms, 1),
            "rss_mb": round(res.rss_mb, 1),
            "skip_reason": res.skip_reason,
            "issues": [issue.to_dict() for issue in res.issues],
        }
        if args.include_page_id:
            rec["page_id"] = item["page_id"]
        if res.status == "issues_found":
            kinds = ",".join(i.kind for i in res.issues)
            print(
                f"  [{idx:>3}/{len(pages)}] ISSUES({kinds}) model={item['model_key']:<18} "
                f"id={item['page_hash']} {res.duration_ms:.0f}ms | {res.issues[0].summary}",
                flush=True,
            )
        return rec

    records = list(await asyncio.gather(*(_inspect_one(i + 1, item) for i, item in enumerate(pages))))
    elapsed = time.perf_counter() - wall_start
    summary = summarize_audit_results(records, elapsed_wall_sec=elapsed)

    pct_str = f"{summary['issue_rate'] * 100:.2f}%" if summary["issue_rate"] is not None else "-"
    print("\n=== 渲染检查离线抽检汇总 ===")
    print(
        f"  总样本={summary['total_pages']} | passed={summary['passed_count']} | "
        f"issues_found={summary['issues_found_count']} ({pct_str}) | skipped={summary['skipped_count']}"
    )
    print(
        f"  问题分类={summary['pages_by_issue_kind']} | 均耗时={summary['avg_duration_ms']}ms | "
        f"P95={summary['p95_duration_ms']}ms | 最大RSS={summary['max_rss_mb']}MB | 墙钟={summary['elapsed_wall_seconds']}s"
    )
    for m in summary["by_model"]:
        m_rate = f"{m['issue_rate'] * 100:.1f}%" if m["issue_rate"] is not None else "-"
        print(
            f"  - {m['model_key']:<20}: total={m['total']:>3}, passed={m['passed']:>3}, "
            f"issues={m['issues_found']:>3} ({m_rate:>5}) "
            f"[js={m['js_error_pages']}, blank={m['blank_page_pages']}, hang={m['hang_pages']}]"
        )

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": {
            "limit": args.limit,
            "concurrency": args.concurrency,
            "timeout_seconds": args.timeout,
            "settle_ms": args.settle_ms,
            "allow_cdn": args.allow_cdn,
        },
        "summary": summary,
        "issue_samples": [r for r in records if r["status"] == "issues_found"],
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[3/3] 报告已写入: {args.output}")
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
