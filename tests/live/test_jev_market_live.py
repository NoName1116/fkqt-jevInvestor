import os
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from typesafe_sdk import AsyncTypeSafeClient

from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationCommand,
    JevEvaluationStatus,
    JevScope,
    JevStateHeaderV1,
    JevSymbolStateV1,
)
from fkqt_jevinvestor.providers.jev_market import TypeSafeJevMarketProvider


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_jev_market_contract_with_synthetic_state() -> None:
    api_key = os.getenv("TYPESAFE_API_KEY")
    if os.getenv("RUN_LIVE_JEV_TESTS") != "1" or not api_key:
        pytest.skip("set RUN_LIVE_JEV_TESTS=1 and TYPESAFE_API_KEY to run live Jev tests")

    model = os.getenv("TYPESAFE_JEV_MODEL", "jev-latest")
    provider_version = "typesafe-sdk-live"
    header = JevStateHeaderV1(
        decision_date=date(2026, 9, 18),
        decision_cutoff=datetime(2026, 9, 18, 15, tzinfo=UTC),
        candidate_universe_id="synthetic-live-contract-v1",
        candidate_universe_hash="a" * 64,
        candidate_limit=1,
        candidate_actual_size=1,
        market_snapshot_hash="b" * 64,
        feature_set_version="synthetic-market-features-v1",
    )
    state = JevSymbolStateV1(
        header=header,
        symbol="SYNTHETIC.TEST",
        security={
            "market": "SYNTHETIC",
            "board": "TEST",
            "listing_age_trading_days": 500,
            "trading_status": "TRADING",
            "is_st_or_delisting_risk": False,
            "is_initial_no_limit_period": False,
            "corporate_action_status": "NONE",
            "adjustment_mode": "QFQ",
            "available_feature_count": 20,
            "required_feature_count": 20,
        },
        features={
            "return_5d": Decimal("0.01000000"),
            "return_20d": Decimal("0.03000000"),
            "return_60d": Decimal("0.05000000"),
            "close_vs_ma5": Decimal("0.00500000"),
            "close_vs_ma20": Decimal("0.01500000"),
            "close_vs_ma60": Decimal("0.02500000"),
            "ma5_slope_5d": Decimal("0.00200000"),
            "ma20_slope_5d": Decimal("0.00100000"),
            "realized_vol_20d": Decimal("0.18000000"),
            "atr_pct_14d": Decimal("0.02000000"),
            "downside_vol_20d": Decimal("0.12000000"),
            "distance_from_20d_high": Decimal("-0.03000000"),
            "distance_from_20d_low": Decimal("0.08000000"),
            "short_term_reversal_3d": Decimal("-0.00500000"),
            "volume_ratio_5d_20d": Decimal("1.10000000"),
            "amount_ratio_5d_20d": Decimal("1.15000000"),
            "turnover_pct": Decimal("0.02000000"),
            "liquidity_percentile": Decimal("0.70000000"),
            "return_20d_percentile": Decimal("0.65000000"),
            "volatility_percentile": Decimal("0.45000000"),
        },
        missing_reasons=(),
    )
    command = JevEvaluationCommand(
        scope=JevScope.SYMBOL,
        state=state,
        provider_name="typesafe",
        provider_version=provider_version,
        model_id=model,
    )

    async with AsyncTypeSafeClient(api_key=api_key, model=model) as client:
        provider = TypeSafeJevMarketProvider(
            client=client,
            model=model,
            provider_version=provider_version,
        )
        result = await provider.evaluate(command)

    assert result.status == JevEvaluationStatus.AVAILABLE
    assert len(result.results) == 5
    assert {item.question_id for item in result.results} == {
        "next_session_pnl",
        "profitability_5d",
        "drawdown_risk_5d",
        "payoff_asymmetry_5d",
        "data_sufficiency",
    }
    for item in result.results:
        assert all(value.is_finite() for value in item.distribution.values())
        assert abs(sum(item.distribution.values(), Decimal(0)) - Decimal(1)) <= Decimal(
            "0.000001"
        )

