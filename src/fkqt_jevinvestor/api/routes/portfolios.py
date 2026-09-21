import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.market import MarketExecutionProvider, MarketExecutionSnapshot
from fkqt_jevinvestor.domain.portfolio import PortfolioState, PositionState
from fkqt_jevinvestor.domain.signals import FixtureSignal, FixtureSignalBatch
from fkqt_jevinvestor.persistence.repositories import (
    PortfolioAlreadyExists,
    PortfolioNotFound,
    PortfolioRepository,
    PortfolioTransactionError,
    PortfolioVersionConflict,
    StoredSignalBatch,
)
from fkqt_jevinvestor.services.execution_engine import ExecutionBatchResult
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio, ExecuteTradeDate, NavState
from fkqt_jevinvestor.services.signal_validator import SignalValidationError, validate_signal_batch


class CreatePortfolioRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    portfolio_id: str | None = Field(default=None, min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=128)
    initial_cash: Decimal = Field(default=Decimal(1000000), gt=0, le=Decimal("9999999999.9999"))


class FixtureSignalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_date: date
    planned_execution_date: date
    fixture_version: str = Field(min_length=1, max_length=40)
    candidate_symbols: tuple[str, ...]
    signals: tuple[FixtureSignal, ...]
    cash_target_pct: Decimal = Field(ge=0, le=1)


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    expected_version: int = Field(ge=1)
    market_snapshots: tuple[MarketExecutionSnapshot, ...] = ()


class PortfolioService:
    def __init__(
        self,
        repository: PortfolioRepository,
        market_execution_provider: MarketExecutionProvider | None = None,
    ) -> None:
        self.repository = repository
        self.market_execution_provider = market_execution_provider

    async def submit_fixture(
        self,
        portfolio_id: str,
        request: FixtureSignalRequest,
    ) -> StoredSignalBatch:
        batch = FixtureSignalBatch(
            portfolio_id=portfolio_id,
            decision_date=request.decision_date,
            planned_execution_date=request.planned_execution_date,
            fixture_version=request.fixture_version,
            candidate_symbols=request.candidate_symbols,
            signals=request.signals,
            cash_target_pct=request.cash_target_pct,
        )
        existing = await self.repository.find_signal_batch(batch)
        if existing is not None:
            return existing
        state = await self.repository.get_state(portfolio_id, request.decision_date)
        validated = validate_signal_batch(batch, state, set(request.candidate_symbols))
        return await self.repository.save_signal_batch(validated)

    async def execute(
        self,
        portfolio_id: str,
        trade_date: date,
        request: ExecuteRequest,
    ) -> ExecutionBatchResult:
        snapshots = {item.symbol: item for item in request.market_snapshots}
        if any(key != item.symbol or item.trade_date != trade_date for key, item in snapshots.items()):
            raise ValueError("MARKET_SNAPSHOT_MISMATCH")
        if not snapshots and self.market_execution_provider is not None:
            pending = await self.repository.load_pending_orders(portfolio_id, trade_date)
            symbols = tuple(sorted({item.symbol for item in pending}))
            snapshots = dict(
                await self.market_execution_provider.get_execution_snapshots(symbols, trade_date)
            )
        return await self.repository.execute_trade_date(
            ExecuteTradeDate(
                portfolio_id=portfolio_id,
                trade_date=trade_date,
                expected_version=request.expected_version,
                market_snapshots=snapshots,
            )
        )


def build_portfolio_router(
    service: PortfolioService,
    *,
    include_fixture_routes: bool,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/portfolios", tags=["portfolios"])

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def create_portfolio(request: CreatePortfolioRequest) -> Response:
        portfolio_id = request.portfolio_id or f"portfolio-{uuid.uuid4().hex[:24]}"
        try:
            state = await service.repository.create(
                CreatePortfolio(
                    portfolio_id=portfolio_id,
                    name=request.name,
                    initial_cash=request.initial_cash,
                )
            )
        except PortfolioAlreadyExists:
            return _error(status.HTTP_409_CONFLICT, "PORTFOLIO_ALREADY_EXISTS")
        return JSONResponse(_portfolio_payload(state), status_code=status.HTTP_201_CREATED)

    @router.get("/{portfolio_id}")
    async def get_portfolio(portfolio_id: str) -> Response:
        try:
            state = await service.repository.get_state(portfolio_id)
        except PortfolioNotFound:
            return _error(status.HTTP_404_NOT_FOUND, "PORTFOLIO_NOT_FOUND")
        return JSONResponse(_portfolio_payload(state))

    @router.get("/{portfolio_id}/nav")
    async def get_nav(portfolio_id: str) -> Response:
        try:
            await service.repository.get_state(portfolio_id)
            nav = await service.repository.list_nav(portfolio_id)
        except PortfolioNotFound:
            return _error(status.HTTP_404_NOT_FOUND, "PORTFOLIO_NOT_FOUND")
        return JSONResponse([_nav_payload(item) for item in nav])

    if include_fixture_routes:

        @router.post("/{portfolio_id}/fixture-signals")
        async def submit_fixture(portfolio_id: str, request: FixtureSignalRequest) -> Response:
            try:
                stored = await service.submit_fixture(portfolio_id, request)
            except PortfolioNotFound:
                return _error(status.HTTP_404_NOT_FOUND, "PORTFOLIO_NOT_FOUND")
            except SignalValidationError as exc:
                return JSONResponse(
                    {"error_code": "SIGNAL_VALIDATION_FAILED", "codes": list(exc.codes)},
                    status_code=status.HTTP_409_CONFLICT,
                )
            return JSONResponse(_stored_batch_payload(stored))

        @router.post("/{portfolio_id}/executions/{trade_date}")
        async def execute(portfolio_id: str, trade_date: date, request: ExecuteRequest) -> Response:
            try:
                result = await service.execute(portfolio_id, trade_date, request)
            except PortfolioNotFound:
                return _error(status.HTTP_404_NOT_FOUND, "PORTFOLIO_NOT_FOUND")
            except PortfolioVersionConflict:
                return _error(status.HTTP_409_CONFLICT, "PORTFOLIO_VERSION_CONFLICT")
            except ValueError as exc:
                return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
            except PortfolioTransactionError:
                return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "PORTFOLIO_TRANSACTION_FAILED")
            return JSONResponse(result.model_dump(mode="json"))

    return router


def _portfolio_payload(state: PortfolioState) -> dict[str, object]:
    return {
        "portfolio_id": state.portfolio_id,
        "cash_balance": _money(state.cash_balance),
        "frozen_cash": _money(state.frozen_cash),
        "realized_pnl": _money(state.realized_pnl),
        "positions": [_position_payload(item) for item in state.positions],
        "version": state.version,
    }


def _stored_batch_payload(stored: StoredSignalBatch) -> dict[str, object]:
    return {
        "batch_id": stored.batch_id,
        "input_hash": stored.input_hash,
        "orders": [
            {
                "order_id": order.order_id,
                "idempotency_key": order.idempotency_key,
                "signal_id": order.signal_id,
                "symbol": order.symbol,
                "side": order.side.value,
                "action": order.action.value,
                "target_position_pct": str(
                    order.target_position_pct.quantize(Decimal("0.00000001"))
                ),
                "planned_execution_date": order.planned_execution_date.isoformat(),
                "policy": order.policy.model_dump(mode="json"),
                "status": order.status.value,
                "code": order.code,
                "intended_quantity": order.intended_quantity,
                "filled_quantity": order.filled_quantity,
                "remaining_quantity": order.remaining_quantity,
            }
            for order in stored.orders
        ],
    }


def _position_payload(position: PositionState) -> dict[str, object]:
    return {
        "symbol": position.symbol,
        "quantity": position.quantity,
        "sellable_quantity": position.sellable_quantity,
        "average_cost": str(position.average_cost),
        "total_cost": str(position.total_cost),
        "last_price": _money(position.last_price),
        "current_position_pct": str(position.current_position_pct),
        "unrealized_pnl": _money(position.unrealized_pnl),
        "holding_trading_days": position.holding_trading_days,
    }


def _nav_payload(nav: NavState) -> dict[str, object]:
    return {
        "valuation_date": nav.valuation_date.isoformat(),
        "cash_balance": _money(nav.cash_balance),
        "market_value": _money(nav.market_value),
        "total_equity": _money(nav.total_equity),
        "realized_pnl": _money(nav.realized_pnl),
        "unrealized_pnl": _money(nav.unrealized_pnl),
        "unit_nav": str(nav.unit_nav),
        "daily_return": str(nav.daily_return),
        "cumulative_return": str(nav.cumulative_return),
        "max_drawdown": str(nav.max_drawdown),
    }


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _error(status_code: int, error_code: str) -> JSONResponse:
    return JSONResponse({"error_code": error_code}, status_code=status_code)
