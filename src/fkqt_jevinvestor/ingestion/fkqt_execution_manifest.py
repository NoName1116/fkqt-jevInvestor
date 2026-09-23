"""只读校验 FKQT 发布的 D+1 Tushare 执行 Manifest。"""

import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

import pyarrow.parquet as pq
from pydantic import ValidationError

from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot, TradingStatus
from fkqt_jevinvestor.ingestion.execution_bundle import ExecutionBundleV1
from fkqt_jevinvestor.ingestion.fkqt_manifest import DatasetManifest, FkqtBundleError

EXECUTION_DATASET_VERSION = "TUSHARE_EXECUTION_V1"
REQUIRED_FIELDS = frozenset({
    "symbol", "trade_date", "trading_day_status", "trading_status",
    "open_price", "unadjusted_close", "daily_amount_cny",
    "upper_limit_price", "lower_limit_price", "is_initial_no_limit_period",
})
SYMBOL_PATTERN = re.compile(r"\d{6}\.(?:SH|SZ)")


def _positive_decimal(value: object, error_code: str, *, zero_allowed: bool = False) -> None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FkqtBundleError(error_code) from exc
    if not number.is_finite() or (number < 0 if zero_allowed else number <= 0):
        raise FkqtBundleError(error_code)


def load_fkqt_execution_manifest(
    root: Path, trade_date: date, required_symbols: set[str]
) -> ExecutionBundleV1:
    bundle_root = root.resolve()
    paths = sorted((bundle_root / "manifests").glob("*.json"))
    if not paths:
        raise FkqtBundleError("DATASET_MANIFEST_MISSING")
    if len(paths) != 1:
        raise FkqtBundleError("DATASET_MANIFEST_AMBIGUOUS")
    try:
        manifest = DatasetManifest.model_validate_json(paths[0].read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        raise FkqtBundleError("DATASET_MANIFEST_INVALID") from exc
    if manifest.dataset_id != paths[0].stem or manifest.dataset_type != "execution_snapshots":
        raise FkqtBundleError("DATASET_MANIFEST_ID_MISMATCH")
    if manifest.as_of_date != trade_date.strftime("%Y%m%d"):
        raise FkqtBundleError("DATASET_AS_OF_DATE_MISMATCH")
    if manifest.source != "TUSHARE" or manifest.dataset_version != EXECUTION_DATASET_VERSION:
        raise FkqtBundleError("DATASET_VERSION_UNSUPPORTED")
    requested = manifest.request_params.get("symbols")
    requested_items = cast(list[object], requested) if isinstance(requested, list) else []
    if (
        not requested_items
        or any(not isinstance(item, str) or not SYMBOL_PATTERN.fullmatch(item) for item in requested_items)
        or len(requested_items) != len({str(item) for item in requested_items})
    ):
        raise FkqtBundleError("EXECUTION_SYMBOL_COVERAGE_INCOMPLETE")
    requested_symbols = cast(list[str], requested_items)
    path = (bundle_root / manifest.storage_path).resolve()
    if not path.is_relative_to(bundle_root):
        raise FkqtBundleError("DATASET_STORAGE_PATH_INVALID")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise FkqtBundleError("DATASET_STORAGE_MISSING") from exc
    if hashlib.sha256(payload).hexdigest() != manifest.content_hash:
        raise FkqtBundleError("DATASET_HASH_MISMATCH")
    try:
        table = pq.read_table(path, partitioning=None)  # pyright: ignore[reportUnknownMemberType]
    except Exception as exc:
        raise FkqtBundleError("DATASET_PARQUET_INVALID") from exc
    if hashlib.sha256(table.schema.serialize().to_pybytes()).hexdigest() != manifest.schema_hash:
        raise FkqtBundleError("DATASET_SCHEMA_HASH_MISMATCH")
    if table.num_rows != manifest.row_count:
        raise FkqtBundleError("DATASET_ROW_COUNT_MISMATCH")
    if not REQUIRED_FIELDS.issubset(table.schema.names):
        raise FkqtBundleError("EXECUTION_SCHEMA_INVALID")
    rows = cast(list[dict[str, object]], table.to_pylist())
    symbols = [str(row["symbol"]) for row in rows]
    if (
        len(symbols) != len(set(symbols))
        or set(symbols) != set(requested_symbols)
        or not required_symbols.issubset(symbols)
    ):
        raise FkqtBundleError("EXECUTION_SYMBOL_COVERAGE_INCOMPLETE")
    snapshots: dict[str, MarketExecutionSnapshot] = {}
    for row in rows:
        raw_date = str(row["trade_date"]).replace("-", "")
        try:
            if not re.fullmatch(r"\d{8}", raw_date):
                raise ValueError("EXECUTION_BUNDLE_DATE_MISMATCH")
            row_date = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8]))
        except ValueError as exc:
            raise FkqtBundleError("EXECUTION_BUNDLE_DATE_MISMATCH") from exc
        if row_date != trade_date:
            raise FkqtBundleError("EXECUTION_BUNDLE_DATE_MISMATCH")
        if row["unadjusted_close"] is None:
            raise FkqtBundleError("EXECUTION_CLOSE_PRICE_REQUIRED")
        _positive_decimal(row["unadjusted_close"], "EXECUTION_CLOSE_PRICE_REQUIRED")
        status = row["trading_status"]
        if status == TradingStatus.TRADING.value:
            _positive_decimal(row["open_price"], "OPEN_PRICE_UNAVAILABLE")
            _positive_decimal(row["daily_amount_cny"], "CAPACITY_DATA_UNAVAILABLE", zero_allowed=True)
            if row["is_initial_no_limit_period"] is not True:
                _positive_decimal(row["upper_limit_price"], "PRICE_LIMIT_DATA_UNAVAILABLE")
                _positive_decimal(row["lower_limit_price"], "PRICE_LIMIT_DATA_UNAVAILABLE")
        elif status != TradingStatus.SUSPENDED.value:
            raise FkqtBundleError("TRADING_STATUS_UNKNOWN")
        try:
            snapshots[str(row["symbol"])] = MarketExecutionSnapshot.model_validate({
                **row, "trade_date": row_date,
            })
        except (ValueError, ValidationError) as exc:
            raise FkqtBundleError("EXECUTION_SCHEMA_INVALID") from exc
    try:
        return ExecutionBundleV1.create(
            trade_date, snapshots, requires_origin=True,
            source_manifest_id=manifest.dataset_id,
            source_manifest_content_hash=manifest.content_hash,
        )
    except (ValueError, ValidationError) as exc:
        raise FkqtBundleError(str(exc)) from exc
