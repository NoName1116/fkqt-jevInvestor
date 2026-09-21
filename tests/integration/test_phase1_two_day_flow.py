import asyncio
from collections.abc import Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.api.app import create_app
from fkqt_jevinvestor.config import Settings
from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot
from fkqt_jevinvestor.persistence.models import (
    PortfolioRecord,
    PositionLotRecord,
    PositionRecord,
    VirtualFillRecord,
)
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory

SessionFactory = async_sessionmaker[AsyncSession]


class CountingMarketProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def get_execution_snapshots(
        self,
        symbols: tuple[str, ...],
        trade_date: date,
    ) -> Mapping[str, MarketExecutionSnapshot]:
        self.calls += 1
        raise AssertionError(f"unexpected external market call: {symbols} {trade_date}")


def test_reduce_then_open_across_two_trading_days(tmp_path: Path) -> None:
    run_two_day_fixture(tmp_path)


def run_two_day_fixture(tmp_path: Path) -> dict[str, str | int]:
    database = tmp_path / "phase1-flow.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    session_factory = create_session_factory(engine)
    provider = CountingMarketProvider()
    app = create_app(
        Settings(environment="test"),
        session_factory=session_factory,
        market_execution_provider=provider,
    )

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/portfolios",
            json={
                "portfolio_id": "phase1-demo",
                "name": "Phase 1 Demo",
                "initial_cash": "1000000.00",
            },
        )
        assert created.status_code == 201
        asyncio.run(seed_historical_position(session_factory))

        submitted = client.post(
            "/api/v1/portfolios/phase1-demo/fixture-signals",
            json={
                "decision_date": "2026-09-21",
                "planned_execution_date": "2026-09-22",
                "fixture_version": "two-day-v1",
                "candidate_symbols": ["600000.SH", "000001.SZ"],
                "cash_target_pct": "0.796",
                "signals": [
                    {
                        "symbol": "600000.SH",
                        "action": "REDUCE",
                        "target_position_pct": "0.004",
                        "confidence": "0.80",
                        "thesis": "Reduce historical holding",
                        "invalidation": "Fixture only",
                        "factor_codes": [],
                        "evidence_ids": [],
                    },
                    {
                        "symbol": "000001.SZ",
                        "action": "OPEN",
                        "target_position_pct": "0.20",
                        "confidence": "0.80",
                        "thesis": "Open replacement holding",
                        "invalidation": "Fixture only",
                        "factor_codes": [],
                        "evidence_ids": [],
                    },
                ],
            },
        )
        assert submitted.status_code == 200

        execution_request = {
            "expected_version": 1,
            "market_snapshots": [
                market_payload("600000.SH"),
                market_payload("000001.SZ"),
            ],
        }
        executed = client.post(
            "/api/v1/portfolios/phase1-demo/executions/2026-09-22",
            json=execution_request,
        )
        assert executed.status_code == 200
        result = executed.json()
        assert [fill["side"] for fill in result["fills"]] == ["SELL", "BUY"]
        assert [fill["quantity"] for fill in result["fills"]] == [400, 20000]
        assert result["fills"][0]["commission"] == "1.20"
        assert result["fills"][0]["stamp_tax"] == "2.00"
        assert result["fills"][1]["commission"] == "60.03"

        portfolio = client.get(
            "/api/v1/portfolios/phase1-demo"
        ).json()
        positions = {item["symbol"]: item for item in portfolio["positions"]}
        assert portfolio["cash_balance"] == "795834.77"
        assert positions["600000.SH"]["quantity"] == 400
        assert positions["000001.SZ"]["quantity"] == 20000
        assert all(item["quantity"] % 100 == 0 for item in positions.values())

        snapshot = result["snapshot"]
        identity = Decimal(snapshot["cash_balance"]) + Decimal(snapshot["market_value"])
        assert abs(identity - Decimal(snapshot["total_equity"])) <= Decimal("0.01")
        assert snapshot["total_equity"] == "999834.77"
        assert result["nav"]["total_equity"] == "999834.77"

        repeated = client.post(
            "/api/v1/portfolios/phase1-demo/executions/2026-09-22",
            json={**execution_request, "expected_version": 2},
        )
        assert repeated.status_code == 200
        assert repeated.json()["fills"] == []
        fill_count = asyncio.run(count_fills(session_factory))
        assert fill_count == 2
        assert provider.calls == 0

    asyncio.run(engine.dispose())
    return {
        "cash_balance": f'{Decimal(portfolio["cash_balance"]):.4f}',
        "total_equity": f'{Decimal(snapshot["total_equity"]):.4f}',
        "sell_commission": f'{Decimal(result["fills"][0]["commission"]):.4f}',
        "stamp_tax": f'{Decimal(result["fills"][0]["stamp_tax"]):.4f}',
        "buy_commission": f'{Decimal(result["fills"][1]["commission"]):.4f}',
        "600000.SH.quantity": positions["600000.SH"]["quantity"],
        "000001.SZ.quantity": positions["000001.SZ"]["quantity"],
        "duplicate_fill_count": fill_count - 2,
    }


def market_payload(symbol: str) -> dict[str, object]:
    return {
        "symbol": symbol,
        "trade_date": "2026-09-22",
        "trading_day_status": "OPEN",
        "trading_status": "TRADING",
        "open_price": "10.00",
        "unadjusted_close": "10.00",
        "daily_amount_cny": "100000000.00",
        "upper_limit_price": "11.00",
        "lower_limit_price": "9.00",
        "is_initial_no_limit_period": False,
    }


async def seed_historical_position(session_factory: SessionFactory) -> None:
    async with session_factory.begin() as session:
        await session.execute(
            update(PortfolioRecord)
            .where(PortfolioRecord.id == "phase1-demo")
            .values(cash_balance=Decimal("992000.00"))
        )
        now = datetime.now(UTC)
        session.add(
            PositionRecord(
                id="position-seed-600000",
                portfolio_id="phase1-demo",
                symbol="600000.SH",
                quantity=800,
                average_cost=Decimal("10.00000000"),
                total_cost=Decimal("8000.00000000"),
                last_price=Decimal("10.0000"),
                target_position_pct=Decimal("0.00800000"),
                updated_at=now,
            )
        )
        session.add(
            PositionLotRecord(
                id="lot-seed-600000",
                portfolio_id="phase1-demo",
                position_id="position-seed-600000",
                symbol="600000.SH",
                acquired_on=date(2026, 9, 18),
                remaining_quantity=800,
                unit_cost=Decimal("10.00000000"),
                total_cost=Decimal("8000.00000000"),
            )
        )


async def count_fills(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return int(
            (await session.scalar(select(func.count()).select_from(VirtualFillRecord))) or 0
        )
