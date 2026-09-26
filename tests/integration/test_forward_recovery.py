from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.persistence.forward_operation_repository import ForwardOperationRepository
from fkqt_jevinvestor.persistence.models import ForwardRecoveryRecord
from tests.integration import test_portfolio_repository as fixtures
from tests.unit.test_forward_operation_config import values

session_factory = fixtures.session_factory
DAY = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 13, tzinfo=UTC)


async def seed(factory: fixtures.SessionFactory, config: ForwardOperationConfig, *, code: str = "NETWORK_TEMPORARY_FAILURE") -> ForwardOperationRepository:
    repo = ForwardOperationRepository(factory)
    assert await repo.acquire_lease(config.portfolio_id, owner_id="seed", now=NOW, lease_seconds=60)
    await repo.record_stage(config.portfolio_id, DAY, "CALENDAR", config.content_hash(), owner_id="seed", now=NOW,
                            status="FAILED", attempts=4, outcome={}, error_code=code, input_hash=config.content_hash())
    await repo.release_lease(config.portfolio_id, "seed")
    return repo


@pytest.mark.asyncio
async def test_recovery_preview_commit_and_repeat_preserve_error(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    from fkqt_jevinvestor.operations.recovery import RecoveryService

    config = ForwardOperationConfig.model_validate(values(tmp_path))
    repo = await seed(session_factory, config)
    service = RecoveryService(session_factory)
    preview = await service.preview(config, DAY, "CALENDAR")
    assert preview["allowed"] is True
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ForwardRecoveryRecord)) == 0
    result = await service.commit(config, DAY, "CALENDAR", "已修复网络配置", preview["stage_hash"], NOW)
    assert result["status"] == "RECOVERED"
    row = await repo.get_stage(config.portfolio_id, DAY, "CALENDAR", config.content_hash())
    assert row is not None and row.status == "RETRY_PENDING" and row.attempts == 4
    async with session_factory() as session:
        audit = await session.get(ForwardRecoveryRecord, result["recovery_id"])
        assert audit is not None
        assert audit.previous_json["error_code"] == "NETWORK_TEMPORARY_FAILURE"
        assert audit.previous_json["attempts"] == 4
    repeat = await service.commit(config, DAY, "CALENDAR", "已修复网络配置", preview["stage_hash"], NOW)
    assert repeat["recovery_id"] == result["recovery_id"]


@pytest.mark.asyncio
async def test_stale_preview_or_active_lease_never_recovers(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    from fkqt_jevinvestor.operations.recovery import RecoveryService

    config = ForwardOperationConfig.model_validate(values(tmp_path))
    repo = await seed(session_factory, config)
    service = RecoveryService(session_factory)
    preview = await service.preview(config, DAY, "CALENDAR")
    await seed(session_factory, config, code="DATA_NOT_READY")
    with pytest.raises(ValueError, match="FORWARD_RECOVERY_STALE_PREVIEW"):
        await service.commit(config, DAY, "CALENDAR", "修复", preview["stage_hash"], NOW)
    assert await repo.acquire_lease(config.portfolio_id, owner_id="other", now=NOW, lease_seconds=60)
    with pytest.raises(ValueError, match="FORWARD_LEASE_BUSY"):
        await service.commit(config, DAY, "CALENDAR", "修复", preview["stage_hash"], NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["DECIDE", "VERIFY"])
async def test_model_stages_cannot_be_recovered(session_factory: fixtures.SessionFactory, tmp_path: Path, stage: str) -> None:
    from fkqt_jevinvestor.operations.recovery import RecoveryService

    config = ForwardOperationConfig.model_validate(values(tmp_path))
    preview = await RecoveryService(session_factory).preview(config, DAY, stage)
    assert preview["allowed"] is False
    assert preview["error_code"] == "FORWARD_RECOVERY_MODEL_REEVALUATION_FORBIDDEN"


@pytest.mark.asyncio
async def test_lease_replacement_between_acquire_and_commit_blocks_write(session_factory: fixtures.SessionFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fkqt_jevinvestor.operations.recovery import RecoveryService

    config = ForwardOperationConfig.model_validate(values(tmp_path))
    repo = await seed(session_factory, config)
    service = RecoveryService(session_factory)
    preview = await service.preview(config, DAY, "CALENDAR")
    acquire = service.repository.acquire_lease

    async def replaced(portfolio_id: str, **kwargs: Any) -> bool:
        success = await acquire(portfolio_id, **kwargs)
        await repo.release_lease(portfolio_id, kwargs["owner_id"])
        assert await repo.acquire_lease(portfolio_id, owner_id="intruder", now=NOW, lease_seconds=60)
        return success

    monkeypatch.setattr(service.repository, "acquire_lease", replaced)
    with pytest.raises(ValueError, match="FORWARD_LEASE_LOST"):
        await service.commit(config, DAY, "CALENDAR", "修复", preview["stage_hash"], NOW)
    row = await repo.get_stage(config.portfolio_id, DAY, "CALENDAR", config.content_hash())
    assert row is not None and row.status == "FAILED"
