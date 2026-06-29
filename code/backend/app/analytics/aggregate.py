"""每日运营指标聚合：把明细层数据幂等写入 metric_daily / retention_cohort / funnel_daily。

口径约定：
- 所有「自然日」按 UTC+8 划分，与财务账期一致。
- 注册用户（真实账号）判定为 `users.phone IS NOT NULL`；匿名转化的中间账号 phone 为空、不计入。
- DAU/WAU/MAU 的「活跃」= 当日有生成任务、或有会话活动、或有前端埋点的去重用户。
- 留存采用 day-N 口径（cohort 起始日 + 第 N 天当天是否活跃），login 看登录活跃、create 看是否再次生成。
- 幂等：按唯一键 upsert，可反复重跑修正历史。

CLI：
    python -m app.analytics.aggregate                # 刷新昨天+今天（默认）并重算留存窗口
    python -m app.analytics.aggregate --date 2026-06-27
    python -m app.analytics.aggregate --days 7       # 最近 7 天
    python -m app.analytics.aggregate --backfill 90  # 回填最近 90 天
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import UTC, date, datetime, timedelta, timezone

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.entities import FunnelDaily, MetricDaily, RetentionCohort

try:  # tqdm 用于进度展示；缺失时退化为无进度，不影响功能。
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    def tqdm(iterable, **_kwargs):  # type: ignore
        return iterable


DAY_TZ = timezone(timedelta(hours=8))

# 留存口径：cohort 起始日 + 第 N 天。窗口取 35 天，保证 30 日留存可计算。
RETENTION_PERIODS = [1, 3, 7, 14, 30]
RETENTION_WINDOW_DAYS = 35

# 漏斗步骤（顺序决定展示），前段是前端意图事件、后段是后端真实结果，按当日量级口径拼装。
FUNNEL_STEPS: list[tuple[str, int]] = [
    ("landing", 1),
    ("prompt_input", 2),
    ("generate_click", 3),
    ("generate_success", 4),
    ("register", 5),
    ("first_recharge", 6),
]

# 活跃用户子查询：当日有生成任务、会话活动或前端埋点的用户 id 集合。
_ACTIVE_IDS_SQL = """
  SELECT gt.requested_by_user_id AS uid FROM generation_tasks gt
    WHERE gt.created_at >= :start AND gt.created_at < :end
  UNION
  SELECT s.user_id FROM user_sessions s
    WHERE s.user_id IS NOT NULL
      AND ((s.created_at >= :start AND s.created_at < :end)
           OR (s.last_seen_at >= :start AND s.last_seen_at < :end))
  UNION
  SELECT ae.user_id FROM analytics_events ae
    WHERE ae.user_id IS NOT NULL AND ae.created_at >= :start AND ae.created_at < :end
"""


def day_bounds(d: date) -> tuple[datetime, datetime]:
    """返回 [当日 00:00, 次日 00:00) 的 UTC+8 时间窗。"""
    start = datetime(d.year, d.month, d.day, tzinfo=DAY_TZ)
    return start, start + timedelta(days=1)


def today_in_tz() -> date:
    return datetime.now(DAY_TZ).date()


async def _scalar(session: AsyncSession, sql: str, **params) -> float:
    value = (await session.execute(text(sql), params)).scalar()
    return float(value) if value is not None else 0.0


async def _upsert_metric(
    session: AsyncSession, stat_date: date, metric_key: str, value: float, dims: dict | None = None
) -> None:
    dims = dims or {}
    stmt = pg_insert(MetricDaily).values(
        id=uuid.uuid4(), stat_date=stat_date, metric_key=metric_key, dims=dims, value=value
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_metric_daily", set_={"value": stmt.excluded.value, "updated_at": func.now()}
    )
    await session.execute(stmt)


async def _upsert_funnel(session: AsyncSession, stat_date: date, step: str, step_order: int, count: float) -> None:
    stmt = pg_insert(FunnelDaily).values(
        id=uuid.uuid4(), stat_date=stat_date, step=step, step_order=step_order, count=int(count)
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_funnel_daily",
        set_={"count": stmt.excluded.count, "step_order": stmt.excluded.step_order, "updated_at": func.now()},
    )
    await session.execute(stmt)


async def _upsert_retention(
    session: AsyncSession, cohort_date: date, kind: str, period_index: int, cohort_size: int, retained: int
) -> None:
    stmt = pg_insert(RetentionCohort).values(
        id=uuid.uuid4(),
        cohort_date=cohort_date,
        cohort_kind=kind,
        period_index=period_index,
        cohort_size=cohort_size,
        retained_count=retained,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_retention_cohort",
        set_={
            "cohort_size": stmt.excluded.cohort_size,
            "retained_count": stmt.excluded.retained_count,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def _active_count(session: AsyncSession, start: datetime, end: datetime, registered_only: bool) -> float:
    if registered_only:
        sql = (
            f"SELECT count(DISTINCT t.uid) FROM ({_ACTIVE_IDS_SQL}) t "
            "JOIN users u ON u.id = t.uid WHERE u.phone IS NOT NULL"
        )
    else:
        sql = f"SELECT count(DISTINCT t.uid) FROM ({_ACTIVE_IDS_SQL}) t WHERE t.uid IS NOT NULL"
    return await _scalar(session, sql, start=start, end=end)


async def aggregate_day(session: AsyncSession, stat_date: date) -> None:
    """聚合单日全部 metric_daily 与 funnel_daily 指标。"""
    start, end = day_bounds(stat_date)
    p = {"start": start, "end": end}

    # ---------- 增长 Acquisition ----------
    new_registered = await _scalar(
        session,
        "SELECT count(*) FROM users WHERE phone IS NOT NULL AND created_at >= :start AND created_at < :end",
        **p,
    )
    new_anon = await _scalar(
        session,
        "SELECT count(*) FROM anon_visitors WHERE created_at >= :start AND created_at < :end",
        **p,
    )
    cumulative_registered = await _scalar(
        session, "SELECT count(*) FROM users WHERE phone IS NOT NULL AND created_at < :end", end=end
    )
    cumulative_anon = await _scalar(
        session, "SELECT count(*) FROM anon_visitors WHERE created_at < :end", end=end
    )
    anon_converted_cumulative = await _scalar(
        session, "SELECT count(*) FROM users WHERE merged_into_user_id IS NOT NULL AND created_at < :end", end=end
    )
    await _upsert_metric(session, stat_date, "new_registered_users", new_registered)
    await _upsert_metric(session, stat_date, "new_anonymous_visitors", new_anon)
    await _upsert_metric(session, stat_date, "cumulative_registered_users", cumulative_registered)
    await _upsert_metric(session, stat_date, "cumulative_anonymous_visitors", cumulative_anon)
    await _upsert_metric(session, stat_date, "anon_converted_cumulative", anon_converted_cumulative)

    # ---------- 活跃 DAU / WAU / MAU ----------
    dau = await _active_count(session, start, end, registered_only=True)
    dau_all = await _active_count(session, start, end, registered_only=False)
    wau = await _active_count(session, end - timedelta(days=7), end, registered_only=True)
    mau = await _active_count(session, end - timedelta(days=30), end, registered_only=True)
    await _upsert_metric(session, stat_date, "dau", dau)
    await _upsert_metric(session, stat_date, "dau_all", dau_all)
    await _upsert_metric(session, stat_date, "wau", wau)
    await _upsert_metric(session, stat_date, "mau", mau)

    # ---------- 激活 Activation：注册当日起 24h 内有成功生成 ----------
    activated = await _scalar(
        session,
        """
        SELECT count(*) FROM users u
        WHERE u.phone IS NOT NULL AND u.created_at >= :start AND u.created_at < :end
          AND EXISTS (
            SELECT 1 FROM generation_tasks gt
            WHERE gt.requested_by_user_id = u.id AND gt.status = 'succeeded'
              AND gt.created_at >= u.created_at AND gt.created_at < u.created_at + interval '24 hours'
          )
        """,
        **p,
    )
    await _upsert_metric(session, stat_date, "activated_users", activated)

    # ---------- 参与 Engagement ----------
    batches_total = await _scalar(
        session, "SELECT count(*) FROM generation_batches WHERE created_at >= :start AND created_at < :end", **p
    )
    continuation_batches = await _scalar(
        session,
        "SELECT count(*) FROM generation_batches WHERE created_at >= :start AND created_at < :end AND kind <> 'create'",
        **p,
    )
    file_batches = await _scalar(
        session,
        """
        SELECT count(*) FROM generation_batches
        WHERE created_at >= :start AND created_at < :end
          AND input_file_names IS NOT NULL AND jsonb_array_length(input_file_names) > 0
        """,
        **p,
    )
    tasks_total = await _scalar(
        session, "SELECT count(*) FROM generation_tasks WHERE created_at >= :start AND created_at < :end", **p
    )
    distinct_creators = await _scalar(
        session,
        "SELECT count(DISTINCT requested_by_user_id) FROM generation_tasks "
        "WHERE created_at >= :start AND created_at < :end",
        **p,
    )
    await _upsert_metric(session, stat_date, "generation_batches", batches_total)
    await _upsert_metric(session, stat_date, "continuation_batches", continuation_batches)
    await _upsert_metric(session, stat_date, "file_upload_batches", file_batches)
    await _upsert_metric(session, stat_date, "generation_tasks", tasks_total)
    await _upsert_metric(session, stat_date, "distinct_creators", distinct_creators)

    # ---------- 质量 Quality ----------
    succeeded = await _scalar(
        session,
        "SELECT count(*) FROM generation_tasks WHERE status = 'succeeded' "
        "AND finished_at >= :start AND finished_at < :end",
        **p,
    )
    failed = await _scalar(
        session,
        "SELECT count(*) FROM generation_tasks WHERE status = 'failed' "
        "AND finished_at >= :start AND finished_at < :end",
        **p,
    )
    await _upsert_metric(session, stat_date, "gen_succeeded", succeeded)
    await _upsert_metric(session, stat_date, "gen_failed", failed)
    for pct, key in [(0.5, "gen_latency_p50"), (0.9, "gen_latency_p90"), (0.99, "gen_latency_p99")]:
        latency = await _scalar(
            session,
            f"""
            SELECT percentile_cont({pct}) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (finished_at - started_at)))
            FROM generation_tasks
            WHERE status = 'succeeded' AND started_at IS NOT NULL AND finished_at IS NOT NULL
              AND finished_at >= :start AND finished_at < :end
            """,
            **p,
        )
        await _upsert_metric(session, stat_date, key, latency)

    # 按模型拆分（生成量/成功/失败），dims = {"model_key": k}
    model_rows = (
        await session.execute(
            text(
                """
                SELECT model_key,
                       count(*) AS total,
                       count(*) FILTER (WHERE status = 'succeeded') AS ok,
                       count(*) FILTER (WHERE status = 'failed') AS bad
                FROM generation_tasks
                WHERE created_at >= :start AND created_at < :end AND model_key IS NOT NULL
                GROUP BY model_key
                """
            ),
            p,
        )
    ).all()
    for model_key, total, ok, bad in model_rows:
        dims = {"model_key": model_key}
        await _upsert_metric(session, stat_date, "gen_tasks_by_model", float(total), dims)
        await _upsert_metric(session, stat_date, "gen_success_by_model", float(ok), dims)
        await _upsert_metric(session, stat_date, "gen_failed_by_model", float(bad), dims)

    # 按技能场景拆分（命中分布），dims = {"skill_key": k|"none"}
    skill_rows = (
        await session.execute(
            text(
                """
                SELECT COALESCE(skill_key, 'none') AS sk, count(*) AS total
                FROM generation_tasks
                WHERE created_at >= :start AND created_at < :end
                GROUP BY COALESCE(skill_key, 'none')
                """
            ),
            p,
        )
    ).all()
    for skill_key, total in skill_rows:
        await _upsert_metric(session, stat_date, "gen_by_skill", float(total), {"skill_key": skill_key})

    # ---------- 传播 Virality ----------
    pv_total = await _scalar(
        session, "SELECT count(*) FROM page_view_events WHERE created_at >= :start AND created_at < :end", **p
    )
    pv_external = await _scalar(
        session,
        "SELECT count(*) FROM page_view_events WHERE created_at >= :start AND created_at < :end "
        "AND is_owner_view = false",
        **p,
    )
    uv_external = await _scalar(
        session,
        "SELECT count(DISTINCT ip_hash) FROM page_view_events WHERE created_at >= :start AND created_at < :end "
        "AND is_owner_view = false AND ip_hash IS NOT NULL",
        **p,
    )
    pages_with_external_view = await _scalar(
        session,
        "SELECT count(DISTINCT page_id) FROM page_view_events WHERE created_at >= :start AND created_at < :end "
        "AND is_owner_view = false",
        **p,
    )
    generated_pages = await _scalar(
        session,
        "SELECT count(*) FROM pages WHERE created_at >= :start AND created_at < :end AND deleted_at IS NULL",
        **p,
    )
    await _upsert_metric(session, stat_date, "page_views_total", pv_total)
    await _upsert_metric(session, stat_date, "page_views_external", pv_external)
    await _upsert_metric(session, stat_date, "page_uv_external", uv_external)
    await _upsert_metric(session, stat_date, "pages_with_external_view", pages_with_external_view)
    await _upsert_metric(session, stat_date, "generated_pages", generated_pages)

    # ---------- 商业化 Monetization ----------
    recharge_created = await _scalar(
        session, "SELECT count(*) FROM recharge_orders WHERE created_at >= :start AND created_at < :end", **p
    )
    recharge_paid = await _scalar(
        session,
        "SELECT count(*) FROM recharge_orders WHERE status = 'paid' AND paid_at >= :start AND paid_at < :end",
        **p,
    )
    recharge_amount = await _scalar(
        session,
        "SELECT COALESCE(sum(amount_cny), 0) FROM recharge_orders "
        "WHERE status = 'paid' AND paid_at >= :start AND paid_at < :end",
        **p,
    )
    new_paying_users = await _scalar(
        session,
        """
        SELECT count(*) FROM (
          SELECT user_id, min(paid_at) AS first_paid FROM recharge_orders
          WHERE status = 'paid' AND paid_at IS NOT NULL GROUP BY user_id
        ) t WHERE t.first_paid >= :start AND t.first_paid < :end
        """,
        **p,
    )
    paying_users_cumulative = await _scalar(
        session,
        "SELECT count(DISTINCT user_id) FROM recharge_orders WHERE status = 'paid' AND paid_at < :end",
        end=end,
    )
    consume_credits = await _scalar(
        session,
        "SELECT COALESCE(sum(-credits_delta), 0) FROM credit_transactions "
        "WHERE type = 'consume' AND created_at >= :start AND created_at < :end",
        **p,
    )
    gift_granted_credits = await _scalar(
        session,
        "SELECT COALESCE(sum(gift_delta), 0) FROM credit_transactions "
        "WHERE type = 'gift' AND created_at >= :start AND created_at < :end",
        **p,
    )
    gift_consumed_credits = await _scalar(
        session,
        "SELECT COALESCE(sum(-gift_delta), 0) FROM credit_transactions "
        "WHERE type = 'consume' AND created_at >= :start AND created_at < :end",
        **p,
    )
    await _upsert_metric(session, stat_date, "recharge_orders_created", recharge_created)
    await _upsert_metric(session, stat_date, "recharge_orders_paid", recharge_paid)
    await _upsert_metric(session, stat_date, "recharge_amount_cny", recharge_amount)
    await _upsert_metric(session, stat_date, "new_paying_users", new_paying_users)
    await _upsert_metric(session, stat_date, "paying_users_cumulative", paying_users_cumulative)
    await _upsert_metric(session, stat_date, "consume_credits", consume_credits)
    await _upsert_metric(session, stat_date, "gift_granted_credits", gift_granted_credits)
    await _upsert_metric(session, stat_date, "gift_consumed_credits", gift_consumed_credits)

    # ---------- 漏斗 Funnel（当日量级口径）----------
    await _aggregate_funnel_day(session, stat_date, start, end, new_registered, succeeded, new_paying_users)


async def _aggregate_funnel_day(
    session: AsyncSession,
    stat_date: date,
    start: datetime,
    end: datetime,
    new_registered: float,
    succeeded: float,
    new_paying_users: float,
) -> None:
    p = {"start": start, "end": end}

    def _session_distinct(event_name: str) -> str:
        return (
            "SELECT count(DISTINCT COALESCE(client_session_id, ip_hash)) FROM analytics_events "
            f"WHERE event_name = '{event_name}' AND created_at >= :start AND created_at < :end"
        )

    landing = await _scalar(session, _session_distinct("landing_view"), **p)
    prompt_input = await _scalar(session, _session_distinct("prompt_input"), **p)
    generate_click = await _scalar(session, _session_distinct("generate_click"), **p)

    values = {
        "landing": landing,
        "prompt_input": prompt_input,
        "generate_click": generate_click,
        "generate_success": succeeded,
        "register": new_registered,
        "first_recharge": new_paying_users,
    }
    for step, order in FUNNEL_STEPS:
        await _upsert_funnel(session, stat_date, step, order, values[step])


async def aggregate_retention(session: AsyncSession, reference_date: date) -> None:
    """重算最近 RETENTION_WINDOW_DAYS 个 cohort 的可计算留存单元（login / create 两种口径）。"""
    cohorts = [reference_date - timedelta(days=offset) for offset in range(RETENTION_WINDOW_DAYS + 1)]
    for cohort_date in tqdm(cohorts, desc="聚合留存 cohort", file=sys.stderr):
        c_start, c_end = day_bounds(cohort_date)
        cohort_size = int(
            await _scalar(
                session,
                "SELECT count(*) FROM users WHERE phone IS NOT NULL AND created_at >= :start AND created_at < :end",
                start=c_start,
                end=c_end,
            )
        )
        if cohort_size == 0:
            continue
        for period in RETENTION_PERIODS:
            target_day = cohort_date + timedelta(days=period)
            if target_day > reference_date:
                continue  # 该周期尚未到达，数据不完整，跳过
            t_start, t_end = day_bounds(target_day)
            # login 口径：cohort 用户在第 N 天是否活跃
            login_retained = int(
                await _scalar(
                    session,
                    f"""
                    SELECT count(DISTINCT c.id) FROM (
                      SELECT id FROM users WHERE phone IS NOT NULL AND created_at >= :c_start AND created_at < :c_end
                    ) c
                    JOIN ({_ACTIVE_IDS_SQL}) a ON a.uid = c.id
                    """,
                    c_start=c_start,
                    c_end=c_end,
                    start=t_start,
                    end=t_end,
                )
            )
            # create 口径：cohort 用户在第 N 天是否再次发起生成
            create_retained = int(
                await _scalar(
                    session,
                    """
                    SELECT count(DISTINCT c.id) FROM (
                      SELECT id FROM users WHERE phone IS NOT NULL AND created_at >= :c_start AND created_at < :c_end
                    ) c
                    WHERE EXISTS (
                      SELECT 1 FROM generation_tasks gt
                      WHERE gt.requested_by_user_id = c.id AND gt.created_at >= :start AND gt.created_at < :end
                    )
                    """,
                    c_start=c_start,
                    c_end=c_end,
                    start=t_start,
                    end=t_end,
                )
            )
            await _upsert_retention(session, cohort_date, "login", period, cohort_size, login_retained)
            await _upsert_retention(session, cohort_date, "create", period, cohort_size, create_retained)


async def run_aggregation(dates: list[date]) -> None:
    """聚合给定日期列表的每日指标，并以最大日期为基准重算留存窗口。"""
    if not dates:
        return
    print(f"开始聚合运营指标：{dates[0]} ~ {dates[-1]}（共 {len(dates)} 天）", flush=True)
    async with AsyncSessionLocal() as session:
        for stat_date in tqdm(dates, desc="聚合每日指标", file=sys.stderr):
            await aggregate_day(session, stat_date)
            await session.commit()
        await aggregate_retention(session, max(dates))
        await session.commit()
    print("运营指标聚合完成。", flush=True)


def _parse_dates(args: argparse.Namespace) -> list[date]:
    today = today_in_tz()
    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").date()
        return [target]
    if args.backfill:
        return [today - timedelta(days=offset) for offset in range(args.backfill - 1, -1, -1)]
    if args.days:
        return [today - timedelta(days=offset) for offset in range(args.days - 1, -1, -1)]
    # 默认：补全昨天 + 刷新今天
    return [today - timedelta(days=1), today]


def main() -> None:
    parser = argparse.ArgumentParser(description="星页运营指标聚合")
    parser.add_argument("--date", help="只聚合指定日期 YYYY-MM-DD")
    parser.add_argument("--days", type=int, help="聚合最近 N 天（含今天）")
    parser.add_argument("--backfill", type=int, help="回填最近 N 天（含今天）")
    args = parser.parse_args()
    dates = _parse_dates(args)
    asyncio.run(run_aggregation(dates))


if __name__ == "__main__":
    main()
