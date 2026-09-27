# 回测运行手册

本手册记录回测模块的职责、运行方式、结果格式、错误码和已知边界。

2026-09-27 已将沙耶分支整合到本地开发分支，使用真实研究端口完成四组两日离线联合验收；不代表已更新GitHub main或证明模型有效。

## 1. 这个模块做什么

回测引擎负责按交易日推进冻结好的历史数据，调用目标仓位接口与虚拟执行端口，
累计逐日指标并输出规范化结果。它不负责行情下载、特征计算、模型调用或券商连接：
引擎不认识 FKQT、不调用 Jev/LLM、不连接数据库，只依赖三个 Protocol
（`ReplayDataProvider`、`TargetProvider`、`ExecutionPort`）和冻结快照。

## 2. 怎么跑

回测引擎是库，目前没有独立 CLI 入口。开发期验证用以下命令：

```cmd
uv run ruff check src tests
uv run pytest tests/backtest -q
uv run pyright
```

结果 JSON 由 `fkqt_jevinvestor.backtest.serialization` 的
`canonical_result_json`（生成字符串）与 `write_result`（写成文件）产出。

## 3. 结果文件的格式

`write_result` 写出的 JSON 具有以下性质：

- UTF-8 编码；
- 键按字典序排序；
- 金额与比例（Decimal）序列化为字符串；
- 日期使用 ISO 8601 格式（如 `2026-01-05`）；
- 末尾恰好一个换行；
- 不随平台转换换行：实现用字节写入，保证 Windows 与 Linux 写出的字节完全一致，
  否则同一份结果在两处写出的哈希会对不上。

两个哈希的覆盖范围：

- `config_hash`：`BacktestConfig` 全部字段的规范化 JSON 的 SHA-256；
- `result_hash`：完整 `BacktestResult` 的规范化 JSON 的 SHA-256，计算前从
  `summary` 中排除 `result_hash` 字段（`config_hash` 保留在预映像中）。

## 4. 错误码表

| 错误码 | 触发条件 |
|---|---|
| INVALID_BACKTEST_WINDOW | 起始日期晚于结束日期 |
| DECISION_DATES_NOT_STRICTLY_ORDERED | 决策日未严格升序或存在重复 |
| REPLAY_DAY_DATE_MISMATCH | 回放日的决策日期与请求不符 |
| EXECUTION_DATE_NOT_AFTER_DECISION_DATE | 计划执行日不晚于决策日 |
| DECISION_CUTOFF_DATE_MISMATCH | 截止时间不落在决策日当天 |
| POINT_IN_TIME_VIOLATION | 某特征值的 as_of 晚于决策截止时间 |
| TARGET_DATE_MISMATCH | 目标批次的决策日或计划执行日与当天不符 |
| TARGET_EXPERIMENT_ARM_MISMATCH | 目标批次的实验组与配置不符 |
| TARGET_SIZING_VERSION_MISMATCH | 目标仓位的 sizing_version 与配置不符 |
| TARGET_SYMBOL_COVERAGE_MISMATCH | 目标批次未恰好覆盖特征证券与当前持仓证券 |
| EMPTY_BACKTEST_WINDOW | 决策日为空 |
| INSUFFICIENT_WARMUP_DATA | 预热日不足、乱序、重复或晚于起始日 |
| PORTFOLIO_EQUITY_DEPLETED | 执行结果总权益归零 |
| REPLAY_CALENDAR_CHAIN_MISMATCH | 预热或正式日期链不满足前一日执行日等于下一决策日 |
| DECISION_DATES_OUTSIDE_WINDOW | Provider返回正式窗口外的日期 |
| EXECUTION_RESULT_IDENTITY_MISMATCH | 执行结果日期或组合身份与输入不同 |
| EXECUTION_RESULT_ACCOUNTING_MISMATCH | 执行资产恒等式、返回现金或组合估值不一致 |
| REPLAY_CANDIDATE_COVERAGE_MISMATCH | 可选候选队列证券缺当日特征 |

四组运行器在公平性检查失败时抛 `BacktestError`，消息以
`EXPERIMENT_CONFIG_MISMATCH` 开头。
重复组使用 EXPERIMENT_CONFIG_MISMATCH: DUPLICATE_ARM，在任何执行器创建或目标生成前整批拒绝。重复证券目标使用 TARGET_SYMBOL_COVERAGE_MISMATCH，在执行前拒绝，不使用集合去重掩盖重复。

## 5. 预热期与实现状态

预热期已实现：引擎按 `warmup_trading_days` 读取并校验起始日之前的预热日
（决策日一致、执行日在后、截止时间当天、特征不晚于截止时间），但预热期不产生
逐日记录、不调用目标生成器与执行端口、不重算特征、不推进权益与运行峰值。

累计收益与回撤由 `build_daily_record` 按初始资金与运行峰值计算；结果哈希由
`summarize` 计算。

## 6. 四组实验

- A_RULE：确定性特征 → 版本化趋势规则 → 确定性仓位
- B_LLM：确定性特征 → LLM 离散动作 → 确定性仓位
- C_JEV_LLM：确定性特征 → Jev 盈亏概率 → LLM 离散动作 → 确定性仓位（主链）
- D_JEV_DIRECT：确定性特征 → Jev 盈亏概率 → 版本化动作规则 → 确定性仓位

旧 A_LLM/B_JEV_LLM/C_JEV_DIRECT/D_RULE 仅保留兼容，不能按旧字母解释新记录。

真实三端口现已由 research 包提供。完整构造、数据导出、四组审计与参数比较见 research-bridge.md；引擎仍不直接依赖数据库或模型SDK。Replay Provider可选提供candidate_symbols(decision_date)，引擎对该类Provider按冻结候选队列与实际持仓并集核对目标；无此方法的旧Provider继续按features与持仓并集。不能把额外潜在持仓数据当作新候选。

旧回测契约遇到公司行为或未清权益明确拒绝，不代表前向V2权益能力不可用。Sortino沿用冻结契约的负收益样本标准差口径，不是通用下行均方根定义；横向比较第三方指标必须先统一口径。total_fees仅为模拟成交费用，不包含未知模型调用费用。

## 7. 本机离线规则组完整运行

先按 research-bridge.md 设置工作目录与PYTHONPATH，再在Python中执行下面的完整程序。不需要API Key，不调用模型。示例使用已有合成数据，不能用于评价真实收益。多日时替换为完整连续数据集并设置正确start/end/warmup。

```python
import asyncio
from decimal import Decimal
from pathlib import Path
from datetime import date
from fkqt_jevinvestor.backtest.engine import BacktestEngine
from fkqt_jevinvestor.domain.backtest import BacktestConfig, ExperimentArm
from fkqt_jevinvestor.research.artifacts import write_immutable
from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider
from fkqt_jevinvestor.research.execution import ResearchExecutionPort
from fkqt_jevinvestor.research.models import ResearchStrategyConfig
from fkqt_jevinvestor.research.strategy import ExperimentTargetProvider

async def main():
    manifest = Path('C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/research-bridge-20260927/datasets/research-fixture/c164abfabc10a1c67931f124b5058d6a6c809bacea9c57a56606777758778a5b/manifest.json')
    replay = FrozenReplayDataProvider(DatasetStore(manifest.parent.parent.parent), manifest)
    config = BacktestConfig(
        run_id='offline-rule-demo', experiment_arm=ExperimentArm.A_RULE,
        start_date=date(2026, 9, 18), end_date=date(2026, 9, 18),
        warmup_trading_days=0, initial_cash=Decimal('100000'),
        benchmark_symbol='000300.SH', dataset_id=replay.manifest.dataset_id,
        dataset_hash=replay.manifest.dataset_hash,
        execution_policy_version='execution-v1', sizing_version='position-sizing-v1',
    )
    targets = ExperimentTargetProvider(replay, Path('data/research/demo-audits'), ResearchStrategyConfig())
    execution = ResearchExecutionPort(replay, replay.manifest.execution_policy)
    result = await BacktestEngine(replay, targets, execution).run(config)
    path = write_immutable(Path('data/research/demo-results') / f'{result.summary.result_hash}.json', result)
    print(path.resolve())

asyncio.run(main())
```

四组联合接线由 run_experiment_arms(configs,replay,target_providers,execution_factory) 完成；各组配置需统一日期、资金、数据集、执行和仓位版本，并使用不同run_id。execution_factory必须每次构造新的ResearchExecutionPort；生成结果后使用research compare及同一审计目录验证完整实际参数。不能用同一执行器实例轮流跑四组。
