import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from fkqt_jevinvestor.domain.market_features import (
    AdjustmentMode,
    DailyBar,
    MarketSnapshot,
    MarketSourceAudit,
    SecurityTradeState,
)
from fkqt_jevinvestor.ingestion.snapshot_store import (
    MarketSnapshotStore,
    SnapshotValidationError,
)


def _with_content_hash(snapshot: MarketSnapshot) -> MarketSnapshot:
    payload = snapshot.model_dump(mode="json")
    payload["content_hash"] = ""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return snapshot.model_copy(update={"content_hash": digest})


def _bar(trade_date: date) -> DailyBar:
    return DailyBar(
        symbol="600000.SH",
        trade_date=trade_date,
        open=Decimal("10.00"),
        high=Decimal("10.20"),
        low=Decimal("9.80"),
        close=Decimal("10.10"),
        previous_close=Decimal("9.90"),
        volume=Decimal(1000),
        amount_cny=Decimal(10000),
        adjustment_mode=AdjustmentMode.NONE,
    )


def _snapshot(*, bar_date: date) -> MarketSnapshot:
    decision_date = date(2026, 9, 18)
    snapshot = MarketSnapshot(
        snapshot_id="snapshot-2026-09-18",
        decision_date=decision_date,
        decision_cutoff=datetime(2026, 9, 18, 15, tzinfo=UTC),
        next_trade_date=date(2026, 9, 21),
        calendar_complete_through=date(2026, 9, 21),
        universe_snapshot_id="universe-v1",
        universe_snapshot_hash="a" * 64,
        daily_bars={"600000.SH": (_bar(bar_date),)},
        security_states={
            "600000.SH": SecurityTradeState(
                symbol="600000.SH",
                trade_date=decision_date,
                trading_day_status="OPEN",
                trading_status="TRADING",
                is_st_or_delisting_risk=False,
                upper_limit_price=Decimal("10.89"),
                lower_limit_price=Decimal("8.91"),
                is_initial_no_limit_period=False,
                corporate_action_status="NONE",
                market="SSE",
                board="MAIN",
                listing_date=date(1999, 11, 10),
            )
        },
        source_manifest_ids=("manifest-v1",),
        source_audits=(
            MarketSourceAudit(
                upstream_type="FIXTURE",
                upstream_version="v1",
                request_scope={"symbols": ["600000.SH"]},
                data_cutoff=datetime(2026, 9, 18, 15, tzinfo=UTC),
                schema_version="schema-v1",
                fetched_at=datetime(2026, 9, 18, 15, tzinfo=UTC),
                record_count=1,
                raw_snapshot_ref="fixture",
                content_hash="b" * 64,
            ),
        ),
        content_hash="0" * 64,
    )
    return _with_content_hash(snapshot)


def test_future_record_rejects_entire_snapshot(tmp_path: Path) -> None:
    store = MarketSnapshotStore(tmp_path)
    snapshot_with_future_bar = _snapshot(bar_date=date(2026, 9, 21))

    with pytest.raises(SnapshotValidationError, match="POINT_IN_TIME_VIOLATION"):
        store.save(snapshot_with_future_bar)

    assert tuple(tmp_path.rglob("*.json")) == ()


def test_same_snapshot_is_idempotent(tmp_path: Path) -> None:
    store = MarketSnapshotStore(tmp_path)
    valid_snapshot = _snapshot(bar_date=date(2026, 9, 18))

    first = store.save(valid_snapshot)
    second = store.save(valid_snapshot)

    assert first == second
    assert len(tuple(tmp_path.rglob("*.json"))) == 1
    assert store.load(valid_snapshot.decision_date, valid_snapshot.content_hash) == valid_snapshot


def test_snapshot_rejects_naive_cutoff(tmp_path: Path) -> None:
    snapshot = _snapshot(bar_date=date(2026, 9, 18)).model_copy(
        update={"decision_cutoff": datetime(2026, 9, 18, 15, tzinfo=UTC).replace(tzinfo=None)}
    )
    snapshot = _with_content_hash(snapshot)

    with pytest.raises(SnapshotValidationError, match="TIMEZONE_REQUIRED"):
        MarketSnapshotStore(tmp_path).save(snapshot)
