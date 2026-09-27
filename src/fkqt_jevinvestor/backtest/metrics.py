"""回测逐日记录与汇总指标。

纯计算模块，只依赖标准库与 fkqt_jevinvestor.domain 的现有类型。不访问网络、
数据库或任何外部服务，不使用当前时间或随机数；所有运算走 Decimal，不经过
float，结果确定可复现。
"""

import hashlib
import json
from decimal import ROUND_HALF_EVEN, Decimal

from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
    BacktestExecutionResult,
    BacktestSummary,
    DailyBacktestRecord,
)

_RATIO_QUANTUM: Decimal = Decimal("0.00000001")
_AMOUNT_QUANTUM: Decimal = Decimal("0.0001")
_TRADING_DAYS_PER_YEAR: Decimal = Decimal(252)
_SQRT_TRADING_DAYS: Decimal = _TRADING_DAYS_PER_YEAR.sqrt()


def _canonical_json(payload: object) -> str:
    """规范化 JSON。参数必须与 serialization.canonical_result_json 完全一致。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    """把 value 量化到 quantum 指定的位数，显式使用 ROUND_HALF_EVEN。"""
    return value.quantize(quantum, rounding=ROUND_HALF_EVEN)


def _mean(values: list[Decimal]) -> Decimal:
    """算术平均；调用方保证 values 非空。"""
    return sum(values, Decimal(0)) / Decimal(len(values))


def _sample_std(values: list[Decimal]) -> Decimal:
    """样本标准差，分母 n-1；调用方保证 len(values) >= 2。"""
    mean = _mean(values)
    variance = sum((value - mean) ** 2 for value in values) / (Decimal(len(values)) - 1)
    return variance.sqrt()


def _sharpe(returns: list[Decimal]) -> Decimal | None:
    """夏普比率；收益样本少于 2 条或标准差为零时返回 None。"""
    if len(returns) < 2:
        return None
    std = _sample_std(returns)
    if std == 0:
        return None
    return _quantize(_mean(returns) / std * _SQRT_TRADING_DAYS, _RATIO_QUANTUM)


def _sortino(returns: list[Decimal]) -> Decimal | None:
    """Sortino 比率；负收益样本少于 2 条或其标准差为零时返回 None。"""
    negatives = [value for value in returns if value < 0]
    if len(negatives) < 2:
        return None
    std = _sample_std(negatives)
    if std == 0:
        return None
    return _quantize(_mean(returns) / std * _SQRT_TRADING_DAYS, _RATIO_QUANTUM)


def build_daily_record(
    previous_equity: Decimal,
    initial_cash: Decimal,
    running_peak_equity: Decimal,
    execution: BacktestExecutionResult,
) -> DailyBacktestRecord:
    """把一次执行结果转成一条逐日记录。

    换手率分母是成交前权益 previous_equity，不是 execution.total_equity。
    cumulative_return 与 drawdown 由本函数按初始资金与运行峰值算出。
    """
    daily_return = execution.total_equity / previous_equity - Decimal(1)
    turnover = execution.gross_traded_value / previous_equity
    cumulative_return = execution.total_equity / initial_cash - Decimal(1)
    peak = max(running_peak_equity, execution.total_equity)
    drawdown = execution.total_equity / peak - Decimal(1)

    return DailyBacktestRecord(
        decision_date=execution.decision_date,
        execution_date=execution.execution_date,
        total_equity=execution.total_equity,
        cash_balance=execution.cash_balance,
        market_value=execution.market_value,
        daily_return=_quantize(daily_return, _RATIO_QUANTUM),
        cumulative_return=_quantize(cumulative_return, _RATIO_QUANTUM),
        drawdown=_quantize(drawdown, _RATIO_QUANTUM),
        turnover=_quantize(turnover, _RATIO_QUANTUM),
        fees=_quantize(execution.total_fees, _AMOUNT_QUANTUM),
        submitted_orders=execution.submitted_orders,
        filled_orders=execution.filled_orders,
        rejected_orders=execution.rejected_orders,
    )


def summarize(
    config: BacktestConfig,
    records: tuple[DailyBacktestRecord, ...],
) -> BacktestSummary:
    """把逐日记录汇总成一份 BacktestSummary，并计算 config_hash 与 result_hash。

    records 为空元组时抛 ValueError：引擎层已有"窗口为空就整批失败"的保证，
    这里收到空列表属于调用错误。
    """
    if not records:
        raise ValueError("summarize 需要至少一条逐日记录")

    trading_days = len(records)
    returns = [record.daily_return for record in records]

    cumulative_return = records[-1].cumulative_return
    annualized_return = (
        (Decimal(1) + cumulative_return).ln() * _TRADING_DAYS_PER_YEAR / Decimal(trading_days)
    ).exp() - Decimal(1)

    max_drawdown = abs(min(record.drawdown for record in records))

    sharpe_ratio = _sharpe(returns)
    sortino_ratio = _sortino(returns)

    turnover = sum((record.turnover for record in records), Decimal(0))
    total_fees = sum((record.fees for record in records), Decimal(0))
    submitted_orders = sum(record.submitted_orders for record in records)
    filled_orders = sum(record.filled_orders for record in records)
    rejected_orders = sum(record.rejected_orders for record in records)

    if submitted_orders == 0:
        fill_rate = None
    else:
        fill_rate = _quantize(Decimal(filled_orders) / Decimal(submitted_orders), _RATIO_QUANTUM)

    config_hash = _sha256(_canonical_json(config.model_dump(mode="json")))

    provisional = BacktestSummary(
        run_id=config.run_id,
        experiment_arm=config.experiment_arm,
        trading_days=trading_days,
        cumulative_return=cumulative_return,
        annualized_return=_quantize(annualized_return, _RATIO_QUANTUM),
        max_drawdown=_quantize(max_drawdown, _RATIO_QUANTUM),
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        turnover=_quantize(turnover, _RATIO_QUANTUM),
        total_fees=_quantize(total_fees, _AMOUNT_QUANTUM),
        submitted_orders=submitted_orders,
        filled_orders=filled_orders,
        rejected_orders=rejected_orders,
        fill_rate=fill_rate,
        config_hash=config_hash,
        result_hash="0" * 64,
    )

    # result_hash = 完整结果的规范化 JSON 的 SHA-256，计算前从 summary 排除 result_hash；
    # config_hash 保留在预映像中。
    summary_payload = provisional.model_dump(mode="json")
    summary_payload.pop("result_hash")
    preimage = {
        "config": config.model_dump(mode="json"),
        "daily_records": [record.model_dump(mode="json") for record in records],
        "summary": summary_payload,
    }
    result_hash = _sha256(_canonical_json(preimage))

    return provisional.model_copy(update={"result_hash": result_hash})
