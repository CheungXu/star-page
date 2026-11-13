from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ConversationForkSource(BaseModel):
    """分叉会话的来源：从哪条会话的第几轮分出来。"""

    conversation_id: UUID
    title: str
    # 分叉点在来源会话里是第几轮（从 1 开始），也就是本会话继承了几轮。
    round_number: int
    # 来源会话被删后为 false：继承来的历史照常显示，但不能再跳回去。
    available: bool


class ConversationListItem(BaseModel):
    id: UUID
    title: str
    origin: str
    is_favorite: bool
    model_keys: list[str]
    node_count: int
    latest_batch_status: str | None
    created_at: datetime
    updated_at: datetime
    forked_from: ConversationForkSource | None = None


class ConversationUpdate(BaseModel):
    is_favorite: bool


class ConversationUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


class ConversationBilling(BaseModel):
    credits_charged: int
    discount_credits: int = 0


class ConversationRenderIssue(BaseModel):
    summary: str
    issues: list[str] = Field(default_factory=list)


class ConversationNode(BaseModel):
    page_id: UUID
    task_id: UUID | None
    model_key: str | None
    model_label: str | None
    model_name: str | None
    parent_page_id: UUID | None
    page_status: str
    generation_status: str | None
    page_url: str
    usage: ConversationUsage | None = None
    billing: ConversationBilling | None = None
    render_issue: ConversationRenderIssue | None = None
    # 生成任务从开始到结束的用时；任务未结束或缺时间戳时为空。
    duration_ms: int | None = None


class ConversationBatch(BaseModel):
    batch_id: UUID
    kind: str
    base_page_id: UUID | None
    selected_models: list[str]
    status: str
    prompt: str
    user_prompt: str | None
    file_names: list[str]
    created_at: datetime
    nodes: list[ConversationNode]
    # 从来源会话继承来的轮次（分叉点及之前）。
    inherited: bool = False


class ConversationDetail(BaseModel):
    id: UUID
    title: str
    origin: str
    scene_key: str | None = None
    created_at: datetime
    updated_at: datetime
    batches: list[ConversationBatch]
    forked_from: ConversationForkSource | None = None
