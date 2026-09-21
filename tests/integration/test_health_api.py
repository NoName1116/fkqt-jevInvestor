from datetime import UTC, datetime

from fastapi.testclient import TestClient
from pydantic import SecretStr

from fkqt_jevinvestor.api.app import create_app
from fkqt_jevinvestor.config import Settings
from fkqt_jevinvestor.domain.enums import ProviderStatus
from fkqt_jevinvestor.providers.base import ProviderHealth


class HealthyProvider:
    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider="jev",
            status=ProviderStatus.AVAILABLE,
            checked_at=datetime.now(UTC),
        )


async def database_available() -> bool:
    return True


async def database_unavailable() -> bool:
    raise RuntimeError("sqlite+aiosqlite:///secret.db")


def test_liveness() -> None:
    client = TestClient(create_app(Settings(), database_probe=database_available))
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "UP", "service": "fkqt-jevinvestor"}


def test_readiness_without_key_is_degraded() -> None:
    client = TestClient(
        create_app(Settings(typesafe_api_key=None), database_probe=database_available)
    )
    payload = client.get("/api/v1/health/ready").json()
    assert payload["status"] == "DEGRADED"
    assert payload["providers"]["jev"]["status"] == "UNAVAILABLE"
    assert "api_key" not in str(payload).lower()
    assert "secret-value" not in str(payload)


def test_readiness_uses_injected_provider_health() -> None:
    client = TestClient(
        create_app(
            Settings(typesafe_api_key=SecretStr("secret-value")),
            provider=HealthyProvider(),
            database_probe=database_available,
        )
    )

    payload = client.get("/api/v1/health/ready").json()

    assert payload["status"] == "UP"
    assert payload["providers"]["jev"]["status"] == "AVAILABLE"
    assert "secret-value" not in str(payload)


def test_readiness_reports_database_failure_without_exception_details() -> None:
    client = TestClient(
        create_app(Settings(typesafe_api_key=None), database_probe=database_unavailable)
    )

    payload = client.get("/api/v1/health/ready").json()

    assert payload["status"] == "DOWN"
    assert payload["providers"]["database"] == {
        "status": "UNAVAILABLE",
        "error_code": "DATABASE_UNAVAILABLE",
    }
    assert "secret.db" not in str(payload)
