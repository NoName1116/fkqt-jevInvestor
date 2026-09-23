import base64
import hashlib
import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fkqt_jevinvestor.cli.main import main
from fkqt_jevinvestor.ingestion.execution_bundle import ExecutionBundleStore, load_execution_bundle
from fkqt_jevinvestor.ingestion.fkqt_execution_manifest import (
    load_fkqt_execution_manifest,
)
from fkqt_jevinvestor.ingestion.fkqt_manifest import FkqtBundleError
from tests.contract.fkqt_execution_gold import MANIFEST_JSON, PARQUET_BASE64


def _row(symbol: str = "600000.SH") -> dict[str, object]:
    return {
        "symbol": symbol, "trade_date": "20260925", "trading_day_status": "OPEN",
        "trading_status": "TRADING", "open_price": "10", "unadjusted_close": "10.2",
        "daily_amount_cny": "10000000", "upper_limit_price": "11",
        "lower_limit_price": "9", "is_initial_no_limit_period": False,
    }


def _write_manifest(
    root: Path, rows: list[dict[str, object]], *, day: str = "20260925",
    version: str = "TUSHARE_EXECUTION_V1", suffix: str = "first",
    request_symbols: list[str] | None = None,
) -> Path:
    table = pa.Table.from_pylist(rows)
    dataset_id = hashlib.sha256(f"execution:{day}:{suffix}".encode()).hexdigest()
    relative = Path("raw/execution_snapshots") / f"as_of_date={day}" / f"{dataset_id}.parquet"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(  # pyright: ignore[reportUnknownMemberType]
        table, path, compression="zstd", version="2.6"
    )
    manifest = {
        "dataset_id": dataset_id, "dataset_type": "execution_snapshots",
        "as_of_date": day, "source": "TUSHARE", "dataset_version": version,
        "request_params": {
            "symbols": request_symbols if request_symbols is not None else [row["symbol"] for row in rows],
        },
        "fetched_at": "2026-09-25T08:00:00+00:00", "row_count": len(rows),
        "schema_hash": hashlib.sha256(table.schema.serialize().to_pybytes()).hexdigest(),
        "raw_record_hash": "a" * 64,
        "content_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "storage_path": relative.as_posix(),
    }
    directory = root / "manifests"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{dataset_id}.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_fkqt_execution_manifest_maps_decimal_and_compact_date(tmp_path: Path) -> None:
    _write_manifest(tmp_path, [_row()])
    bundle = load_fkqt_execution_manifest(tmp_path, date(2026, 9, 25), {"600000.SH"})
    assert bundle.trade_date == date(2026, 9, 25)
    assert str(bundle.snapshots["600000.SH"].daily_amount_cny) == "10000000"
    assert bundle.snapshots["600000.SH"].trade_date == date(2026, 9, 25)


def test_real_fkqt_publisher_gold_is_consumable_without_importing_fkqt(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST_JSON)
    assert manifest["source"] == "TUSHARE"
    assert manifest["dataset_version"] == "TUSHARE_EXECUTION_V1"
    assert manifest["dataset_id"] == "e53eb5c3cb65115ab6039b121a7a29eaa1c20c1bf209fadf4908538b686866bb"
    manifest_path = tmp_path / "manifests" / f"{manifest['dataset_id']}.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(MANIFEST_JSON, encoding="utf-8")
    parquet_path = tmp_path / manifest["storage_path"]
    parquet_path.parent.mkdir(parents=True)
    parquet_path.write_bytes(base64.b64decode(PARQUET_BASE64))
    bundle = load_fkqt_execution_manifest(tmp_path, date(2026, 9, 25), {"600000.SH"})
    assert bundle.source_manifest_id == manifest["dataset_id"]
    assert bundle.snapshots["600000.SH"].daily_amount_cny == 10_000_000


def test_fkqt_execution_manifest_rejects_missing_held_symbol(tmp_path: Path) -> None:
    _write_manifest(tmp_path, [_row()])
    with pytest.raises(FkqtBundleError, match="EXECUTION_SYMBOL_COVERAGE_INCOMPLETE"):
        load_fkqt_execution_manifest(tmp_path, date(2026, 9, 25), {"600000.SH", "300750.SZ"})


def test_fkqt_execution_manifest_rejects_tampered_parquet(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, [_row()])
    path.write_bytes(path.read_bytes() + b"corrupt")
    with pytest.raises(FkqtBundleError, match="DATASET_HASH_MISMATCH"):
        load_fkqt_execution_manifest(tmp_path, date(2026, 9, 25), set())


@pytest.mark.parametrize("version,day,request_symbols,error", [
    ("TUSHARE_EXECUTION_V0", "20260925", None, "DATASET_VERSION_UNSUPPORTED"),
    ("TUSHARE_EXECUTION_V1", "20260924", None, "DATASET_AS_OF_DATE_MISMATCH"),
    ("TUSHARE_EXECUTION_V1", "20260925", ["000001.SZ"], "EXECUTION_SYMBOL_COVERAGE_INCOMPLETE"),
])
def test_fkqt_execution_manifest_rejects_bad_manifest(
    tmp_path: Path, version: str, day: str, request_symbols: list[str] | None, error: str,
) -> None:
    _write_manifest(tmp_path, [_row()], version=version, day=day, request_symbols=request_symbols)
    with pytest.raises(FkqtBundleError, match=error):
        load_fkqt_execution_manifest(tmp_path, date(2026, 9, 25), set())


def test_fkqt_execution_manifest_rejects_ambiguous_day(tmp_path: Path) -> None:
    _write_manifest(tmp_path, [_row()], suffix="first")
    _write_manifest(tmp_path, [_row()], suffix="second")
    with pytest.raises(FkqtBundleError, match="DATASET_MANIFEST_AMBIGUOUS"):
        load_fkqt_execution_manifest(tmp_path, date(2026, 9, 25), set())


@pytest.mark.parametrize("field,error", [
    ("unadjusted_close", "EXECUTION_CLOSE_PRICE_REQUIRED"),
    ("upper_limit_price", "PRICE_LIMIT_DATA_UNAVAILABLE"),
    ("daily_amount_cny", "CAPACITY_DATA_UNAVAILABLE"),
])
def test_fkqt_execution_manifest_rejects_missing_execution_fields(
    tmp_path: Path, field: str, error: str,
) -> None:
    row = _row()
    row[field] = None
    _write_manifest(tmp_path, [row])
    with pytest.raises(FkqtBundleError, match=error):
        load_fkqt_execution_manifest(tmp_path, date(2026, 9, 25), set())


def test_prepare_execution_accepts_fkqt_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "upstream"
    _write_manifest(root, [_row()])
    monkeypatch.setenv("JEV_INVESTOR_EXECUTION_BUNDLE_ROOT", str(tmp_path / "frozen"))
    assert main([
        "daily", "prepare-execution", "--trade-date", "2026-09-25",
        "--manifest-root", str(root),
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert Path(output["execution_ref"]).is_file()
    assert len(output["source_manifest_id"]) == 64
    origin = Path(output["execution_ref"]).with_suffix(".origin.json")
    assert origin.is_file()
    assert json.loads(origin.read_text(encoding="utf-8"))["source_manifest_id"] == output["source_manifest_id"]


def test_manual_bundle_cannot_gain_fkqt_origin_after_freeze(tmp_path: Path) -> None:
    root = tmp_path / "upstream"
    _write_manifest(root, [_row()])
    sourced = load_fkqt_execution_manifest(root, date(2026, 9, 25), set())
    manual = sourced.model_copy(update={
        "source_manifest_id": None, "source_manifest_content_hash": None,
    })
    store = ExecutionBundleStore(tmp_path / "frozen")
    store.save(manual)
    with pytest.raises(ValueError, match="EXECUTION_SOURCE_CONFLICT"):
        store.save(sourced)


def test_loaded_bundle_retains_verified_fkqt_origin(tmp_path: Path) -> None:
    root = tmp_path / "upstream"
    _write_manifest(root, [_row()])
    sourced = load_fkqt_execution_manifest(root, date(2026, 9, 25), set())
    path = ExecutionBundleStore(tmp_path / "frozen").save(sourced)
    loaded = load_execution_bundle(path)
    assert loaded.source_manifest_id == sourced.source_manifest_id
    origin = path.with_suffix(".origin.json")
    payload = json.loads(origin.read_text(encoding="utf-8"))
    payload["execution_hash"] = "0" * 64
    origin.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="EXECUTION_SOURCE_CONFLICT"):
        load_execution_bundle(path)
