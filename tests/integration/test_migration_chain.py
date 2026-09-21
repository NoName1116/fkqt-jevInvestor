import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_migrated_revision_chain_is_repeatable_and_reversible(tmp_path: Path) -> None:
    database = tmp_path / "migration-chain.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")

    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert revision == ("0003_phase1_audit_snapshot",)
    assert len({name for name in tables if name.startswith("ai_signal_")}) == 11

    command.downgrade(config, "base")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert not {name for name in tables if name.startswith("ai_signal_")}
