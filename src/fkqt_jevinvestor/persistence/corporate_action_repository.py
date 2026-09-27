from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fkqt_jevinvestor.domain.corporate_actions import CorporateActionEventV1, EntitlementV1
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.persistence.models import (
    CorporateActionEventRecord,
    CorporateEntitlementRecord,
    CorporateStageRecord,
)
from fkqt_jevinvestor.services.corporate_action_ledger import (
    apply_entitlement_stage,
    freeze_entitlement,
)
from fkqt_jevinvestor.services.portfolio_service import PortfolioLedger


class CorporateActionRepository:
    async def validate_daily_events(
        self, session: AsyncSession, portfolio_id: str, trade_date: date,
        events: tuple[CorporateActionEventV1, ...],
    ) -> set[str]:
        affected: set[str] = set()
        for event in events:
            record = await session.get(CorporateEntitlementRecord, (portfolio_id, event.event_id))
            if record is not None:
                stored_event = await session.get(CorporateActionEventRecord, (event.event_id, record.source_version))
                if stored_event is None or record.source_version != event.source_version:
                    raise ValueError("CORPORATE_ACTION_EVENT_CONFLICT")
                original = dict(stored_event.event_json)
                observed = event.model_dump(mode="json")
                original.pop("available_at", None)
                observed.pop("available_at", None)
                if original != observed:
                    raise ValueError("CORPORATE_ACTION_EVENT_CONFLICT")
            elif event.record_date < trade_date:
                settled = max(day for day in (event.ex_date, event.pay_date, event.share_list_date) if day is not None)
                if settled >= trade_date:
                    raise ValueError("CORPORATE_ACTION_RECORD_HISTORY_MISSING")
            if event.ex_date == trade_date:
                affected.add(event.symbol)
        return affected

    async def freeze_record_date(
        self, session: AsyncSession, portfolio_id: str, event: CorporateActionEventV1,
        ledger: PortfolioLedger,
    ) -> EntitlementV1:
        payload = event.model_dump(mode="json")
        stored_event = await session.get(CorporateActionEventRecord, (event.event_id, event.source_version))
        if stored_event is None:
            session.add(CorporateActionEventRecord(event_id=event.event_id, source_version=event.source_version,
                                                  event_json=payload, content_hash=sha256_json(payload)))
        else:
            prior = dict(stored_event.event_json)
            observed = dict(payload)
            prior.pop("available_at", None)
            observed.pop("available_at", None)
            if prior != observed or stored_event.content_hash != sha256_json(stored_event.event_json):
                raise ValueError("CORPORATE_ACTION_EVENT_CONFLICT")
        record = await session.get(CorporateEntitlementRecord, (portfolio_id, event.event_id))
        if record is not None:
            if record.source_version != event.source_version:
                raise ValueError("CORPORATE_ACTION_EVENT_CONFLICT")
            return self._right(record)
        right = freeze_entitlement(event, ledger, event.record_date)
        frozen = right.model_dump(mode="json")
        session.add(CorporateEntitlementRecord(portfolio_id=portfolio_id, event_id=event.event_id,
                                               source_version=event.source_version,
                                               entitlement_json=frozen, content_hash=sha256_json(frozen)))
        await session.flush()
        return right

    @staticmethod
    def _right(record: CorporateEntitlementRecord) -> EntitlementV1:
        if sha256_json(record.entitlement_json) != record.content_hash:
            raise ValueError("CORPORATE_ACTION_ENTITLEMENT_HASH_MISMATCH")
        return EntitlementV1.model_validate(record.entitlement_json)

    async def apply_due_stages(
        self, session: AsyncSession, portfolio_id: str, trade_date: date, ledger: PortfolioLedger,
    ) -> tuple[str, ...]:
        records = (await session.scalars(select(CorporateEntitlementRecord).where(
            CorporateEntitlementRecord.portfolio_id == portfolio_id
        ).order_by(CorporateEntitlementRecord.event_id))).all()
        due: list[tuple[date, int, str, CorporateEntitlementRecord, CorporateActionEventV1]] = []
        for record in records:
            event_record = await session.get(CorporateActionEventRecord, (record.event_id, record.source_version))
            if event_record is None or sha256_json(event_record.event_json) != event_record.content_hash:
                raise ValueError("CORPORATE_ACTION_EVENT_HASH_MISMATCH")
            event = CorporateActionEventV1.model_validate(event_record.event_json)
            right = self._right(record)
            for index, (stage, effective, applied) in enumerate((
                ("EX", event.ex_date, right.ex_applied),
                ("PAY", event.pay_date if right.cash_receivable > 0 else None, right.paid),
                ("LIST", event.share_list_date if right.bonus_quantity > 0 else None, right.shares_listed),
            )):
                if effective is not None and effective <= trade_date and not applied:
                    due.append((effective, index, stage, record, event))
        applied_stages: list[str] = []
        for effective, _, stage, record, event in sorted(due, key=lambda row: (row[0], row[1], row[3].event_id)):
            if await session.get(CorporateStageRecord, (portfolio_id, record.event_id, stage)) is not None:
                raise ValueError("CORPORATE_ACTION_STAGE_INCONSISTENT")
            right = apply_entitlement_stage(ledger, self._right(record), event, stage=stage, effective_date=effective)
            record.entitlement_json = right.model_dump(mode="json")
            record.content_hash = sha256_json(record.entitlement_json)
            session.add(CorporateStageRecord(portfolio_id=portfolio_id, event_id=record.event_id,
                                             stage=stage, effective_date=effective, processed_date=trade_date))
            applied_stages.append(stage)
        await session.flush()
        return tuple(applied_stages)

    async def required_symbols(self, session: AsyncSession, portfolio_id: str) -> set[str]:
        records = (await session.scalars(select(CorporateEntitlementRecord).where(
            CorporateEntitlementRecord.portfolio_id == portfolio_id
        ))).all()
        rights = (self._right(record) for record in records)
        return {right.symbol for right in rights if
                (right.cash_receivable > 0 and not right.paid) or
                (right.bonus_quantity > 0 and not right.shares_listed)}

    async def has_outstanding_entitlements(self, session: AsyncSession, portfolio_id: str) -> bool:
        return bool(await self.required_symbols(session, portfolio_id))
