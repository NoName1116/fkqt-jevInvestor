from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer


class PositionState(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    quantity: int = Field(ge=0)
    sellable_quantity: int = Field(ge=0)
    average_cost: Decimal = Field(ge=0)
    total_cost: Decimal = Field(ge=0)
    last_price: Decimal = Field(ge=0)
    current_position_pct: Decimal = Field(ge=0, le=1)
    unrealized_pnl: Decimal
    holding_trading_days: int = Field(ge=0)
    locked_bonus_quantity: int = Field(default=0, ge=0)

    @model_serializer(mode="wrap")
    def serialize_compatible(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        payload = handler(self)
        if self.locked_bonus_quantity == 0:
            payload.pop("locked_bonus_quantity", None)
        return payload


class PortfolioState(BaseModel):
    model_config = ConfigDict(frozen=True)

    portfolio_id: str
    cash_balance: Decimal = Field(ge=0)
    frozen_cash: Decimal = Field(ge=0)
    realized_pnl: Decimal
    dividend_receivable: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)
    dividend_income: Decimal = Field(default=Decimal(0), ge=0, allow_inf_nan=False)
    positions: tuple[PositionState, ...] = ()
    version: int = Field(ge=1)

    @property
    def total_equity(self) -> Decimal:
        return self.cash_balance + self.dividend_receivable + sum(
            (position.quantity * position.last_price for position in self.positions), Decimal(0)
        )

    @model_serializer(mode="wrap")
    def serialize_compatible(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        payload = handler(self)
        for name in ("dividend_receivable", "dividend_income"):
            if getattr(self, name) == 0:
                payload.pop(name, None)
        return payload

    def position(self, symbol: str) -> PositionState | None:
        return next((item for item in self.positions if item.symbol == symbol), None)
