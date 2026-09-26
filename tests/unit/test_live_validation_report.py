import json
from datetime import date
from pathlib import Path

import pytest

from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.operations.live_validation import validate_live
from fkqt_jevinvestor.operations.process import ProcessOutcome
from tests.unit.test_forward_operation_config import values


@pytest.mark.asyncio
async def test_missing_keys_and_no_live_opt_in_are_not_success(tmp_path: Path) -> None:
    config = ForwardOperationConfig.model_validate(values(tmp_path))
    report = await validate_live(config, mode="PROVIDER_CONTRACT", trade_date=date(2026, 9, 25), live=False)
    assert report.status == "BLOCKED"
    assert len(report.probes) == 9
    assert all(probe["code"] == "LIVE_OPT_IN_REQUIRED" for probe in report.probes)
    assert not report.strict_point_in_time
    assert Path(report.report_ref).is_file()


@pytest.mark.asyncio
async def test_historical_replay_cannot_claim_forward(tmp_path: Path) -> None:
    config = ForwardOperationConfig.model_validate(values(tmp_path))
    report = await validate_live(config, mode="REAL_SOURCE_REPLAY", trade_date=date(2026, 9, 25), live=False)
    assert report.mode == "REAL_SOURCE_REPLAY"
    assert not report.is_forward
    assert report.tax_policy_version == "GROSS_V1"


@pytest.mark.asyncio
async def test_future_session_reports_pending_not_completed_forward(tmp_path: Path) -> None:
    config = ForwardOperationConfig.model_validate(values(tmp_path))
    def process(argv: tuple[str, ...], cwd: Path, **kwargs: object) -> ProcessOutcome:
        if "permission-probe" in argv:
            payload = {"endpoints": [{"endpoint": name, "status": "AVAILABLE", "code": "OK",
                                     "coverage": "SAMPLE_ONLY", "latency_ms": 0, "source_date": "20990101", "row_count": 1}
                                    for name in ("daily", "trade_cal", "stock_basic", "namechange", "suspend_d", "stk_limit", "dividend")]}
            return ProcessOutcome(0, json.dumps(payload), "", False, 0)
        return ProcessOutcome(0, "1 passed", "", False, 0)
    report = await validate_live(config, mode="LIVE_FORWARD", trade_date=date(2099, 1, 1), live=True, process=process,
                                 env={"TUSHARE_TOKEN": "never-log-token", "TYPESAFE_API_KEY": "never-log-jev", "DEEPSEEK_API_KEY": "never-log-ds"})
    assert report.status == "LIVE_FORWARD_PENDING"
    assert report.pending_execution_date == date(2099, 1, 1)
    assert report.is_forward is False
    assert "never-log" not in report.model_dump_json()
