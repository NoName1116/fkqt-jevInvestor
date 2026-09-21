# 回测模块 Contributor 开发契约

日期：2026-09-21

负责人：沙耶

目标仓库：`fkqt-jevInvestor`

契约版本：`backtest-contract-v1`

状态：等待 Phase 2 Task 1 合并并打标签后开始实现

## 1. 任务目标

实现一个纯 Python、无外部服务依赖、可复现且防前视的 A 股日频回测模块。回测模块只负责按交易日推进冻结数据、调用统一目标仓位接口、调用统一虚拟执行端口并计算指标；不负责行情下载、技术特征、Jev、LLM、仓位公式和券商连接。

完成后，A/B/C/D 四个实验组通过不同 `TargetProvider` 使用同一回测引擎，保证数据、成本、成交规则和指标口径一致。

## 2. 开始开发的前置条件

以下条件全部满足后再创建分支：

| 条件 | 标准值 | 检查方法 |
|---|---|---|
| 独立仓库迁移 | Phase 0/1 完整离线测试通过 | 查看 `main` CI |
| 核心契约 | `backtest-contract-v1` 标签存在 | `git tag --list backtest-contract-v1` |
| 领域文件 | 下述 Entity 与 Protocol 已进入 `main` | 运行 import 测试 |
| GitHub 访问 | 已 Fork 公开仓库，或已接受 Collaborator 邀请 | 可向上游提交 Pull Request |
| Python 环境 | Python 3.12 | `python --version` |

使用 Collaborator 权限时创建分支：

```bash
git fetch origin --tags
git switch -c feature/backtest-engine backtest-contract-v1
```

使用 Fork 工作流时，先在 GitHub Fork 公开仓库，将个人 Fork 配置为 `origin`、主仓库配置为 `upstream`，再从 `upstream/backtest-contract-v1` 创建相同分支并向主仓库发 Pull Request。

不得从标签之前的提交开发，不得复制核心 Entity 到回测目录。

## 3. 文件所有权

### 3.1 沙耶可以修改

```text
src/fkqt_jevinvestor/backtest/__init__.py
src/fkqt_jevinvestor/backtest/engine.py
src/fkqt_jevinvestor/backtest/metrics.py
src/fkqt_jevinvestor/backtest/runner.py
src/fkqt_jevinvestor/backtest/serialization.py
tests/backtest/__init__.py
tests/backtest/fixtures.py
tests/backtest/test_engine.py
tests/backtest/test_metrics.py
tests/backtest/test_runner.py
tests/backtest/test_serialization.py
docs/runbooks/backtesting.md
```

### 3.2 沙耶不得在实现 PR 中修改

```text
src/fkqt_jevinvestor/domain/
src/fkqt_jevinvestor/ingestion/
src/fkqt_jevinvestor/providers/
src/fkqt_jevinvestor/services/
src/fkqt_jevinvestor/persistence/
migrations/
REQUIREMENTS.md
```

如果核心契约不能满足回测需求，先创建只改 Contract 的独立 Pull Request，说明失败用例和所需字段；Contract PR 合并后再 rebase 回测分支。禁止在回测 PR 中同时改变业务契约。

## 4. 核心团队提供的 Entity

以下代码由核心团队在 `src/fkqt_jevinvestor/domain/backtest.py` 提供，沙耶只 import，不复制。

```python
from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot
from fkqt_jevinvestor.domain.market_features import MarketFeatureSnapshot
from fkqt_jevinvestor.domain.portfolio import PortfolioState


class ExperimentArm(StrEnum):
    A_LLM = "A_LLM"
    B_JEV_LLM = "B_JEV_LLM"
    C_JEV_DIRECT = "C_JEV_DIRECT"
    D_RULE = "D_RULE"


class DecisionAction(StrEnum):
    ENTER = "ENTER"
    KEEP = "KEEP"
    EXIT = "EXIT"
    AVOID = "AVOID"
    NO_SIGNAL = "NO_SIGNAL"


class TargetPosition(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    action: DecisionAction
    target_position_pct: Decimal = Field(ge=0, le=1)
    sizing_version: str
    sizing_input_hash: str = Field(min_length=64, max_length=64)


class TargetPositionBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_date: date
    planned_execution_date: date
    experiment_arm: ExperimentArm
    targets: tuple[TargetPosition, ...]
    input_hash: str = Field(min_length=64, max_length=64)


class ReplayDay(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_date: date
    decision_cutoff: datetime
    planned_execution_date: date
    universe_snapshot_hash: str = Field(min_length=64, max_length=64)
    market_snapshot_hash: str = Field(min_length=64, max_length=64)
    feature_snapshot_hash: str = Field(min_length=64, max_length=64)
    features: Mapping[str, MarketFeatureSnapshot]
    execution_market: Mapping[str, MarketExecutionSnapshot]


class BacktestConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    experiment_arm: ExperimentArm
    start_date: date
    end_date: date
    warmup_trading_days: int = Field(ge=0)
    initial_cash: Decimal = Field(gt=0)
    benchmark_symbol: str
    dataset_id: str
    dataset_hash: str = Field(min_length=64, max_length=64)
    execution_policy_version: str
    sizing_version: str


class DailyBacktestRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_date: date
    execution_date: date
    total_equity: Decimal
    cash_balance: Decimal
    market_value: Decimal
    daily_return: Decimal
    cumulative_return: Decimal
    drawdown: Decimal
    turnover: Decimal
    fees: Decimal
    submitted_orders: int = Field(ge=0)
    filled_orders: int = Field(ge=0)
    rejected_orders: int = Field(ge=0)


class BacktestExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_date: date
    execution_date: date
    portfolio_after: PortfolioState
    total_equity: Decimal = Field(gt=0)
    cash_balance: Decimal = Field(ge=0)
    market_value: Decimal = Field(ge=0)
    gross_traded_value: Decimal = Field(ge=0)
    total_fees: Decimal = Field(ge=0)
    submitted_orders: int = Field(ge=0)
    filled_orders: int = Field(ge=0)
    rejected_orders: int = Field(ge=0)


class BacktestSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    experiment_arm: ExperimentArm
    trading_days: int = Field(ge=0)
    cumulative_return: Decimal
    annualized_return: Decimal
    max_drawdown: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    turnover: Decimal
    total_fees: Decimal
    submitted_orders: int = Field(ge=0)
    filled_orders: int = Field(ge=0)
    rejected_orders: int = Field(ge=0)
    fill_rate: Decimal | None
    config_hash: str = Field(min_length=64, max_length=64)
    result_hash: str = Field(min_length=64, max_length=64)


class BacktestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    config: BacktestConfig
    daily_records: tuple[DailyBacktestRecord, ...]
    summary: BacktestSummary


class ReplayDataProvider(Protocol):
    async def decision_dates(self, start: date, end: date) -> tuple[date, ...]: ...

    async def load_day(self, decision_date: date) -> ReplayDay: ...


class TargetProvider(Protocol):
    async def build_targets(
        self,
        config: BacktestConfig,
        day: ReplayDay,
        portfolio: PortfolioState,
    ) -> TargetPositionBatch: ...


class ExecutionPort(Protocol):
    async def execute(
        self,
        portfolio: PortfolioState,
        targets: TargetPositionBatch,
        market: Mapping[str, MarketExecutionSnapshot],
    ) -> BacktestExecutionResult: ...
```

`backtest-contract-v1` 标签中的实际类型与本节必须逐字段一致；如果打标签前发现命名冲突，核心负责人必须同时修正文档和 Entity，禁止让 Contributor 自行猜测映射。

## 5. 回测引擎职责

`BacktestEngine.run()` 必须执行以下顺序：

1. 校验 `start_date <= end_date`。
2. 从 `ReplayDataProvider` 取得按升序排列且无重复的决策日。
3. 加载预热期数据，但预热期不得产生订单、收益或指标记录。
4. 对每个正式决策日读取一个 `ReplayDay`。
5. 校验 `decision_date < planned_execution_date`。
6. 校验 `decision_cutoff.date() == decision_date`。
7. 校验所有特征 `as_of <= decision_cutoff`。
8. 调用一次 `TargetProvider.build_targets()`。
9. 校验目标批次日期、实验组和 `sizing_version` 与配置一致。
10. 调用一次 `ExecutionPort.execute()`，只传入 D+1 `execution_market`。
11. 使用实际成交后组合状态生成 `DailyBacktestRecord`。
12. 全部交易日结束后计算 `BacktestSummary`。
13. 使用 canonical JSON 计算 `config_hash` 和 `result_hash`。

遇到日期不连续、未来特征、目标批次错组或错日时整次回测失败，不跳过错误日期继续。

## 6. 必须实现的公开接口

`src/fkqt_jevinvestor/backtest/engine.py`：

```python
class BacktestError(RuntimeError):
    pass


class BacktestEngine:
    def __init__(
        self,
        replay: ReplayDataProvider,
        targets: TargetProvider,
        execution: ExecutionPort,
    ) -> None: ...

    async def run(self, config: BacktestConfig) -> BacktestResult: ...
```

稳定错误码：

```text
INVALID_BACKTEST_WINDOW
DECISION_DATES_NOT_STRICTLY_ORDERED
REPLAY_DAY_DATE_MISMATCH
POINT_IN_TIME_VIOLATION
TARGET_DATE_MISMATCH
TARGET_EXPERIMENT_ARM_MISMATCH
TARGET_SIZING_VERSION_MISMATCH
EMPTY_BACKTEST_WINDOW
```

`src/fkqt_jevinvestor/backtest/metrics.py`：

```python
def build_daily_record(
    previous_equity: Decimal,
    execution: BacktestExecutionResult,
) -> DailyBacktestRecord: ...


def summarize(
    config: BacktestConfig,
    records: tuple[DailyBacktestRecord, ...],
) -> BacktestSummary: ...
```

`src/fkqt_jevinvestor/backtest/runner.py`：

```python
async def run_experiment_arms(
    configs: tuple[BacktestConfig, ...],
    replay: ReplayDataProvider,
    target_providers: Mapping[ExperimentArm, TargetProvider],
    execution_factory: Callable[[], ExecutionPort],
) -> Mapping[ExperimentArm, BacktestResult]: ...
```

`src/fkqt_jevinvestor/backtest/serialization.py`：

```python
def canonical_result_json(result: BacktestResult) -> str: ...


def write_result(result: BacktestResult, destination: Path) -> None: ...
```

JSON 必须 UTF-8、`ensure_ascii=False`、键排序、Decimal 序列化为字符串、日期使用 ISO 8601，文件末尾包含一个换行符。

## 7. 指标口径

| 指标 | 公式 |
|---|---|
| `daily_return` | `total_equity_t / total_equity_t-1 - 1` |
| `cumulative_return` | `total_equity_t / initial_cash - 1` |
| `annualized_return` | `(1 + cumulative_return) ** (252 / trading_days) - 1` |
| `drawdown` | `total_equity_t / running_peak_equity - 1` |
| `max_drawdown` | 全部 `drawdown` 的最小值绝对值，非负数 |
| `turnover` | 当日成交绝对金额之和 / 当日成交前总资产 |
| `sharpe_ratio` | `mean(daily_return) / sample_std(daily_return) * sqrt(252)` |
| `sortino_ratio` | `mean(daily_return) / sample_std(negative_daily_return) * sqrt(252)` |
| `fill_rate` | `filled_orders / submitted_orders` |

少于 2 个收益样本、标准差为零或下行样本不足 2 个时，对应风险比率返回 `None`，不得返回无穷大。所有最终数值量化为 8 位小数；金额沿用执行域的 4 位小数。

年化收益不得把 Decimal 隐式转为 float，使用 `(ln(1 + cumulative_return) * Decimal(252) / trading_days).exp() - 1`；标准差和风险比率使用 Decimal 的 `sqrt()`。汇总 `turnover` 为逐日 turnover 之和，`total_fees` 为逐日费用之和。

## 8. A/B/C/D 公平性规则

`run_experiment_arms()` 在执行前必须断言所有配置具有完全相同的：

- `start_date`；
- `end_date`；
- `warmup_trading_days`；
- `initial_cash`；
- `benchmark_symbol`；
- `dataset_id`；
- `dataset_hash`；
- `execution_policy_version`；
- `sizing_version`。

只有 `run_id` 和 `experiment_arm` 可以不同。每个实验组必须取得独立的 `ExecutionPort`，禁止共享可变组合状态。缺失某个实验组的 `TargetProvider` 时整批拒绝，不运行剩余组。

## 9. 必须通过的测试

| 测试 | 输入 | 期望 |
|---|---|---|
| 两交易日正常回测 | D1、D2 冻结数据 | 2 条逐日记录，资产恒等式成立 |
| 预热期 | 60 天预热 + 2 天正式窗口 | 只生成 2 条记录和 2 次 Target 调用 |
| 周末与节假日 | 周五决策、下周有效交易日执行 | 使用 Provider 给出的 D+1，不用自然日加一 |
| 未来特征 | `feature.as_of > decision_cutoff` | `POINT_IN_TIME_VIOLATION` |
| 错日目标 | Target 批次日期与 ReplayDay 不同 | `TARGET_DATE_MISMATCH` |
| 错实验组 | B 配置收到 A 批次 | `TARGET_EXPERIMENT_ARM_MISMATCH` |
| 空窗口 | Provider 返回 0 个正式决策日 | `EMPTY_BACKTEST_WINDOW` |
| 重放一致 | 相同 Config 和 Fixture 运行两次 | canonical JSON 与 `result_hash` 完全相同 |
| 零波动收益 | 每日收益完全相同 | Sharpe 为 `None` |
| 无订单 | `submitted_orders == 0` | `fill_rate` 为 `None` |
| A/B/C/D 公平性 | B 使用不同 dataset hash | 整批拒绝，四组均不运行 |
| 状态隔离 | 四组各执行一次 | 四个 ExecutionPort 实例且状态互不影响 |

每个测试使用内存 Fake，不访问网络、磁盘数据库、FKQT、Jev 或 LLM。

## 10. 开发顺序与提交

1. `测试：固定回测时间推进与防前视行为`
2. `功能：实现日频回测事件循环`
3. `测试：固定回测指标边界`
4. `功能：实现回测指标累计`
5. `测试：固定多实验公平性与状态隔离`
6. `功能：实现 A/B/C/D 回测运行器`
7. `功能：增加回测结果规范化输出`
8. `文档：补充回测运行手册`

每次出现测试失败，只重跑失败的 node ID；全部实现结束后运行一次：

```bash
uv run ruff check src/fkqt_jevinvestor/backtest tests/backtest
uv run pyright
uv run pytest tests/backtest -q
```

## 11. Pull Request 交付要求

PR 标题：

```text
功能：实现防前视日频回测引擎
```

PR 描述必须列出：

- 基于的 `backtest-contract-v1` commit SHA；
- 修改文件清单；
- 测试命令与实际通过数量；
- 两次重放的 `result_hash`；
- A/B/C/D 公平性失败测试证据；
- 已知问题；
- 明确声明未修改核心 Entity、迁移和 Provider。

合并前由核心负责人检查 Contract import、Point-in-Time、指标公式、四组状态隔离和 canonical JSON。不得 squash 掉所有测试与实现边界；保留有意义的提交序列。

## 12. 自检清单

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| 修改范围 | 仅允许目录 | `git diff --name-only backtest-contract-v1...HEAD` | 必须 |
| FKQT 调用 | 0 次 | 搜索 `FKQT` URL、`agent.` import | 必须 |
| 模型调用 | 0 次 | 搜索 `typesafe`、LLM Client | 必须 |
| 数据库调用 | 0 次 | 搜索 SQLAlchemy import | 必须 |
| 防前视 | 未来特征整批失败 | 指定失败测试 | 必须 |
| 自然日推算 | 0 处 | 搜索 `timedelta(days=1)` | 必须 |
| 资产恒等式 | 误差不超过 0.01 CNY | 两日集成 Fixture | 必须 |
| 重放一致性 | JSON 与哈希逐字节相同 | 双运行测试 | 必须 |
| 实验公平性 | 9 个共享字段一致 | 参数不一致测试 | 必须 |
| 完整测试 | Ruff、Pyright、backtest tests 全通过 | PR CI | 必须 |

## 13. 已知边界

- 回测引擎不负责生成 A/B/C/D 的真实 Target；核心项目在 Phase 3/4 提供相应 `TargetProvider`。
- 初期可以使用 Fake TargetProvider 完成全部引擎开发。
- Phase 2 文件 Adapter 合并后，核心负责人负责增加真实冻结快照的集成测试。
- 第一版不实现并行进程、分布式回测、参数网格搜索、GPU、分钟级撮合和真实券商回放。
