import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from fkqt_jevinvestor.persistence.corporate_action_repository import CorporateActionRepository
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio, ExecuteTradeDate
from fkqt_jevinvestor.persistence.models import PortfolioRecord
from tests.integration.test_cash_only_daily import cash_command
from tests.unit.test_corporate_action_ledger import EX, PAY, event, ledger


@pytest.mark.asyncio
async def test_entitlements_survive_sellout_and_stage_replay(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'rights.db').as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_engine(url)
    factory = create_session_factory(engine)
    try:
        await PortfolioRepository(factory).create(CreatePortfolio(portfolio_id="p", name="Rights"))
        repository = CorporateActionRepository()
        account = ledger()
        async with factory.begin() as session:
            await repository.freeze_record_date(session, "p", event(), account)
        with pytest.raises(ValueError, match="CASH_ONLY_PORTFOLIO_NOT_EMPTY"):
            await PortfolioRepository(factory).execute_cash_only_date(
                cash_command().model_copy(update={"portfolio_id": "p"})
            )
        account.sell_fifo("600000.SH", 1000, Decimal("9.8"), Decimal(0), EX)
        async with factory.begin() as session:
            assert await repository.required_symbols(session, "p") == {"600000.SH"}
            assert await repository.has_outstanding_entitlements(session, "p")
            assert await repository.apply_due_stages(session, "p", EX, account) == ("EX",)
        assert account.dividend_receivable == Decimal(200)
        async with factory.begin() as session:
            assert await repository.apply_due_stages(session, "p", EX, account) == ()
            assert await repository.apply_due_stages(session, "p", PAY, account) == ("PAY",)
        assert account.cash_balance == Decimal(100000)
        assert account.dividend_income == Decimal(200)
        async with factory.begin() as session:
            assert not await repository.has_outstanding_entitlements(session, "p")
            assert await repository.required_symbols(session, "p") == set()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_receivables_persist_through_execution_and_restart(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'restore.db').as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_engine(url)
    factory = create_session_factory(engine)
    try:
        repo = PortfolioRepository(factory)
        await repo.create(CreatePortfolio(portfolio_id="p", name="Rights"))
        async with factory.begin() as session:
            record = await session.get(PortfolioRecord, "p")
            assert record is not None
            record.dividend_receivable = Decimal(200)
            record.dividend_income = Decimal(200)
        result = await repo.execute_trade_date(ExecuteTradeDate(
            portfolio_id="p", trade_date=EX, expected_version=1, market_snapshots={},
        ))
        assert result.nav.total_equity == Decimal(1000200)
        state = await PortfolioRepository(factory).get_state("p")
        assert state.dividend_receivable == Decimal(200)
        nav = (await repo.list_nav("p"))[0]
        assert nav.dividend_income == Decimal(200)
        assert nav.dividend_receivable == Decimal(200)
    finally:
        await engine.dispose()
