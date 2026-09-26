import re
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.operations.runner import STAGES
from fkqt_jevinvestor.persistence.forward_operation_repository import ForwardOperationRepository
from fkqt_jevinvestor.persistence.models import (
    ForwardLeaseRecord,
    ForwardRecoveryRecord,
    ForwardStageRecord,
    NavRecord,
    PortfolioSnapshotRecord,
)


def stage_snapshot(row: ForwardStageRecord) -> dict[str, Any]:
    return {"id": row.id, "stage": row.stage, "status": row.status, "attempts": row.attempts,
            "error_code": row.error_code, "input_hash": row.input_hash, "config_hash": row.config_hash,
            "next_retry_at": row.next_retry_at, "outcome": row.outcome_json}


class RecoveryService:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory
        self.repository = ForwardOperationRepository(factory)

    async def _preview(self, session: AsyncSession, config: ForwardOperationConfig, day: date, stage: str) -> dict[str, Any]:
        info: dict[str, Any] = {"portfolio_id": config.portfolio_id, "trade_date": day.isoformat(),
                                "stage": stage, "config_hash": config.content_hash(), "allowed": False}
        if stage in {"DECIDE", "VERIFY"}:
            return {**info, "error_code": "FORWARD_RECOVERY_MODEL_REEVALUATION_FORBIDDEN"}
        if stage not in STAGES:
            return {**info, "error_code": "FORWARD_RECOVERY_STAGE_INVALID"}
        rows = tuple(await session.scalars(select(ForwardStageRecord).where(
            ForwardStageRecord.portfolio_id == config.portfolio_id,
            ForwardStageRecord.trade_date == day, ForwardStageRecord.config_hash == config.content_hash())))
        by_stage = {row.stage: row for row in rows}
        row = by_stage.get(stage)
        if row is None or row.status != "FAILED":
            return {**info, "error_code": "FORWARD_RECOVERY_FAILED_STAGE_REQUIRED"}
        index = STAGES.index(stage)
        if any(name not in by_stage or by_stage[name].status != "SUCCESS" for name in STAGES[:index]):
            return {**info, "error_code": "FORWARD_RECOVERY_NOT_FIRST_FAILURE"}
        if any(name in by_stage and by_stage[name].status in {"SUCCESS", "RUNNING"} for name in STAGES[index + 1:]):
            return {**info, "error_code": "FORWARD_RECOVERY_DOWNSTREAM_COMMITTED"}
        if row.input_hash != config.content_hash():
            return {**info, "error_code": "FORWARD_RECOVERY_INPUT_CONFLICT"}
        business: dict[str, Any] = {}
        if stage == "EXECUTE":
            nav = await session.scalar(select(NavRecord).where(NavRecord.portfolio_id == config.portfolio_id,
                                                             NavRecord.valuation_date == day))
            records = (await session.scalars(select(PortfolioSnapshotRecord).where(
                PortfolioSnapshotRecord.portfolio_id == config.portfolio_id,
                PortfolioSnapshotRecord.snapshot_type == "POST_EXECUTION"))).all()
            snapshots = [record for record in records if record.as_of.date() == day]
            prepared = by_stage["EXECUTION_PREPARE"].outcome_json
            expected = prepared.get("execution_hash")
            business = {"nav_id": nav.id if nav else None, "snapshot_ids": sorted(record.id for record in snapshots),
                        "expected_execution_hash": expected,
                        "snapshot_hashes": sorted(record.content_hash for record in snapshots)}
            if (nav is None) != (len(snapshots) == 0):
                return {**info, "business": business, "error_code": "FORWARD_RECOVERY_INPUT_CONFLICT"}
            if nav is not None and (len(snapshots) != 1 or not isinstance(expected, str)
                    or (snapshots[0].details_json or {}).get("execution_input_hash") != expected
                    or snapshots[0].total_equity != nav.total_equity):
                return {**info, "business": business, "error_code": "FORWARD_RECOVERY_INPUT_CONFLICT"}
        snapshot = stage_snapshot(row)
        snapshot["business"] = business
        return {**info, "allowed": True, "error_code": None, "business": business,
                "stage_hash": sha256_json(snapshot), "previous": snapshot}

    async def preview(self, config: ForwardOperationConfig, day: date, stage: str) -> dict[str, Any]:
        async with self.factory() as session:
            return await self._preview(session, config, day, stage)

    async def commit(self, config: ForwardOperationConfig, day: date, stage: str, reason: str,
                     expected_stage_hash: str, now: datetime) -> dict[str, Any]:
        if now.tzinfo is None or not re.fullmatch(r"[0-9a-f]{64}", expected_stage_hash):
            raise ValueError("FORWARD_RECOVERY_ARGUMENT_INVALID")
        if (not 1 <= len(reason.strip()) <= 256 or any(ord(c) < 32 or ord(c) == 127 for c in reason)
                or re.search(r"bearer|api.?key|password|secret|token|https?://|\bsk-", reason, re.IGNORECASE)):
            raise ValueError("FORWARD_RECOVERY_REASON_UNSAFE")
        key = sha256_json([config.portfolio_id, day.isoformat(), stage, config.content_hash(), expected_stage_hash])
        async with self.factory() as session:
            previous = await session.get(ForwardRecoveryRecord, key)
            if previous is not None:
                if previous.reason != reason.strip():
                    raise ValueError("FORWARD_RECOVERY_IDENTITY_CONFLICT")
                return {"status": "RECOVERED", "recovery_id": key, "idempotent": True}
        owner = str(uuid4())
        if not await self.repository.acquire_lease(config.portfolio_id, owner_id=owner, now=now, lease_seconds=60):
            raise ValueError("FORWARD_LEASE_BUSY")
        try:
            async with self.factory.begin() as session:
                fence = await session.execute(update(ForwardLeaseRecord).where(
                    ForwardLeaseRecord.portfolio_id == config.portfolio_id,
                    ForwardLeaseRecord.owner_id == owner, ForwardLeaseRecord.expires_at > now.timestamp(),
                ).values(owner_id=owner))
                if getattr(fence, "rowcount", 0) != 1:
                    raise ValueError("FORWARD_LEASE_LOST")
                preview = await self._preview(session, config, day, stage)
                if not preview["allowed"]:
                    raise ValueError(preview["error_code"])
                if preview["stage_hash"] != expected_stage_hash:
                    raise ValueError("FORWARD_RECOVERY_STALE_PREVIEW")
                snapshot = preview["previous"]
                changed = await session.execute(update(ForwardStageRecord).where(
                    ForwardStageRecord.id == snapshot["id"], ForwardStageRecord.status == "FAILED",
                    ForwardStageRecord.attempts == snapshot["attempts"],
                    ForwardStageRecord.input_hash == snapshot["input_hash"],
                ).values(status="RETRY_PENDING", next_retry_at=now.timestamp()))
                if getattr(changed, "rowcount", 0) != 1:
                    raise ValueError("FORWARD_RECOVERY_STALE_PREVIEW")
                session.add(ForwardRecoveryRecord(id=key, portfolio_id=config.portfolio_id, trade_date=day,
                    stage_id=snapshot["id"], stage=stage, config_hash=config.content_hash(),
                    expected_stage_hash=expected_stage_hash, reason=reason.strip(), previous_json=snapshot,
                    created_at=now.astimezone(UTC), consumed_at=None))
            return {"status": "RECOVERED", "recovery_id": key, "idempotent": False}
        finally:
            await self.repository.release_lease(config.portfolio_id, owner)
