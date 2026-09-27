"""回测指标公式测试：不涉及引擎循环，直接调用 build_daily_record 与 summarize。

期望值全部按规格手工计算，不依赖实现输出。
"""

from datetime import date
from decimal import Decimal

from fkqt_jevinvestor.backtest.metrics import build_daily_record, summarize
from fkqt_jevinvestor.domain.backtest import DailyBacktestRecord
from tests.backtest.fixtures import make_config, make_execution_result

_INITIAL_CASH = Decimal(1000000)


def _record(
    *,
    total_equity: Decimal,
    daily_return: Decimal,
    cumulative_return: Decimal,
    drawdown: Decimal = Decimal(0),
    turnover: Decimal = Decimal(0),
    fees: Decimal = Decimal(0),
    submitted_orders: int = 0,
    filled_orders: int = 0,
    rejected_orders: int = 0,
) -> DailyBacktestRecord:
    """构造一条逐日记录；现金取 0、市值取权益，使资产恒等式成立。"""
    return DailyBacktestRecord(
        decision_date=date(2026, 1, 5),
        execution_date=date(2026, 1, 6),
        total_equity=total_equity,
        cash_balance=Decimal(0),
        market_value=total_equity,
        daily_return=daily_return,
        cumulative_return=cumulative_return,
        drawdown=drawdown,
        turnover=turnover,
        fees=fees,
        submitted_orders=submitted_orders,
        filled_orders=filled_orders,
        rejected_orders=rejected_orders,
    )


def _spec_records() -> tuple[DailyBacktestRecord, ...]:
    """规格手算样例：初始资金 1,000,000，逐日权益 1,010,000 / 999,900 / 1,050,000。

    这些记录直接构造，用于 summarize 测试；cumulative_return 与 drawdown 是
    手算好的期望输入（build_daily_record 的计算另有专门测试）。
    """
    return (
        _record(
            total_equity=Decimal(1010000),
            daily_return=Decimal("0.01"),
            cumulative_return=Decimal("0.01"),
            drawdown=Decimal(0),
            turnover=Decimal("0.1"),
        ),
        _record(
            total_equity=Decimal(999900),
            daily_return=Decimal("-0.01"),
            cumulative_return=Decimal("-0.0001"),
            drawdown=Decimal("-0.01"),
        ),
        _record(
            total_equity=Decimal(1050000),
            daily_return=Decimal("0.05010501"),
            cumulative_return=Decimal("0.05"),
            drawdown=Decimal(0),
        ),
    )


def test_daily_return_is_exact() -> None:
    day1 = build_daily_record(
        Decimal(1000000),
        _INITIAL_CASH,
        Decimal(1000000),
        make_execution_result(total_equity=Decimal(1010000)),
    )
    day2 = build_daily_record(
        Decimal(1010000),
        _INITIAL_CASH,
        Decimal(1010000),
        make_execution_result(total_equity=Decimal(999900)),
    )
    day3 = build_daily_record(
        Decimal(999900),
        _INITIAL_CASH,
        Decimal(1010000),
        make_execution_result(total_equity=Decimal(1050000)),
    )
    assert day1.daily_return == Decimal("0.01")
    assert day2.daily_return == Decimal("-0.01")
    assert day3.daily_return == Decimal("0.05010501")


def test_cumulative_return_and_max_drawdown() -> None:
    summary = summarize(make_config(), _spec_records())
    assert summary.cumulative_return == Decimal("0.05")
    assert summary.max_drawdown == Decimal("0.01")


def test_cumulative_return_and_drawdown_from_builder() -> None:
    # 新高：回撤为 0，累计收益为正
    new_high = build_daily_record(
        Decimal(1000000),
        _INITIAL_CASH,
        Decimal(1000000),
        make_execution_result(total_equity=Decimal(1010000)),
    )
    assert new_high.cumulative_return == Decimal("0.01")
    assert new_high.drawdown == Decimal(0)

    # 回撤：回撤为负，累计收益为负
    drawdown_day = build_daily_record(
        Decimal(1010000),
        _INITIAL_CASH,
        Decimal(1010000),
        make_execution_result(total_equity=Decimal(999900)),
    )
    assert drawdown_day.cumulative_return == Decimal("-0.0001")
    assert drawdown_day.drawdown == Decimal("-0.01")


def test_turnover_uses_previous_equity() -> None:
    record = build_daily_record(
        Decimal(1000000),
        _INITIAL_CASH,
        Decimal(1000000),
        make_execution_result(total_equity=Decimal(1100000), gross_traded_value=Decimal(100000)),
    )
    assert record.turnover == Decimal("0.1")


def test_fill_rate() -> None:
    records = (
        _record(
            total_equity=Decimal(1000000),
            daily_return=Decimal(0),
            cumulative_return=Decimal(0),
            submitted_orders=4,
            filled_orders=3,
        ),
    )
    summary = summarize(make_config(), records)
    assert summary.fill_rate == Decimal("0.75")


def test_zero_volatility_returns_none() -> None:
    records = tuple(
        _record(
            total_equity=Decimal(1000000), daily_return=Decimal(0), cumulative_return=Decimal(0)
        )
        for _ in range(3)
    )
    summary = summarize(make_config(), records)
    assert summary.sharpe_ratio is None
    assert summary.sortino_ratio is None


def test_no_orders_fill_rate_none() -> None:
    records = tuple(
        _record(
            total_equity=Decimal(1000000), daily_return=Decimal(0), cumulative_return=Decimal(0)
        )
        for _ in range(2)
    )
    summary = summarize(make_config(), records)
    assert summary.fill_rate is None


def test_insufficient_downside_sortino_none() -> None:
    summary = summarize(make_config(), _spec_records())
    assert summary.sortino_ratio is None
    assert summary.sharpe_ratio is not None
    assert abs(summary.sharpe_ratio - Decimal("8.662197312483")) <= Decimal("1e-8")


def test_insufficient_samples_sharpe_none() -> None:
    records = (
        _record(
            total_equity=Decimal(1010000),
            daily_return=Decimal("0.01"),
            cumulative_return=Decimal("0.01"),
        ),
    )
    summary = summarize(make_config(), records)
    assert summary.sharpe_ratio is None


def test_annualized_return_uses_ln() -> None:
    summary = summarize(make_config(), _spec_records())
    assert abs(summary.annualized_return - Decimal("59.2422413758")) <= Decimal("1e-8")


def test_quantization_precision() -> None:
    record = build_daily_record(
        Decimal(999900),
        _INITIAL_CASH,
        Decimal(999900),
        make_execution_result(total_equity=Decimal(1050000), total_fees=Decimal("12.3400")),
    )
    assert record.daily_return == Decimal("0.05010501")
    assert record.daily_return.as_tuple().exponent == -8
    assert record.fees == Decimal("12.3400")
    assert record.fees.as_tuple().exponent == -4
