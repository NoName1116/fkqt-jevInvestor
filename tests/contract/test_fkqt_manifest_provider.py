import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fkqt_jevinvestor.domain.market import TradingStatus
from fkqt_jevinvestor.ingestion.fkqt_manifest import (
    FkqtBundleError,
    FkqtManifestProvider,
)

SHANGHAI = timezone(timedelta(hours=8))


def _write_dataset(root: Path, dataset_type: str, rows: list[dict[str, object]]) -> Path:
    table = pa.Table.from_pylist(rows)
    dataset_id = hashlib.sha256(dataset_type.encode("utf-8")).hexdigest()
    relative_path = (
        Path("raw")
        / dataset_type
        / "as_of_date=20260918"
        / f"{dataset_id}.parquet"
    )
    parquet_path = root / relative_path
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        table,
        parquet_path,
        compression="zstd",
        version="2.6",
    )
    manifest = {
        "dataset_id": dataset_id,
        "dataset_type": dataset_type,
        "as_of_date": "20260918",
        "source": "TUSHARE",
        "dataset_version": "TUSHARE_PRO_V1",
        "request_params": {
            "volume_unit": "TUSHARE_100_SHARES" if dataset_type == "daily_bars" else None
        },
        "fetched_at": "2026-09-18T08:00:00+00:00",
        "row_count": table.num_rows,
        "schema_hash": hashlib.sha256(table.schema.serialize().to_pybytes()).hexdigest(),
        "raw_record_hash": "b" * 64,
        "content_hash": hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
        "storage_path": relative_path.as_posix(),
    }
    manifest_root = root / "manifests"
    manifest_root.mkdir(parents=True, exist_ok=True)
    (manifest_root / f"{dataset_id}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return parquet_path


@pytest.fixture
def valid_bundle(tmp_path: Path) -> Path:
    _write_dataset(
        tmp_path,
        "candidate_universe",
        [{"symbol": "600000.SH", "universe_version": "fkqt-pool-v1"}],
    )
    _write_dataset(
        tmp_path,
        "trading_calendar",
        [
            {"exchange": "SSE", "cal_date": "20260918", "is_open": 1},
            {"exchange": "SSE", "cal_date": "20260919", "is_open": 0},
            {"exchange": "SSE", "cal_date": "20260920", "is_open": 0},
            {"exchange": "SSE", "cal_date": "20260921", "is_open": 1},
        ],
    )
    _write_dataset(
        tmp_path,
        "security_master",
        [
            {
                "ts_code": "600000.SH",
                "name": "浦发银行",
                "market": "主板",
                "list_date": "19991110",
                "delist_date": None,
            }
        ],
    )
    _write_dataset(tmp_path, "security_name_history", [])
    _write_dataset(tmp_path, "suspension_status", [])
    _write_dataset(
        tmp_path,
        "daily_bars",
        [
            {
                "ts_code": "600000.SH",
                "trade_date": "20260918",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.1,
                "pre_close": 9.9,
                "vol": 1000.0,
                "amount": 10000.0,
            }
        ],
    )
    return tmp_path


@pytest.fixture
def tampered_bundle(valid_bundle: Path) -> Path:
    daily_path = next((valid_bundle / "raw" / "daily_bars").rglob("*.parquet"))
    with daily_path.open("ab") as handle:
        handle.write(b"tampered")
    return valid_bundle


async def test_manifest_content_hash_mismatch_is_rejected(tampered_bundle: Path) -> None:
    provider = FkqtManifestProvider(tampered_bundle)

    with pytest.raises(FkqtBundleError, match="DATASET_HASH_MISMATCH"):
        await provider.freeze_snapshot(
            symbols=("600000.SH",),
            decision_date=date(2026, 9, 18),
            decision_cutoff=datetime(2026, 9, 18, 15, 30, tzinfo=SHANGHAI),
            lookback_trading_days=61,
        )


async def test_valid_bundle_maps_to_auditable_market_snapshot(valid_bundle: Path) -> None:
    snapshot = await FkqtManifestProvider(valid_bundle).freeze_snapshot(
        symbols=("600000.SH",),
        decision_date=date(2026, 9, 18),
        decision_cutoff=datetime(2026, 9, 18, 15, 30, tzinfo=SHANGHAI),
        lookback_trading_days=61,
    )

    assert snapshot.next_trade_date == date(2026, 9, 21)
    assert snapshot.daily_bars["600000.SH"][0].amount_cny == 10_000_000
    assert snapshot.security_states["600000.SH"].trading_status == TradingStatus.TRADING
    assert snapshot.security_states["600000.SH"].missing_reasons == (
        "CORPORATE_ACTION_DATA_UNAVAILABLE",
        "INITIAL_LIMIT_PERIOD_UNAVAILABLE",
        "PRICE_LIMIT_DATA_UNAVAILABLE",
    )
    assert snapshot.universe_snapshot_id == hashlib.sha256(
        b"candidate_universe"
    ).hexdigest()
    assert snapshot.universe_snapshot_hash == snapshot.source_audits[0].content_hash
    assert len(snapshot.source_manifest_ids) == 6
    assert len(snapshot.source_audits) == 6
    assert snapshot.source_audits[0].upstream_version == "TUSHARE_PRO_V1"
    assert snapshot.source_audits[0].request_scope["symbols"] == ["600000.SH"]
    assert len(snapshot.content_hash) == 64


async def test_manifest_can_freeze_held_only_symbol_outside_candidate_universe(
    valid_bundle: Path,
) -> None:
    _write_dataset(
        valid_bundle,
        "security_master",
        [
            {
                "ts_code": symbol,
                "name": symbol,
                "market": "主板",
                "list_date": "19991110",
                "delist_date": None,
            }
            for symbol in ("600000.SH", "000001.SZ")
        ],
    )
    _write_dataset(
        valid_bundle,
        "daily_bars",
        [
            {
                "ts_code": symbol,
                "trade_date": "20260918",
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.1,
                "pre_close": 9.9,
                "vol": 1000.0,
                "amount": 10000.0,
            }
            for symbol in ("600000.SH", "000001.SZ")
        ],
    )
    snapshot = await FkqtManifestProvider(
        valid_bundle, candidate_symbols=("600000.SH",)
    ).freeze_snapshot(
        symbols=("000001.SZ", "600000.SH"),
        decision_date=date(2026, 9, 18),
        decision_cutoff=datetime(2026, 9, 18, 15, 30, tzinfo=SHANGHAI),
        lookback_trading_days=61,
    )
    assert set(snapshot.daily_bars) == {"600000.SH", "000001.SZ"}
    assert snapshot.source_audits[0].request_scope["candidate_symbols"] == ["600000.SH"]
    assert snapshot.source_audits[0].request_scope["symbols"] == [
        "000001.SZ", "600000.SH"
    ]


async def test_manifest_provider_rejects_symbols_outside_frozen_universe(
    valid_bundle: Path,
) -> None:
    with pytest.raises(FkqtBundleError, match="UNIVERSE_SYMBOL_MISMATCH"):
        await FkqtManifestProvider(valid_bundle).freeze_snapshot(
            symbols=("600000.SH", "000001.SZ"),
            decision_date=date(2026, 9, 18),
            decision_cutoff=datetime(2026, 9, 18, 15, 30, tzinfo=SHANGHAI),
            lookback_trading_days=61,
        )


async def test_manifest_provider_rejects_incomplete_calendar_between_d_and_d_plus_one(
    valid_bundle: Path,
) -> None:
    _write_dataset(
        valid_bundle,
        "trading_calendar",
        [
            {"exchange": "SSE", "cal_date": "20260918", "is_open": 1},
            {"exchange": "SSE", "cal_date": "20260921", "is_open": 1},
        ],
    )

    with pytest.raises(FkqtBundleError, match="TRADING_CALENDAR_INCOMPLETE"):
        await FkqtManifestProvider(valid_bundle).freeze_snapshot(
            symbols=("600000.SH",),
            decision_date=date(2026, 9, 18),
            decision_cutoff=datetime(2026, 9, 18, 15, 30, tzinfo=SHANGHAI),
            lookback_trading_days=61,
        )


async def test_manifest_provider_rejects_same_day_pre_close_cutoff(
    valid_bundle: Path,
) -> None:
    with pytest.raises(FkqtBundleError, match="POINT_IN_TIME_VIOLATION"):
        await FkqtManifestProvider(valid_bundle).freeze_snapshot(
            symbols=("600000.SH",),
            decision_date=date(2026, 9, 18),
            decision_cutoff=datetime(2026, 9, 18, 9, tzinfo=SHANGHAI),
            lookback_trading_days=61,
        )
