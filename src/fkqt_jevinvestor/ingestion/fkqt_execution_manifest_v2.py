import hashlib
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pyarrow.parquet as pq

from fkqt_jevinvestor.domain.forward import TradingDayProofV1
from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot
from fkqt_jevinvestor.ingestion.execution_bundle_v2 import ExecutionBundleV2
from fkqt_jevinvestor.ingestion.fkqt_execution_manifest import REQUIRED_FIELDS
from fkqt_jevinvestor.ingestion.fkqt_manifest import DatasetManifest, FkqtBundleError


def _read_dataset(
    root: Path, trade_date: date, dataset_type: str, version: str,
) -> tuple[DatasetManifest, list[dict[str, object]], set[str]]:
    root = root.resolve()
    paths = sorted((root / "manifests").glob("*.json"))
    if not paths:
        raise FkqtBundleError("DATASET_MANIFEST_MISSING")
    if len(paths) != 1:
        raise FkqtBundleError("DATASET_MANIFEST_AMBIGUOUS")
    try:
        manifest = DatasetManifest.model_validate_json(paths[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FkqtBundleError("DATASET_MANIFEST_INVALID") from exc
    if manifest.dataset_id != paths[0].stem or manifest.dataset_type != dataset_type:
        raise FkqtBundleError("DATASET_MANIFEST_ID_MISMATCH")
    if manifest.as_of_date != trade_date.strftime("%Y%m%d"):
        raise FkqtBundleError("DATASET_AS_OF_DATE_MISMATCH")
    if manifest.source != "TUSHARE" or manifest.dataset_version != version:
        raise FkqtBundleError("DATASET_VERSION_UNSUPPORTED")
    path = (root / manifest.storage_path).resolve()
    if not path.is_relative_to(root):
        raise FkqtBundleError("DATASET_STORAGE_PATH_INVALID")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise FkqtBundleError("DATASET_STORAGE_MISSING") from exc
    if hashlib.sha256(raw).hexdigest() != manifest.content_hash:
        raise FkqtBundleError("DATASET_HASH_MISMATCH")
    try:
        table = pq.read_table(path, partitioning=None)  # pyright: ignore[reportUnknownMemberType]
    except Exception as exc:
        raise FkqtBundleError("DATASET_PARQUET_INVALID") from exc
    if hashlib.sha256(table.schema.serialize().to_pybytes()).hexdigest() != manifest.schema_hash:
        raise FkqtBundleError("DATASET_SCHEMA_HASH_MISMATCH")
    if table.num_rows != manifest.row_count:
        raise FkqtBundleError("DATASET_ROW_COUNT_MISMATCH")
    return manifest, cast(list[dict[str, object]], table.to_pylist()), set(table.schema.names)


def load_fkqt_execution_manifest_v2(
    root: Path, trade_date: date, required_symbols: set[str],
) -> ExecutionBundleV2:
    from fkqt_jevinvestor.ingestion.fkqt_execution_manifest import (
        _load_fkqt_execution_manifest,  # pyright: ignore[reportPrivateUsage]
    )

    manifest, rows, fields = _read_dataset(root, trade_date, "execution_snapshots", "TUSHARE_EXECUTION_V2")
    if not REQUIRED_FIELDS.issubset(fields):
        raise FkqtBundleError("EXECUTION_SCHEMA_INVALID")
    calendar, calendar_rows, calendar_fields = _read_dataset(
        root / "calendar", trade_date, "execution_calendar", "TUSHARE_EXECUTION_CALENDAR_V1",
    )
    if (calendar.dataset_id != manifest.request_params.get("calendar_manifest_id")
            or calendar.content_hash != manifest.request_params.get("calendar_content_hash")):
        raise FkqtBundleError("TRADING_CALENDAR_HASH_MISMATCH")
    if len(calendar_rows) != 1 or not {"exchange", "cal_date", "is_open", "pretrade_date"}.issubset(calendar_fields):
        raise FkqtBundleError("TRADING_CALENDAR_INCOMPLETE")
    row = calendar_rows[0]
    if str(row["cal_date"]) != trade_date.strftime("%Y%m%d") or row["exchange"] != "SSE":
        raise FkqtBundleError("TRADING_CALENDAR_INCOMPLETE")
    try:
        previous = date.fromisoformat(str(row["pretrade_date"]))
        proof = TradingDayProofV1.create(
            trade_date, is_open=str(row["is_open"]) == "1", exchange="SSE",
            source_manifest_id=calendar.dataset_id, source_content_hash=calendar.content_hash,
            previous_trade_date=previous,
        )
        proof.verify(trade_date)
    except ValueError as exc:
        raise FkqtBundleError(str(exc)) from exc
    snapshots: Mapping[str, MarketExecutionSnapshot] = {}
    if rows:
        snapshots = _load_fkqt_execution_manifest(
            root, trade_date, required_symbols, dataset_version="TUSHARE_EXECUTION_V2",
        ).snapshots
    elif manifest.request_params.get("symbols") != [] or required_symbols:
        raise FkqtBundleError("EXECUTION_SYMBOL_COVERAGE_INCOMPLETE")
    action_hash = manifest.request_params.get("corporate_action_manifest_hash")
    if action_hash is not None and not isinstance(action_hash, str):
        raise FkqtBundleError("CORPORATE_ACTION_HASH_MISMATCH")
    actions = None
    if action_hash is not None:
        from fkqt_jevinvestor.ingestion.fkqt_corporate_action_manifest import load_corporate_actions

        actions = load_corporate_actions(root / "corporate_actions", trade_date, datetime.now(UTC))
        if action_hash != actions.source_content_hash:
            raise FkqtBundleError("CORPORATE_ACTION_HASH_MISMATCH")
    return ExecutionBundleV2.create(
        trade_date, snapshots, calendar_proof=proof,
        source_manifest_id=manifest.dataset_id, source_manifest_content_hash=manifest.content_hash,
        corporate_action_manifest_hash=action_hash, corporate_actions=actions,
    )
