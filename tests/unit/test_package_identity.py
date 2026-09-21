from fkqt_jevinvestor.config import Settings


def test_package_and_environment_prefix_are_independent() -> None:
    settings = Settings(_env_file=None)
    assert settings.environment == "local"
    assert settings.database_url.endswith("data/fkqt_jevinvestor.db")
