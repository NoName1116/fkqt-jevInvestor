"""假零件自检测试。

只验证假零件本身的行为，不涉及回测引擎。
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from fkqt_jevinvestor.domain.backtest import ExperimentArm
from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot
from tests.backtest.fixtures import (
    DEFAULT_HASH,
    DEFAULT_SYMBOL,
    FakeExecutionPort,
    FakeReplayProvider,
    FakeTargetProvider,
    make_config,
    make_daily_bar,
    make_execution_market,
    make_execution_result,
    make_feature_snapshot,
    make_feature_value,
    make_portfolio,
    make_replay_day,
    make_security_state,
    make_target_batch,
    make_target_position,
)


async def test_shuffled_dates_are_not_ascending() -> None:
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    provider = FakeReplayProvider(
        days={d1: make_replay_day(decision_date=d1), d2: make_replay_day(decision_date=d2)},
        decision_dates=[d2, d1],
    )
    result = await provider.decision_dates(d1, d2)
    assert result == (d2, d1)
    assert result != tuple(sorted(result))


async def test_empty_window_is_empty() -> None:
    provider = FakeReplayProvider(days={})
    result = await provider.decision_dates(date(2026, 1, 5), date(2026, 1, 6))
    assert result == ()


def test_wrong_arm_batch_mismatches_config() -> None:
    config = make_config(experiment_arm=ExperimentArm.A_LLM)
    batch = make_target_batch(experiment_arm=ExperimentArm.B_JEV_LLM)
    assert batch.experiment_arm is not config.experiment_arm


def test_wrong_date_batch_is_configurable() -> None:
    config = make_config(start_date=date(2026, 1, 5))
    batch = make_target_batch(decision_date=date(2026, 1, 7))
    assert batch.decision_date != config.start_date


def test_wrong_sizing_version_is_configurable() -> None:
    config = make_config(sizing_version="sizing-v1")
    batch = make_target_batch(targets=(make_target_position(sizing_version="sizing-other"),))
    assert batch.targets[0].sizing_version != config.sizing_version


def test_future_feature_is_after_cutoff() -> None:
    cutoff = datetime(2026, 1, 5, 15, 0, 0, tzinfo=UTC)
    future = datetime(2026, 1, 6, 9, 30, 0, tzinfo=UTC)
    feature = make_feature_value(as_of=future)
    snapshot = make_feature_snapshot(values={"momentum_20": feature})
    day = make_replay_day(decision_cutoff=cutoff, features={DEFAULT_SYMBOL: snapshot})
    actual = day.features[DEFAULT_SYMBOL].values["momentum_20"]
    assert actual.as_of > cutoff


async def test_execution_calls_are_recorded() -> None:
    port = FakeExecutionPort(results=(make_execution_result(),))
    portfolio = make_portfolio()
    batch = make_target_batch()
    market: dict[str, MarketExecutionSnapshot] = {}
    await port.execute(portfolio, batch, market)
    await port.execute(portfolio, batch, market)
    assert port.calls == 2
    assert len(port.received_targets) == 2
    assert len(port.received_market_dates) == 2


async def test_execution_instances_are_isolated() -> None:
    first = FakeExecutionPort(results=(make_execution_result(),))
    second = FakeExecutionPort(results=(make_execution_result(),))
    market: dict[str, MarketExecutionSnapshot] = {}
    await first.execute(make_portfolio(), make_target_batch(), market)
    assert first.calls == 1
    assert second.calls == 0


async def test_target_provider_detects_execution_market_presence() -> None:
    provider = FakeTargetProvider(batch=make_target_batch())
    config = make_config()
    portfolio = make_portfolio()
    full_day = make_replay_day()
    await provider.build_targets(config, full_day, portfolio)
    await provider.build_targets(config, full_day.decision_view(), portfolio)
    assert provider.received_has_execution_market == [True, False]


def test_factories_produce_valid_data() -> None:
    assert make_daily_bar().open == Decimal("10.00")
    assert make_security_state().symbol == DEFAULT_SYMBOL
    assert make_feature_value().value == Decimal("0.05")
    assert make_feature_snapshot().content_hash == DEFAULT_HASH
    assert make_execution_market().trade_date == date(2026, 1, 6)

    day = make_replay_day()
    assert day.decision_date == date(2026, 1, 5)
    assert day.execution_market[DEFAULT_SYMBOL].trade_date == date(2026, 1, 6)

    portfolio = make_portfolio()
    assert portfolio.cash_balance == Decimal(1000000)
    assert portfolio.positions == ()

    target = make_target_position()
    assert target.target_position_pct == Decimal("0.1")
    assert target.sizing_version == "sizing-v1"

    batch = make_target_batch()
    assert batch.experiment_arm is ExperimentArm.A_LLM

    result = make_execution_result()
    assert result.total_equity == Decimal(1000000)

    config = make_config()
    assert config.initial_cash == Decimal(1000000)
    assert config.dataset_hash == DEFAULT_HASH


def test_replay_day_cutoff_follows_decision_date() -> None:
    day = make_replay_day(decision_date=date(2026, 1, 9))
    assert day.decision_cutoff.date() == date(2026, 1, 9)


async def test_warmup_dates_returns_count() -> None:
    d0 = date(2026, 1, 2)
    d1 = date(2026, 1, 3)
    d2 = date(2026, 1, 4)
    before = date(2026, 1, 5)
    provider = FakeReplayProvider(
        days={
            d0: make_replay_day(decision_date=d0),
            d1: make_replay_day(decision_date=d1),
            d2: make_replay_day(decision_date=d2),
        }
    )
    result = await provider.warmup_dates(before, 3)
    assert result == (d0, d1, d2)


async def test_warmup_dates_returns_fewer_when_short() -> None:
    d0 = date(2026, 1, 4)
    before = date(2026, 1, 5)
    provider = FakeReplayProvider(days={d0: make_replay_day(decision_date=d0)})
    result = await provider.warmup_dates(before, 3)
    assert result == (d0,)
    assert len(result) < 3


async def test_warmup_dates_zero_count_returns_empty() -> None:
    d0 = date(2026, 1, 4)
    before = date(2026, 1, 5)
    provider = FakeReplayProvider(days={d0: make_replay_day(decision_date=d0)})
    result = await provider.warmup_dates(before, 0)
    assert result == ()
