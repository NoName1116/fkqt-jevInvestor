import os
import sys
from pathlib import Path

from fkqt_jevinvestor.operations.process import run_cli


def test_cli_uses_explicit_cwd_and_redacts_environment_secret(tmp_path: Path) -> None:
    outcome = run_cli((sys.executable, "-c", "import os;print(os.getcwd());print(os.environ['TYPESAFE_API_KEY'])"),
                      tmp_path, timeout_seconds=5, env={**os.environ, "TYPESAFE_API_KEY": "sensitive-test-value"})
    assert outcome.exit_code == 0
    assert str(tmp_path) in outcome.safe_stdout
    assert "sensitive-test-value" not in outcome.safe_stdout
    assert "[REDACTED]" in outcome.safe_stdout


def test_timeout_kills_child_and_limits_logs(tmp_path: Path) -> None:
    outcome = run_cli((sys.executable, "-c", "import time;print('x'*70000,flush=True);time.sleep(30)"),
                      tmp_path, timeout_seconds=1, env=os.environ)
    assert outcome.timed_out
    assert outcome.exit_code != 0
    assert len(outcome.safe_stdout.encode("utf-8")) <= 65536
