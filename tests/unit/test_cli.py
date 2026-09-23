import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from fkqt_jevinvestor.cli.main import decision_cutoff_for_date, main, stable_daily_run_id


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


def test_daily_run_id_is_stable_and_input_bound() -> None:
    first = stable_daily_run_id("paper-main", date(2026, 9, 24), "a" * 64, ("600000.SH",), 1, 2)
    assert first == stable_daily_run_id(
        "paper-main", date(2026, 9, 24), "a" * 64, ("600000.SH",), 1, 2
    )
    assert first != stable_daily_run_id(
        "paper-main", date(2026, 9, 24), "b" * 64, ("600000.SH",), 1, 2
    )


def test_prepare_execution_freezes_raw_market_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JEV_INVESTOR_EXECUTION_BUNDLE_ROOT", str(tmp_path / "frozen"))
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps({
        "600000.SH": {
            "symbol": "600000.SH",
            "trade_date": "2026-09-24",
            "trading_day_status": "OPEN",
            "trading_status": "TRADING",
            "open_price": "10",
            "unadjusted_close": "10.5",
            "daily_amount_cny": "10000000",
        }
    }), encoding="utf-8")
    assert main([
        "daily", "prepare-execution", "--trade-date", "2026-09-24",
        "--raw-file", str(raw),
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert Path(output["execution_ref"]).is_file()
    assert output["symbol_count"] == 1
