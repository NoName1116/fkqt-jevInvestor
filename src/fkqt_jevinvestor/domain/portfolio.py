from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


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


class PortfolioState(BaseModel):
    model_config = ConfigDict(frozen=True)

    portfolio_id: str
    cash_balance: Decimal = Field(ge=0)
    frozen_cash: Decimal = Field(ge=0)
    realized_pnl: Decimal
    positions: tuple[PositionState, ...] = ()
    version: int = Field(ge=1)

    def position(self, symbol: str) -> PositionState | None:
        return next((item for item in self.positions if item.symbol == symbol), None)
