from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AnalyticsCollectRequest(BaseModel):
    """前端埋点上报体。event 必须命中后端白名单，否则丢弃。"""

    event: str = Field(min_length=1, max_length=64)
    props: dict | None = None
    client_session_id: str | None = Field(default=None, max_length=64)


# ---------- 实时动态 ----------


class RealtimeEvent(BaseModel):
    kind: str          # register / gen_success / gen_failed / recharge / page_view
    title: str
    detail: str | None = None
    at: datetime


class RealtimeStats(BaseModel):
    online_now: int                 # 近 5 分钟有会话活动的用户
    running_tasks: int              # 进行中的生成任务
    pending_tasks: int              # 排队中的生成任务
    failed_tasks_1h: int            # 近 1 小时失败任务
    today_new_registered: int
    today_new_anonymous: int
    today_gen_succeeded: int
    today_gen_failed: int
    today_active_users: int
    today_recharge_cny: float
    today_page_views: int
    recent_events: list[RealtimeEvent]


# ---------- 通用趋势 ----------


class MetricPoint(BaseModel):
    date: str
    value: float


class TrendSeries(BaseModel):
    metric: str
    points: list[MetricPoint]


class TrendsResponse(BaseModel):
    days: int
    series: list[TrendSeries]


# ---------- 总览（北极星 + KPI 卡）----------


class MetricCard(BaseModel):
    key: str
    label: str
    value: float
    unit: str = ""                  # ""=数量, "cny"=元, "pct"=百分比, "sec"=秒
    delta_pct: float | None = None  # 环比上一周期变化
    hint: str | None = None


class OverviewResponse(BaseModel):
    days: int
    range_start: str
    range_end: str
    north_star_wagp: int            # 北极星：本周「成功生成且产生外部访问」的页面数
    cards: list[MetricCard]


# ---------- 留存 ----------


class RetentionRow(BaseModel):
    cohort_date: str
    cohort_size: int
    cells: dict[int, float | None]  # period_index -> 留存率(0~1)，未到期为 None


class RetentionResponse(BaseModel):
    kind: str
    periods: list[int]
    rows: list[RetentionRow]


# ---------- 漏斗 ----------


class FunnelStepItem(BaseModel):
    step: str
    label: str
    step_order: int
    count: int
    rate_from_prev: float | None = None  # 相对上一步转化率
    rate_from_top: float | None = None   # 相对首步转化率


class FunnelResponse(BaseModel):
    days: int
    steps: list[FunnelStepItem]


# ---------- 参与（分布）----------


class DistributionItem(BaseModel):
    key: str
    label: str
    value: float
    share: float | None = None


class EngagementResponse(BaseModel):
    days: int
    total_batches: int
    total_tasks: int
    continuation_rate: float | None      # 续写率
    file_upload_rate: float | None       # 文件上传使用率
    avg_generations_per_creator: float | None
    model_distribution: list[DistributionItem]
    skill_distribution: list[DistributionItem]


# ---------- 质量 ----------


class FailureReasonItem(BaseModel):
    reason: str
    count: int


class QualityResponse(BaseModel):
    days: int
    total_tasks: int
    succeeded: int
    failed: int
    success_rate: float | None
    latency_p50_sec: float | None
    latency_p90_sec: float | None
    latency_p99_sec: float | None
    model_success: list[DistributionItem]   # 各模型成功率
    failure_reasons: list[FailureReasonItem]
    latency_trend: list[MetricPoint]         # p90 趋势


# ---------- 传播 ----------


class HotPageItem(BaseModel):
    page_id: UUID
    conversation_id: UUID | None
    title: str
    owner_label: str | None
    page_url: str | None
    views: int
    uv: int


class ViralityResponse(BaseModel):
    days: int
    page_views_total: int
    page_views_external: int
    uv_external: int
    pages_with_external_view: int
    generated_pages: int
    share_ratio: float | None        # 被外部访问页面 / 生成页面
    views_per_page: float | None
    hot_pages: list[HotPageItem]


# ---------- 用户明细 ----------


class OpsUserItem(BaseModel):
    user_id: UUID
    phone: str | None
    display_name: str
    is_anonymous: bool
    created_at: datetime
    last_login_at: datetime | None
    generations: int
    succeeded: int
    paid_balance: int
    gift_balance: int
    total_recharged_credits: int
    total_spent_credits: int


class OpsUserConversation(BaseModel):
    conversation_id: UUID
    title: str
    node_count: int
    created_at: datetime
    updated_at: datetime


class OpsUserProfile(BaseModel):
    user_id: UUID
    phone: str | None
    display_name: str
    is_anonymous: bool
    created_at: datetime
    last_login_at: datetime | None
    generations: int
    succeeded: int
    failed: int
    success_rate: float | None
    conversations_count: int
    pages_count: int
    paid_balance: int
    gift_balance: int
    total_recharged_credits: int
    total_spent_credits: int
    first_generation_at: datetime | None
    last_generation_at: datetime | None
    recent_conversations: list[OpsUserConversation]


# ---------- Case 查看 ----------


class OpsCaseItem(BaseModel):
    task_id: UUID
    page_id: UUID | None
    conversation_id: UUID | None
    page_url: str | None
    user_label: str | None
    model_key: str | None
    skill_key: str | None
    status: str
    user_prompt: str | None
    file_names: list[str]
    duration_sec: float | None
    created_at: datetime


class OpsCaseEvent(BaseModel):
    sequence: int
    event_type: str
    payload: dict
    created_at: datetime


class OpsCaseDetail(BaseModel):
    task_id: UUID
    page_id: UUID | None
    conversation_id: UUID | None
    page_url: str | None
    user_label: str | None
    model_key: str | None
    model_name: str | None
    skill_key: str | None
    status: str
    error_message: str | None
    retry_count: int
    user_prompt: str | None
    prompt: str | None
    model_prompt: str | None
    model_output_text: str | None
    file_names: list[str]
    extracted_file_text: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    total_cost_cny: float | None
    duration_sec: float | None
    page_view_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    events: list[OpsCaseEvent]
