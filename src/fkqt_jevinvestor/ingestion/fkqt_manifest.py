import hashlib
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fkqt_jevinvestor.domain.market import TradingDayStatus, TradingStatus
from fkqt_jevinvestor.domain.market_features import (
    AdjustmentMode,
    DailyBar,
    MarketSnapshot,
    SecurityTradeState,
)
from fkqt_jevinvestor.ingestion.canonical import sha256_json

REQUIRED_DATASETS = (
    "trading_calendar",
    "security_master",
    "security_name_history",
    "suspension_status",
    "daily_bars",
)
SUPPORTED_DATASET_VERSION = "TUSHARE_PRO_V1"


class FkqtBundleError(RuntimeError):
    pass


class DatasetManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_id: str = Field(min_length=64, max_length=64)
    dataset_type: str
    as_of_date: str
    source: str
    dataset_version: str
    request_params: Mapping[str, object]
    fetched_at: str
    row_count: int = Field(ge=0)
    schema_hash: str = Field(min_length=64, max_length=64)
    raw_record_hash: str = Field(min_length=64, max_length=64)
    content_hash: str = Field(min_length=64, max_length=64)
    storage_path: str


class FkqtManifestProvider:
    def __init__(self, bundle_root: Path) -> None:
        self.root = bundle_root.resolve()

    async def freeze_snapshot(
        self,
        *,
        symbols: tuple[str, ...],
        decision_date: date,
        decision_cutoff: datetime,
        lookback_trading_days: int,
    ) -> MarketSnapshot:
        if decision_cutoff.date() != decision_date:
            raise FkqtBundleError("POINT_IN_TIME_VIOLATION")
        if lookback_trading_days <= 0:
            raise FkqtBundleError("INVALID_LOOKBACK_TRADING_DAYS")
        normalized_symbols = tuple(sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()}))
        if not normalized_symbols:
            raise FkqtBundleError("SYMBOLS_REQUIRED")

        manifests = self._load_manifests(decision_date)
        rows = {
            dataset_type: self._read_rows(manifests[dataset_type])
            for dataset_type in REQUIRED_DATASETS
        }
        self._validate_volume_unit(manifests["daily_bars"])

        next_trade_date = self._next_trade_date(rows["trading_calendar"], decision_date)
        bars = self._daily_bars(
            rows["daily_bars"],
            normalized_symbols,
            decision_date,
            lookback_trading_days,
        )
        states = self._security_states(
            symbols=normalized_symbols,
            decision_date=decision_date,
            calendar_rows=rows["trading_calendar"],
            master_rows=rows["security_master"],
            name_rows=rows["security_name_history"],
            suspension_rows=rows["suspension_status"],
            bars=bars,
        )
        manifest_ids = tuple(manifests[name].dataset_id for name in REQUIRED_DATASETS)
        universe_hash = sha256_json(
            {
                "decision_date": decision_date.isoformat(),
                "symbols": normalized_symbols,
                "source_manifest_ids": manifest_ids,
            }
        )
        snapshot_id = sha256_json(
            {
                "decision_cutoff": decision_cutoff.isoformat(),
                "lookback_trading_days": lookback_trading_days,
                "universe_snapshot_hash": universe_hash,
            }
        )
        snapshot = MarketSnapshot(
            snapshot_id=snapshot_id,
            decision_date=decision_date,
            decision_cutoff=decision_cutoff,
            next_trade_date=next_trade_date,
            universe_snapshot_hash=universe_hash,
            daily_bars=bars,
            security_states=states,
            source_manifest_ids=manifest_ids,
            content_hash="0" * 64,
        )
        payload = snapshot.model_dump(mode="json")
        payload["content_hash"] = ""
        return snapshot.model_copy(update={"content_hash": sha256_json(payload)})

    def _load_manifests(self, decision_date: date) -> dict[str, DatasetManifest]:
        manifest_root = self.root / "manifests"
        if not manifest_root.is_dir():
            raise FkqtBundleError("DATASET_MANIFEST_MISSING")
        result: dict[str, DatasetManifest] = {}
        expected_as_of = decision_date.strftime("%Y%m%d")
        for path in sorted(manifest_root.glob("*.json")):
            try:
                manifest = DatasetManifest.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValidationError, ValueError) as exc:
                raise FkqtBundleError("DATASET_MANIFEST_INVALID") from exc
            if manifest.dataset_type not in REQUIRED_DATASETS:
                continue
            if path.stem != manifest.dataset_id:
                raise FkqtBundleError("DATASET_MANIFEST_ID_MISMATCH")
            if manifest.as_of_date != expected_as_of:
                raise FkqtBundleError("DATASET_AS_OF_DATE_MISMATCH")
            if manifest.dataset_version != SUPPORTED_DATASET_VERSION:
                raise FkqtBundleError("DATASET_VERSION_UNSUPPORTED")
            if manifest.dataset_type in result:
                raise FkqtBundleError("DATASET_MANIFEST_AMBIGUOUS")
            result[manifest.dataset_type] = manifest

        if set(result) != set(REQUIRED_DATASETS):
            raise FkqtBundleError("DATASET_MANIFEST_MISSING")
        return result

    def _read_rows(self, manifest: DatasetManifest) -> list[dict[str, object]]:
        path = (self.root / manifest.storage_path).resolve()
        if not path.is_relative_to(self.root):
            raise FkqtBundleError("DATASET_STORAGE_PATH_INVALID")
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise FkqtBundleError("DATASET_STORAGE_MISSING") from exc
        if hashlib.sha256(payload).hexdigest() != manifest.content_hash:
            raise FkqtBundleError("DATASET_HASH_MISMATCH")
        try:
            table = pq.read_table(  # pyright: ignore[reportUnknownMemberType]
                path,
                partitioning=None,
            )
        except Exception as exc:
            raise FkqtBundleError("DATASET_PARQUET_INVALID") from exc
        schema_hash = hashlib.sha256(table.schema.serialize().to_pybytes()).hexdigest()
        if schema_hash != manifest.schema_hash:
            raise FkqtBundleError("DATASET_SCHEMA_HASH_MISMATCH")
        if table.num_rows != manifest.row_count:
            raise FkqtBundleError("DATASET_ROW_COUNT_MISMATCH")
        return cast(list[dict[str, object]], table.to_pylist())

    @staticmethod
    def _validate_volume_unit(manifest: DatasetManifest) -> None:
        if manifest.request_params.get("volume_unit") != "TUSHARE_100_SHARES":
            raise FkqtBundleError("DAILY_VOLUME_UNIT_MISMATCH")

    @staticmethod
    def _next_trade_date(calendar_rows: list[dict[str, object]], current: date) -> date:
        candidates = sorted(
            parsed
            for row in calendar_rows
            if int(str(row.get("is_open", 0))) == 1
            if (parsed := _parse_date(row.get("cal_date"), "CALENDAR_DATE_INVALID")) > current
        )
        if not candidates:
            raise FkqtBundleError("NEXT_TRADE_DATE_UNAVAILABLE")
        return candidates[0]

    @staticmethod
    def _daily_bars(
        rows: list[dict[str, object]],
        symbols: tuple[str, ...],
        decision_date: date,
        lookback: int,
    ) -> dict[str, tuple[DailyBar, ...]]:
        grouped: dict[str, list[DailyBar]] = {symbol: [] for symbol in symbols}
        for row in rows:
            symbol = str(row.get("ts_code", "")).strip().upper()
            if symbol not in grouped:
                continue
            trade_date = _parse_date(row.get("trade_date"), "DAILY_BAR_DATE_INVALID")
            if trade_date > decision_date:
                raise FkqtBundleError("POINT_IN_TIME_VIOLATION")
            grouped[symbol].append(
                DailyBar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=_decimal(row.get("open"), "DAILY_BAR_PRICE_INVALID"),
                    high=_decimal(row.get("high"), "DAILY_BAR_PRICE_INVALID"),
                    low=_decimal(row.get("low"), "DAILY_BAR_PRICE_INVALID"),
                    close=_decimal(row.get("close"), "DAILY_BAR_PRICE_INVALID"),
                    previous_close=_decimal(
                        row.get("pre_close"), "DAILY_BAR_PRICE_INVALID"
                    ),
                    volume=_decimal(row.get("vol"), "DAILY_BAR_VOLUME_INVALID"),
                    amount_cny=(
                        _decimal(row.get("amount"), "DAILY_BAR_AMOUNT_INVALID")
                        * Decimal(1000)
                    ),
                    adjustment_mode=AdjustmentMode.NONE,
                )
            )
        return {
            symbol: tuple(sorted(values, key=lambda item: item.trade_date)[-lookback:])
            for symbol, values in grouped.items()
        }

    @staticmethod
    def _security_states(
        *,
        symbols: tuple[str, ...],
        decision_date: date,
        calendar_rows: list[dict[str, object]],
        master_rows: list[dict[str, object]],
        name_rows: list[dict[str, object]],
        suspension_rows: list[dict[str, object]],
        bars: Mapping[str, tuple[DailyBar, ...]],
    ) -> dict[str, SecurityTradeState]:
        is_open = any(
            _parse_date(row.get("cal_date"), "CALENDAR_DATE_INVALID") == decision_date
            and int(str(row.get("is_open", 0))) == 1
            for row in calendar_rows
        )
        if not is_open:
            raise FkqtBundleError("DECISION_DATE_NOT_OPEN")
        master_by_symbol = {
            str(row.get("ts_code", "")).strip().upper(): row for row in master_rows
        }
        suspended = {
            str(row.get("ts_code", "")).strip().upper()
            for row in suspension_rows
            if _parse_date(
                row.get("suspend_date", row.get("trade_date")),
                "SUSPENSION_DATE_INVALID",
            )
            == decision_date
        }
        result: dict[str, SecurityTradeState] = {}
        for symbol in symbols:
            master = master_by_symbol.get(symbol)
            missing = [
                "CORPORATE_ACTION_DATA_UNAVAILABLE",
                "INITIAL_LIMIT_PERIOD_UNAVAILABLE",
                "PRICE_LIMIT_DATA_UNAVAILABLE",
            ]
            if not bars.get(symbol):
                missing.append("DAILY_BARS_MISSING")
            if master is None:
                missing.extend(("SECURITY_MASTER_MISSING", "ST_STATUS_UNAVAILABLE"))
                listing_date = None
                board = "UNKNOWN"
                is_st = None
            else:
                listing_date = _optional_date(master.get("list_date"))
                board = str(master.get("market") or "UNKNOWN").strip().upper()
                active_name = _active_security_name(symbol, decision_date, master, name_rows)
                is_st = "ST" in active_name.upper()
            result[symbol] = SecurityTradeState(
                symbol=symbol,
                trade_date=decision_date,
                trading_day_status=TradingDayStatus.OPEN,
                trading_status=(
                    TradingStatus.SUSPENDED if symbol in suspended else TradingStatus.TRADING
                ),
                is_st_or_delisting_risk=is_st,
                upper_limit_price=None,
                lower_limit_price=None,
                is_initial_no_limit_period=None,
                corporate_action_status="UNKNOWN",
                market=_market_from_symbol(symbol),
                board=board,
                listing_date=listing_date,
                missing_reasons=tuple(sorted(set(missing))),
            )
        return result


def _active_security_name(
    symbol: str,
    decision_date: date,
    master: Mapping[str, object],
    name_rows: list[dict[str, object]],
) -> str:
    active = str(master.get("name") or "")
    for row in name_rows:
        if str(row.get("ts_code", "")).strip().upper() != symbol:
            continue
        start = _optional_date(row.get("start_date"))
        end = _optional_date(row.get("end_date"))
        if (start is None or start <= decision_date) and (end is None or end >= decision_date):
            active = str(row.get("name") or active)
    return active


def _parse_date(value: object, error_code: str) -> date:
    parsed = _optional_date(value)
    if parsed is None:
        raise FkqtBundleError(error_code)
    return parsed


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("-", "")
    if not text or text.lower() in {"none", "nan", "nat"}:
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError as exc:
        raise FkqtBundleError("DATE_INVALID") from exc


def _decimal(value: object, error_code: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise FkqtBundleError(error_code) from exc
    if not result.is_finite():
        raise FkqtBundleError(error_code)
    return result


def _market_from_symbol(symbol: str) -> str:
    if symbol.endswith(".SH"):
        return "SSE"
    if symbol.endswith(".SZ"):
        return "SZSE"
    return "UNKNOWN"
