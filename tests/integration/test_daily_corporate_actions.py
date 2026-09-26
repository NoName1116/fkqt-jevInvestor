from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from fkqt_jevinvestor.domain.corporate_actions import CorporateActionManifestV1
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.persistence.repositories import PortfolioTransactionError
from fkqt_jevinvestor.persistence.corporate_action_repository import CorporateActionRepository
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio, ExecuteTradeDate
from tests.integration import test_portfolio_repository as fixtures
from tests.unit.test_corporate_action_ledger import event
from fkqt_jevinvestor.services.portfolio_service import PortfolioLedger

session_factory = fixtures.session_factory


def actions(day: date) -> CorporateActionManifestV1:
    evt = event(bonus="0.1").model_copy(update={
        "symbol": "000001.SZ", "record_date": date(2026, 9, 22),
        "ex_date": date(2026, 9, 23), "pay_date": date(2026, 9, 24),
        "share_list_date": date(2026, 9, 25), "available_at": datetime(2026, 9, 1, tzinfo=UTC),
    })
    return CorporateActionManifestV1.create(
        trade_date=day, requested_symbols=("000001.SZ",), coverage_complete=True,
        events=(evt,), source_manifest_id="a" * 64, source_content_hash="b" * 64,
    )


@pytest.mark.asyncio
async def test_record_after_execution_pay_and_list_before_execution(session_factory: fixtures.SessionFactory) -> None:
    repo = PortfolioRepository(session_factory)
    await repo.create(CreatePortfolio(portfolio_id="portfolio-1", name="Rights"))
    await fixtures.accepted_batch(repo)
    record_day = date(2026, 9, 22)
    first = await repo.execute_trade_date(ExecuteTradeDate(
        portfolio_id="portfolio-1", trade_date=record_day, expected_version=1,
        market_snapshots={"000001.SZ": fixtures.execution_market(record_day)},
        corporate_actions=actions(record_day),
    ))
    qty = first.fills[0].quantity
    assert qty % 10 == 0
    for index, day in enumerate((date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25)), start=2):
        result = await repo.execute_trade_date(ExecuteTradeDate(
            portfolio_id="portfolio-1", trade_date=day, expected_version=index,
            market_snapshots={"000001.SZ": fixtures.execution_market(day)},
            corporate_actions=actions(day),
        ))
        state = await repo.get_state("portfolio-1", as_of=day)
        assert state.positions[0].quantity == qty * 11 // 10
        assert state.positions[0].sellable_quantity == (qty if day.day < 25 else qty * 11 // 10)
        assert result.nav.dividend_income == Decimal(qty) * Decimal("0.2")
        assert result.nav.dividend_receivable == (Decimal(qty) * Decimal("0.2") if day.day == 23 else 0)


@pytest.mark.asyncio
async def test_missing_record_history_rolls_back_entitlements_nav_and_version(session_factory: fixtures.SessionFactory) -> None:
    repo = PortfolioRepository(session_factory)
    await repo.create(CreatePortfolio(portfolio_id="portfolio-1", name="Rights"))
    ex_day = date(2026, 9, 23)
    with pytest.raises(ValueError, match="CORPORATE_ACTION_RECORD_HISTORY_MISSING"):
        await repo.execute_trade_date(ExecuteTradeDate(
            portfolio_id="portfolio-1", trade_date=ex_day, expected_version=1,
            market_snapshots={"000001.SZ": fixtures.execution_market(ex_day)},
            corporate_actions=actions(ex_day),
        ))
    assert (await repo.get_state("portfolio-1")).version == 1
    assert await repo.list_nav("portfolio-1") == ()


@pytest.mark.asyncio
async def test_replay_rejects_changed_corporate_action_facts(session_factory: fixtures.SessionFactory) -> None:
    repo = PortfolioRepository(session_factory)
    await repo.create(CreatePortfolio(portfolio_id="portfolio-1", name="Rights"))
    await fixtures.accepted_batch(repo)
    day = date(2026, 9, 22)
    cmd = ExecuteTradeDate(portfolio_id="portfolio-1", trade_date=day, expected_version=1,
                           market_snapshots={"000001.SZ": fixtures.execution_market(day)}, corporate_actions=actions(day))
    await repo.execute_trade_date(cmd)
    changed = actions(day).events[0].model_copy(update={"cash_per_share_gross": Decimal("0.3")})
    altered = CorporateActionManifestV1.create(**actions(day).model_dump(exclude={"content_hash", "events"}), events=(changed,))
    with pytest.raises(PortfolioTransactionError, match="EXECUTION_INPUT_CONFLICT"):
        await repo.execute_trade_date(cmd.model_copy(update={"expected_version": 2, "corporate_actions": altered}))
    assert (await repo.get_state("portfolio-1")).version == 2


@pytest.mark.asyncio
async def test_ex_date_expires_old_orders_before_open(session_factory: fixtures.SessionFactory) -> None:
    repo = PortfolioRepository(session_factory)
    await repo.create(CreatePortfolio(portfolio_id="portfolio-1", name="Rights"))
    await fixtures.accepted_batch(repo)
    day = date(2026, 9, 22)
    evt = actions(day).events[0].model_copy(update={"record_date": date(2026, 9, 21), "ex_date": day})
    async with session_factory.begin() as session:
        await CorporateActionRepository().freeze_record_date(session, "portfolio-1", evt,
                                                            PortfolioLedger("portfolio-1", Decimal(1000000), Decimal(1000000)))
    manifest = CorporateActionManifestV1.create(**actions(day).model_dump(exclude={"content_hash", "events"}), events=(evt,))
    result = await repo.execute_trade_date(ExecuteTradeDate(
        portfolio_id="portfolio-1", trade_date=day, expected_version=1,
        market_snapshots={"000001.SZ": fixtures.execution_market(day)}, corporate_actions=manifest,
    ))
    assert result.fills == ()
    assert result.orders[0].status.value == "EXPIRED"
    assert result.orders[0].code == "CORPORATE_ACTION_ORDER_EXPIRED"
