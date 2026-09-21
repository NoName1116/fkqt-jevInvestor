import pytest

from fkqt_jevinvestor.config import Settings


def test_package_and_environment_prefix_are_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JEV_INVESTOR_ENV", raising=False)
    monkeypatch.delenv("JEV_INVESTOR_DATABASE_URL", raising=False)
    settings = Settings()
    assert settings.environment == "local"
    assert settings.database_url.endswith("data/fkqt_jevinvestor.db")
