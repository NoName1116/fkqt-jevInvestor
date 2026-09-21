from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class TradingDayStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class TradingStatus(StrEnum):
    TRADING = "TRADING"
    SUSPENDED = "SUSPENDED"
    UNKNOWN = "UNKNOWN"


class MarketExecutionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    trade_date: date
    trading_day_status: TradingDayStatus
    trading_status: TradingStatus
    open_price: Decimal | None = Field(default=None, gt=0)
    unadjusted_close: Decimal | None = Field(default=None, gt=0)
    daily_amount_cny: Decimal | None = Field(default=None, ge=0)
    upper_limit_price: Decimal | None = Field(default=None, gt=0)
    lower_limit_price: Decimal | None = Field(default=None, gt=0)
    is_initial_no_limit_period: bool = False


class MarketExecutionProvider(Protocol):
    async def get_execution_snapshots(
        self,
        symbols: tuple[str, ...],
        trade_date: date,
    ) -> Mapping[str, MarketExecutionSnapshot]: ...
