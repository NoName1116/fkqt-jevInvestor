import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot

MONEY_QUANTUM = Decimal("0.01")
COST_QUANTUM = Decimal("0.00000001")
RATIO_QUANTUM = Decimal("0.00000001")


class CreatePortfolio(BaseModel):
    model_config = ConfigDict(frozen=True)

    portfolio_id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=128)
    initial_cash: Decimal = Field(default=Decimal(1000000), gt=0)


class ExecuteTradeDate(BaseModel):
    model_config = ConfigDict(frozen=True)

    portfolio_id: str = Field(min_length=1, max_length=40)
    trade_date: date
    expected_version: int = Field(ge=1)
    market_snapshots: Mapping[str, MarketExecutionSnapshot]


class AssetIdentityError(RuntimeError):
    pass


@dataclass
class PositionLot:
    acquired_on: date
    remaining_quantity: int
    total_cost: Decimal

    @property
    def unit_cost(self) -> Decimal:
        if self.remaining_quantity == 0:
            return Decimal(0).quantize(COST_QUANTUM)
        return (self.total_cost / self.remaining_quantity).quantize(COST_QUANTUM)

    def consume(self, quantity: int) -> Decimal:
        consumed = min(max(quantity, 0), self.remaining_quantity)
        if consumed == 0:
            return Decimal(0).quantize(COST_QUANTUM)
        cost = (self.total_cost * Decimal(consumed) / Decimal(self.remaining_quantity)).quantize(
            COST_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        self.remaining_quantity -= consumed
        self.total_cost = (self.total_cost - cost).quantize(COST_QUANTUM)
        return cost


@dataclass
class LedgerPosition:
    symbol: str
    lots: list[PositionLot] = field(default_factory=lambda: list[PositionLot]())
    last_price: Decimal = Decimal(0)

    @property
    def quantity(self) -> int:
        return sum(item.remaining_quantity for item in self.lots)

    @property
    def total_cost(self) -> Decimal:
        return sum((item.total_cost for item in self.lots), Decimal(0)).quantize(COST_QUANTUM)

    @property
    def average_cost(self) -> Decimal:
        if self.quantity == 0:
            return Decimal(0).quantize(COST_QUANTUM)
        return (self.total_cost / Decimal(self.quantity)).quantize(COST_QUANTUM)

    @property
    def market_value(self) -> Decimal:
        return (self.last_price * self.quantity).quantize(MONEY_QUANTUM, ROUND_HALF_UP)

    @property
    def unrealized_pnl(self) -> Decimal:
        return (self.market_value - self.total_cost).quantize(MONEY_QUANTUM, ROUND_HALF_UP)


class SaleResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    quantity: int
    gross_value: Decimal
    cost_basis: Decimal
    fees: Decimal
    realized_pnl: Decimal


class NavState(BaseModel):
    model_config = ConfigDict(frozen=True)

    valuation_date: date
    cash_balance: Decimal
    market_value: Decimal
    total_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    unit_nav: Decimal
    daily_return: Decimal
    cumulative_return: Decimal
    max_drawdown: Decimal


class PortfolioSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    portfolio_id: str
    snapshot_type: str
    as_of: datetime
    cash_balance: Decimal
    frozen_cash: Decimal
    market_value: Decimal
    total_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    content_hash: str


class PortfolioLedger:
    def __init__(
        self,
        portfolio_id: str,
        initial_cash: Decimal,
        cash_balance: Decimal,
        *,
        frozen_cash: Decimal = Decimal(0),
        realized_pnl: Decimal = Decimal(0),
    ) -> None:
        if initial_cash <= 0 or cash_balance < 0 or frozen_cash < 0:
            raise ValueError("portfolio cash values are invalid")
        self.portfolio_id = portfolio_id
        self.initial_cash = initial_cash.quantize(MONEY_QUANTUM)
        self.cash_balance = cash_balance.quantize(MONEY_QUANTUM)
        self.frozen_cash = frozen_cash.quantize(MONEY_QUANTUM)
        self.realized_pnl = realized_pnl.quantize(MONEY_QUANTUM)
        self._positions: dict[str, LedgerPosition] = {}
        self._nav: list[NavState] = []
        self._peak_equity = self.initial_cash
        self._max_drawdown = Decimal(0)

    def position(self, symbol: str) -> LedgerPosition:
        return self._positions.setdefault(symbol, LedgerPosition(symbol=symbol))

    @property
    def positions(self) -> tuple[LedgerPosition, ...]:
        return tuple(self._positions.values())

    @property
    def latest_nav(self) -> NavState | None:
        return self._nav[-1] if self._nav else None

    def restore_nav_history(self, history: tuple[NavState, ...]) -> None:
        self._nav = list(history)
        if history:
            self._peak_equity = max(item.total_equity for item in history)
            self._max_drawdown = max(item.max_drawdown for item in history)

    def total_equity(self, prices: dict[str, Decimal]) -> Decimal:
        market_value = Decimal(0)
        for symbol, position in self._positions.items():
            if position.quantity == 0:
                continue
            price = prices.get(symbol, position.last_price)
            if price <= 0:
                raise ValueError(f"missing valuation price for {symbol}")
            market_value += price * position.quantity
        return (self.cash_balance + market_value).quantize(MONEY_QUANTUM, ROUND_HALF_UP)

    def buy(
        self,
        symbol: str,
        quantity: int,
        fill_price: Decimal,
        fees: Decimal,
        trade_date: date,
    ) -> None:
        if quantity <= 0 or fill_price <= 0 or fees < 0:
            raise ValueError("buy values are invalid")
        total_cost = (fill_price * quantity + fees).quantize(COST_QUANTUM)
        cash_required = total_cost.quantize(MONEY_QUANTUM, ROUND_HALF_UP)
        if cash_required > self.cash_balance:
            raise ValueError("insufficient cash")
        self.cash_balance = (self.cash_balance - cash_required).quantize(MONEY_QUANTUM)
        position = self.position(symbol)
        position.lots.append(
            PositionLot(
                acquired_on=trade_date,
                remaining_quantity=quantity,
                total_cost=total_cost,
            )
        )
        position.last_price = fill_price

    def sellable_quantity(self, symbol: str, trade_date: date) -> int:
        position = self._positions.get(symbol)
        if position is None:
            return 0
        return sum(
            item.remaining_quantity
            for item in position.lots
            if item.acquired_on < trade_date
        )

    def sell_fifo(
        self,
        symbol: str,
        quantity: int,
        fill_price: Decimal,
        fees: Decimal,
        trade_date: date,
    ) -> SaleResult:
        if quantity <= 0 or fill_price <= 0 or fees < 0:
            raise ValueError("sell values are invalid")
        if quantity > self.sellable_quantity(symbol, trade_date):
            raise ValueError("quantity exceeds T+1 sellable quantity")

        position = self.position(symbol)
        remaining = quantity
        cost_basis = Decimal(0)
        for item in sorted(position.lots, key=lambda lot: lot.acquired_on):
            if item.acquired_on >= trade_date or remaining == 0:
                continue
            consumed = min(remaining, item.remaining_quantity)
            cost_basis += item.consume(consumed)
            remaining -= consumed

        gross = (fill_price * quantity).quantize(MONEY_QUANTUM, ROUND_HALF_UP)
        normalized_fees = fees.quantize(MONEY_QUANTUM, ROUND_HALF_UP)
        proceeds = gross - normalized_fees
        realized = (proceeds - cost_basis).quantize(MONEY_QUANTUM, ROUND_HALF_UP)
        self.cash_balance = (self.cash_balance + proceeds).quantize(MONEY_QUANTUM)
        self.realized_pnl = (self.realized_pnl + realized).quantize(MONEY_QUANTUM)
        position.last_price = fill_price
        return SaleResult(
            quantity=quantity,
            gross_value=gross,
            cost_basis=cost_basis.quantize(COST_QUANTUM),
            fees=normalized_fees,
            realized_pnl=realized,
        )

    def mark_to_market(
        self,
        prices: dict[str, Decimal],
        valuation_date: date,
    ) -> NavState:
        for symbol, position in self._positions.items():
            if position.quantity > 0:
                price = prices.get(symbol)
                if price is None or price <= 0:
                    raise ValueError(f"missing valuation price for {symbol}")
                position.last_price = price

        market_value = sum(
            (item.market_value for item in self._positions.values()),
            Decimal(0),
        ).quantize(MONEY_QUANTUM)
        unrealized = sum(
            (item.unrealized_pnl for item in self._positions.values()),
            Decimal(0),
        ).quantize(MONEY_QUANTUM)
        total_equity = (self.cash_balance + market_value).quantize(MONEY_QUANTUM)
        previous_equity = self._nav[-1].total_equity if self._nav else self.initial_cash
        daily_return = (total_equity / previous_equity - 1).quantize(RATIO_QUANTUM)
        cumulative_return = (total_equity / self.initial_cash - 1).quantize(RATIO_QUANTUM)
        self._peak_equity = max(self._peak_equity, total_equity)
        drawdown = ((self._peak_equity - total_equity) / self._peak_equity).quantize(RATIO_QUANTUM)
        self._max_drawdown = max(self._max_drawdown, drawdown)
        nav = NavState(
            valuation_date=valuation_date,
            cash_balance=self.cash_balance,
            market_value=market_value,
            total_equity=total_equity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized,
            unit_nav=(total_equity / self.initial_cash).quantize(RATIO_QUANTUM),
            daily_return=daily_return,
            cumulative_return=cumulative_return,
            max_drawdown=self._max_drawdown,
        )
        self._nav.append(nav)
        self.assert_asset_identity()
        return nav

    def assert_asset_identity(
        self,
        *,
        reported_total_equity: Decimal | None = None,
        tolerance: Decimal = Decimal("0.01"),
    ) -> None:
        market_value = sum(
            (item.market_value for item in self._positions.values()),
            Decimal(0),
        ).quantize(MONEY_QUANTUM)
        calculated = (self.cash_balance + market_value).quantize(MONEY_QUANTUM)
        reported = reported_total_equity
        if reported is None:
            reported = self._nav[-1].total_equity if self._nav else calculated
        if abs(calculated - reported) > tolerance:
            raise AssetIdentityError("cash + market value does not equal total equity")

    def snapshot(self, snapshot_type: str, as_of: datetime) -> PortfolioSnapshot:
        market_value = sum(
            (item.market_value for item in self._positions.values()),
            Decimal(0),
        ).quantize(MONEY_QUANTUM)
        unrealized = sum(
            (item.unrealized_pnl for item in self._positions.values()),
            Decimal(0),
        ).quantize(MONEY_QUANTUM)
        total_equity = (self.cash_balance + market_value).quantize(MONEY_QUANTUM)
        payload = {
            "portfolio_id": self.portfolio_id,
            "snapshot_type": snapshot_type,
            "as_of": as_of.isoformat(),
            "cash_balance": str(self.cash_balance),
            "frozen_cash": str(self.frozen_cash),
            "market_value": str(market_value),
            "total_equity": str(total_equity),
            "realized_pnl": str(self.realized_pnl),
            "unrealized_pnl": str(unrealized),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return PortfolioSnapshot(
            portfolio_id=self.portfolio_id,
            snapshot_type=snapshot_type,
            as_of=as_of,
            cash_balance=self.cash_balance,
            frozen_cash=self.frozen_cash,
            market_value=market_value,
            total_equity=total_equity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized,
            content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
