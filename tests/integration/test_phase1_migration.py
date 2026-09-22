import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

EXPECTED_AI_SIGNAL_TABLES = {
    "ai_signal_nav",
    "ai_signal_portfolio",
    "ai_signal_portfolio_snapshot",
    "ai_signal_position",
    "ai_signal_position_lot",
    "ai_signal_provider_call",
    "ai_signal_schema_version",
    "ai_signal_signal",
    "ai_signal_signal_batch",
    "ai_signal_virtual_fill",
    "ai_signal_virtual_order",
}


def migration_config(database: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    return config


def tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    }


def foreign_key_targets(connection: sqlite3.Connection, table_names: set[str]) -> set[str]:
    return {
        row[2]
        for table_name in table_names
        for row in connection.execute(f'PRAGMA foreign_key_list("{table_name}")')
    }


def unique_column_sets(connection: sqlite3.Connection, table_name: str) -> set[tuple[str, ...]]:
    indexes = connection.execute(f'PRAGMA index_list("{table_name}")').fetchall()
    return {
        tuple(
            row[2]
            for row in connection.execute(f'PRAGMA index_info("{index_name}")').fetchall()
        )
        for _, index_name, is_unique, *_ in indexes
        if is_unique
    }


def column_type(connection: sqlite3.Connection, table_name: str, column_name: str) -> str:
    columns = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    return next(row[2] for row in columns if row[1] == column_name)


def table_sql(connection: sqlite3.Connection, table_name: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    assert row is not None
    return row[0]


def test_phase1_migration_is_namespaced_constrained_and_reversible(tmp_path: Path) -> None:
    database = tmp_path / "phase1.db"
    config = migration_config(database)

    command.upgrade(config, "0003_phase1_audit_snapshot")
    command.upgrade(config, "0003_phase1_audit_snapshot")

    with sqlite3.connect(database) as connection:
        assert tables(connection) == EXPECTED_AI_SIGNAL_TABLES | {"alembic_version"}
        assert all(
            target.startswith("ai_signal_")
            for target in foreign_key_targets(connection, EXPECTED_AI_SIGNAL_TABLES)
        )
        assert unique_column_sets(connection, "ai_signal_position") >= {
            ("portfolio_id", "symbol")
        }
        assert unique_column_sets(connection, "ai_signal_signal_batch") >= {
            ("portfolio_id", "decision_date", "fixture_version")
        }
        assert unique_column_sets(connection, "ai_signal_virtual_order") >= {
            ("idempotency_key",)
        }
        assert unique_column_sets(connection, "ai_signal_virtual_fill") >= {
            ("order_id", "fill_sequence")
        }
        assert unique_column_sets(connection, "ai_signal_portfolio_snapshot") >= {
            ("portfolio_id", "snapshot_type", "as_of")
        }
        assert unique_column_sets(connection, "ai_signal_nav") >= {
            ("portfolio_id", "valuation_date")
        }
        assert column_type(connection, "ai_signal_portfolio", "cash_balance") == "NUMERIC(20, 4)"
        assert column_type(connection, "ai_signal_position", "total_cost") == "NUMERIC(24, 8)"
        assert "quantity >= 0" in table_sql(connection, "ai_signal_position")
        assert "remaining_quantity >= 0" in table_sql(connection, "ai_signal_position_lot")
        assert "filled_quantity >= 0" in table_sql(connection, "ai_signal_virtual_order")
        assert "quantity > 0" in table_sql(connection, "ai_signal_virtual_fill")

    command.downgrade(config, "0001_phase0_core")

    with sqlite3.connect(database) as connection:
        assert tables(connection) == {
            "ai_signal_provider_call",
            "ai_signal_schema_version",
            "alembic_version",
        }
