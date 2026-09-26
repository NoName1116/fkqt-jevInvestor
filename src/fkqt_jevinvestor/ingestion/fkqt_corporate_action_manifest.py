import re
from datetime import date, datetime

from pathlib import Path

from fkqt_jevinvestor.domain.corporate_actions import CorporateActionEventV1, CorporateActionManifestV1
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.ingestion.fkqt_execution_manifest_v2 import _read_dataset  # pyright: ignore[reportPrivateUsage]
from fkqt_jevinvestor.ingestion.fkqt_manifest import FkqtBundleError


def load_corporate_actions(root: Path, trade_date: date, cutoff: datetime) -> CorporateActionManifestV1:
    if cutoff.tzinfo is None:
        raise FkqtBundleError("POINT_IN_TIME_VIOLATION")
    manifest, rows, fields = _read_dataset(root, trade_date, "corporate_actions", "TUSHARE_CORPORATE_ACTION_V1")
    try:
        fetched_at = datetime.fromisoformat(manifest.fetched_at)
    except ValueError as exc:
        raise FkqtBundleError("POINT_IN_TIME_VIOLATION") from exc
    if fetched_at.tzinfo is None or fetched_at > cutoff:
        raise FkqtBundleError("POINT_IN_TIME_VIOLATION")
    required = set(CorporateActionEventV1.model_fields)
    if not required.issubset(fields):
        raise FkqtBundleError("CORPORATE_ACTION_SCHEMA_INVALID")
    requested = manifest.request_params.get("symbols")
    if (not isinstance(requested, list)
            or manifest.request_params.get("coverage_complete") is not True
            or manifest.request_params.get("checked_symbols") != requested):
        raise FkqtBundleError("CORPORATE_ACTION_COVERAGE_INCOMPLETE")
    symbols = tuple(str(item) for item in requested)
    if len(set(symbols)) != len(symbols) or any(re.fullmatch(r"\d{6}\.(SH|SZ)", s) is None for s in symbols):
        raise FkqtBundleError("CORPORATE_ACTION_SYMBOL_MISMATCH")
    try:
        events = tuple(CorporateActionEventV1.model_validate(row) for row in rows)
    except ValueError as exc:
        raise FkqtBundleError("CORPORATE_ACTION_SCHEMA_INVALID") from exc
    if len({event.event_id for event in events}) != len(events):
        raise FkqtBundleError("CORPORATE_ACTION_DUPLICATE_EVENT")
    if any(event.symbol not in symbols for event in events):
        raise FkqtBundleError("CORPORATE_ACTION_SYMBOL_MISMATCH")
    if any(event.available_at > cutoff for event in events):
        raise FkqtBundleError("POINT_IN_TIME_VIOLATION")
    provisional = CorporateActionManifestV1(
        trade_date=trade_date, requested_symbols=symbols, coverage_complete=True,
        events=events, source_manifest_id=manifest.dataset_id,
        source_content_hash=manifest.content_hash, content_hash="0" * 64,
    )
    payload = provisional.model_dump(mode="json")
    payload["content_hash"] = ""
    return provisional.model_copy(update={"content_hash": sha256_json(payload)})
