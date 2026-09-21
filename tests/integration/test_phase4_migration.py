import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

PHASE4_TABLES = {
    "ai_signal_llm_decision_evaluation",
    "ai_signal_llm_decision_attempt",
    "ai_signal_llm_decision_run_link",
    "ai_signal_position_sizing_run",
    "ai_signal_position_target",
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


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def test_phase4_decision_and_sizing_tables_are_constrained_and_reversible(
    tmp_path: Path,
) -> None:
    database = tmp_path / "phase4.db"
    config = _config(database)

    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert PHASE4_TABLES <= _tables(connection)
        assert ("formal_key",) in _unique_columns(
            connection, "ai_signal_llm_decision_evaluation"
        )
        assert {"current_owner_token", "lease_expires_at", "raw_response_text"} <= (
            _columns(connection, "ai_signal_llm_decision_evaluation")
        )
        assert ("evaluation_id", "sequence") in _unique_columns(
            connection, "ai_signal_llm_decision_attempt"
        )
        assert ("run_id", "evaluation_id") in _unique_columns(
            connection, "ai_signal_llm_decision_run_link"
        )
        assert ("run_id",) in _unique_columns(
            connection, "ai_signal_position_sizing_run"
        )
        assert ("sizing_run_id", "symbol") in _unique_columns(
            connection, "ai_signal_position_target"
        )

    command.downgrade(config, "0006_phase3_jev_pnl_probabilities")
    with sqlite3.connect(database) as connection:
        assert not (PHASE4_TABLES & _tables(connection))
        assert {
            "ai_signal_jev_evaluation",
            "ai_signal_jev_attempt",
            "ai_signal_jev_question_result",
            "ai_signal_jev_run_link",
        } <= _tables(connection)
