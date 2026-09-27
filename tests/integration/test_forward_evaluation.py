from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from fkqt_jevinvestor.domain.market_features import MarketSnapshot
from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.persistence.models import ForwardRecoveryRecord, ForwardStageRecord
from tests.integration import test_portfolio_repository as fixtures
from tests.unit.test_forward_operation_config import values

session_factory = fixtures.session_factory


@pytest.mark.asyncio
async def test_empty_report_is_read_only_unknown_and_content_addressed(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    from fkqt_jevinvestor.operations.evaluation import build_report, list_diagnostics, write_report

    config = ForwardOperationConfig.model_validate({**values(tmp_path), "start_date": date(2026, 9, 21)})
    report = await build_report(session_factory, config, date(2026, 9, 21), date(2026, 9, 22))
    assert report["completeness"]["coverage"] is None
    assert report["portfolio"]["interval_return"] is None
    assert report["probabilities"]["status"] == "PENDING_LABELS"
    assert report["providers"] == []
    paths = write_report(report, tmp_path / "reports")
    assert paths == write_report(report, tmp_path / "reports")
    assert all(path.is_file() for path in paths)
    assert (await list_diagnostics(session_factory, config, date(2026, 9, 21)))["attempts"] == []
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ForwardRecoveryRecord)) == 0


@pytest.mark.asyncio
async def test_frozen_population_keeps_missing_decisions_in_denominator(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    from fkqt_jevinvestor.ingestion.canonical import sha256_json
    from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
    from fkqt_jevinvestor.operations.evaluation import build_report
    from fkqt_jevinvestor.persistence.jev_repository import JevEvaluationRepository
    from fkqt_jevinvestor.persistence.market_repository import MarketSnapshotRepository
    from tests.integration.test_market_repository import (
        _inputs,  # pyright: ignore[reportPrivateUsage]
    )
    from tests.unit.test_jev_market_provider import _command  # pyright: ignore[reportPrivateUsage]

    day = date(2026, 9, 18)
    config = ForwardOperationConfig.model_validate({**values(tmp_path), "start_date": day})
    snapshot, _ = _inputs()
    snapshot = snapshot.model_copy(update={"daily_bars": {"600000.SH": (), "000001.SZ": ()}})
    payload = snapshot.model_dump(mode="json")
    payload["content_hash"] = ""
    snapshot = snapshot.model_copy(update={"content_hash": sha256_json(payload)})
    path = MarketSnapshotStore(tmp_path / "snapshots").save(snapshot)
    await MarketSnapshotRepository(session_factory).save_run_inputs(snapshot, {}, str(path))
    command = _command()
    state = command.state.model_copy(update={"header": command.state.header.model_copy(update={"market_snapshot_hash": snapshot.content_hash})})
    command = command.model_copy(update={"state": state})
    run_id = uuid4()
    repository = JevEvaluationRepository(session_factory)
    claim = await repository.claim(run_id, command)
    from fkqt_jevinvestor.operations.run_identity import write_run_identity
    write_run_identity(config.data_root, {"portfolio_id": "p", "date": day.isoformat(), "run_id": str(run_id),
        "market_snapshot_hash": snapshot.content_hash, "required_symbols": ["600000.SH", "000001.SZ"],
        "operation_config_hash": config.content_hash()})
    async with session_factory.begin() as session:
        session.add(ForwardStageRecord(id="c" * 64, portfolio_id="p", trade_date=day, stage="DECIDE",
            config_hash=config.content_hash(), status="FAILED", attempts=1, input_hash=config.content_hash(),
            outcome_json={}))
    report = await build_report(session_factory, config, day, day)
    assert report["completeness"]["expected_count"] == 2
    assert report["completeness"]["coverage"] == "0E-8"
    assert report["completeness"]["daily"][0]["missing_symbols"] == ["000001.SZ", "600000.SH"]
    assert report["providers"][0]["billing_status"] == "UNKNOWN"
    assert report["providers"][0]["estimated_cost"] is None
    assert report["portfolio"]["interval_return"] is None
    from datetime import UTC, datetime
    from decimal import Decimal

    from fkqt_jevinvestor.providers.jev_market import TypeSafeJevMarketProvider
    from tests.unit.test_forward_labels import (  # pyright: ignore[reportPrivateUsage]
        _bars,  # pyright: ignore[reportPrivateUsage]
        _trade_state,  # pyright: ignore[reportPrivateUsage]
    )
    from tests.unit.test_jev_market_provider import (  # pyright: ignore[reportPrivateUsage]
        FakeClient,
        _valid_response,  # pyright: ignore[reportPrivateUsage]
    )

    response = _valid_response()
    for answer in response.choices.values():
        answer.probabilities = dict(zip(answer.probabilities, (Decimal("0.7"), Decimal("0.2"), Decimal("0.1")), strict=True))
    result = await TypeSafeJevMarketProvider(FakeClient(response), command.model_id, command.provider_version).evaluate(command)
    await repository.record_success(claim, result)
    bars = _bars()
    for i, bar in enumerate(bars):
        cutoff = datetime.combine(bar.trade_date, datetime.min.time(), UTC).replace(hour=15)
        future = snapshot.model_copy(update={"snapshot_id": f"future-{i}", "decision_date": bar.trade_date,
            "decision_cutoff": cutoff, "next_trade_date": bars[i + 1].trade_date if i < 4 else date(2026, 9, 28),
            "calendar_complete_through": date(2026, 9, 28), "daily_bars": {"600000.SH": bars[:i + 1]},
            "security_states": {"600000.SH": _trade_state(bar.trade_date)},
            "source_audits": tuple(audit.model_copy(update={"data_cutoff": cutoff, "fetched_at": cutoff}) for audit in snapshot.source_audits)})
        frozen = future.model_dump(mode="json")
        frozen["content_hash"] = ""
        future = future.model_copy(update={"content_hash": sha256_json(frozen)})
        ref = MarketSnapshotStore(tmp_path / "snapshots").save(future)
        await MarketSnapshotRepository(session_factory).save_run_inputs(future, {}, str(ref))
    scored = await build_report(session_factory, config, day, bars[-1].trade_date)
    next_session = next(group for group in scored["probabilities"]["groups"] if group["question_id"] == "next_session_pnl")
    assert next_session["sample_count"] == 1
    assert next_session["brier"] == "1.14000000"
    assert next_session["accuracy"] == "0E-8"
    assert scored["probabilities"]["status"] == "AVAILABLE"


def test_five_session_label_chain_uses_final_snapshot_adjustment_basis() -> None:
    from fkqt_jevinvestor.operations.evaluation_labels import mature_labels
    from tests.integration.test_market_repository import (
        _inputs,  # pyright: ignore[reportPrivateUsage]
    )
    from tests.unit.test_forward_labels import (  # pyright: ignore[reportPrivateUsage]
        _bars,  # pyright: ignore[reportPrivateUsage]
        _trade_state,  # pyright: ignore[reportPrivateUsage]
    )

    base, _ = _inputs()
    bars = _bars()
    chain: dict[date, list[MarketSnapshot]] = {}
    for i, bar in enumerate(bars):
        next_day = bars[i + 1].trade_date if i < 4 else date(2026, 9, 28)
        # 单日快照的前复权基准可能不同；D5 完整窗口才是同一价格口径。
        own_bars = bars if i == 4 else (bar.model_copy(update={"open": bar.open * 2}),)
        snapshot = base.model_copy(update={"decision_date": bar.trade_date, "next_trade_date": next_day,
            "daily_bars": {"600000.SH": own_bars}, "security_states": {"600000.SH": _trade_state(bar.trade_date)},
            "content_hash": str(i + 1) * 64})
        chain[bar.trade_date] = [snapshot]
    assert mature_labels(base, "600000.SH", chain, bars[-1].trade_date)["next_session_pnl"] == "FLAT"
    assert mature_labels(base, "600000.SH", chain, bars[-2].trade_date)["status"] == "PENDING_LABELS"
    chain[bars[2].trade_date] = []
    assert mature_labels(base, "600000.SH", chain, bars[-1].trade_date)["status"] == "LABEL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_runner_attests_mode_without_changing_formal_inputs(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from fkqt_jevinvestor.operations.evaluation import build_report
    from fkqt_jevinvestor.operations.process import ProcessOutcome
    from fkqt_jevinvestor.operations.runner import ForwardRunner
    from fkqt_jevinvestor.persistence.forward_operation_repository import ForwardOperationRepository

    day = date(2026, 9, 28)
    config = ForwardOperationConfig.model_validate(values(tmp_path))
    def closed(*args: object, **kwargs: object) -> ProcessOutcome:
        return ProcessOutcome(0, '{"is_open": false, "open_dates": []}', "", False, 0)
    runner = ForwardRunner(ForwardOperationRepository(session_factory), process=closed)
    assert (await runner.run_once(config, day, datetime(2026, 9, 29, 13, tzinfo=UTC))).status == "CLOSED"
    report = await build_report(session_factory, config, day, day)
    assert report["mode"] == "REAL_SOURCE_REPLAY"


@pytest.mark.asyncio
async def test_complete_cny_usage_can_reduce_interval_return(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    from decimal import Decimal
    from types import SimpleNamespace

    from fkqt_jevinvestor.operations.evaluation import build_report
    from fkqt_jevinvestor.operations.run_identity import write_run_identity
    from fkqt_jevinvestor.persistence.jev_repository import JevEvaluationRepository
    from fkqt_jevinvestor.persistence.models import NavRecord
    from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
    from fkqt_jevinvestor.providers.jev_market import TypeSafeJevMarketProvider
    from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio
    from tests.unit.test_jev_market_provider import (  # pyright: ignore[reportPrivateUsage]
        FakeClient,
        _command,  # pyright: ignore[reportPrivateUsage]
        _valid_response,  # pyright: ignore[reportPrivateUsage]
    )

    day = date(2026, 9, 18)
    config = ForwardOperationConfig.model_validate({**values(tmp_path), "start_date": day})
    await PortfolioRepository(session_factory).create(CreatePortfolio(portfolio_id="p", name="费用验收", initial_cash=Decimal(100)))
    command = _command()
    run_id = uuid4()
    repo = JevEvaluationRepository(session_factory)
    claim = await repo.claim(run_id, command)
    response = _valid_response()
    response.usage = SimpleNamespace(input_tokens=1000000, output_tokens=500000)
    result = await TypeSafeJevMarketProvider(FakeClient(response), command.model_id, command.provider_version).evaluate(command)
    await repo.record_success(claim, result)
    write_run_identity(config.data_root, {"portfolio_id": "p", "date": day.isoformat(), "run_id": str(run_id),
        "market_snapshot_hash": "b" * 64, "required_symbols": ["600000.SH"], "operation_config_hash": config.content_hash()})
    async with session_factory.begin() as session:
        session.add(ForwardStageRecord(id="c" * 64, portfolio_id="p", trade_date=day, stage="CALENDAR",
            config_hash=config.content_hash(), status="SUCCESS", attempts=1,
            outcome_json={"is_open": True, "open_dates": [day.isoformat()], "_run_mode": "REAL_SOURCE_REPLAY"}))
        session.add(NavRecord(id="nav-fee", portfolio_id="p", valuation_date=day, total_equity=Decimal(100), unit_nav=Decimal(1),
            daily_return=Decimal(0), cumulative_return=Decimal(0), max_drawdown=Decimal(0)))
    price = {"provider": command.provider_name, "model": command.model_id, "version": command.provider_version,
        "effective_from": "2026-09-01", "effective_to": "2026-09-30", "currency": "CNY",
        "input_per_million": "2", "output_per_million": "4"}
    report = await build_report(session_factory, config, day, day, [price])
    assert report["providers"][0]["estimated_cost"] == "4.00000000"
    assert report["portfolio"]["interval_return"] == "0E-8"
    assert report["portfolio"]["after_model_cost_return"] == "-0.04000000"
