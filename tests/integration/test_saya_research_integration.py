from datetime import date
from decimal import Decimal
from pathlib import Path

from fkqt_jevinvestor.backtest.runner import run_experiment_arms
from fkqt_jevinvestor.backtest.serialization import write_result
from fkqt_jevinvestor.domain.backtest import ExperimentArm
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.research.audit import ExperimentArtifact
from fkqt_jevinvestor.research.comparison import compare_results
from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider
from fkqt_jevinvestor.research.execution import ResearchExecutionPort
from fkqt_jevinvestor.research.models import ResearchStrategyConfig
from fkqt_jevinvestor.research.strategy import ExperimentTargetProvider
from tests.unit.research_fixtures import config, two_days
from tests.unit.test_research_strategy import FakeJev, FakeLlm


async def test_saya_engine_four_arms_real_research_ports_and_frozen_replay(tmp_path: Path) -> None:
    days = two_days()
    store = DatasetStore(tmp_path / "datasets")
    replay = FrozenReplayDataProvider(store, store.publish("research-fixture", days))
    arms = (
        ExperimentArm.A_RULE,
        ExperimentArm.B_LLM,
        ExperimentArm.C_JEV_LLM,
        ExperimentArm.D_JEV_DIRECT,
    )
    configs = tuple(
        config(arm, replay.manifest.dataset_hash).model_copy(
            update={
                "run_id": f"saya-{arm.value}",
                "end_date": date(2026, 9, 21),
            }
        )
        for arm in arms
    )
    llm = FakeLlm()
    providers = {
        arm: ExperimentTargetProvider(
            replay,
            tmp_path / "audit",
            ResearchStrategyConfig(),
            llm=llm,
            jev=FakeJev(),
        )
        for arm in arms
    }
    results = await run_experiment_arms(
        configs,
        replay,
        providers,
        lambda: ResearchExecutionPort(replay, replay.manifest.execution_policy),
    )
    assert len(results) == 4
    for result in results.values():
        write_result(result, tmp_path / f"{result.config.experiment_arm.value}.json")
        assert len(result.daily_records) == 2
        for record in result.daily_records:
            assert record.total_equity == record.cash_balance + record.market_value
        assert result.summary.config_hash == sha256_json(result.config)
        payload = result.model_dump(mode="json")
        payload["summary"].pop("result_hash")
        assert result.summary.result_hash == sha256_json(payload)
    report = compare_results(tuple(results.values()), audit_root=tmp_path / "audit")
    assert report.verification_status == "AUDIT_VERIFIED"
    assert all(len(refs) == 2 for refs in report.audit_hashes.values())
    assert len(llm.requests) == 8
    for request in llm.requests:
        assert request.symbol != "000002.SZ"
        if request.decision_date == date(2026, 9, 21):
            assert len(request.payload["portfolio"]["positions"]) == 2
            assert all(
                p["holding_trading_days"] == 1 for p in request.payload["portfolio"]["positions"]
            )
            assert request.payload["recent_actions"]
    artifacts = [
        ExperimentArtifact.model_validate_json(p.read_text(encoding="utf-8"))
        for p in (tmp_path / "audit" / "experiments").glob("*.json")
    ]
    assert len(artifacts) == 8
    assert all(len(a.targets.targets) == 2 for a in artifacts)
    llm.fail = True
    replayed = await run_experiment_arms(
        configs,
        replay,
        {
            arm: ExperimentTargetProvider(
                replay,
                tmp_path / "audit",
                ResearchStrategyConfig(),
                replay_only=True,
            )
            for arm in arms
        },
        lambda: ResearchExecutionPort(replay, replay.manifest.execution_policy),
    )
    assert replayed == results
    assert len(llm.requests) == 8


async def test_saya_engine_preserves_held_only_after_queue_rotation(tmp_path: Path) -> None:
    from fkqt_jevinvestor.backtest.engine import BacktestEngine
    from fkqt_jevinvestor.domain.candidate_queue import CandidateQueueV1

    first, second = two_days()
    queue = CandidateQueueV1.create(
        trade_date=second.queue.trade_date,
        source_id=second.queue.source_id,
        source_version=second.queue.source_version,
        source_content_hash=second.queue.source_content_hash,
        candidate_limit=second.queue.candidate_limit,
        ordered_symbols=("600000.SH",),
        created_at=second.queue.created_at,
    )
    store = DatasetStore(tmp_path / "datasets")
    replay = FrozenReplayDataProvider(
        store,
        store.publish(
            "research-fixture",
            (
                first,
                second.model_copy(update={"queue": queue}),
            ),
        ),
    )
    cfg = config(ExperimentArm.B_LLM, replay.manifest.dataset_hash).model_copy(
        update={
            "run_id": "saya-held-only",
            "end_date": date(2026, 9, 21),
        }
    )
    llm = FakeLlm()
    targets = ExperimentTargetProvider(
        replay, tmp_path / "audit", ResearchStrategyConfig(), llm=llm
    )
    result = await BacktestEngine(
        replay,
        targets,
        ResearchExecutionPort(
            replay,
            replay.manifest.execution_policy,
        ),
    ).run(cfg)
    assert len(result.daily_records) == 2
    held = next(
        r for r in llm.requests if r.decision_date == cfg.end_date and r.symbol == "000001.SZ"
    )
    assert held.payload["membership"] == "HELD_ONLY"
    assert held.payload["allowed_actions"] == ["KEEP", "EXIT"]
    assert result.daily_records[1].market_value > Decimal(0)
