from datetime import date, timedelta

import pytest

from fkqt_jevinvestor.cli.main import decision_cutoff_for_date, main


def test_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["version"])
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "fkqt-jevinvestor 0.1.0"


def test_manifest_freeze_requires_configured_bundle_root(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FKQT_MANIFEST_BUNDLE_ROOT", raising=False)

    exit_code = main(
        [
            "market",
            "freeze",
            "--date",
            "2026-09-25",
            "--symbols",
            "600000.SH,000001.SZ",
            "--source",
            "manifest",
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "FKQT_MANIFEST_BUNDLE_ROOT_REQUIRED"


def test_manifest_freeze_uses_bundled_fixed_china_timezone() -> None:
    cutoff = decision_cutoff_for_date(date(2026, 9, 25))

    assert cutoff.isoformat() == "2026-09-25T15:00:00+08:00"
    assert cutoff.utcoffset() == timedelta(hours=8)


def test_c_group_requires_all_three_cli_arguments() -> None:
    with pytest.raises(SystemExit) as error:
        main(["decision", "run-c-group", "--date", "2026-09-25"])

    assert error.value.code == 2


def test_c_group_rejects_non_positive_candidate_limit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        main(
            [
                "decision",
                "run-c-group",
                "--date",
                "2026-09-25",
                "--portfolio-id",
                "paper-main",
                "--candidate-limit",
                "0",
            ]
        )

    assert error.value.code == 2
    assert "CANDIDATE_LIMIT_MUST_BE_POSITIVE" in capsys.readouterr().err


def test_c_group_missing_snapshot_config_fails_before_provider_creation(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JEV_INVESTOR_C_GROUP_SNAPSHOT_HASH", raising=False)
    monkeypatch.delenv("JEV_INVESTOR_C_GROUP_CANDIDATE_SYMBOLS", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-be-read")
    monkeypatch.setenv("TYPESAFE_API_KEY", "must-not-be-read")

    exit_code = main(
        [
            "decision",
            "run-c-group",
            "--date",
            "2026-09-25",
            "--portfolio-id",
            "paper-main",
            "--candidate-limit",
            "2",
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "C_GROUP_SNAPSHOT_CONFIG_REQUIRED"


def test_c_group_missing_provider_config_returns_stable_error(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JEV_INVESTOR_C_GROUP_SNAPSHOT_HASH", "a" * 64)
    monkeypatch.setenv(
        "JEV_INVESTOR_C_GROUP_CANDIDATE_SYMBOLS",
        "600000.SH,000001.SZ",
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    exit_code = main(
        [
            "decision",
            "run-c-group",
            "--date",
            "2026-09-25",
            "--portfolio-id",
            "paper-main",
            "--candidate-limit",
            "2",
        ]
    )

    assert exit_code == 2
    assert capsys.readouterr().err.strip() == "C_GROUP_PROVIDER_CONFIG_REQUIRED"
