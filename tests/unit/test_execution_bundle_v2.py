import json
from datetime import date
from pathlib import Path

import pytest

from fkqt_jevinvestor.domain.forward import TradingDayProofV1
from fkqt_jevinvestor.ingestion.execution_bundle import load_execution_bundle
from fkqt_jevinvestor.ingestion.execution_bundle import ExecutionBundleStore
from fkqt_jevinvestor.ingestion.execution_bundle_v2 import ExecutionBundleV2

DAY = date(2026, 9, 24)


def proof() -> TradingDayProofV1:
    return TradingDayProofV1.create(
        DAY, is_open=True, exchange="SSE",
        source_manifest_id="a" * 64, source_content_hash="b" * 64,
    )


def empty_bundle() -> ExecutionBundleV2:
    return ExecutionBundleV2.create(
        DAY, {}, calendar_proof=proof(), source_manifest_id="c" * 64,
        source_manifest_content_hash="d" * 64,
    )


def test_empty_v2_requires_verified_open_calendar() -> None:
    bundle = empty_bundle()
    bundle.verify(DAY, set())
    assert bundle.snapshots == {}
    assert bundle.schema_version == "execution-bundle-v2"
    with pytest.raises(ValueError, match="EXECUTION_SYMBOL_COVERAGE_INCOMPLETE"):
        bundle.verify(DAY, {"600000.SH"})
    with pytest.raises(ValueError, match="EXECUTION_TRADING_DAY_NOT_CONFIRMED"):
        bundle.model_copy(update={"calendar_proof": proof().model_copy(
            update={"is_open": False}
        )}).verify(DAY, set())


def test_v2_detects_calendar_date_source_and_bundle_tampering() -> None:
    bundle = empty_bundle()
    with pytest.raises(ValueError, match="TRADING_DAY_PROOF_HASH_MISMATCH"):
        bundle.model_copy(update={"calendar_proof": proof().model_copy(
            update={"source_content_hash": "0" * 64}
        )}).verify(DAY, set())
    with pytest.raises(ValueError, match="EXECUTION_BUNDLE_HASH_MISMATCH"):
        bundle.model_copy(update={"source_manifest_id": "0" * 64}).verify(DAY, set())
    with pytest.raises(ValueError, match="EXECUTION_BUNDLE_DATE_MISMATCH"):
        bundle.verify(date(2026, 9, 25), set())


def test_v2_load_validates_hash_and_requires_origin(tmp_path: Path) -> None:
    bundle = empty_bundle()
    path = tmp_path / "bundle.json"
    path.write_text(bundle.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="EXECUTION_SOURCE_MISSING"):
        load_execution_bundle(path)
    path.with_suffix(".origin.json").write_text(json.dumps({
        "execution_hash": bundle.content_hash,
        "source_type": "FKQT_TUSHARE_MANIFEST",
        "source_manifest_id": bundle.source_manifest_id,
        "source_manifest_content_hash": bundle.source_manifest_content_hash,
    }), encoding="utf-8")
    loaded = load_execution_bundle(path)
    loaded.verify(DAY, set())
    assert loaded.content_hash == bundle.content_hash


def test_v2_store_roundtrip_and_origin_tamper(tmp_path: Path) -> None:
    bundle = empty_bundle()
    store = ExecutionBundleStore(tmp_path)
    path = store.save(bundle)
    assert store.save(bundle) == path
    assert load_execution_bundle(path).content_hash == bundle.content_hash
    path.with_suffix(".origin.json").write_text(json.dumps({
        "execution_hash": bundle.content_hash,
        "source_type": "FKQT_TUSHARE_MANIFEST",
        "source_manifest_id": "0" * 64,
        "source_manifest_content_hash": bundle.source_manifest_content_hash,
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="EXECUTION_SOURCE_CONFLICT"):
        load_execution_bundle(path)
