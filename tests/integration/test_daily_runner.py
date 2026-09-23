import asyncio
import json
from argparse import Namespace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import update

from fkqt_jevinvestor.cli.main import (
    _daily_close,  # pyright: ignore[reportPrivateUsage]
    _daily_execute,  # pyright: ignore[reportPrivateUsage]
    _daily_required_symbols,  # pyright: ignore[reportPrivateUsage]
    _daily_status,  # pyright: ignore[reportPrivateUsage]
)
from fkqt_jevinvestor.config import Settings
from fkqt_jevinvestor.domain.decision import DecisionAction
from fkqt_jevinvestor.ingestion.execution_bundle import ExecutionBundleV1
from fkqt_jevinvestor.persistence.models import (
    DecisionEvaluationRecord,
    DecisionRunLinkRecord,
    MarketSnapshotRecord,
    PositionRecord,
)
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.services.market_pipeline import MarketPipeline
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio
from fkqt_jevinvestor.services.position_sizing import (
    PositionSizingConfigV1,
    to_validated_signal_batch,
)
from tests.integration.test_portfolio_repository import (
    accepted_batch,
    execution_market,
    seed_c_group_evaluation,
)
from tests.unit.test_position_sizing import (
    _evaluation,  # pyright: ignore[reportPrivateUsage]
    _features,  # pyright: ignore[reportPrivateUsage]
    _run,  # pyright: ignore[reportPrivateUsage]
)


@pytest.mark.asyncio
async def test_daily_required_symbols_exports_pending_and_held_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "symbols.db"
    database_url = f"sqlite+aiosqlite:///{database.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    await asyncio.to_thread(command.upgrade, config, "head")
    settings = Settings(database_url=database_url)
    engine = create_engine(database_url)
    try:
        factory = create_session_factory(engine)
        repository = PortfolioRepository(factory)
        await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
        await accepted_batch(repository)
        async with factory.begin() as session:
            session.add(PositionRecord(
                id="held-only", portfolio_id="portfolio-1", symbol="300750.SZ",
                quantity=100, average_cost=Decimal(10), total_cost=Decimal(1000),
                last_price=Decimal(10), target_position_pct=Decimal("0.01"),
                updated_at=datetime.now(UTC),
            ))
    finally:
        await engine.dispose()
    output = tmp_path / "required.txt"
    args = Namespace(
        portfolio_id="portfolio-1", trade_date=date(2026, 9, 22),
        output_file=str(output),
    )
    assert await _daily_required_symbols(args, settings) == 0
    assert output.read_text(encoding="utf-8") == "000001.SZ\n300750.SZ\n"
    assert json.loads(capsys.readouterr().out)["symbol_count"] == 2


@pytest.mark.asyncio
async def test_daily_execute_reuses_nav_and_rejects_changed_bundle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "daily.db"
    database_url = f"sqlite+aiosqlite:///{database.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    await asyncio.to_thread(command.upgrade, config, "head")
    settings = Settings(database_url=database_url, execution_bundle_root=tmp_path / "frozen")
    engine = create_engine(database_url)
    try:
        repository = PortfolioRepository(create_session_factory(engine))
        await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
        await accepted_batch(repository)
    finally:
        await engine.dispose()

    trade_date = date(2026, 9, 22)
    bundle = ExecutionBundleV1.create(trade_date, {"000001.SZ": execution_market()})
    source = tmp_path / "execution.json"
    source.write_text(bundle.model_dump_json(), encoding="utf-8")
    args = Namespace(
        portfolio_id="portfolio-1", trade_date=trade_date, execution_bundle=str(source)
    )
    assert await _daily_execute(args, settings) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["fill_count"] == 1
    assert Path(first["execution_ref"]).is_file()
    assert await _daily_execute(args, settings) == 0
    assert json.loads(capsys.readouterr().out)["fill_count"] == 0

    engine = create_engine(database_url)
    try:
        async with create_session_factory(engine).begin() as session:
            session.add(PositionRecord(
                id="future-position",
                portfolio_id="portfolio-1",
                symbol="600000.SH",
                quantity=100,
                average_cost=Decimal(10),
                total_cost=Decimal(1000),
                last_price=Decimal(10),
                target_position_pct=Decimal("0.01"),
                updated_at=datetime.now(UTC),
            ))
    finally:
        await engine.dispose()
    assert await _daily_execute(args, settings) == 0
    assert json.loads(capsys.readouterr().out)["fill_count"] == 0

    changed = ExecutionBundleV1.create(
        trade_date, {"000001.SZ": execution_market(price=Decimal(12))}
    )
    source.write_text(changed.model_dump_json(), encoding="utf-8")
    assert await _daily_execute(args, settings) == 2
    assert capsys.readouterr().err.strip() == "EXECUTION_INPUT_CONFLICT"

    status = Namespace(portfolio_id="portfolio-1", trade_date=trade_date)
    assert await _daily_status(status, settings) == 0
    assert json.loads(capsys.readouterr().out)["execution_complete"] is True

    next_date = date(2026, 9, 18)
    next_bundle = ExecutionBundleV1.create(
        next_date, {"000001.SZ": execution_market(trade_date=next_date)}
    )
    source.write_text(next_bundle.model_dump_json(), encoding="utf-8")
    next_args = Namespace(
        portfolio_id="portfolio-1", trade_date=next_date, execution_bundle=str(source)
    )
    assert await _daily_execute(next_args, settings) == 2
    assert capsys.readouterr().err.strip() == "DAILY_EXECUTION_NOT_SCHEDULED"


@pytest.mark.asyncio
async def test_daily_status_reports_next_day_orders_and_close_rejects_changed_inputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "status.db"
    database_url = f"sqlite+aiosqlite:///{database.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    await asyncio.to_thread(command.upgrade, config, "head")
    settings = Settings(
        database_url=database_url,
        fkqt_manifest_bundle_root=tmp_path,
        c_group_candidate_symbols="000001.SZ",
        typesafe_api_key=SecretStr("test-key"),
        deepseek_api_key=SecretStr("test-key"),
    )
    engine = create_engine(database_url)
    try:
        session_factory = create_session_factory(engine)
        repository = PortfolioRepository(session_factory)
        await repository.create(CreatePortfolio(portfolio_id="portfolio-1", name="Demo"))
        await seed_c_group_evaluation(session_factory)
        state = await repository.get_state("portfolio-1", date(2026, 9, 18))
        sizing = _run(
            (_evaluation("000001.SZ", DecisionAction.ENTER),),
            {"000001.SZ": _features("000001.SZ")},
            state,
        )
        await repository.save_c_group_signal_batch(
            sizing, PositionSizingConfigV1(), to_validated_signal_batch(sizing), state
        )
        async with session_factory.begin() as session:
            session.add(DecisionRunLinkRecord(
                id="run-link-1",
                run_id=str(sizing.run_id),
                evaluation_id="decision-000001.SZ",
                created_at=datetime.now(UTC),
            ))
            await session.execute(
                update(DecisionEvaluationRecord)
                .where(DecisionEvaluationRecord.id == "decision-000001.SZ")
                .values(input_json={"market_snapshot_hash": "a" * 64})
            )
            session.add(MarketSnapshotRecord(
                id="unrelated-snapshot",
                decision_date=date(2026, 9, 18),
                decision_cutoff=datetime(2026, 9, 18, 15, tzinfo=UTC),
                next_trade_date=date(2026, 9, 21),
                calendar_complete_through=date(2026, 9, 21),
                universe_snapshot_id="other-portfolio",
                universe_snapshot_hash="b" * 64,
                content_hash="c" * 64,
                storage_path="unrelated.json",
                source_manifests=[],
                source_audits=[],
                created_at=datetime.now(UTC),
            ))
    finally:
        await engine.dispose()

    status = Namespace(portfolio_id="portfolio-1", trade_date=date(2026, 9, 18))
    assert await _daily_status(status, settings) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scheduled_pending_order_count"] == 1
    assert payload["planned_execution_dates"] == ["2026-09-21"]
    assert payload["market_snapshot_hashes"] == ["a" * 64]

    async def changed_snapshot(*args: object, **kwargs: object) -> tuple[SimpleNamespace, dict[str, object]]:
        return SimpleNamespace(content_hash="b" * 64), {}

    monkeypatch.setattr(MarketPipeline, "freeze_and_compute", changed_snapshot)
    close = Namespace(portfolio_id="portfolio-1", decision_date=date(2026, 9, 18), candidate_limit=1)
    assert await _daily_close(close, settings) == 2
    assert capsys.readouterr().err.strip() == "DAILY_DECISION_INPUT_CONFLICT"
