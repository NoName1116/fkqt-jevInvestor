import os
from datetime import date
from pathlib import Path

import pytest

from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.operations.live_validation import validate_live


@pytest.mark.live
@pytest.mark.asyncio
async def test_explicit_forward_live_contract_probe() -> None:
    if os.environ.get("RUN_LIVE_FORWARD_TESTS") != "1":
        pytest.skip("set RUN_LIVE_FORWARD_TESTS=1 and FORWARD_CONFIG to opt in")
    configured = os.environ.get("FORWARD_CONFIG")
    if not configured:
        pytest.skip("FORWARD_CONFIG is required")
    config = ForwardOperationConfig.load(Path(configured))
    report = await validate_live(config, mode="PROVIDER_CONTRACT", trade_date=date.fromisoformat(os.environ["FORWARD_SOURCE_DATE"]), live=True)
    assert report.status == "SUCCESS", report.model_dump_json()
