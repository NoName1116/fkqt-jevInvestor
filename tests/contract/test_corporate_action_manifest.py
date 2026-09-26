import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fkqt_jevinvestor.ingestion.fkqt_corporate_action_manifest import load_corporate_actions
from fkqt_jevinvestor.ingestion.fkqt_manifest import FkqtBundleError
from tests.unit.test_corporate_action_ledger import event


def write_actions(root: Path) -> Path:
    table = pa.Table.from_pylist([event().model_dump(mode="json")])
    path = root / "events.parquet"
    pq.write_table(table, path)  # pyright: ignore[reportUnknownMemberType]
    identity = "a" * 64
    manifest = {
        "dataset_id": identity, "dataset_type": "corporate_actions", "as_of_date": "20260924",
        "source": "TUSHARE", "dataset_version": "TUSHARE_CORPORATE_ACTION_V1",
        "request_params": {"symbols": ["600000.SH"], "checked_symbols": ["600000.SH"], "coverage_complete": True},
        "fetched_at": "2026-09-24T08:00:00+00:00", "row_count": 1,
        "schema_hash": hashlib.sha256(table.schema.serialize().to_pybytes()).hexdigest(),
        "raw_record_hash": "b" * 64, "content_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "storage_path": "events.parquet",
    }
    identity = hashlib.sha256(json.dumps(
        {key: manifest[key] for key in (
            "dataset_type", "as_of_date", "source", "dataset_version",
            "request_params", "schema_hash", "raw_record_hash",
        )}, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    manifest["dataset_id"] = identity
    (root / "manifests").mkdir()
    (root / "manifests" / f"{identity}.json").write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_future_availability_and_manifest_tamper_fail(tmp_path: Path) -> None:
    path = write_actions(tmp_path)
    with pytest.raises(FkqtBundleError, match="POINT_IN_TIME_VIOLATION"):
        load_corporate_actions(tmp_path, date(2026, 9, 24), datetime(2026, 8, 1, tzinfo=UTC))
    result = load_corporate_actions(tmp_path, date(2026, 9, 24), datetime(2026, 9, 24, 12, tzinfo=UTC))
    assert result.events[0].cash_per_share_gross.as_tuple().digits == (2,)
    path.write_bytes(path.read_bytes() + b"corrupt")
    with pytest.raises(FkqtBundleError, match="DATASET_HASH_MISMATCH"):
        load_corporate_actions(tmp_path, date(2026, 9, 24), datetime(2026, 9, 24, 12, tzinfo=UTC))


def test_fetched_coverage_cannot_be_used_before_observation(tmp_path: Path) -> None:
    write_actions(tmp_path)
    with pytest.raises(FkqtBundleError, match="POINT_IN_TIME_VIOLATION"):
        load_corporate_actions(tmp_path, date(2026, 9, 24), datetime(2026, 9, 24, 7, tzinfo=UTC))
