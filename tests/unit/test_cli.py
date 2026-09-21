import pytest

from fkqt_jevinvestor.cli.main import main


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
