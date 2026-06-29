"""运营后台管理端接口（需要管理员权限）。

读取策略：
- 实时动态、用户明细、Case、传播/质量明细等走明细表实时查询（数据量小、要求新鲜）。
- 趋势、总览、留存、漏斗、参与分布等走聚合快照表（metric_daily / retention_cohort / funnel_daily），读得快。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

from app.analytics.aggregate import DAY_TZ, day_bounds, today_in_tz
from app.core.auth import require_admin
from app.core.config import get_model_registry, get_settings
from app.core.database import AsyncSessionLocal
from app.core.urls import build_page_url
from app.schemas.analytics import (
    DistributionItem,
    EngagementResponse,
    FailureReasonItem,
    FunnelResponse,
    FunnelStepItem,
    HotPageItem,
    MetricCard,
    MetricPoint,
    OpsCaseDetail,
    OpsCaseEvent,
    OpsCaseItem,
    OpsUserConversation,
    OpsUserItem,
    OpsUserProfile,
    OverviewResponse,
    QualityResponse,
    RealtimeEvent,
    RealtimeStats,
    RetentionResponse,
    RetentionRow,
    TrendSeries,
    TrendsResponse,
    ViralityResponse,
)

router = APIRouter(prefix="/api/admin/analytics", tags=["admin-analytics"])

# 漏斗步骤中文名
_FUNNEL_LABELS = {
    "landing": "落地访问",
    "prompt_input": "输入需求",
    "generate_click": "点击生成",
    "generate_success": "生成成功",
    "register": "注册",
    "first_recharge": "首次充值",
}


def _user_label(phone: str | None, display_name: str | None) -> str | None:
    return phone or display_name or None


def _model_label(model_key: str | None) -> str:
    if not model_key:
        return "未知"
    model = get_model_registry().get(model_key)
    return model.label if model else model_key


async def _scalar(session, sql: str, **params):
    return (await session.execute(text(sql), params)).scalar()


async def _scalar_f(session, sql: str, **params) -> float:
    value = await _scalar(session, sql, **params)
    return float(value) if value is not None else 0.0


async def _scalar_i(session, sql: str, **params) -> int:
    return int(await _scalar_f(session, sql, **params))


async def _metric_sum(session, metric_key: str, start: date, end: date) -> float:
    """聚合表中某流量型指标在 [start, end] 闭区间内的求和（dims 为空的总量行）。"""
    return await _scalar_f(
        session,
        "SELECT COALESCE(sum(value), 0) FROM metric_daily "
        "WHERE metric_key = :k AND dims = '{}'::jsonb AND stat_date >= :s AND stat_date <= :e",
        k=metric_key,
        s=start,
        e=end,
    )


async def _metric_latest(session, metric_key: str, on_or_before: date) -> float:
    """聚合表中某存量型指标在某日（含）之前的最新值。"""
    return await _scalar_f(
        session,
        "SELECT value FROM metric_daily "
        "WHERE metric_key = :k AND dims = '{}'::jsonb AND stat_date <= :d "
        "ORDER BY stat_date DESC LIMIT 1",
        k=metric_key,
        d=on_or_before,
    )


async def _metric_series(session, metric_key: str, start: date, end: date) -> list[MetricPoint]:
    rows = (
        await session.execute(
            text(
                "SELECT stat_date, value FROM metric_daily "
                "WHERE metric_key = :k AND dims = '{}'::jsonb AND stat_date >= :s AND stat_date <= :e "
                "ORDER BY stat_date ASC"
            ),
            {"k": metric_key, "s": start, "e": end},
        )
    ).all()
    return [MetricPoint(date=d.isoformat(), value=float(v)) for d, v in rows]


def _delta_pct(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current - previous) / previous


@router.get("/realtime", response_model=RealtimeStats)
async def realtime(request: Request) -> RealtimeStats:
    """实时动态：在线、进行中任务、今日累计与最近关键事件。直查明细表保证新鲜。"""
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        start, end = day_bounds(today_in_tz())
        p = {"start": start, "end": end}

        online_now = await _scalar_i(
            session,
            "SELECT count(DISTINCT user_id) FROM user_sessions "
            "WHERE revoked_at IS NULL AND last_seen_at >= now() - interval '5 minutes'",
        )
        running_tasks = await _scalar_i(session, "SELECT count(*) FROM generation_tasks WHERE status = 'running'")
        pending_tasks = await _scalar_i(session, "SELECT count(*) FROM generation_tasks WHERE status = 'pending'")
        failed_1h = await _scalar_i(
            session,
            "SELECT count(*) FROM generation_tasks WHERE status = 'failed' "
            "AND finished_at >= now() - interval '1 hour'",
        )

        today_new_registered = await _scalar_i(
            session,
            "SELECT count(*) FROM users WHERE phone IS NOT NULL AND created_at >= :start AND created_at < :end",
            **p,
        )
        today_new_anon = await _scalar_i(
            session, "SELECT count(*) FROM anon_visitors WHERE created_at >= :start AND created_at < :end", **p
        )
        today_ok = await _scalar_i(
            session,
            "SELECT count(*) FROM generation_tasks WHERE status = 'succeeded' "
            "AND finished_at >= :start AND finished_at < :end",
            **p,
        )
        today_failed = await _scalar_i(
            session,
            "SELECT count(*) FROM generation_tasks WHERE status = 'failed' "
            "AND finished_at >= :start AND finished_at < :end",
            **p,
        )
        today_recharge = await _scalar_f(
            session,
            "SELECT COALESCE(sum(amount_cny), 0) FROM recharge_orders "
            "WHERE status = 'paid' AND paid_at >= :start AND paid_at < :end",
            **p,
        )
        today_pv = await _scalar_i(
            session, "SELECT count(*) FROM page_view_events WHERE created_at >= :start AND created_at < :end", **p
        )
        today_active = await _scalar_i(
            session,
            """
            SELECT count(DISTINCT t.uid) FROM (
              SELECT requested_by_user_id AS uid FROM generation_tasks
                WHERE created_at >= :start AND created_at < :end
              UNION
              SELECT user_id FROM user_sessions
                WHERE user_id IS NOT NULL AND last_seen_at >= :start AND last_seen_at < :end
              UNION
              SELECT user_id FROM analytics_events
                WHERE user_id IS NOT NULL AND created_at >= :start AND created_at < :end
            ) t JOIN users u ON u.id = t.uid WHERE u.phone IS NOT NULL
            """,
            **p,
        )

        events = await _recent_events(session)

    return RealtimeStats(
        online_now=online_now,
        running_tasks=running_tasks,
        pending_tasks=pending_tasks,
        failed_tasks_1h=failed_1h,
        today_new_registered=today_new_registered,
        today_new_anonymous=today_new_anon,
        today_gen_succeeded=today_ok,
        today_gen_failed=today_failed,
        today_active_users=today_active,
        today_recharge_cny=today_recharge,
        today_page_views=today_pv,
        recent_events=events,
    )


async def _recent_events(session) -> list[RealtimeEvent]:
    events: list[RealtimeEvent] = []

    for phone, display_name, created_at in (
        await session.execute(
            text(
                "SELECT phone, display_name, created_at FROM users "
                "WHERE phone IS NOT NULL ORDER BY created_at DESC LIMIT 8"
            )
        )
    ).all():
        events.append(
            RealtimeEvent(kind="register", title="新用户注册", detail=_user_label(phone, display_name), at=created_at)
        )

    for status_, model_key, finished_at, phone, display_name in (
        await session.execute(
            text(
                "SELECT t.status, t.model_key, t.finished_at, u.phone, u.display_name "
                "FROM generation_tasks t JOIN users u ON u.id = t.requested_by_user_id "
                "WHERE t.status IN ('succeeded', 'failed') AND t.finished_at IS NOT NULL "
                "ORDER BY t.finished_at DESC LIMIT 12"
            )
        )
    ).all():
        ok = status_ == "succeeded"
        events.append(
            RealtimeEvent(
                kind="gen_success" if ok else "gen_failed",
                title="生成成功" if ok else "生成失败",
                detail=f"{_user_label(phone, display_name) or '匿名'} · {_model_label(model_key)}",
                at=finished_at,
            )
        )

    for amount, paid_at, phone, display_name in (
        await session.execute(
            text(
                "SELECT r.amount_cny, r.paid_at, u.phone, u.display_name "
                "FROM recharge_orders r JOIN users u ON u.id = r.user_id "
                "WHERE r.status = 'paid' AND r.paid_at IS NOT NULL ORDER BY r.paid_at DESC LIMIT 6"
            )
        )
    ).all():
        events.append(
            RealtimeEvent(
                kind="recharge",
                title=f"充值 ¥{float(amount):.2f}",
                detail=_user_label(phone, display_name),
                at=paid_at,
            )
        )

    for created_at, title in (
        await session.execute(
            text(
                "SELECT pv.created_at, p.title FROM page_view_events pv "
                "LEFT JOIN pages p ON p.id = pv.page_id "
                "WHERE pv.is_owner_view = false ORDER BY pv.created_at DESC LIMIT 8"
            )
        )
    ).all():
        events.append(
            RealtimeEvent(kind="page_view", title="页面被访问", detail=title or "（无标题）", at=created_at)
        )

    events.sort(key=lambda e: e.at, reverse=True)
    return events[:20]


# 趋势接口允许查询的指标键（流量型，逐日成点）。
_TREND_METRICS = {
    "dau",
    "dau_all",
    "wau",
    "mau",
    "new_registered_users",
    "new_anonymous_visitors",
    "cumulative_registered_users",
    "generation_tasks",
    "generation_batches",
    "gen_succeeded",
    "gen_failed",
    "distinct_creators",
    "page_views_total",
    "page_views_external",
    "page_uv_external",
    "generated_pages",
    "recharge_amount_cny",
    "recharge_orders_paid",
    "new_paying_users",
    "consume_credits",
    "activated_users",
}


@router.get("/trends", response_model=TrendsResponse)
async def trends(
    request: Request,
    metrics: str = Query(default="dau,new_registered_users,gen_succeeded"),
    days: int = Query(default=30, ge=1, le=180),
) -> TrendsResponse:
    """通用趋势：从聚合表读取若干指标的逐日序列。"""
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        end = today_in_tz()
        start = end - timedelta(days=days - 1)
        keys = [m.strip() for m in metrics.split(",") if m.strip() in _TREND_METRICS]
        if not keys:
            keys = ["dau"]
        series = [TrendSeries(metric=k, points=await _metric_series(session, k, start, end)) for k in keys]
    return TrendsResponse(days=days, series=series)


@router.get("/overview", response_model=OverviewResponse)
async def overview(request: Request, days: int = Query(default=30, ge=1, le=180)) -> OverviewResponse:
    """总览：北极星 + 核心 KPI 卡（含环比上一等长周期）。"""
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        end = today_in_tz()
        start = end - timedelta(days=days - 1)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)

        # 北极星 WAGP：最近 7 天「成功生成 + 至少一次外部访问」的页面数。
        wagp_start, _ = day_bounds(end - timedelta(days=6))
        _, wagp_end = day_bounds(end)
        north_star = await _scalar_i(
            session,
            """
            SELECT count(*) FROM pages p
            WHERE p.deleted_at IS NULL AND p.status = 'ready'
              AND p.created_at >= :s AND p.created_at < :e
              AND EXISTS (
                SELECT 1 FROM page_view_events pv
                WHERE pv.page_id = p.id AND pv.is_owner_view = false
              )
            """,
            s=wagp_start,
            e=wagp_end,
        )

        cards: list[MetricCard] = []

        async def add_sum(key: str, label: str, unit: str, hint: str | None = None) -> None:
            cur = await _metric_sum(session, key, start, end)
            prev = await _metric_sum(session, key, prev_start, prev_end)
            cards.append(
                MetricCard(key=key, label=label, value=cur, unit=unit, delta_pct=_delta_pct(cur, prev), hint=hint)
            )

        async def add_latest(key: str, label: str, unit: str, hint: str | None = None) -> None:
            cur = await _metric_latest(session, key, end)
            prev = await _metric_latest(session, key, prev_end)
            cards.append(
                MetricCard(key=key, label=label, value=cur, unit=unit, delta_pct=_delta_pct(cur, prev), hint=hint)
            )

        await add_latest("dau", "DAU（日活）", "", "最新一日活跃注册用户")
        await add_latest("wau", "WAU（周活）", "", "近 7 日活跃")
        await add_latest("mau", "MAU（月活）", "", "近 30 日活跃")
        await add_latest("cumulative_registered_users", "累计注册用户", "")
        await add_sum("new_registered_users", "新增注册", "", "周期内新增注册用户")
        await add_sum("new_anonymous_visitors", "新增匿名访客", "")
        await add_sum("gen_succeeded", "成功生成页面", "")
        await add_sum("recharge_amount_cny", "充值金额", "cny")
        await add_sum("new_paying_users", "新增付费用户", "")

        # 成功率（周期内）
        ok = await _metric_sum(session, "gen_succeeded", start, end)
        bad = await _metric_sum(session, "gen_failed", start, end)
        success_rate = ok / (ok + bad) if (ok + bad) > 0 else 0.0
        prev_ok = await _metric_sum(session, "gen_succeeded", prev_start, prev_end)
        prev_bad = await _metric_sum(session, "gen_failed", prev_start, prev_end)
        prev_rate = prev_ok / (prev_ok + prev_bad) if (prev_ok + prev_bad) > 0 else 0.0
        cards.append(
            MetricCard(
                key="success_rate",
                label="生成成功率",
                value=success_rate,
                unit="pct",
                delta_pct=_delta_pct(success_rate, prev_rate),
            )
        )

    return OverviewResponse(
        days=days,
        range_start=start.isoformat(),
        range_end=end.isoformat(),
        north_star_wagp=north_star,
        cards=cards,
    )


@router.get("/retention", response_model=RetentionResponse)
async def retention(
    request: Request,
    kind: str = Query(default="login", pattern="^(login|create)$"),
    weeks: int = Query(default=5, ge=1, le=8),
) -> RetentionResponse:
    """留存矩阵：按 cohort 起始日返回各周期留存率。kind=login 看登录活跃，create 看再次生成。"""
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        since = today_in_tz() - timedelta(days=weeks * 7)
        rows = (
            await session.execute(
                text(
                    "SELECT cohort_date, period_index, cohort_size, retained_count FROM retention_cohort "
                    "WHERE cohort_kind = :k AND cohort_date >= :s "
                    "ORDER BY cohort_date DESC, period_index ASC"
                ),
                {"k": kind, "s": since},
            )
        ).all()

    periods_set: set[int] = set()
    by_cohort: dict[date, dict] = {}
    for cohort_date, period_index, cohort_size, retained in rows:
        periods_set.add(int(period_index))
        entry = by_cohort.setdefault(cohort_date, {"size": int(cohort_size), "cells": {}})
        entry["size"] = int(cohort_size)
        rate = (retained / cohort_size) if cohort_size else None
        entry["cells"][int(period_index)] = rate

    periods = sorted(periods_set)
    result_rows = [
        RetentionRow(
            cohort_date=cohort_date.isoformat(),
            cohort_size=entry["size"],
            cells={p: entry["cells"].get(p) for p in periods},
        )
        for cohort_date, entry in sorted(by_cohort.items(), key=lambda kv: kv[0], reverse=True)
    ]
    return RetentionResponse(kind=kind, periods=periods, rows=result_rows)


@router.get("/funnel", response_model=FunnelResponse)
async def funnel(request: Request, days: int = Query(default=7, ge=1, le=90)) -> FunnelResponse:
    """全链路漏斗：聚合表中近 N 天各步骤求和，并算逐步/整体转化率。"""
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        end = today_in_tz()
        start = end - timedelta(days=days - 1)
        rows = (
            await session.execute(
                text(
                    "SELECT step, max(step_order) AS so, COALESCE(sum(count), 0) AS c FROM funnel_daily "
                    "WHERE stat_date >= :s AND stat_date <= :e GROUP BY step"
                ),
                {"s": start, "e": end},
            )
        ).all()

    counts = {step: (int(so), int(c)) for step, so, c in rows}
    ordered = sorted(_FUNNEL_LABELS.items(), key=lambda kv: ["landing", "prompt_input", "generate_click", "generate_success", "register", "first_recharge"].index(kv[0]))

    steps: list[FunnelStepItem] = []
    top_count: int | None = None
    prev_count: int | None = None
    for step, label in ordered:
        so, c = counts.get(step, (0, 0))
        if top_count is None:
            top_count = c
        steps.append(
            FunnelStepItem(
                step=step,
                label=label,
                step_order=so or len(steps) + 1,
                count=c,
                rate_from_prev=(c / prev_count) if prev_count else None,
                rate_from_top=(c / top_count) if top_count else None,
            )
        )
        prev_count = c
    return FunnelResponse(days=days, steps=steps)


@router.get("/engagement", response_model=EngagementResponse)
async def engagement(request: Request, days: int = Query(default=30, ge=1, le=180)) -> EngagementResponse:
    """参与度：续写率、文件上传率、人均生成，以及模型/技能分布。"""
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        end = today_in_tz()
        start = end - timedelta(days=days - 1)
        d_start, _ = day_bounds(start)
        _, d_end = day_bounds(end)

        total_batches = await _metric_sum(session, "generation_batches", start, end)
        continuation = await _metric_sum(session, "continuation_batches", start, end)
        file_batches = await _metric_sum(session, "file_upload_batches", start, end)
        total_tasks = await _metric_sum(session, "generation_tasks", start, end)

        # 人均生成：按明细去重创作者（聚合表的逐日 distinct 不可直接相加）。
        distinct_creators = await _scalar_i(
            session,
            "SELECT count(DISTINCT requested_by_user_id) FROM generation_tasks "
            "WHERE created_at >= :s AND created_at < :e",
            s=d_start,
            e=d_end,
        )

        model_rows = (
            await session.execute(
                text(
                    "SELECT dims->>'model_key' AS k, COALESCE(sum(value), 0) AS v FROM metric_daily "
                    "WHERE metric_key = 'gen_tasks_by_model' AND stat_date >= :s AND stat_date <= :e "
                    "GROUP BY dims->>'model_key' ORDER BY v DESC"
                ),
                {"s": start, "e": end},
            )
        ).all()
        skill_rows = (
            await session.execute(
                text(
                    "SELECT dims->>'skill_key' AS k, COALESCE(sum(value), 0) AS v FROM metric_daily "
                    "WHERE metric_key = 'gen_by_skill' AND stat_date >= :s AND stat_date <= :e "
                    "GROUP BY dims->>'skill_key' ORDER BY v DESC"
                ),
                {"s": start, "e": end},
            )
        ).all()

    model_total = sum(float(v) for _, v in model_rows) or 1.0
    model_distribution = [
        DistributionItem(key=k or "unknown", label=_model_label(k), value=float(v), share=float(v) / model_total)
        for k, v in model_rows
    ]
    skill_total = sum(float(v) for _, v in skill_rows) or 1.0
    skill_distribution = [
        DistributionItem(
            key=k or "none",
            label="无技能" if (k is None or k == "none") else k,
            value=float(v),
            share=float(v) / skill_total,
        )
        for k, v in skill_rows
    ]

    return EngagementResponse(
        days=days,
        total_batches=int(total_batches),
        total_tasks=int(total_tasks),
        continuation_rate=(continuation / total_batches) if total_batches else None,
        file_upload_rate=(file_batches / total_batches) if total_batches else None,
        avg_generations_per_creator=(total_tasks / distinct_creators) if distinct_creators else None,
        model_distribution=model_distribution,
        skill_distribution=skill_distribution,
    )


@router.get("/quality", response_model=QualityResponse)
async def quality(request: Request, days: int = Query(default=30, ge=1, le=180)) -> QualityResponse:
    """质量：成功率、耗时分位、按模型成功率、失败原因 TOP。分位与失败原因走明细实时算。"""
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        end = today_in_tz()
        start = end - timedelta(days=days - 1)
        d_start, _ = day_bounds(start)
        _, d_end = day_bounds(end)
        p = {"s": d_start, "e": d_end}

        succeeded = await _scalar_i(
            session,
            "SELECT count(*) FROM generation_tasks WHERE status = 'succeeded' AND finished_at >= :s "
            "AND finished_at < :e",
            **p,
        )
        failed = await _scalar_i(
            session,
            "SELECT count(*) FROM generation_tasks WHERE status = 'failed' AND finished_at >= :s AND finished_at < :e",
            **p,
        )
        total = succeeded + failed

        latencies: dict[str, float | None] = {}
        for pct, name in [(0.5, "p50"), (0.9, "p90"), (0.99, "p99")]:
            val = await _scalar(
                session,
                f"""
                SELECT percentile_cont({pct}) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (finished_at - started_at)))
                FROM generation_tasks
                WHERE status = 'succeeded' AND started_at IS NOT NULL AND finished_at IS NOT NULL
                  AND finished_at >= :s AND finished_at < :e
                """,
                **p,
            )
            latencies[name] = float(val) if val is not None else None

        model_rows = (
            await session.execute(
                text(
                    """
                    SELECT model_key,
                           count(*) AS total,
                           count(*) FILTER (WHERE status = 'succeeded') AS ok
                    FROM generation_tasks
                    WHERE created_at >= :s AND created_at < :e AND model_key IS NOT NULL
                    GROUP BY model_key ORDER BY total DESC
                    """
                ),
                p,
            )
        ).all()
        model_success = [
            DistributionItem(
                key=k, label=_model_label(k), value=float(ok) / float(t) if t else 0.0, share=float(t)
            )
            for k, t, ok in model_rows
        ]

        reason_rows = (
            await session.execute(
                text(
                    """
                    SELECT left(COALESCE(error_message, '未知错误'), 80) AS reason, count(*) AS c
                    FROM generation_tasks
                    WHERE status = 'failed' AND finished_at >= :s AND finished_at < :e
                    GROUP BY left(COALESCE(error_message, '未知错误'), 80) ORDER BY c DESC LIMIT 10
                    """
                ),
                p,
            )
        ).all()
        failure_reasons = [FailureReasonItem(reason=r, count=int(c)) for r, c in reason_rows]

        latency_trend = await _metric_series(session, "gen_latency_p90", start, end)

    return QualityResponse(
        days=days,
        total_tasks=total,
        succeeded=succeeded,
        failed=failed,
        success_rate=(succeeded / total) if total else None,
        latency_p50_sec=latencies["p50"],
        latency_p90_sec=latencies["p90"],
        latency_p99_sec=latencies["p99"],
        model_success=model_success,
        failure_reasons=failure_reasons,
        latency_trend=latency_trend,
    )


@router.get("/virality", response_model=ViralityResponse)
async def virality(request: Request, days: int = Query(default=30, ge=1, le=180)) -> ViralityResponse:
    """传播：PV/UV、分享传播比、热门页 TOP。明细实时算（区间 UV 需去重）。"""
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        end = today_in_tz()
        start = end - timedelta(days=days - 1)
        d_start, _ = day_bounds(start)
        _, d_end = day_bounds(end)
        p = {"s": d_start, "e": d_end}

        pv_total = await _scalar_i(
            session, "SELECT count(*) FROM page_view_events WHERE created_at >= :s AND created_at < :e", **p
        )
        pv_external = await _scalar_i(
            session,
            "SELECT count(*) FROM page_view_events WHERE created_at >= :s AND created_at < :e AND is_owner_view = false",
            **p,
        )
        uv_external = await _scalar_i(
            session,
            "SELECT count(DISTINCT ip_hash) FROM page_view_events WHERE created_at >= :s AND created_at < :e "
            "AND is_owner_view = false AND ip_hash IS NOT NULL",
            **p,
        )
        pages_with_view = await _scalar_i(
            session,
            "SELECT count(DISTINCT page_id) FROM page_view_events WHERE created_at >= :s AND created_at < :e "
            "AND is_owner_view = false",
            **p,
        )
        generated_pages = await _scalar_i(
            session,
            "SELECT count(*) FROM pages WHERE created_at >= :s AND created_at < :e AND deleted_at IS NULL",
            **p,
        )

        hot_rows = (
            await session.execute(
                text(
                    """
                    SELECT pv.page_id, count(*) AS views, count(DISTINCT pv.ip_hash) AS uv
                    FROM page_view_events pv
                    WHERE pv.created_at >= :s AND pv.created_at < :e AND pv.is_owner_view = false
                    GROUP BY pv.page_id ORDER BY views DESC LIMIT 12
                    """
                ),
                p,
            )
        ).all()

        hot_pages: list[HotPageItem] = []
        for page_id, views, uv in hot_rows:
            row = (
                await session.execute(
                    text(
                        "SELECT p.title, p.conversation_id, p.deleted_at, u.phone, u.display_name "
                        "FROM pages p LEFT JOIN users u ON u.id = p.owner_user_id WHERE p.id = :pid"
                    ),
                    {"pid": page_id},
                )
            ).first()
            if row is None:
                continue
            title, conversation_id, deleted_at, phone, display_name = row
            url = (
                build_page_url(settings, conversation_id, page_id)
                if deleted_at is None and conversation_id is not None
                else None
            )
            hot_pages.append(
                HotPageItem(
                    page_id=page_id,
                    conversation_id=conversation_id,
                    title=title or "（无标题）",
                    owner_label=_user_label(phone, display_name),
                    page_url=url,
                    views=int(views),
                    uv=int(uv),
                )
            )

    return ViralityResponse(
        days=days,
        page_views_total=pv_total,
        page_views_external=pv_external,
        uv_external=uv_external,
        pages_with_external_view=pages_with_view,
        generated_pages=generated_pages,
        share_ratio=(pages_with_view / generated_pages) if generated_pages else None,
        views_per_page=(pv_external / pages_with_view) if pages_with_view else None,
        hot_pages=hot_pages,
    )


@router.get("/users", response_model=list[OpsUserItem])
async def users(
    request: Request,
    q: str | None = Query(default=None, max_length=100),
    sort: str = Query(default="recent", pattern="^(recent|generations|recharge)$"),
    include_anon: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[OpsUserItem]:
    """用户明细列表：支持按手机号/昵称搜索、按活跃/生成/充值排序。"""
    order_by = {
        "recent": "u.last_login_at DESC NULLS LAST, u.created_at DESC",
        "generations": "generations DESC",
        "recharge": "ca.total_recharged_credits DESC",
    }[sort]
    where = ["1=1"]
    params: dict = {"limit": limit}
    if not include_anon:
        where.append("u.phone IS NOT NULL")
    if q:
        where.append("(u.phone ILIKE :q OR u.display_name ILIKE :q)")
        params["q"] = f"%{q.strip()}%"

    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT u.id, u.phone, u.display_name, u.is_anonymous, u.created_at, u.last_login_at,
                           COALESCE(ca.paid_balance, 0), COALESCE(ca.gift_balance, 0),
                           COALESCE(ca.total_recharged_credits, 0), COALESCE(ca.total_spent_credits, 0),
                           (SELECT count(*) FROM generation_tasks gt WHERE gt.requested_by_user_id = u.id) AS generations,
                           (SELECT count(*) FROM generation_tasks gt
                              WHERE gt.requested_by_user_id = u.id AND gt.status = 'succeeded') AS succeeded
                    FROM users u
                    LEFT JOIN credit_accounts ca ON ca.user_id = u.id
                    WHERE {' AND '.join(where)}
                    ORDER BY {order_by}
                    LIMIT :limit
                    """
                ),
                params,
            )
        ).all()

    return [
        OpsUserItem(
            user_id=r[0],
            phone=r[1],
            display_name=r[2],
            is_anonymous=r[3],
            created_at=r[4],
            last_login_at=r[5],
            paid_balance=int(r[6]),
            gift_balance=int(r[7]),
            total_recharged_credits=int(r[8]),
            total_spent_credits=int(r[9]),
            generations=int(r[10]),
            succeeded=int(r[11]),
        )
        for r in rows
    ]


@router.get("/users/{user_id}", response_model=OpsUserProfile)
async def user_profile(user_id: uuid.UUID, request: Request) -> OpsUserProfile:
    """单用户画像：基础信息、生成/成功、消费/充值、最近会话。"""
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        base = (
            await session.execute(
                text(
                    """
                    SELECT u.id, u.phone, u.display_name, u.is_anonymous, u.created_at, u.last_login_at,
                           COALESCE(ca.paid_balance, 0), COALESCE(ca.gift_balance, 0),
                           COALESCE(ca.total_recharged_credits, 0), COALESCE(ca.total_spent_credits, 0)
                    FROM users u LEFT JOIN credit_accounts ca ON ca.user_id = u.id
                    WHERE u.id = :uid
                    """
                ),
                {"uid": user_id},
            )
        ).first()
        if base is None:
            raise HTTPException(status_code=404, detail="用户不存在")

        gen = (
            await session.execute(
                text(
                    """
                    SELECT count(*) AS total,
                           count(*) FILTER (WHERE status = 'succeeded') AS ok,
                           count(*) FILTER (WHERE status = 'failed') AS bad,
                           min(created_at) AS first_at, max(created_at) AS last_at
                    FROM generation_tasks WHERE requested_by_user_id = :uid
                    """
                ),
                {"uid": user_id},
            )
        ).first()
        total_gen, ok, bad, first_at, last_at = int(gen[0]), int(gen[1]), int(gen[2]), gen[3], gen[4]

        conv_count = await _scalar_i(
            session,
            "SELECT count(*) FROM conversations WHERE owner_user_id = :uid AND deleted_at IS NULL",
            uid=user_id,
        )
        pages_count = await _scalar_i(
            session, "SELECT count(*) FROM pages WHERE owner_user_id = :uid AND deleted_at IS NULL", uid=user_id
        )

        conv_rows = (
            await session.execute(
                text(
                    """
                    SELECT c.id, c.title, c.created_at, c.updated_at,
                           (SELECT count(*) FROM pages p WHERE p.conversation_id = c.id AND p.deleted_at IS NULL) AS nodes
                    FROM conversations c
                    WHERE c.owner_user_id = :uid AND c.deleted_at IS NULL
                    ORDER BY c.updated_at DESC LIMIT 20
                    """
                ),
                {"uid": user_id},
            )
        ).all()

    recent_conversations = [
        OpsUserConversation(
            conversation_id=cid, title=title, node_count=int(nodes), created_at=created_at, updated_at=updated_at
        )
        for cid, title, created_at, updated_at, nodes in conv_rows
    ]

    return OpsUserProfile(
        user_id=base[0],
        phone=base[1],
        display_name=base[2],
        is_anonymous=base[3],
        created_at=base[4],
        last_login_at=base[5],
        paid_balance=int(base[6]),
        gift_balance=int(base[7]),
        total_recharged_credits=int(base[8]),
        total_spent_credits=int(base[9]),
        generations=total_gen,
        succeeded=ok,
        failed=bad,
        success_rate=(ok / total_gen) if total_gen else None,
        conversations_count=conv_count,
        pages_count=pages_count,
        first_generation_at=first_at,
        last_generation_at=last_at,
        recent_conversations=recent_conversations,
    )


def _duration_sec(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    if started_at is None or finished_at is None:
        return None
    return (finished_at - started_at).total_seconds()


@router.get("/cases", response_model=list[OpsCaseItem])
async def cases(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    model_key: str | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=80, ge=1, le=300),
) -> list[OpsCaseItem]:
    """生成 Case 列表：支持按状态、模型、用户、关键词筛选，用于排查与精选。"""
    settings = get_settings()
    where = ["1=1"]
    params: dict = {"limit": limit}
    if status_filter in {"pending", "running", "succeeded", "failed", "cancelled"}:
        where.append("t.status = :st")
        params["st"] = status_filter
    if model_key:
        where.append("t.model_key = :mk")
        params["mk"] = model_key
    if user_id:
        where.append("t.requested_by_user_id = :uid")
        params["uid"] = user_id
    if q:
        where.append("(t.user_prompt ILIKE :q OR t.prompt ILIKE :q)")
        params["q"] = f"%{q.strip()}%"

    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT t.id, t.page_id, t.model_key, t.skill_key, t.status, t.user_prompt,
                           t.input_file_names, t.created_at, t.started_at, t.finished_at,
                           p.conversation_id, p.deleted_at, u.phone, u.display_name
                    FROM generation_tasks t
                    LEFT JOIN pages p ON p.id = t.page_id
                    LEFT JOIN users u ON u.id = t.requested_by_user_id
                    WHERE {' AND '.join(where)}
                    ORDER BY t.created_at DESC
                    LIMIT :limit
                    """
                ),
                params,
            )
        ).all()

    items: list[OpsCaseItem] = []
    for r in rows:
        (
            task_id, page_id, mk, skill_key, status_, user_prompt, file_names,
            created_at, started_at, finished_at, conversation_id, deleted_at, phone, display_name,
        ) = r
        url = (
            build_page_url(settings, conversation_id, page_id)
            if page_id and conversation_id and deleted_at is None and status_ == "succeeded"
            else None
        )
        items.append(
            OpsCaseItem(
                task_id=task_id,
                page_id=page_id,
                conversation_id=conversation_id,
                page_url=url,
                user_label=_user_label(phone, display_name),
                model_key=mk,
                skill_key=skill_key,
                status=status_,
                user_prompt=user_prompt,
                file_names=list(file_names or []),
                duration_sec=_duration_sec(started_at, finished_at),
                created_at=created_at,
            )
        )
    return items


@router.get("/cases/{task_id}", response_model=OpsCaseDetail)
async def case_detail(task_id: uuid.UUID, request: Request) -> OpsCaseDetail:
    """单次生成 Case 详情：完整 prompt/输出/用量/SSE 事件流/访问数，用于深度排查。"""
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        row = (
            await session.execute(
                text(
                    """
                    SELECT t.id, t.page_id, t.model_key, t.model_name, t.skill_key, t.status, t.error_message,
                           t.retry_count, t.user_prompt, t.prompt, t.model_prompt, t.model_output_text,
                           t.input_file_names, t.extracted_file_text, t.created_at, t.started_at, t.finished_at,
                           p.conversation_id, p.deleted_at, u.phone, u.display_name
                    FROM generation_tasks t
                    LEFT JOIN pages p ON p.id = t.page_id
                    LEFT JOIN users u ON u.id = t.requested_by_user_id
                    WHERE t.id = :tid
                    """
                ),
                {"tid": task_id},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="生成任务不存在")

        (
            tid, page_id, model_key, model_name, skill_key, status_, error_message, retry_count,
            user_prompt, prompt, model_prompt, model_output_text, file_names, extracted_text,
            created_at, started_at, finished_at, conversation_id, deleted_at, phone, display_name,
        ) = row

        # 用量/成本取当前页面版本（若有）。
        usage = None
        if page_id is not None:
            usage = (
                await session.execute(
                    text(
                        """
                        SELECT pv.input_tokens, pv.output_tokens, pv.total_tokens, pv.total_cost_cny
                        FROM page_versions pv
                        JOIN pages p ON p.current_version_id = pv.id
                        WHERE p.id = :pid
                        """
                    ),
                    {"pid": page_id},
                )
            ).first()

        page_view_count = 0
        if page_id is not None:
            page_view_count = await _scalar_i(
                session, "SELECT count(*) FROM page_view_events WHERE page_id = :pid", pid=page_id
            )

        event_rows = (
            await session.execute(
                text(
                    "SELECT sequence, event_type, payload, created_at FROM generation_events "
                    "WHERE task_id = :tid ORDER BY sequence ASC"
                ),
                {"tid": task_id},
            )
        ).all()

    url = (
        build_page_url(settings, conversation_id, page_id)
        if page_id and conversation_id and deleted_at is None and status_ == "succeeded"
        else None
    )
    events = [
        OpsCaseEvent(sequence=int(seq), event_type=et, payload=payload or {}, created_at=ca)
        for seq, et, payload, ca in event_rows
    ]

    return OpsCaseDetail(
        task_id=tid,
        page_id=page_id,
        conversation_id=conversation_id,
        page_url=url,
        user_label=_user_label(phone, display_name),
        model_key=model_key,
        model_name=model_name,
        skill_key=skill_key,
        status=status_,
        error_message=error_message,
        retry_count=int(retry_count or 0),
        user_prompt=user_prompt,
        prompt=prompt,
        model_prompt=model_prompt,
        model_output_text=model_output_text,
        file_names=list(file_names or []),
        extracted_file_text=extracted_text,
        input_tokens=int(usage[0]) if usage and usage[0] is not None else None,
        output_tokens=int(usage[1]) if usage and usage[1] is not None else None,
        total_tokens=int(usage[2]) if usage and usage[2] is not None else None,
        total_cost_cny=float(usage[3]) if usage and usage[3] is not None else None,
        duration_sec=_duration_sec(started_at, finished_at),
        page_view_count=page_view_count,
        created_at=created_at,
        started_at=started_at,
        finished_at=finished_at,
        events=events,
    )
