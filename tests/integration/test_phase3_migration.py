import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

PHASE3_TABLES = {
    "ai_signal_jev_evaluation",
    "ai_signal_jev_attempt",
    "ai_signal_jev_question_result",
    "ai_signal_jev_run_link",
}


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


def test_phase3_jev_tables_are_constrained_and_reversible(tmp_path: Path) -> None:
    database = tmp_path / "phase3.db"
    config = _config(database)

    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert PHASE3_TABLES <= _tables(connection)
        assert ("formal_key",) in _unique_columns(connection, "ai_signal_jev_evaluation")
        assert ("evaluation_id", "sequence") in _unique_columns(
            connection, "ai_signal_jev_attempt"
        )
        assert ("evaluation_id", "question_id") in _unique_columns(
            connection, "ai_signal_jev_question_result"
        )
        assert ("run_id", "evaluation_id") in _unique_columns(
            connection, "ai_signal_jev_run_link"
        )

    command.downgrade(config, "0005_phase2_audit_hardening")
    with sqlite3.connect(database) as connection:
        assert not (PHASE3_TABLES & _tables(connection))
        assert {"ai_signal_market_snapshot", "ai_signal_market_feature"} <= _tables(connection)

