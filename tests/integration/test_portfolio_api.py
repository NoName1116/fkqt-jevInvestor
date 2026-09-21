import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.api.app import create_app
from fkqt_jevinvestor.config import Settings
from fkqt_jevinvestor.persistence.models import VirtualOrderRecord
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture
def api_client(tmp_path: Path) -> Iterator[tuple[TestClient, SessionFactory]]:
    database = tmp_path / "api.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    session_factory = create_session_factory(engine)
    with TestClient(
        create_app(Settings(environment="test"), session_factory=session_factory)
    ) as client:
        yield client, session_factory
    asyncio.run(engine.dispose())


def create_portfolio(client: TestClient, portfolio_id: str = "portfolio-1") -> dict[str, object]:
    response = client.post(
        "/api/v1/portfolios",
        json={"portfolio_id": portfolio_id, "name": "Demo"},
    )
    assert response.status_code == 201
    return response.json()


def fixture_payload(*, cash_target_pct: str = "0.50") -> dict[str, object]:
    return {
        "decision_date": "2026-09-21",
        "planned_execution_date": "2026-09-22",
        "fixture_version": "fixture-v1",
        "candidate_symbols": ["000001.SZ"],
        "cash_target_pct": cash_target_pct,
        "signals": [
            {
                "symbol": "000001.SZ",
                "action": "OPEN",
                "target_position_pct": "0.50",
                "confidence": "0.80",
                "thesis": "Phase 1 fixture",
                "invalidation": "Fixture invalidation",
                "factor_codes": [],
                "evidence_ids": [],
            }
        ],
    }


def execution_payload(*, expected_version: int = 1) -> dict[str, object]:
    return {
        "expected_version": expected_version,
        "market_snapshots": [
            {
                "symbol": "000001.SZ",
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
        ],
    }


async def count_orders(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return int(
            (await session.scalar(select(func.count()).select_from(VirtualOrderRecord))) or 0
        )


def test_create_and_read_portfolio_round_trips_decimals_as_strings(
    api_client: tuple[TestClient, SessionFactory],
) -> None:
    client, _ = api_client

    created = create_portfolio(client)
    read = client.get("/api/v1/portfolios/portfolio-1")

    assert created["cash_balance"] == "1000000.00"
    assert read.status_code == 200
    assert read.json() == created


def test_same_fixture_returns_same_batch_and_orders(
    api_client: tuple[TestClient, SessionFactory],
) -> None:
    client, _ = api_client
    create_portfolio(client)
    path = "/api/v1/portfolios/portfolio-1/fixture-signals"

    first = client.post(path, json=fixture_payload())
    second = client.post(path, json=fixture_payload())

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()


def test_invalid_fixture_returns_409_and_writes_zero_orders(
    api_client: tuple[TestClient, SessionFactory],
) -> None:
    client, session_factory = api_client
    create_portfolio(client)

    response = client.post(
        "/api/v1/portfolios/portfolio-1/fixture-signals",
        json=fixture_payload(cash_target_pct="0.30"),
    )

    assert response.status_code == 409
    assert response.json() == {
        "error_code": "SIGNAL_VALIDATION_FAILED",
        "codes": ["TARGET_WEIGHT_SUM_INVALID"],
    }
    assert asyncio.run(count_orders(session_factory)) == 0


def test_execute_next_day_returns_orders_fills_snapshot_and_nav(
    api_client: tuple[TestClient, SessionFactory],
) -> None:
    client, _ = api_client
    create_portfolio(client)
    client.post(
        "/api/v1/portfolios/portfolio-1/fixture-signals",
        json=fixture_payload(),
    )

    response = client.post(
        "/api/v1/portfolios/portfolio-1/executions/2026-09-22",
        json=execution_payload(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["orders"][0]["status"] == "FILLED"
    assert payload["fills"][0]["symbol"] == "000001.SZ"
    assert payload["snapshot"]["snapshot_type"] == "POST_EXECUTION"
    assert payload["nav"]["valuation_date"] == "2026-09-22"
    nav_response = client.get("/api/v1/portfolios/portfolio-1/nav")
    assert nav_response.status_code == 200
    assert nav_response.json() == [payload["nav"]]

    repeated_fixture = client.post(
        "/api/v1/portfolios/portfolio-1/fixture-signals",
        json=fixture_payload(),
    )
    assert repeated_fixture.status_code == 200


def test_execution_rejects_snapshot_from_another_date(
    api_client: tuple[TestClient, SessionFactory],
) -> None:
    client, _ = api_client
    create_portfolio(client)
    payload = execution_payload()
    payload["market_snapshots"][0]["trade_date"] = "2026-09-29"  # type: ignore[index]
    response = client.post(
        "/api/v1/portfolios/portfolio-1/executions/2026-09-22",
        json=payload,
    )
    assert response.status_code == 422
    assert response.json() == {"error_code": "MARKET_SNAPSHOT_MISMATCH"}


def test_unknown_portfolio_returns_404(api_client: tuple[TestClient, SessionFactory]) -> None:
    client, _ = api_client

    response = client.get("/api/v1/portfolios/missing")

    assert response.status_code == 404
    assert response.json() == {"error_code": "PORTFOLIO_NOT_FOUND"}


def test_fixture_routes_are_hidden_in_production() -> None:
    client = TestClient(create_app(Settings(environment="production")))

    response = client.post(
        "/api/v1/portfolios/p-1/fixture-signals",
        json=fixture_payload(),
    )

    assert response.status_code == 404
    execution = client.post(
        "/api/v1/portfolios/p-1/executions/2026-09-22",
        json=execution_payload(),
    )
    assert execution.status_code == 404


def test_database_error_response_does_not_leak_url_sql_or_traceback(tmp_path: Path) -> None:
    database = tmp_path / "secret-database.db"
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    session_factory = create_session_factory(engine)
    with TestClient(
        create_app(Settings(environment="test"), session_factory=session_factory),
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            "/api/v1/portfolios",
            json={"portfolio_id": "portfolio-1", "name": "Demo"},
        )
    asyncio.run(engine.dispose())

    assert response.status_code == 500
    assert response.json() == {"error_code": "INTERNAL_ERROR"}
    body = response.text.lower()
    assert "secret-database" not in body
    assert "insert into" not in body
    assert "traceback" not in body
