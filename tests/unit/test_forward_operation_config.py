import sys
from datetime import date, time
from pathlib import Path

import pytest

from fkqt_jevinvestor.operations.config import ForwardOperationConfig


def values(tmp_path: Path) -> dict[str, object]:
    return {"portfolio_id": "p", "start_date": date(2026, 9, 28), "fkqt_cwd": tmp_path,
            "fkqt_python": Path(sys.executable), "jev_cwd": tmp_path, "jev_python": Path(sys.executable),
            "data_root": tmp_path / "data", "candidate_source": tmp_path,
            "candidate_limit": 2, "readiness_deadline_local": time(23)}


def test_absolute_paths_positive_capacity_and_secret_free_config(tmp_path: Path) -> None:
    config = ForwardOperationConfig.model_validate(values(tmp_path))
    assert config.max_attempts == 4
    for invalid in ({"candidate_limit": 0}, {"jev_cwd": "relative"},
                    {"webhook_url": "http://example.org"}, {"webhook_url": "https://u:p@example.org"},
                    {"api_key": "not-permitted"}):
        with pytest.raises(ValueError):
            ForwardOperationConfig.model_validate({**values(tmp_path), **invalid})


def test_forward_config_rejects_legacy_model_instead_of_silent_downgrade(tmp_path: Path) -> None:
    assert ForwardOperationConfig.model_validate(values(tmp_path)).deepseek_model == "deepseek-flash"
    with pytest.raises(ValueError, match="DEEPSEEK_MODEL_POLICY_MISMATCH"):
        ForwardOperationConfig.model_validate({**values(tmp_path), "deepseek_model": "deepseek-chat"})
