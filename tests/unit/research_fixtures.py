from datetime import UTC, date, datetime
from decimal import Decimal

from fkqt_jevinvestor.domain.backtest import BacktestConfig, ExperimentArm
from fkqt_jevinvestor.domain.candidate_queue import CandidateQueueV1
from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot, TradingDayStatus, TradingStatus
from fkqt_jevinvestor.domain.market_features import (
    FeatureValue,
    MarketFeatureSnapshot,
    MarketSnapshot,
)
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.ingestion.execution_bundle import ExecutionBundleV1
from fkqt_jevinvestor.services.jev_state_builder import REQUIRED_SYMBOL_FEATURES
from tests.integration.test_c_group_decision import _snapshot  # pyright: ignore[reportPrivateUsage]


def inputs() -> tuple[
    MarketSnapshot, dict[str, MarketFeatureSnapshot], CandidateQueueV1, ExecutionBundleV1
]:
    snapshot = _snapshot()
    snapshot = snapshot.model_copy(
        update={
            "content_hash": sha256_json(snapshot.model_dump(mode="json") | {"content_hash": ""})
        }
    )
    features: dict[str, MarketFeatureSnapshot] = {}
    for symbol in snapshot.daily_bars:
        values = {
            code: FeatureValue(
                feature_code=code,
                feature_version="market-features-v1",
                as_of=snapshot.decision_cutoff,
                lookback_window=20,
                value=Decimal("0.5") if code == "liquidity_percentile" else Decimal("0.02"),
                missing_reason=None,
                source_snapshot_hash=snapshot.content_hash,
            )
            for code in REQUIRED_SYMBOL_FEATURES
        }
        draft = MarketFeatureSnapshot(
            symbol=symbol,
            decision_date=snapshot.decision_date,
            values=values,
            content_hash="0" * 64,
        )
        features[symbol] = draft.model_copy(
            update={
                "content_hash": sha256_json(draft.model_dump(mode="json") | {"content_hash": ""})
            }
        )
    queue = CandidateQueueV1.create(
        trade_date=snapshot.decision_date,
        source_id="a" * 64,
        source_version="fixture-v1",
        source_content_hash="e" * 64,
        candidate_limit=2,
        ordered_symbols=("000001.SZ", "600000.SH"),
        created_at=snapshot.decision_cutoff,
    )
    market = {
        symbol: MarketExecutionSnapshot(
            symbol=symbol,
            trade_date=snapshot.next_trade_date,
            trading_day_status=TradingDayStatus.OPEN,
            trading_status=TradingStatus.TRADING,
            open_price=Decimal(10),
            unadjusted_close=Decimal(11),
            daily_amount_cny=Decimal(10000000),
            upper_limit_price=Decimal(12),
            lower_limit_price=Decimal(8),
        )
        for symbol in features
    }
    execution = ExecutionBundleV1.create(snapshot.next_trade_date, market)
    return snapshot, features, queue, execution


def cash() -> PortfolioState:
    return PortfolioState(
        portfolio_id="research",
        cash_balance=Decimal(100000),
        frozen_cash=Decimal(0),
        realized_pnl=Decimal(0),
        version=1,
    )


def two_days():
    from fkqt_jevinvestor.research.dataset import ResearchDayV1

    snapshot, features, queue, execution = inputs()
    first = ResearchDayV1(snapshot=snapshot, features=features, queue=queue, execution=execution)
    day, next_day = date(2026, 9, 21), date(2026, 9, 22)
    cutoff = datetime(2026, 9, 21, 15, tzinfo=UTC)
    snapshot2 = snapshot.model_copy(
        update={
            "snapshot_id": "market-2",
            "decision_date": day,
            "decision_cutoff": cutoff,
            "next_trade_date": next_day,
            "calendar_complete_through": next_day,
            "daily_bars": {
                s: (bars[0].model_copy(update={"trade_date": day, "close": Decimal(11)}),)
                for s, bars in snapshot.daily_bars.items()
            },
            "security_states": {
                s: state.model_copy(update={"trade_date": day})
                for s, state in snapshot.security_states.items()
            },
            "source_audits": tuple(
                a.model_copy(update={"data_cutoff": cutoff, "fetched_at": cutoff})
                for a in snapshot.source_audits
            ),
            "content_hash": "",
        }
    )
    snapshot2 = snapshot2.model_copy(update={"content_hash": sha256_json(snapshot2)})
    features2: dict[str, MarketFeatureSnapshot] = {}
    for symbol, feature in features.items():
        draft = feature.model_copy(
            update={
                "decision_date": day,
                "content_hash": "",
                "values": {
                    c: v.model_copy(
                        update={"as_of": cutoff, "source_snapshot_hash": snapshot2.content_hash}
                    )
                    for c, v in feature.values.items()
                },
            }
        )
        features2[symbol] = draft.model_copy(update={"content_hash": sha256_json(draft)})
    queue2 = CandidateQueueV1.create(
        trade_date=day,
        source_id=queue.source_id,
        source_version=queue.source_version,
        source_content_hash=queue.source_content_hash,
        candidate_limit=queue.candidate_limit,
        ordered_symbols=queue.ordered_symbols,
        created_at=cutoff,
    )
    execution2 = ExecutionBundleV1.create(
        next_day,
        {s: m.model_copy(update={"trade_date": next_day}) for s, m in execution.snapshots.items()},
    )
    return first, ResearchDayV1(
        snapshot=snapshot2, features=features2, queue=queue2, execution=execution2
    )


def config(arm: ExperimentArm, dataset_hash: str) -> BacktestConfig:
    return BacktestConfig(
        run_id="research-1",
        experiment_arm=arm,
        start_date=date(2026, 9, 18),
        end_date=date(2026, 9, 18),
        warmup_trading_days=0,
        initial_cash=Decimal(100000),
        benchmark_symbol="000300.SH",
        dataset_id="research-fixture",
        dataset_hash=dataset_hash,
        sizing_version="position-sizing-v1",
        execution_policy_version="execution-v1",
    )
