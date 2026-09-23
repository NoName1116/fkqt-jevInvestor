import asyncio
import json
from argparse import Namespace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from fkqt_jevinvestor.cli.main import (
    _daily_execute,  # pyright: ignore[reportPrivateUsage]
    _daily_status,  # pyright: ignore[reportPrivateUsage]
)
from fkqt_jevinvestor.config import Settings
from fkqt_jevinvestor.ingestion.execution_bundle import ExecutionBundleV1
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio
from tests.integration.test_portfolio_repository import accepted_batch, execution_market


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

    changed = ExecutionBundleV1.create(
        trade_date, {"000001.SZ": execution_market(price=Decimal(12))}
    )
    source.write_text(changed.model_dump_json(), encoding="utf-8")
    assert await _daily_execute(args, settings) == 2
    assert capsys.readouterr().err.strip() == "EXECUTION_INPUT_CONFLICT"

    status = Namespace(portfolio_id="portfolio-1", trade_date=trade_date)
    assert await _daily_status(status, settings) == 0
    assert json.loads(capsys.readouterr().out)["execution_complete"] is True
