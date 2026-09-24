from pathlib import Path

from pytest import MonkeyPatch

from fkqt_jevinvestor.config import Settings


def test_settings_allow_missing_typesafe_key(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    settings = Settings()

    assert settings.typesafe_api_key is None
    assert settings.typesafe_model == "jev-latest"
    assert settings.database_url.startswith("sqlite+aiosqlite:///")


def test_settings_default_to_deepseek_v41_flash(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    settings = Settings()

    assert settings.deepseek_api_key is None
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model == "deepseek-flash"
    assert settings.deepseek_reasoning_effort == "high"
    assert settings.deepseek_timeout_seconds == 30


def test_settings_hide_secrets(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-value")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")

    settings = Settings()

    assert "secret-value" not in repr(settings)
    assert "deepseek-secret-value" not in repr(settings)
