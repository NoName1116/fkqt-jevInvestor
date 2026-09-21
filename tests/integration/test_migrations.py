import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_phase_zero_migration_is_idempotent_and_namespaced(tmp_path: Path) -> None:
    database = tmp_path / "phase0.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")

    command.upgrade(config, "0001_phase0_core")
    command.upgrade(config, "0001_phase0_core")

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            )
        }
        provider_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(ai_signal_provider_call)")
        }

    assert tables == {
        "ai_signal_provider_call",
        "ai_signal_schema_version",
        "alembic_version",
    }
    assert "full_content" not in provider_columns
    assert "raw_response" not in provider_columns
    assert "api_key" not in provider_columns
