import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.ingestion.canonical import canonical_json, sha256_json
from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.persistence.models import ForwardAlertRecord


class AlertEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    portfolio_id: str
    trade_date: date
    stage: str
    error_code: str
    severity: Literal["ERROR", "INFO"]
    recovered: bool
    created_at: datetime
    tax_policy_version: Literal["GROSS_V1"] = "GROSS_V1"

    @classmethod
    def create(cls, *, portfolio_id: str, trade_date: date, stage: str, error_code: str,
               severity: Literal["ERROR", "INFO"], recovered: bool, created_at: datetime, episode: str) -> "AlertEvent":
        return cls(event_id=sha256_json([portfolio_id, trade_date.isoformat(), stage, error_code, episode, recovered]),
                   portfolio_id=portfolio_id, trade_date=trade_date, stage=stage, error_code=error_code,
                   severity=severity, recovered=recovered, created_at=created_at)


async def enqueue_alert(session: AsyncSession, event: AlertEvent) -> None:
    if await session.get(ForwardAlertRecord, event.event_id) is not None:
        return
    try:
        async with session.begin_nested():
            session.add(ForwardAlertRecord(event_id=event.event_id, event_json=event.model_dump(mode="json"),
                                           status="PENDING", attempts=0, local_written=0))
            await session.flush()
    except IntegrityError:
        if await session.get(ForwardAlertRecord, event.event_id) is None:
            raise


@dataclass(frozen=True)
class AlertDeliverySummary:
    local_count: int
    delivered_count: int
    pending_count: int


async def dispatch_outbox(config: ForwardOperationConfig, *, factory: async_sessionmaker[AsyncSession],
                          limit: int = 20, transport: httpx.AsyncBaseTransport | None = None) -> AlertDeliverySummary:
    if limit <= 0:
        raise ValueError("FORWARD_ALERT_LIMIT_INVALID")
    async with factory() as session:
        records = tuple((await session.scalars(select(ForwardAlertRecord).where(
            ForwardAlertRecord.status == "PENDING",
            ForwardAlertRecord.event_json["portfolio_id"].as_string() == config.portfolio_id,
        ).order_by(ForwardAlertRecord.event_id).limit(limit))).all())
    path = config.data_root / "alerts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    local = delivered = pending = 0
    for row in records:
        event = AlertEvent.model_validate(row.event_json)
        if event.portfolio_id != config.portfolio_id:
            continue
        if not row.local_written:
            already = False
            if path.exists():
                with path.open(encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            already = json.loads(line).get("event_id") == event.event_id
                        except ValueError:
                            continue
                        if already:
                            break
            if not already:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.write(descriptor, (canonical_json(event) + "\n").encode("utf-8"))
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                local += 1
        sent = config.webhook_url is None
        if config.webhook_url is not None:
            token = os.environ.get("FORWARD_WEBHOOK_AUTH_TOKEN")
            headers = {"Idempotency-Key": event.event_id}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                async with httpx.AsyncClient(timeout=10, follow_redirects=False, transport=transport) as client:
                    response = await client.post(config.webhook_url.get_secret_value(), json=event.model_dump(mode="json"), headers=headers)
                    sent = 200 <= response.status_code < 300
            except httpx.HTTPError:
                sent = False
        async with factory.begin() as session:
            stored = await session.get(ForwardAlertRecord, row.event_id)
            if stored is not None:
                stored.local_written = 1
                stored.attempts += 1
                stored.status = ("DELIVERED" if config.webhook_url else "LOCAL_ONLY") if sent else "PENDING"
        delivered += int(sent and config.webhook_url is not None)
        pending += int(not sent)
    return AlertDeliverySummary(local, delivered, pending)
