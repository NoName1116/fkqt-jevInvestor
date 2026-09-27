# 研究数据集、四组对照与回测交接

## 1. 解决的问题

对应 R65–R69。现在可发布离线冻结数据集、导出正式 C 组目标、生成 A/B/C/D 独立实验目标、复用原模拟执行规则，并比较沙耶引擎返回的四组结果。这里不含回测日期循环和绩效引擎，不替代沙耶的实现。

两种模式不能混用：原记录重放要求完全相同的决策持仓；独立实验根据本组持仓重新生成动作与仓位。独立实验结果写研究文件，绝不写正式账户或模型库。

## 2. 前置条件

- Python 3.12–3.13；项目原有依赖已经安装，无新增依赖。
- database_path 指向存在的 SQLite，行情快照、特征、候选队列、执行包已经冻结；导出不升级数据库。
- D+1 必须有执行包，不得把 D 日包冒充 D+1。首末和预热交易日须连续覆盖；不能用自然日补齐。
- 测试默认不调用真实模型。B/C 首评需 DeepSeek Key，C/D 首评需 TypeSafe Key；凭据仅放未跟踪 `.env` 或进程环境。不要把 Key 填到策略 JSON。
- 本机工作树：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner`。当前 Python：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe`。

新环境安装依赖的完整命令（不打包/发布）：

```powershell
Set-Location 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner'
py -3.12 -m venv .venv
& '.venv/Scripts/python.exe' -m pip install -e '.[dev]'
```

本机后续命令使用现有虚拟环境，避免 editable 安装指向另一工作树：

```powershell
Set-Location 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner'
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
$researchPython='C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe'
```

## 3. 发布请求

`examples/research/export-request.local.json` 是本机已经生成的**合成测试数据库**请求，非真实行情、非收益实验。它的源文件在 ignored 测试目录；新克隆仓库需改成本机实际来源路径，或先运行集成测试生成自己的源文件。不要把这些合成特征用于交易。

| 字段 | 内容与校验 |
|---|---|
| database_path | 绝对 SQLite 文件路径；mode=ro/query_only，一致读事务 |
| dataset_id | 1–128 字符的安全标识；不能包含路径分隔符 |
| days | 按交易日排序、无重复、无间断的完整数组 |
| decision_date | YYYY-MM-DD 决策日 |
| market_snapshot_hash | 当日正式冻结快照的64位小写SHA256 |
| candidate_queue_path | 当日冻结 CandidateQueueV1 文件；顺序和容量保留 |
| execution_bundle_path | 快照 next_trade_date 对应的执行包，不能使用决策日包 |
| run_id | 可省略；提供时导出该正式 C 组 sizing 与原决策持仓 |

每个 days 成员上述五个来源字段必须写全；多日导出必须逐日列出。少一个交易日、必要证券或未来执行文件均报错，不发布完整 manifest。

```powershell
& $researchPython -m fkqt_jevinvestor research export `
  --request 'examples/research/export-request.local.json' `
  --output-root 'data/validation/research-bridge-20260927/datasets'
```

预期：JSON 返回 EXPORTED 与 manifest 绝对路径。输出目录：

```text
datasets/<dataset_id>/<dataset_hash>/
  manifest.json
  days/<day_hash>.json
  recorded/<recorded_target_hash>.json
  recorded-index-<index_hash>.json
```

后两项仅在提供 run_id 时生成。数据集 hash 只依赖行情、特征、队列、执行包与固定执行成本，不依赖目标与运行时间；目标/索引单独寻址，无自引用。相同文件复用；内容冲突拒绝，不覆盖。

## 4. 离线校验与 A 组

本机示例 manifest 路径可直接使用：

```powershell
$researchManifest='C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/research-bridge-20260927/datasets/research-fixture/c164abfabc10a1c67931f124b5058d6a6c809bacea9c57a56606777758778a5b/manifest.json'
& $researchPython -m fkqt_jevinvestor research verify --manifest $researchManifest
& $researchPython -m fkqt_jevinvestor research targets `
  --manifest $researchManifest --arm A_RULE --date '2026-09-18' `
  --portfolio 'examples/research/portfolio.json' `
  --strategy-config 'examples/research/strategy.json' `
  --audit-root 'data/validation/research-bridge-20260927/audits'
```

预期：VERIFIED/days=1；A 组输出两只候选的 ENTER，目标比例由原仓位公式计算，不由模型生成。审计写 `audits/experiments/<input_key>.json`，重复调用复用；锁存在时报 IN_PROGRESS。

## 5. B/C/D 与离线重放

| 组 | 模型输入与动作来源 | 未配置 Provider/失败 |
|---|---|---|
| A_RULE | return_20d 正入场、负退出；零保持/避免 | 特征不足 NO_SIGNAL |
| B_LLM | 特征、结构化持仓与历史动作，无 Jev | NO_SIGNAL，不改原持仓 |
| C_JEV_LLM | 双层 Jev 概率 + 相同 LLM 拍板 | Jev 失败不调 LLM，不伪造概率 |
| D_JEV_DIRECT | 双层 Jev + 版本化规则映射 | 不调用 LLM，证据不足 NO_SIGNAL |

D 固定阈值：SUFFICIENT >= 0.6；RISK_OFF/LOSS/HIGH >= 0.6 为负面；PROFITABLE >= 0.6 且下一日 PROFIT >= 0.5 为正面。版本和阈值保存到审计，不能看完结果再原地调参。基线仅用于消融，不宣称有效。

真实调用必须显式 `--allow-live-models`；以下命令会消耗相应账户额度，**本轮未执行**：

```powershell
& $researchPython -m fkqt_jevinvestor research targets --manifest $researchManifest `
  --arm B_LLM --date '2026-09-18' --portfolio 'examples/research/portfolio.json' `
  --strategy-config 'examples/research/strategy.json' --audit-root 'data/research/experiment-audits' --allow-live-models
& $researchPython -m fkqt_jevinvestor research targets --manifest $researchManifest `
  --arm C_JEV_LLM --date '2026-09-18' --portfolio 'examples/research/portfolio.json' `
  --strategy-config 'examples/research/strategy.json' --audit-root 'data/research/experiment-audits' --allow-live-models
& $researchPython -m fkqt_jevinvestor research targets --manifest $researchManifest `
  --arm D_JEV_DIRECT --date '2026-09-18' --portfolio 'examples/research/portfolio.json' `
  --strategy-config 'examples/research/strategy.json' --audit-root 'data/research/experiment-audits' --allow-live-models
```

禁止用合成示例评估模型收益；上面仅演示入口，实际实验先发布真实数据集。已冻结实验用相同 config/portfolio/audit-root 加 `--replay-only`，不加载 Key、不调 API；缺记录明确拒绝：

```powershell
& $researchPython -m fkqt_jevinvestor research targets --manifest $researchManifest `
  --arm A_RULE --date '2026-09-18' --portfolio 'examples/research/portfolio.json' `
  --strategy-config 'examples/research/strategy.json' `
  --audit-root 'data/validation/research-bridge-20260927/audits' --replay-only
```

如果首次运行使用环境提供的非默认 model，需要在重放时用同样 strategy JSON。不能省略当时配置，否则 hash 改变应报缺记录。完整多日实验应提供 `--backtest-config`，固定 run_id、initial_cash、起止日期与预热，不用单日 CLI 默认配置冒充整段回测。

## 6. 给沙耶的接线接口

无需改她的引擎；本项目提供三条原协议实现：

| 引擎依赖 | 本项目实现 | 方法 |
|---|---|---|
| ReplayDataProvider | research.dataset.FrozenReplayDataProvider | decision_dates(start,end)、load_day(day) |
| 额外预热查询 | 同上 | warmup_dates(start,count)，不足拒绝 |
| TargetProvider（独立实验） | research.strategy.ExperimentTargetProvider | build_targets(config, DecisionReplayDay, portfolio) |
| TargetProvider（原记录） | research.recorded.FrozenRecordedTargetProvider | 同签名，严格原持仓绑定 |
| ExecutionPort | research.execution.ResearchExecutionPort | execute(portfolio, targets, market) |

构造完整接线的 Python 代码（不包含也不猜测她尚未合入的引擎函数名）：

```python
from pathlib import Path
from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider
from fkqt_jevinvestor.research.execution import ResearchExecutionPort
from fkqt_jevinvestor.research.models import ResearchStrategyConfig
from fkqt_jevinvestor.research.strategy import ExperimentTargetProvider

manifest = Path('C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/research-bridge-20260927/datasets/research-fixture/c164abfabc10a1c67931f124b5058d6a6c809bacea9c57a56606777758778a5b/manifest.json')
replay = FrozenReplayDataProvider(DatasetStore(manifest.parent.parent.parent), manifest)
targets = ExperimentTargetProvider(replay, Path('data/research/audits'), ResearchStrategyConfig())
execution = ResearchExecutionPort(replay, replay.manifest.execution_policy)
print(type(replay).__name__, type(targets).__name__, type(execution).__name__)
```

A 不需要 Provider；B/C 传入 ResearchDeepSeekProvider，C/D 传入 TypeSafeJevMarketProvider。回测引擎每组必须创建独立 execution 实例；首次 execute 只接现金组合。已有聚合持仓缺 FIFO 明细时拒绝，不能把平均成本伪装 FIFO。

调用顺序必须是 load_day → 取 decision_view → build_targets → execute；不得把 ReplayDay 本身传给模型决策。后一次 execute 的 portfolio 必须等于前一次 portfolio_after；持仓变更不一致/跨日间断拒绝。执行先复制账本，验证通过后才提交内部状态，重复完全相同输入不重成交。买入日 holding_trading_days=1，包括最后执行日；T+1 可卖数量仍按既有 FIFO 规则计算。

同一 run_id 必须按决策日顺序生成目标，历史按完整回测配置与策略隔离。同日不同持仓记录可以单独冻结，但后续日期不得猜测采用哪个分支；请为分叉实验使用独立 run_id。已经冻结后续日期后，禁止补写此前尚未冻结的日期改变历史链。多日引擎运行必须固定完整 start/end/initial_cash 配置，不能使用 CLI 单日默认配置逐日拼接。

沙耶引擎已完成联合接线，warmup_dates(before,count)仅校验预热，不产生目标/成交。research ReplayProvider提供可选candidate_symbols(decision_date)，引擎据此核对queue∪held；旧无队列Provider仍按features∪held。ReplayDay完整特征和既有冻结文件hash保持不变。原 ReplayDay 无公司行为权益/现金日历字段，遇到公司行为或权益仍明确拒绝；前向V2权益账本不受影响。

## 7. 四组结果比较

取得四个正式 BacktestResult JSON 后执行（结果文件由沙耶引擎产生，本轮不伪造）：

```powershell
& $researchPython -m fkqt_jevinvestor research compare --audit-root 'data/research/audits' --results `
  'data/research/results/A_RULE.json' 'data/research/results/B_LLM.json' `
  'data/research/results/C_JEV_LLM.json' 'data/research/results/D_JEV_DIRECT.json'
```

检查组别唯一、同数据集、起止、预热、资金、基准、成本与仓位版本，以及逐日执行日期。通过审计目录逐日核对完整实验配置和完整仓位参数；参数不一致、缺记录或同日分支有歧义均拒绝。CLI 必须提供审计目录，验证通过返回 AUDIT_VERIFIED。Python compare_results 不提供审计目录时仅返回 DECLARED_CONFIG_ONLY，不能声称实际参数已验证。输出 B−A、C−A、C−B、D−A 的收益、回撤、换手和交易费用差值。差值本身不是统计显著性或样本外增益，未知模型费用不能填0。

## 8. 自检与排错

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| 源库写入 | 0 | 导出前后 Get-FileHash 相同 | 必须 |
| 离线 Provider 调用 | 0 | replay-only；已有缓存复用测试 | 必须 |
| B 请求 Jev 字段 | 0 | strategy/Provider 测试 | 必须 |
| D+1 日期 | 冻结 next_trade_date | research verify + 连续两日执行测试 | 必须 |
| 原目标持仓 | 完全匹配 | RecordedProvider 不同现金拒绝测试 | 必须 |
| 模型失败动作 | NO_SIGNAL、持仓目标不变 | HELD_ONLY 失败测试 | 必须 |
| 成本一致 | Manifest 固定策略 | 非同成本配置拒绝测试 | 必须 |
| 沙耶目录改动 | 0 | git diff -- src/fkqt_jevinvestor/backtest tests/backtest | 必须 |

| 错误 | 原因与处理 |
|---|---|
| RESEARCH_CALENDAR_CHAIN_MISMATCH | 补齐真实交易日包，不按自然日推断 |
| RESEARCH_FEATURE_HASH_MISMATCH | 回到可信冻结特征；拒绝改 JSON 让其通过 |
| RESEARCH_SECURITY_COVERAGE_INCOMPLETE | 补齐每只特征证券的证券状态和必要日线，不把未知状态当 NONE |
| RESEARCH_HELD_SYMBOL_COVERAGE_INCOMPLETE | 请求 FKQT 发布队列轮换后仍可能持有的证券 |
| RECORDED_TARGET_PORTFOLIO_MISMATCH | 这是原记录重放；换独立实验模式，不改原目标 |
| RESEARCH_INITIAL_FIFO_STATE_REQUIRED | 首次接入用现金组合；现有持仓需独立扩展明细契约 |
| LEGACY_BACKTEST_CORPORATE_ACTION_UNSUPPORTED | 本旧契约不可用该区间；需另评审权益回测契约，不能静默忽略事件 |
| RESEARCH_EXPERIMENT_NOT_FROZEN | 首评需显式模型授权或确定性计算；不可在 replay-only 临时生成 |
| RESEARCH_EVALUATION_IN_PROGRESS | 有并发或崩溃遗留锁；确认原进程已停止后人工处理对应单个锁，不能批量清缓存 |
| RESEARCH_ARTIFACT_CONFLICT | 冻结文件有冲突；保留证据，另用实验 ID，不覆盖 |
| RESEARCH_HISTORY_BRANCH_AMBIGUOUS | 同一日期多持仓分支；使用独立 run_id，不合并历史动作 |
| RESEARCH_HISTORY_BACKFILL_FORBIDDEN | 后续已冻结，禁止补写历史；完整重评必须使用新 run_id |
| RESEARCH_COMPARISON_AUDIT_INCOMPLETE_OR_AMBIGUOUS | 补齐四组逐日审计，原记录 C 目标不能冒充四组独立实验 |
| RESEARCH_COMPARISON_STRATEGY_MISMATCH | 四组完整策略或仓位参数不同；另起统一配置实验，不修改冻结记录 |
| OperationalError | 源库缺表或不能只读查询；核对数据源和版本，导出不会自动升级源库 |

## 9. 文件清单

全部根路径为 `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/`：

| 绝对文件路径 | 用途 |
|---|---|
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/dataset.py | 日块、Manifest 与 ReplayProvider |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/export.py | 只读源库与正式目标导出 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/execution.py | FIFO/成交适配 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/recorded.py | 原持仓绑定重放 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/strategy.py | 四组路由 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/models.py | 研究模型配置与协议 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/llm.py | 独立 DeepSeek 请求 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/audit.py | 冻结审计与并发锁 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/artifacts.py | 原子不覆盖写入 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/comparison.py | 四组可比性与差值 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/cli.py | CLI 入口 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/examples/research/export-request.local.json | 本机合成来源请求 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/examples/research/portfolio.json | 初始现金组合 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/examples/research/strategy.json | 完整版本化配置 |

完整改动与实际验收见 `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/validation/2026-09-27-research-bridge.md`。
