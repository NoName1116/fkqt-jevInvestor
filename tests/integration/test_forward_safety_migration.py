import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from fkqt_jevinvestor.persistence.jev_repository import JevEvaluationRepository
from fkqt_jevinvestor.persistence.models import JevAttemptRecord
from fkqt_jevinvestor.providers.jev_market import TypeSafeJevMarketProvider
from tests.integration import test_portfolio_repository as fixtures
from tests.unit.test_jev_market_provider import (  # pyright: ignore[reportPrivateUsage]
    FakeClient,
    _command,  # pyright: ignore[reportPrivateUsage]
    _valid_response,  # pyright: ignore[reportPrivateUsage]
)

session_factory = fixtures.session_factory


@pytest.mark.asyncio
async def test_diagnostic_persists_with_attempt(session_factory: fixtures.SessionFactory) -> None:
    cmd = _command()
    repo = JevEvaluationRepository(session_factory)
    claim = await repo.claim(uuid4(), cmd)
    result = await TypeSafeJevMarketProvider(FakeClient(_valid_response()), cmd.model_id, cmd.provider_version).evaluate(cmd)
    await repo.record_success(claim, result)
    async with session_factory() as session:
        stored = await session.scalar(select(JevAttemptRecord))
        assert stored is not None
        assert stored.diagnostic_json is not None
        assert stored.diagnostic_json["input_hash"] == cmd.input_hash


def test_empty_safety_migration_round_trip(tmp_path: Path) -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{(tmp_path / 'empty.db').as_posix()}")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0009_forward_operations")
    command.upgrade(cfg, "head")


def test_nonempty_recovery_cannot_be_lossily_downgraded(tmp_path: Path) -> None:
    cfg = Config("alembic.ini")
    database = tmp_path / "guard.db"
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    command.upgrade(cfg, "head")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO ai_signal_forward_recovery (id,portfolio_id,trade_date,stage_id,stage,config_hash,expected_stage_hash,reason,previous_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("a" * 64, "p", "2026-09-28", "b" * 64, "CALENDAR", "c" * 64, "d" * 64, "修复", "{}", "2026-09-28T13:00:00"))
    with pytest.raises(RuntimeError, match="FORWARD_SAFETY_DOWNGRADE_DATA_LOSS"):
        command.downgrade(cfg, "0009_forward_operations")
