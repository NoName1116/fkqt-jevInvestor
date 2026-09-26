from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.operations.runner import ForwardRunner, ForwardRunOutcome
from tests.unit.test_forward_operation_config import values


class ForbiddenRepository:
    async def acquire_lease(self, *args: Any, **kwargs: Any) -> bool:
        raise AssertionError("blocked date acquired lease")


@pytest.mark.asyncio
@pytest.mark.parametrize(("day", "now", "code"), [
    (date(2026, 9, 28), datetime(2026, 9, 27, 16, tzinfo=UTC), "FORWARD_BEFORE_MARKET_CLOSE"),
    (date(2026, 9, 28), datetime(2026, 9, 28, 6, 59, 59, tzinfo=UTC), "FORWARD_BEFORE_MARKET_CLOSE"),
    (date(2026, 9, 29), datetime(2026, 9, 28, 15, tzinfo=UTC), "FORWARD_FUTURE_DATE"),
])
async def test_blocked_date_has_no_side_effects(tmp_path: Path, day: date, now: datetime, code: str) -> None:
    config = ForwardOperationConfig.model_validate(values(tmp_path))
    runner = ForwardRunner(ForbiddenRepository())  # type: ignore[arg-type]
    result = await runner.run_once(config, day, now)
    assert result.status == "BLOCKED"
    assert result.error_code == code
    assert not config.data_root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("now", [datetime(2026, 9, 28, 7, tzinfo=UTC), datetime(2026, 9, 29, 0, tzinfo=UTC)])
async def test_closed_or_historical_date_can_reach_existing_flow(tmp_path: Path, now: datetime, monkeypatch: pytest.MonkeyPatch) -> None:
    async def existing(*args: Any) -> ForwardRunOutcome:
        return ForwardRunOutcome("CLOSED", ("CALENDAR",))

    config = ForwardOperationConfig.model_validate(values(tmp_path))
    runner = ForwardRunner(ForbiddenRepository())  # type: ignore[arg-type]
    monkeypatch.setattr(runner, "_run_once", existing)
    assert (await runner.run_once(config, date(2026, 9, 28), now)).status == "CLOSED"
