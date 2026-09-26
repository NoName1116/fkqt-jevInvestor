import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fkqt_jevinvestor.domain.backtest import ExperimentArm
from fkqt_jevinvestor.domain.decision import DecisionModelOutputV1, DecisionProviderResult
from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationCommand,
    JevEvaluationV1,
    JevScope,
    JevSymbolStateV1,
)
from fkqt_jevinvestor.providers.base import (
    JevMarketProvider,
    ProviderHealth,
    ProviderUnavailableError,
)
from fkqt_jevinvestor.research.models import ResearchLlmProvider, ResearchLlmRequest
from tests.unit.research_fixtures import cash, config
from tests.unit.test_decision_contracts import _jev  # pyright: ignore[reportPrivateUsage]
from tests.unit.test_research_dataset import build_day


class FakeLlm:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[ResearchLlmRequest] = []

    async def evaluate(self, request: ResearchLlmRequest) -> DecisionProviderResult:
        self.requests.append(request)
        assert "execution_market" not in request.payload
        if self.fail:
            raise ProviderUnavailableError("offline")
        output = DecisionModelOutputV1.model_validate(
            {
                "action": request.allowed_actions[0].value,
                "thesis": "冻结输入。",
                "invalidation": "趋势改变。",
            }
        )
        raw = output.model_dump_json()
        return DecisionProviderResult(
            output=output,
            raw_response_text=raw,
            raw_response_hash=hashlib.sha256(raw.encode()).hexdigest(),
            usage={"input_tokens": 7, "output_tokens": 3},
        )


class FakeJev:
    async def health(self) -> ProviderHealth:
        raise AssertionError("health is not needed")

    async def evaluate(self, command: JevEvaluationCommand) -> JevEvaluationV1:
        result = _jev(
            command.scope,
            symbol=command.state.symbol if isinstance(command.state, JevSymbolStateV1) else None,
            finished_at=datetime.now(UTC),
        )
        return result.model_copy(
            update={
                "formal_key": command.formal_key,
                "input_hash": command.input_hash,
                "model_id": command.model_id,
                "state_schema_version": command.state.header.state_schema_version,
            }
        )


def provider(
    tmp_path: Path,
    arm: ExperimentArm,
    llm: ResearchLlmProvider | None = None,
    jev: JevMarketProvider | None = None,
):
    from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider
    from fkqt_jevinvestor.research.strategy import ExperimentTargetProvider, ResearchStrategyConfig

    store = DatasetStore(tmp_path / "datasets")
    replay = FrozenReplayDataProvider(store, store.publish("research-fixture", (build_day(),)))
    return (
        ExperimentTargetProvider(
            replay, tmp_path / "audit", ResearchStrategyConfig(), llm=llm, jev=jev
        ),
        replay,
        config(arm, replay.manifest.dataset_hash),
    )


async def test_a_rule_and_d_jev_use_same_sizing(tmp_path: Path) -> None:
    a, replay, cfg = provider(tmp_path, ExperimentArm.A_RULE)
    day = (await replay.load_day(cfg.start_date)).decision_view()
    target = await a.build_targets(cfg, day, cash())
    assert [t.action.value for t in target.targets] == ["ENTER", "ENTER"]
    assert str(target.targets[0].target_position_pct) == "0.05000000"
    d, _, d_cfg = provider(tmp_path, ExperimentArm.D_JEV_DIRECT, jev=FakeJev())
    d_target = await d.build_targets(d_cfg, day, cash())
    assert [(t.symbol, t.action, t.target_position_pct) for t in d_target.targets] == [
        (t.symbol, t.action, t.target_position_pct) for t in target.targets
    ]


async def test_b_request_has_no_jev_and_cache_prevents_reevaluation(tmp_path: Path) -> None:
    fake = FakeLlm()
    strategy, replay, cfg = provider(tmp_path, ExperimentArm.B_LLM, llm=fake)
    day = (await replay.load_day(cfg.start_date)).decision_view()
    first = await strategy.build_targets(cfg, day, cash())
    assert first.targets[0].action.value == "ENTER"
    assert all("jev" not in str(request.payload).lower() for request in fake.requests)
    fake.fail = True
    assert await strategy.build_targets(cfg, day, cash()) == first
    fresh, _, _ = provider(tmp_path, ExperimentArm.B_LLM, llm=FakeLlm(True))
    assert await fresh.build_targets(cfg, day, cash()) == first


async def test_c_calls_jev_once_for_universe_and_keeps_it_out_of_b(tmp_path: Path) -> None:
    strategy, replay, cfg = provider(
        tmp_path, ExperimentArm.C_JEV_LLM, llm=FakeLlm(), jev=FakeJev()
    )
    day = (await replay.load_day(cfg.start_date)).decision_view()
    result = await strategy.build_targets(cfg, day, cash())
    assert result.targets[0].action.value == "ENTER"
    from fkqt_jevinvestor.research.audit import FrozenJevCall

    calls = [
        FrozenJevCall.model_validate_json(p.read_text(encoding="utf-8"))
        for p in (tmp_path / "audit" / "jev").glob("*.json")
    ]
    assert sum(c.command.scope is JevScope.UNIVERSE for c in calls) == 1
    assert sum(c.command.scope is JevScope.SYMBOL for c in calls) == 2
    for call in calls:
        assert "portfolio" not in str(call.command.state.model_dump())


async def test_failure_is_no_signal_and_invalid_arm_is_rejected(tmp_path: Path) -> None:
    strategy, replay, cfg = provider(tmp_path, ExperimentArm.B_LLM, llm=FakeLlm(True))
    day = (await replay.load_day(cfg.start_date)).decision_view()
    result = await strategy.build_targets(cfg, day, cash())
    assert [t.action.value for t in result.targets] == ["NO_SIGNAL", "NO_SIGNAL"]
    assert sum(t.target_position_pct for t in result.targets) == 0
    with pytest.raises(ValueError, match="ARM_UNSUPPORTED"):
        await strategy.build_targets(
            cfg.model_copy(update={"experiment_arm": ExperimentArm.A_LLM}), day, cash()
        )
