"""违规举报：公开提交（允许匿名）+ 管理员审核下架。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from app.core.auth import get_client_ip, get_optional_actor, require_admin
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.urls import build_page_url
from app.models.entities import Page, PagePublication, PageReport, User
from app.services.analytics.tracking import hash_ip
from app.services.publications import publication_url

router = APIRouter(tags=["reports"])

REPORT_CATEGORIES = {
    "fraud": "诈骗、刷单、虚假投资",
    "phishing": "仿冒钓鱼、骗取账号密码",
    "porn_gambling": "色情、赌博",
    "infringement": "侵犯我的权益（侵权、隐私、肖像）",
    "illegal": "其他违法违规内容",
    "other": "其他",
}
_PER_PAGE_WINDOW = timedelta(hours=24)
_DAILY_LIMIT_PER_IP = 20


class ReportTarget(BaseModel):
    page_id: uuid.UUID
    title: str
    page_url: str
    categories: dict[str, str]


class ReportCreate(BaseModel):
    page_id: uuid.UUID
    slug: str | None = Field(default=None, max_length=64)
    category: str
    description: str | None = Field(default=None, max_length=2000)
    contact: str | None = Field(default=None, max_length=128)


class ReportItem(BaseModel):
    id: uuid.UUID
    page_id: uuid.UUID
    page_title: str
    page_url: str
    page_moderation_status: str
    owner_display: str
    category: str
    category_label: str
    description: str | None
    contact: str | None
    status: str
    review_note: str | None
    reviewed_at: datetime | None
    created_at: datetime
    same_page_pending: int


class ReportReview(BaseModel):
    action: str = Field(pattern="^(takedown|reject|restore)$")
    note: str | None = Field(default=None, max_length=1000)


async def _reportable_page(session, page_id: uuid.UUID) -> Page:
    page = await session.get(Page, page_id)
    if page is None or page.deleted_at is not None or page.status != "ready":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="页面不存在或已删除")
    return page


async def _page_public_url(session, page: Page) -> str:
    if page.conversation_id is not None:
        publication = await session.scalar(
            select(PagePublication).where(
                PagePublication.conversation_id == page.conversation_id, PagePublication.page_id == page.id
            )
        )
        if publication is not None:
            return publication_url(publication.slug)
    return build_page_url(get_settings(), page.conversation_id, page.id)


@router.get("/api/reports/target", response_model=ReportTarget)
async def report_target(page: uuid.UUID = Query(...)) -> ReportTarget:
    async with AsyncSessionLocal() as session:
        target = await _reportable_page(session, page)
        return ReportTarget(
            page_id=target.id,
            title=target.title[:80],
            page_url=await _page_public_url(session, target),
            categories=REPORT_CATEGORIES,
        )


@router.post("/api/reports", status_code=status.HTTP_201_CREATED)
async def create_report(payload: ReportCreate, request: Request) -> dict[str, str]:
    if payload.category not in REPORT_CATEGORIES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="请选择举报类型")
    ip_hash = hash_ip(get_client_ip(request))
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        await _reportable_page(session, payload.page_id)
        if ip_hash:
            same_page = await session.scalar(
                select(PageReport.id).where(
                    PageReport.reporter_ip_hash == ip_hash,
                    PageReport.page_id == payload.page_id,
                    PageReport.created_at >= now - _PER_PAGE_WINDOW,
                ).limit(1)
            )
            if same_page is not None:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="你已举报过这个页面，我们会尽快审核")
            daily = await session.scalar(
                select(func.count(PageReport.id)).where(
                    PageReport.reporter_ip_hash == ip_hash, PageReport.created_at >= now - timedelta(hours=24)
                )
            )
            if int(daily or 0) >= _DAILY_LIMIT_PER_IP:
                raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="今日举报次数已达上限，请发邮件联系我们")

        actor = await get_optional_actor(session, request)
        session.add(
            PageReport(
                id=uuid.uuid4(),
                page_id=payload.page_id,
                slug=payload.slug,
                category=payload.category,
                description=(payload.description or "").strip() or None,
                contact=(payload.contact or "").strip() or None,
                reporter_user_id=actor.id if actor is not None else None,
                reporter_ip_hash=ip_hash,
                reporter_user_agent=(request.headers.get("user-agent") or "")[:2000] or None,
            )
        )
        await session.commit()
    return {"status": "ok"}


@router.get("/api/admin/reports", response_model=list[ReportItem])
async def list_reports(request: Request, status_filter: str = Query(default="pending", alias="status")) -> list[ReportItem]:
    async with AsyncSessionLocal() as session:
        await require_admin(session, request)
        query = select(PageReport, Page, User).join(Page, Page.id == PageReport.page_id).join(User, User.id == Page.owner_user_id)
        if status_filter != "all":
            query = query.where(PageReport.status == status_filter)
        rows = (await session.execute(query.order_by(PageReport.created_at.desc()).limit(200))).all()

        pending_counts: dict[uuid.UUID, int] = {}
        page_ids = {page.id for _, page, _ in rows}
        if page_ids:
            counts = await session.execute(
                select(PageReport.page_id, func.count(PageReport.id))
                .where(PageReport.page_id.in_(page_ids), PageReport.status == "pending")
                .group_by(PageReport.page_id)
            )
            pending_counts = {page_id: int(count) for page_id, count in counts.all()}

        items: list[ReportItem] = []
        for report, page, owner in rows:
            items.append(
                ReportItem(
                    id=report.id,
                    page_id=page.id,
                    page_title=page.title[:80],
                    page_url=await _page_public_url(session, page),
                    page_moderation_status=page.moderation_status,
                    owner_display="匿名访客" if owner.is_anonymous else owner.display_name,
                    category=report.category,
                    category_label=REPORT_CATEGORIES.get(report.category, report.category),
                    description=report.description,
                    contact=report.contact,
                    status=report.status,
                    review_note=report.review_note,
                    reviewed_at=report.reviewed_at,
                    created_at=report.created_at,
                    same_page_pending=pending_counts.get(page.id, 0),
                )
            )
        return items


@router.post("/api/admin/reports/{report_id}/review")
async def review_report(report_id: uuid.UUID, payload: ReportReview, request: Request) -> dict[str, str]:
    """下架：页面置为 blocked，同页其他待审举报一并结案；恢复：页面重新可访问；驳回：只结这一条。"""
    async with AsyncSessionLocal() as session:
        admin = await require_admin(session, request)
        report = await session.get(PageReport, report_id)
        if report is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="举报不存在")
        page = await session.get(Page, report.page_id)
        if page is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="页面不存在")
        now = datetime.now(UTC)
        note = (payload.note or "").strip() or None

        if payload.action == "takedown":
            page.moderation_status = "blocked"
            page.moderated_at = now
            await session.execute(
                update(PageReport)
                .where(PageReport.page_id == page.id, PageReport.status == "pending")
                .values(status="taken_down", reviewer_user_id=admin.id, reviewed_at=now, review_note=note)
            )
            report.status = "taken_down"
        elif payload.action == "restore":
            page.moderation_status = "ok"
            page.moderated_at = now
            report.status = "rejected"
        else:
            report.status = "rejected"
        report.reviewer_user_id = admin.id
        report.reviewed_at = now
        report.review_note = note
        await session.commit()
        return {"status": report.status, "page_moderation_status": page.moderation_status}
