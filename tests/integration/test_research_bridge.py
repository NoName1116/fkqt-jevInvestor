import asyncio
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fkqt_jevinvestor.cli.main import main
from fkqt_jevinvestor.domain.market_features import MarketFeatureSnapshot
from fkqt_jevinvestor.ingestion.canonical import canonical_json, sha256_json
from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
from fkqt_jevinvestor.persistence.market_repository import MarketSnapshotRepository
from fkqt_jevinvestor.persistence.models import Base
from tests.unit.research_fixtures import inputs


async def test_export_is_readonly_and_cli_can_verify_and_build_a(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from fkqt_jevinvestor.research.export import ExportDay, ExportRequest, export_dataset

    snapshot, features, queue, execution = inputs()
    normalized: dict[str, MarketFeatureSnapshot] = {}
    for symbol, feature in features.items():
        values = {
            code: value.model_copy(update={"value": value.value.quantize(Decimal("0.00000001"))})
            for code, value in feature.values.items()
            if value.value is not None
        }
        draft = feature.model_copy(update={"values": values, "content_hash": ""})
        normalized[symbol] = draft.model_copy(update={"content_hash": sha256_json(draft)})
    snapshot_path = MarketSnapshotStore(tmp_path / "snapshots").save(snapshot)
    queue_path, execution_path = tmp_path / "queue.json", tmp_path / "execution.json"
    queue_path.write_text(canonical_json(queue), encoding="utf-8")
    execution_path.write_text(canonical_json(execution), encoding="utf-8")
    database = tmp_path / "source.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await MarketSnapshotRepository(async_sessionmaker(engine)).save_run_inputs(
        snapshot, normalized, str(snapshot_path)
    )
    await engine.dispose()
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    request = ExportRequest(
        database_path=database,
        dataset_id="research-fixture",
        days=(
            ExportDay(
                decision_date=snapshot.decision_date,
                market_snapshot_hash=snapshot.content_hash,
                candidate_queue_path=queue_path,
                execution_bundle_path=execution_path,
            ),
        ),
    )
    output = tmp_path / "published"
    manifest = export_dataset(request, output)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert await asyncio.to_thread(main, ["research", "verify", "--manifest", str(manifest)]) == 0
    assert '"days": 1' in capsys.readouterr().out


def test_cli_a_targets_is_available_without_keys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from fkqt_jevinvestor.research.dataset import DatasetStore
    from tests.unit.research_fixtures import cash
    from tests.unit.test_research_dataset import build_day

    manifest = DatasetStore(tmp_path / "datasets").publish("research-fixture", (build_day(),))
    portfolio = tmp_path / "portfolio.json"
    portfolio.write_text(canonical_json(cash()), encoding="utf-8")
    assert (
        main(
            [
                "research",
                "targets",
                "--manifest",
                str(manifest),
                "--arm",
                "A_RULE",
                "--date",
                "2026-09-18",
                "--portfolio",
                str(portfolio),
                "--audit-root",
                str(tmp_path / "audit"),
            ]
        )
        == 0
    )
    assert '"action": "ENTER"' in capsys.readouterr().out
    assert (
        main(
            [
                "research",
                "targets",
                "--manifest",
                str(manifest),
                "--arm",
                "B_LLM",
                "--date",
                "2026-09-18",
                "--portfolio",
                str(portfolio),
                "--audit-root",
                str(tmp_path / "audit"),
            ]
        )
        == 2
    )
    assert "RESEARCH_LIVE_MODEL_OPT_IN_REQUIRED" in capsys.readouterr().err


def test_cli_missing_database_schema_returns_sanitized_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import sqlite3

    from fkqt_jevinvestor.research.export import ExportDay, ExportRequest

    snapshot, _, _, _ = inputs()
    database = tmp_path / "empty.db"
    sqlite3.connect(database).close()
    request = ExportRequest(
        database_path=database,
        dataset_id="research-fixture",
        days=(
            ExportDay(
                decision_date=snapshot.decision_date,
                market_snapshot_hash=snapshot.content_hash,
                candidate_queue_path=tmp_path / "queue.json",
                execution_bundle_path=tmp_path / "execution.json",
            ),
        ),
    )
    path = tmp_path / "request.json"
    path.write_text(request.model_dump_json(), encoding="utf-8")
    assert (
        main(
            [
                "research",
                "export",
                "--request",
                str(path),
                "--output-root",
                str(tmp_path / "output"),
            ]
        )
        == 2
    )
    assert capsys.readouterr().err.strip() == "OperationalError"
    assert not (tmp_path / "output").exists()


async def test_export_formal_targets_preserves_exact_decision_portfolio(tmp_path: Path) -> None:
    from uuid import uuid4

    from fkqt_jevinvestor.domain.backtest import ExperimentArm
    from fkqt_jevinvestor.domain.jev_market import JevEvaluationCommand, JevScope
    from fkqt_jevinvestor.persistence.decision_repository import DecisionEvaluationRepository
    from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
    from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider
    from fkqt_jevinvestor.research.export import ExportDay, ExportRequest, export_dataset
    from fkqt_jevinvestor.research.recorded import FrozenRecordedTargetProvider
    from fkqt_jevinvestor.services.c_group_decision import (
        CGroupDecisionCommandV1,
        CGroupDecisionService,
    )
    from fkqt_jevinvestor.services.jev_market_service import JevRunEvaluationV1
    from fkqt_jevinvestor.services.jev_state_builder import build_jev_states
    from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio
    from fkqt_jevinvestor.services.position_sizing import PositionSizingConfigV1
    from tests.integration.test_c_group_decision import FakeDecisionProvider
    from tests.unit.research_fixtures import config
    from tests.unit.test_research_strategy import FakeJev

    snapshot, features, queue, execution = inputs()
    normalized: dict[str, MarketFeatureSnapshot] = {}
    for symbol, feature in features.items():
        draft = feature.model_copy(
            update={
                "content_hash": "",
                "values": {
                    c: v.model_copy(update={"value": v.value.quantize(Decimal("0.00000001"))})
                    for c, v in feature.values.items()
                    if v.value is not None
                },
            }
        )
        normalized[symbol] = draft.model_copy(update={"content_hash": sha256_json(draft)})
    database = tmp_path / "source.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = PortfolioRepository(factory)
    await repo.create(
        CreatePortfolio(portfolio_id="research", name="Research", initial_cash=Decimal(100000))
    )
    portfolio = await repo.get_state("research", as_of=snapshot.decision_date)
    snapshot_path = MarketSnapshotStore(tmp_path / "snapshots").save(snapshot)
    await MarketSnapshotRepository(factory).save_run_inputs(
        snapshot, normalized, str(snapshot_path)
    )
    u_state, s_states = build_jev_states(
        snapshot=snapshot,
        features=normalized,
        candidate_symbols=queue.ordered_symbols,
        candidate_limit=2,
        held_only_symbols=(),
    )
    fake = FakeJev()
    universe = await fake.evaluate(
        JevEvaluationCommand(
            scope=JevScope.UNIVERSE,
            state=u_state,
            provider_name="typesafe",
            provider_version="0.7.0",
            model_id="jev-latest",
        )
    )
    symbols = {
        s: await fake.evaluate(
            JevEvaluationCommand(
                scope=JevScope.SYMBOL,
                state=state,
                provider_name="typesafe",
                provider_version="0.7.0",
                model_id="jev-latest",
            )
        )
        for s, state in s_states.items()
    }
    run_id = uuid4()
    jev = JevRunEvaluationV1(
        run_id=run_id,
        decision_date=snapshot.decision_date,
        decision_cutoff=snapshot.decision_cutoff,
        planned_execution_date=snapshot.next_trade_date,
        candidate_universe_hash=snapshot.universe_snapshot_hash,
        market_snapshot_hash=snapshot.content_hash,
        universe=universe,
        symbols=symbols,
    )
    await CGroupDecisionService(
        provider=FakeDecisionProvider(),
        decision_repository=DecisionEvaluationRepository(factory),
        portfolio_repository=repo,
    ).evaluate_run(
        CGroupDecisionCommandV1(
            run_id=run_id,
            snapshot=snapshot,
            features=normalized,
            candidate_symbols=queue.ordered_symbols,
            candidate_limit=2,
            jev=jev,
            portfolio=portfolio,
            total_equity=portfolio.total_equity,
            recent_actions={},
            pending_orders={},
            provider_name="deepseek",
            provider_version="fake-v1",
            model_id="fake",
            sizing_config=PositionSizingConfigV1(),
        )
    )
    await engine.dispose()
    queue_path, execution_path = tmp_path / "queue.json", tmp_path / "execution.json"
    queue_path.write_text(canonical_json(queue), encoding="utf-8")
    execution_path.write_text(canonical_json(execution), encoding="utf-8")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    request = ExportRequest(
        database_path=database,
        dataset_id="research-fixture",
        days=(
            ExportDay(
                decision_date=snapshot.decision_date,
                market_snapshot_hash=snapshot.content_hash,
                candidate_queue_path=queue_path,
                execution_bundle_path=execution_path,
                run_id=str(run_id),
            ),
        ),
    )
    root = tmp_path / "output"
    manifest = export_dataset(request, root)
    replay = FrozenReplayDataProvider(DatasetStore(root), manifest)
    paths = list((manifest.parent / "recorded").glob("*.json"))
    assert len(paths) == 1
    result = await FrozenRecordedTargetProvider({snapshot.decision_date: paths[0]}).build_targets(
        config(ExperimentArm.C_JEV_LLM, replay.manifest.dataset_hash),
        (await replay.load_day(snapshot.decision_date)).decision_view(),
        portfolio,
    )
    assert [t.action.value for t in result.targets] == ["ENTER", "ENTER"]
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
