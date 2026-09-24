import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from fkqt_jevinvestor.api.app import create_app
from fkqt_jevinvestor.config import Settings
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory


def test_market_api_is_read_only_without_configured_provider_and_rejects_paths(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market-api.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    with TestClient(
        create_app(
            Settings(environment="production"),
            session_factory=create_session_factory(engine),
        )
    ) as client:
        assert client.get("/api/v1/market-snapshots/missing").status_code == 404
        response = client.post(
            "/api/v1/market-snapshots/freeze",
            json={
                "symbols": ["600000.SH"],
                "decision_date": "2026-09-25",
                "decision_cutoff": "2026-09-25T15:00:00+08:00",
                "storage_path": "C:/arbitrary/input",
            },
        )
        assert response.status_code == 422
    asyncio.run(engine.dispose())
