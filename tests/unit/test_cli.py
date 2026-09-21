from fkqt_jevinvestor.cli.main import main


def test_version_command(capsys) -> None:
    exit_code = main(["version"])
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "fkqt-jevinvestor 0.1.0"
