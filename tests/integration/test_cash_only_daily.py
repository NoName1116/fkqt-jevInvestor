import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from fkqt_jevinvestor.domain.forward import TradingDayProofV1
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository, PortfolioTransactionError
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio, ExecuteCashOnlyDate

DAY = date(2026, 9, 24)


def cash_command(day: date = DAY, *, previous: date = date(2026, 9, 23), version: int = 1, input_hash: str = "c" * 64) -> ExecuteCashOnlyDate:
    return ExecuteCashOnlyDate(
        portfolio_id="cash", trade_date=day, expected_version=version, start_date=DAY,
        calendar_proof=TradingDayProofV1.create(
            day, is_open=True, exchange="SSE", previous_trade_date=previous,
            source_manifest_id="a" * 64, source_content_hash="b" * 64,
        ), execution_input_hash=input_hash,
    )


@pytest.mark.asyncio
async def test_cash_only_advances_nav_and_replays_without_version_change(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'cash.db').as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_engine(url)
    try:
        repo = PortfolioRepository(create_session_factory(engine))
        await repo.create(CreatePortfolio(portfolio_id="cash", name="Cash"))
        result = await repo.execute_cash_only_date(cash_command())
        assert len(result.fills) == 0
        assert result.nav.market_value == Decimal("0.00")
        assert result.nav.total_equity == Decimal("1000000.00")
        assert result.nav.unit_nav == Decimal("1.00000000")
        assert (await repo.get_state("cash")).version == 2
        replay = await repo.execute_cash_only_date(cash_command(version=2))
        assert replay.fills == ()
        assert (await repo.get_state("cash")).version == 2
        assert len(await repo.list_nav("cash")) == 1
        with pytest.raises(PortfolioTransactionError, match="EXECUTION_INPUT_CONFLICT"):
            await repo.execute_cash_only_date(cash_command(version=2, input_hash="d" * 64))
        await repo.execute_cash_only_date(cash_command(date(2026, 9, 25), previous=DAY, version=2))
        assert len(await repo.list_nav("cash")) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cash_only_rejects_before_start_and_missing_previous_nav(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'guard.db').as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_engine(url)
    try:
        repo = PortfolioRepository(create_session_factory(engine))
        await repo.create(CreatePortfolio(portfolio_id="cash", name="Cash"))
        with pytest.raises(ValueError, match="CASH_ONLY_BEFORE_START"):
            await repo.execute_cash_only_date(cash_command(date(2026, 9, 23), previous=date(2026, 9, 22)))
        with pytest.raises(ValueError, match="DAILY_EXECUTION_HISTORY_GAP"):
            await repo.execute_cash_only_date(cash_command(date(2026, 9, 25), previous=DAY))
        assert (await repo.get_state("cash")).version == 1
        assert await repo.list_nav("cash") == ()
    finally:
        await engine.dispose()
