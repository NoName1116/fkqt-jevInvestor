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


@pytest.mark.asyncio
async def test_real_runner_failure_can_recover_once_without_resetting_attempts(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    from fkqt_jevinvestor.operations.process import ProcessOutcome
    from fkqt_jevinvestor.operations.recovery import RecoveryService
    from fkqt_jevinvestor.operations.runner import ForwardRunner

    config = ForwardOperationConfig.model_validate(values(tmp_path))
    repo = ForwardOperationRepository(session_factory)

    def fail(*args: Any, **kwargs: Any) -> ProcessOutcome:
        return ProcessOutcome(2, "", "TRADING_CALENDAR_INCOMPLETE", False, 0)

    runner = ForwardRunner(repo, process=fail)
    assert (await runner.run_once(config, DAY, NOW)).status == "FAILED"
    service = RecoveryService(session_factory)
    preview = await service.preview(config, DAY, "CALENDAR")
    assert preview["allowed"] is True
    recovery = await service.commit(config, DAY, "CALENDAR", "已检查行情配置", preview["stage_hash"], NOW)
    assert (await runner.run_once(config, DAY, NOW)).status == "FAILED"
    row = await repo.get_stage(config.portfolio_id, DAY, "CALENDAR", config.content_hash())
    assert row is not None and row.attempts == 2
    async with session_factory() as session:
        audit = await session.get(ForwardRecoveryRecord, recovery["recovery_id"])
        assert audit is not None and audit.consumed_at is not None
    assert (await runner.run_once(config, DAY, NOW)).status == "FAILED"
    unchanged = await repo.get_stage(config.portfolio_id, DAY, "CALENDAR", config.content_hash())
    assert unchanged is not None and unchanged.attempts == 2


@pytest.mark.asyncio
async def test_execute_recovery_refuses_conflicting_committed_nav(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    from decimal import Decimal

    from fkqt_jevinvestor.operations.recovery import RecoveryService
    from fkqt_jevinvestor.persistence.models import NavRecord, PortfolioSnapshotRecord
    from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
    from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio

    config = ForwardOperationConfig.model_validate(values(tmp_path))
    await PortfolioRepository(session_factory).create(CreatePortfolio(portfolio_id="p", name="恢复验收"))
    repo = ForwardOperationRepository(session_factory)
    assert await repo.acquire_lease("p", owner_id="seed", now=NOW, lease_seconds=60)
    for stage in ("CALENDAR", "EXECUTION_PUBLISH", "EXECUTION_PREPARE", "EXECUTE"):
        await repo.record_stage("p", DAY, stage, config.content_hash(), owner_id="seed", now=NOW,
            status="FAILED" if stage == "EXECUTE" else "SUCCESS", attempts=1, input_hash=config.content_hash(),
            outcome={"execution_hash": "b" * 64} if stage == "EXECUTION_PREPARE" else {})
    await repo.release_lease("p", "seed")
    async with session_factory.begin() as session:
        session.add(NavRecord(id="nav-conflict", portfolio_id="p", valuation_date=DAY, total_equity=Decimal(1000000),
            unit_nav=Decimal(1), daily_return=Decimal(0), cumulative_return=Decimal(0), max_drawdown=Decimal(0)))
        session.add(PortfolioSnapshotRecord(id="snapshot-conflict", portfolio_id="p", snapshot_type="POST_EXECUTION", as_of=NOW,
            cash_balance=Decimal(1000000), frozen_cash=Decimal(0), market_value=Decimal(0), total_equity=Decimal(1000000),
            realized_pnl=Decimal(0), unrealized_pnl=Decimal(0), content_hash="c" * 64,
            details_json={"execution_input_hash": "a" * 64}))
    preview = await RecoveryService(session_factory).preview(config, DAY, "EXECUTE")
    assert preview["allowed"] is False
    assert preview["error_code"] == "FORWARD_RECOVERY_INPUT_CONFLICT"
