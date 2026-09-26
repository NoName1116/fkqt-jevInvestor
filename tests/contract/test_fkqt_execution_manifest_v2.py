import json
from datetime import date
from pathlib import Path

import pytest

from fkqt_jevinvestor.ingestion.fkqt_execution_manifest_v2 import load_fkqt_execution_manifest_v2
from fkqt_jevinvestor.ingestion.fkqt_manifest import FkqtBundleError


def test_missing_calendar_rejected(tmp_path: Path) -> None:
    with pytest.raises(FkqtBundleError, match="DATASET_MANIFEST_MISSING"):
        load_fkqt_execution_manifest_v2(tmp_path, date(2026, 9, 25), set())


def test_real_fkqt_v2_empty_and_nonempty(tmp_path: Path) -> None:
    import os
    import subprocess

    publisher_root = Path("C:/Users/1/Desktop/FKQTPRO/ai-signal-phase0/agent")
    interpreter = Path("C:/Users/1/Desktop/FKQT-pro/agent/.venv-codex/Scripts/python.exe")
    if not publisher_root.exists() or not interpreter.exists():
        pytest.skip("cross-repository publisher runtime is not available")
    script = """
from pathlib import Path
import sys
from market_data.execution_publisher_v2 import publish_execution_bundle_v2
from market_data.store import LocalMarketDataStore
from tests.test_execution_publisher_v2 import client
from tests.test_execution_publisher_v2 import test_v2_binds_corporate_events_before_accepting_bonus_context
for name, scope in [('empty', ()), ('normal', ('600000.SH',))]:
    publish_execution_bundle_v2(client(), LocalMarketDataStore(Path(sys.argv[1])/name), trade_date='20260925', symbols=scope)
test_v2_binds_corporate_events_before_accepting_bonus_context(Path(sys.argv[1])/'corporate')
"""
    outcome = subprocess.run(
        [str(interpreter), "-c", script, str(tmp_path)], cwd=publisher_root,
        env={**os.environ, "PYTHONPATH": str(publisher_root)}, capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert outcome.returncode == 0, outcome.stderr
    empty = load_fkqt_execution_manifest_v2(tmp_path / "empty", date(2026, 9, 25), set())
    assert empty.snapshots == {}
    assert empty.calendar_proof.previous_trade_date == date(2026, 9, 24)
    normal = load_fkqt_execution_manifest_v2(tmp_path / "normal", date(2026, 9, 25), {"600000.SH"})
    assert str(normal.snapshots["600000.SH"].daily_amount_cny) == "10000000"
    corporate = load_fkqt_execution_manifest_v2(tmp_path / "corporate/with-rights", date(2026, 9, 25), {"600000.SH"})
    assert corporate.corporate_actions is not None
    assert str(corporate.corporate_actions.events[0].bonus_ratio) == "0.1"
    outer_path = next((tmp_path / "corporate/with-rights/manifests").glob("*.json"))
    outer = json.loads(outer_path.read_text(encoding="utf-8"))
    outer["request_params"]["corporate_action_manifest_hash"] = None
    outer_path.write_text(json.dumps(outer), encoding="utf-8")
    with pytest.raises(FkqtBundleError, match="DATASET_MANIFEST_ID_MISMATCH"):
        load_fkqt_execution_manifest_v2(tmp_path / "corporate/with-rights", date(2026, 9, 25), {"600000.SH"})
    calendar_path = next((tmp_path / "empty/calendar/manifests").glob("*.json"))
    manifest = json.loads(calendar_path.read_text(encoding="utf-8"))
    parquet = tmp_path / "empty/calendar" / manifest["storage_path"]
    parquet.write_bytes(parquet.read_bytes() + b"tamper")
    with pytest.raises(FkqtBundleError, match="DATASET_HASH_MISMATCH"):
        load_fkqt_execution_manifest_v2(tmp_path / "empty", date(2026, 9, 25), set())
