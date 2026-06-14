from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import LedgerEntry, LedgerEntryLine

_BALANCE_TOLERANCE = Decimal("0.00000001")


@dataclass(frozen=True)
class LedgerLine:
    account_code: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")


class LedgerService:
    """复式记账过账服务：一个业务事件一张凭证，按 (event_type, event_ref) 幂等，借贷必须平衡。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def post(
        self,
        *,
        event_type: str,
        event_ref: str,
        lines: list[LedgerLine],
        user_id: uuid.UUID | None = None,
        memo: str | None = None,
    ) -> LedgerEntry | None:
        """过账。若同一事件已过账则跳过（幂等）。返回新建凭证或 None（已存在）。"""
        existing = await self.session.execute(
            select(LedgerEntry).where(
                LedgerEntry.event_type == event_type, LedgerEntry.event_ref == event_ref
            )
        )
        if existing.scalar_one_or_none() is not None:
            return None

        clean_lines = [line for line in lines if line.debit != 0 or line.credit != 0]
        total_debit = sum((line.debit for line in clean_lines), Decimal("0"))
        total_credit = sum((line.credit for line in clean_lines), Decimal("0"))
        if abs(total_debit - total_credit) > _BALANCE_TOLERANCE:
            raise ValueError(f"复式记账借贷不平衡：借 {total_debit} 贷 {total_credit}（{event_type}:{event_ref}）")

        entry = LedgerEntry(
            id=uuid.uuid4(),
            event_type=event_type,
            event_ref=event_ref,
            user_id=user_id,
            memo=memo,
        )
        self.session.add(entry)
        await self.session.flush()

        for line in clean_lines:
            self.session.add(
                LedgerEntryLine(
                    id=uuid.uuid4(),
                    entry_id=entry.id,
                    account_code=line.account_code,
                    debit=line.debit,
                    credit=line.credit,
                )
            )
        await self.session.flush()
        return entry
