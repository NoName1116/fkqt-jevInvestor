import asyncio

import pytest
from alembic import command
from alembic.config import Config

from fkqt_jevinvestor.persistence.models import PortfolioRecord
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio
from tests.integration import test_portfolio_repository as fixtures

session_factory = fixtures.session_factory


@pytest.mark.asyncio
async def test_nonzero_dividend_refuses_lossy_downgrade(session_factory: fixtures.SessionFactory) -> None:
    await PortfolioRepository(session_factory).create(CreatePortfolio(portfolio_id="p", name="Migration guard"))
    async with session_factory.begin() as session:
        record = await session.get(PortfolioRecord, "p")
        assert record is not None
        record.dividend_receivable = record.initial_cash / 100
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", str(session_factory.kw["bind"].url))
    with pytest.raises(RuntimeError, match="CORPORATE_ACTION_DOWNGRADE_DATA_LOSS"):
        await asyncio.to_thread(command.downgrade, config, "0007_phase4_llm_position_sizing")
    async with session_factory() as session:
        record = await session.get(PortfolioRecord, "p")
        assert record is not None
        assert record.dividend_receivable == 10000
