from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.persistence.models import (
    ForwardLeaseRecord,
    ForwardRecoveryRecord,
    ForwardStageRecord,
)


class ForwardOperationRepository:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    async def acquire_lease(self, portfolio_id: str, *, owner_id: str, now: datetime, lease_seconds: int) -> bool:
        if now.tzinfo is None or lease_seconds <= 0:
            raise ValueError("FORWARD_LEASE_ARGUMENT_INVALID")
        try:
            async with self.factory.begin() as session:
                row = await session.get(ForwardLeaseRecord, portfolio_id)
                if row is None:
                    session.add(ForwardLeaseRecord(portfolio_id=portfolio_id, owner_id=owner_id,
                                                    expires_at=now.timestamp() + lease_seconds))
                    await session.flush()
                    return True
                result = await session.execute(update(ForwardLeaseRecord).where(
                    ForwardLeaseRecord.portfolio_id == portfolio_id,
                    ForwardLeaseRecord.expires_at <= now.timestamp(),
                ).values(owner_id=owner_id, expires_at=now.timestamp() + lease_seconds))
                return getattr(result, "rowcount", 0) == 1
        except IntegrityError:
            return False

    async def renew_lease(self, portfolio_id: str, owner_id: str, *, now: datetime, lease_seconds: int) -> bool:
        async with self.factory.begin() as session:
            result = await session.execute(update(ForwardLeaseRecord).where(
                ForwardLeaseRecord.portfolio_id == portfolio_id, ForwardLeaseRecord.owner_id == owner_id,
                ForwardLeaseRecord.expires_at > now.timestamp(),
            ).values(expires_at=now.timestamp() + lease_seconds))
            return getattr(result, "rowcount", 0) == 1

    async def release_lease(self, portfolio_id: str, owner_id: str) -> None:
        async with self.factory.begin() as session:
            await session.execute(delete(ForwardLeaseRecord).where(
                ForwardLeaseRecord.portfolio_id == portfolio_id, ForwardLeaseRecord.owner_id == owner_id,
            ))

    @staticmethod
    def stage_id(portfolio_id: str, trade_date: date, stage: str, config_hash: str) -> str:
        return sha256_json([portfolio_id, trade_date.isoformat(), stage, config_hash])

    async def get_stage(self, portfolio_id: str, trade_date: date, stage: str, config_hash: str) -> ForwardStageRecord | None:
        async with self.factory() as session:
            return await session.get(ForwardStageRecord, self.stage_id(portfolio_id, trade_date, stage, config_hash))

    async def record_stage(self, portfolio_id: str, trade_date: date, stage: str, config_hash: str,
                           *, owner_id: str, now: datetime, status: str, attempts: int,
                           outcome: dict[str, Any], error_code: str | None = None,
                           next_retry_at: datetime | None = None, input_hash: str | None = None) -> None:
        async with self.factory.begin() as session:
            result = await session.execute(update(ForwardLeaseRecord).where(
                ForwardLeaseRecord.portfolio_id == portfolio_id, ForwardLeaseRecord.owner_id == owner_id,
                ForwardLeaseRecord.expires_at > now.timestamp(),
            ).values(owner_id=owner_id))
            if getattr(result, "rowcount", 0) != 1:
                raise ValueError("FORWARD_LEASE_LOST")
            key = self.stage_id(portfolio_id, trade_date, stage, config_hash)
            row = await session.get(ForwardStageRecord, key)
            if status == "RUNNING":
                await session.execute(update(ForwardRecoveryRecord).where(
                    ForwardRecoveryRecord.stage_id == key, ForwardRecoveryRecord.consumed_at.is_(None),
                ).values(consumed_at=now))
            if row is None:
                row = ForwardStageRecord(id=key, portfolio_id=portfolio_id, trade_date=trade_date,
                                          stage=stage, config_hash=config_hash)
                session.add(row)
            row.status = status
            row.attempts = attempts
            row.outcome_json = outcome
            row.error_code = error_code
            if input_hash is not None:
                row.input_hash = input_hash
            row.next_retry_at = next_retry_at.timestamp() if next_retry_at else None

    async def status(self, portfolio_id: str, trade_date: date, config_hash: str) -> tuple[ForwardStageRecord, ...]:
        async with self.factory() as session:
            return tuple((await session.scalars(select(ForwardStageRecord).where(
                ForwardStageRecord.portfolio_id == portfolio_id, ForwardStageRecord.trade_date == trade_date,
                ForwardStageRecord.config_hash == config_hash,
            ))).all())
