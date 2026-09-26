import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete

from fkqt_jevinvestor.domain.forward import TradingDayProofV1
from fkqt_jevinvestor.ingestion.execution_bundle import ExecutionBundleStore
from fkqt_jevinvestor.ingestion.execution_bundle_v2 import ExecutionBundleV2
from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.operations.process import ProcessOutcome, run_cli
from fkqt_jevinvestor.operations.runner import ForwardRunner
from fkqt_jevinvestor.persistence.forward_operation_repository import ForwardOperationRepository
from fkqt_jevinvestor.persistence.models import ForwardStageRecord
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio
from tests.integration import test_portfolio_repository as fixtures
from tests.unit.test_forward_operation_config import values

session_factory = fixtures.session_factory


@pytest.mark.asyncio
async def test_lease_prevents_parallel_runs_and_expiry_recovers(session_factory: fixtures.SessionFactory) -> None:
    repo = ForwardOperationRepository(session_factory)
    now = datetime(2026, 9, 28, 13, tzinfo=UTC)
    assert await repo.acquire_lease("p", owner_id="one", now=now, lease_seconds=60)
    assert not await repo.acquire_lease("p", owner_id="two", now=now, lease_seconds=60)
    assert await repo.acquire_lease("p", owner_id="two", now=now + timedelta(seconds=61), lease_seconds=60)
    assert not await repo.renew_lease("p", "one", now=now + timedelta(seconds=62), lease_seconds=60)


@pytest.mark.asyncio
async def test_closed_day_has_no_business_writes(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    def process(argv: tuple[str, ...], cwd: Path, **kwargs: object) -> ProcessOutcome:
        calls.append(argv)
        return ProcessOutcome(0, json.dumps({"is_open": False, "open_dates": []}), "", False, 0)
    config = ForwardOperationConfig.model_validate({**values(tmp_path), "start_date": date(2026, 9, 25)})
    result = await ForwardRunner(ForwardOperationRepository(session_factory), process=process).run_once(
        config, date(2026, 9, 26), datetime(2026, 9, 26, 13, 15, tzinfo=UTC))
    assert result.status == "CLOSED"
    assert len(calls) == 1
    assert "calendar" in calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["DATA_NOT_READY", "NETWORK_TEMPORARY_FAILURE", "HTTP_429"])
async def test_only_transient_errors_retry(session_factory: fixtures.SessionFactory, tmp_path: Path, code: str) -> None:
    config = ForwardOperationConfig.model_validate({**values(tmp_path), "start_date": date(2026, 9, 28)})
    def process(argv: tuple[str, ...], cwd: Path, **kwargs: object) -> ProcessOutcome:
        return ProcessOutcome(2, "", code, False, 0)
    runner = ForwardRunner(ForwardOperationRepository(session_factory), process=process)
    now = datetime(2026, 9, 28, 13, 15, tzinfo=UTC)
    first = await runner.run_once(config, date(2026, 9, 28), now)
    assert first.status == "RETRY_PENDING"
    assert first.next_retry_at == now + timedelta(seconds=60)
    for delta in (61, 362, 1263):
        last = await runner.run_once(config, date(2026, 9, 28), now + timedelta(seconds=delta))
    assert last.status == "FAILED"
    assert last.error_code == code


@pytest.mark.asyncio
async def test_decide_consumes_actual_queue_ref_contract(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    def process(argv: tuple[str, ...], cwd: Path, **kwargs: object) -> ProcessOutcome:
        calls.append(argv)
        payload = {"queue_ref": str(tmp_path / "queue.json")} if "prepare-candidates" in argv else {"run_id": "frozen"}
        return ProcessOutcome(0, json.dumps(payload), "", False, 0)
    runner = ForwardRunner(ForwardOperationRepository(session_factory), process=process)
    config = ForwardOperationConfig.model_validate(values(tmp_path))
    result = await runner._stage(config, date(2026, 9, 28), "DECIDE", tmp_path, {}, 30)  # pyright: ignore[reportPrivateUsage]
    assert result["run_id"] == "frozen"
    assert calls[1][-1] == str(tmp_path / "queue.json")


@pytest.mark.asyncio
async def test_execute_nav_precedes_decision_and_crash_recovers_without_reexecution(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    day = date(2026, 9, 25)
    await PortfolioRepository(session_factory).create(CreatePortfolio(portfolio_id="p", name="Operations"))
    source = tmp_path / "candidates.txt"
    source.write_text("600000.SH\n000001.SZ\n", encoding="utf-8")
    config = ForwardOperationConfig.model_validate({**values(tmp_path), "start_date": day,
                                                   "jev_cwd": Path.cwd(), "candidate_source": source})
    bundle = ExecutionBundleV2.create(day, {}, calendar_proof=TradingDayProofV1.create(
        day, is_open=True, exchange="SSE", previous_trade_date=date(2026, 9, 24),
        source_manifest_id="a" * 64, source_content_hash="b" * 64),
        source_manifest_id="c" * 64, source_manifest_content_hash="d" * 64)
    path = ExecutionBundleStore(tmp_path / "bundles").save(bundle)
    calls: list[tuple[str, ...]] = []
    def process(argv: tuple[str, ...], cwd: Path, **kwargs: object) -> ProcessOutcome:
        calls.append(argv)
        if "execute" in argv:
            outcome = run_cli(argv, cwd, timeout_seconds=30, env=kwargs["env"])  # pyright: ignore[reportArgumentType]
            assert outcome.exit_code == 0, outcome.safe_stderr
            return outcome
        payload = {}
        if "calendar" in argv:
            payload = {"is_open": True, "open_dates": [day.isoformat()]}
        elif "prepare-execution" in argv:
            payload = {"execution_ref": str(path), "execution_hash": bundle.content_hash}
        elif "prepare-candidates" in argv:
            payload = {"queue_ref": str(tmp_path / "queue.json")}
        return ProcessOutcome(0, json.dumps(payload), "", False, 0)
    env = {"JEV_INVESTOR_DATABASE_URL": str(session_factory.kw["bind"].url)}
    runner = ForwardRunner(ForwardOperationRepository(session_factory), process=process, env=env)
    now = datetime(2026, 9, 25, 13, 15, tzinfo=UTC)
    first = await runner.run_once(config, day, now)
    assert first.error_code == "FORWARD_DECISION_NOT_COMMITTED"
    execute_index = next(i for i, argv in enumerate(calls) if "execute" in argv)
    decide_index = next(i for i, argv in enumerate(calls) if "close" in argv)
    assert execute_index < decide_index
    assert len(await PortfolioRepository(session_factory).list_nav("p")) == 1
    async with session_factory.begin() as session:
        await session.execute(delete(ForwardStageRecord).where(ForwardStageRecord.stage.in_(["EXECUTE", "VERIFY"])))
    calls.clear()
    await runner.run_once(config, day, now + timedelta(seconds=30))
    assert not any("execute" in argv for argv in calls)
    assert len(await PortfolioRepository(session_factory).list_nav("p")) == 1


@pytest.mark.asyncio
async def test_missing_intermediate_day_blocks_current_decision(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    await PortfolioRepository(session_factory).create(CreatePortfolio(portfolio_id="p", name="Gap"))
    config = ForwardOperationConfig.model_validate({**values(tmp_path), "start_date": date(2026, 9, 24)})
    calls: list[tuple[str, ...]] = []
    def process(argv: tuple[str, ...], cwd: Path, **kwargs: object) -> ProcessOutcome:
        calls.append(argv)
        return ProcessOutcome(0, json.dumps({"is_open": True, "open_dates": ["2026-09-24", "2026-09-25"]}), "", False, 0)
    result = await ForwardRunner(ForwardOperationRepository(session_factory), process=process).run_once(
        config, date(2026, 9, 25), datetime(2026, 9, 25, 13, 15, tzinfo=UTC))
    assert result.error_code == "DAILY_EXECUTION_HISTORY_GAP"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_schema_error_is_permanent_not_automatically_retried(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    config = ForwardOperationConfig.model_validate(values(tmp_path))
    calls: list[tuple[str, ...]] = []
    def process(argv: tuple[str, ...], cwd: Path, **kwargs: object) -> ProcessOutcome:
        calls.append(argv)
        return ProcessOutcome(2, "", "DATASET_SCHEMA_MISMATCH", False, 0)
    runner = ForwardRunner(ForwardOperationRepository(session_factory), process=process)
    now = datetime(2026, 9, 28, 13, 15, tzinfo=UTC)
    first = await runner.run_once(config, now.date(), now)
    second = await runner.run_once(config, now.date(), now + timedelta(seconds=300))
    assert first.status == second.status == "FAILED"
    assert len(calls) == 1
