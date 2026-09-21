import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def _config(database: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    return config


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def _unique_columns(connection: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    return {
        tuple(
            row[2]
            for row in connection.execute(f'PRAGMA index_info("{index_name}")').fetchall()
        )
        for _, index_name, is_unique, *_ in connection.execute(
            f'PRAGMA index_list("{table}")'
        ).fetchall()
        if is_unique
    }


def test_phase2_market_tables_are_constrained_and_reversible(tmp_path: Path) -> None:
    database = tmp_path / "phase2.db"
    config = _config(database)

    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert {"ai_signal_market_snapshot", "ai_signal_market_feature"} <= _tables(connection)
        assert ("decision_date", "content_hash") in _unique_columns(
            connection, "ai_signal_market_snapshot"
        )
        assert (
            "market_snapshot_id",
            "symbol",
            "feature_code",
            "feature_version",
        ) in _unique_columns(connection, "ai_signal_market_feature")

    command.downgrade(config, "0003_phase1_audit_snapshot")
    with sqlite3.connect(database) as connection:
        assert "ai_signal_market_snapshot" not in _tables(connection)
        assert "ai_signal_market_feature" not in _tables(connection)
        assert len(_tables(connection) - {"alembic_version"}) == 11
