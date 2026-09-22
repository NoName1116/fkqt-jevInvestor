from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AdjustmentMode(StrEnum):
    NONE = "NONE"
    QFQ = "QFQ"


class DailyBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    trade_date: date
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    previous_close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    amount_cny: Decimal = Field(ge=0)
    adjustment_mode: AdjustmentMode


class SecurityTradeState(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    trade_date: date
    trading_day_status: str
    trading_status: str
    is_st_or_delisting_risk: bool | None
    upper_limit_price: Decimal | None = Field(default=None, gt=0)
    lower_limit_price: Decimal | None = Field(default=None, gt=0)
    is_initial_no_limit_period: bool | None
    corporate_action_status: str
    market: str
    board: str
    listing_date: date | None
    missing_reasons: tuple[str, ...] = ()


class MarketSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    decision_date: date
    decision_cutoff: datetime
    next_trade_date: date
    universe_snapshot_hash: str = Field(min_length=64, max_length=64)
    daily_bars: Mapping[str, tuple[DailyBar, ...]]
    security_states: Mapping[str, SecurityTradeState]
    source_manifest_ids: tuple[str, ...]
    content_hash: str = Field(min_length=64, max_length=64)


class FeatureValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    feature_code: str
    feature_version: str
    as_of: datetime
    lookback_window: int = Field(gt=0)
    value: Decimal | None
    missing_reason: str | None
    source_snapshot_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def require_value_xor_missing_reason(self) -> Self:
        if (self.value is None) == (self.missing_reason is None):
            raise ValueError("value and missing_reason must have exactly one non-null value")
        return self


class MarketFeatureSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    decision_date: date
    values: Mapping[str, FeatureValue]
    content_hash: str = Field(min_length=64, max_length=64)
