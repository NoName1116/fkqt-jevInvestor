import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from fkqt_jevinvestor.operations.alerts import AlertEvent, dispatch_outbox, enqueue_alert
from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from tests.integration import test_portfolio_repository as fixtures
from tests.unit.test_forward_operation_config import values

session_factory = fixtures.session_factory


@pytest.mark.asyncio
async def test_disabled_webhook_writes_one_local_event_and_recovery(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    config = ForwardOperationConfig.model_validate(values(tmp_path))
    event = AlertEvent.create(portfolio_id="p", trade_date=date(2026, 9, 28), stage="VERIFY",
                              error_code="PROVIDER_UNAVAILABLE", severity="ERROR", recovered=False,
                              created_at=datetime(2026, 9, 28, tzinfo=UTC), episode="one")
    async with session_factory.begin() as session:
        await enqueue_alert(session, event)
        await enqueue_alert(session, event)
    first = await dispatch_outbox(config, factory=session_factory)
    assert first.local_count == 1
    second = await dispatch_outbox(config, factory=session_factory)
    assert second.local_count == 0
    assert len((config.data_root / "alerts.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    recovered = AlertEvent.create(portfolio_id="p", trade_date=event.trade_date, stage=event.stage,
                                  error_code=event.error_code, severity="INFO", recovered=True,
                                  created_at=event.created_at, episode="one")
    assert recovered.event_id != event.event_id
    async with session_factory.begin() as session:
        await enqueue_alert(session, recovered)
    await dispatch_outbox(config, factory=session_factory)
    assert len((config.data_root / "alerts.jsonl").read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.asyncio
async def test_webhook_failure_retries_with_same_event_id(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    config = ForwardOperationConfig.model_validate({**values(tmp_path), "webhook_url": "https://example.test/alerts"})
    event = AlertEvent.create(portfolio_id="p", trade_date=date(2026, 9, 28), stage="VERIFY",
                              error_code="PROVIDER_UNAVAILABLE", severity="ERROR", recovered=False,
                              created_at=datetime(2026, 9, 28, tzinfo=UTC), episode="one")
    async with session_factory.begin() as session:
        await enqueue_alert(session, event)
    received: list[str] = []
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        received.append(payload["event_id"])
        assert "portfolio" not in payload
        assert request.headers["Idempotency-Key"] == event.event_id
        return httpx.Response(500 if len(received) == 1 else 200)
    first = await dispatch_outbox(config, factory=session_factory, transport=httpx.MockTransport(handler))
    assert first.pending_count == 1
    second = await dispatch_outbox(config, factory=session_factory, transport=httpx.MockTransport(handler))
    assert second.delivered_count == 1
    assert received == [event.event_id, event.event_id]
    assert len((config.data_root / "alerts.jsonl").read_text(encoding="utf-8").splitlines()) == 1
