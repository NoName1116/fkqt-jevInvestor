# 前向行情基础 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐现金空仓日和不可变候选队列传递，保留旧日运行行为。

**Architecture:** 新增独立日历证明和 ExecutionBundleV2，不改 V1 哈希。FKQT 发布新数据集；JEV 校验后通过既有账本和日运行 CLI 消费，不建立跨仓库 Python 依赖。

**Tech Stack:** Python 3.12、Pydantic、PyArrow、SQLAlchemy、pytest；复用既有依赖。

**Spec:** `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/superpowers/specs/2026-09-26-forward-operations-design.md`

## Global Constraints

- 对应 R54、R55、R59，遵守 R1–R53；无券商、无新选股策略、无常驻服务。
- 不删除旧 V1 入口，不重算旧哈希，不改 Contributor 的 backtest 文件。
- 当前两个工作区均为已有 linked worktree；开始实施时创建独立功能分支，不创建额外 worktree，不移动用户未提交内容。
- 每次代码修改前重读 REQUIREMENTS.md 并记录编号。每项先失败测试，再最小实现；失败修复后只重跑失败测试。
- 命令统一从 JEV 根目录执行；FKQT 测试显式切换 agent cwd。

## 工作区与命令

```powershell
$jevRoot = 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner'
$fkqtRoot = 'C:/Users/1/Desktop/FKQTPRO/ai-signal-phase0/agent'
$pythonExe = 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe'
Set-Location -LiteralPath $jevRoot
& $pythonExe --version
& $pythonExe -m pytest tests/unit/test_execution_bundle.py tests/integration/test_daily_runner.py -q -m 'not live'
```

只对直接关联基线做一次检查；已有临时测试目录不删除。FKQT 先用同一解释器检查 pandas/tushare/PyArrow 可导入性，缺依赖时按 agent/requirements.txt 设置其独立环境，不盲目安装至 JEV 环境。

## Review Focus

1. 交易日为 OPEN 但日历来源或哈希被篡改：必须拒绝，T1 覆盖。
2. “空仓”仍存在待派息现金或锁定红股：不得走 CASH_ONLY，T3 覆盖。
3. 日历已关闭、初始日期以前或缺失中间日：不得补假 NAV，T3 覆盖。
4. 队列顺序改变但成员相同：必须换哈希，T4 覆盖。
5. 队列文件与行情 Manifest 来源不一致：不得从环境变量悄悄覆盖，T5 覆盖。

## Task T1：V2 与独立交易日证明

需求：R54、R59。

文件（以下路径相对 `$jevRoot`，绝对根已在上方固定）：新增 `domain/forward.py` 位于 `src/fkqt_jevinvestor/domain/forward.py`、`ingestion/execution_bundle_v2.py`、`tests/unit/test_execution_bundle_v2.py`；修改 `ingestion/execution_bundle.py` 的加载分派，不改 V1 类体哈希算法。

接口：`TradingDayProofV1(trade_date, is_open, exchange, source_manifest_id, source_content_hash, content_hash)`；`ExecutionBundleV2.create(trade_date: date, snapshots: Mapping[str, MarketExecutionSnapshot], *, calendar_proof: TradingDayProofV1, source_manifest_id: str, source_manifest_content_hash: str, corporate_action_manifest_hash: str | None = None) -> ExecutionBundleV2`；`verify(trade_date: date, required_symbols: AbstractSet[str]) -> None`；加载函数返回 V1/V2 联合类型。

- [ ] 写 `test_empty_v2_requires_verified_open_calendar`：OPEN 且完整来源允许 snapshots={}，hash 为空再 canonical SHA256；关闭日、缺来源、改哈希分别断言稳定错误。写 `test_v1_hash_is_unchanged`：旧 Fixture 哈希逐字相同。
- [ ] 执行 `& $pythonExe -m pytest tests/unit/test_execution_bundle_v2.py -q`，预期新增类未定义导致失败。
- [ ] 实现上述类与加载分派；V2 独立 schema_version=`execution-bundle-v2`，来源与日历证明均进入哈希，自身 hash 字段置空。V1 来源 sidecar 必需性保持。V2 与 V1 都禁止日期/证券身份错配。
- [ ] 重跑该文件及 `tests/unit/test_execution_bundle.py`，全部通过后只暂存该任务文件，提交 `功能：新增带独立日历证明的执行包 V2`。

## Task T2：FKQT V2 发布与 JEV 导入

需求：R50–R52、R54、R59。

文件：`$fkqtRoot/market_data/execution_publisher.py`、`market_data/forward_cli.py`、新增 `market_data/calendar_publisher.py`、`tests/test_execution_publisher_v2.py`；JEV 新增 `src/fkqt_jevinvestor/ingestion/fkqt_execution_manifest_v2.py`、`tests/contract/test_fkqt_execution_manifest_v2.py`，修改既有 Manifest 导入分派。

接口：FKQT `publish_execution_bundle_v2(client, store, *, trade_date: str, symbols: tuple[str, ...], corporate_action_manifest_hash: str | None = None) -> DatasetManifest`；JEV `load_fkqt_execution_manifest_v2(root: Path, trade_date: date, required_symbols: set[str]) -> ExecutionBundleV2`。数据集版本 `TUSHARE_EXECUTION_V2`，request_params 独立绑定日历 Manifest ID/hash。空 Arrow 表仍显式声明全部必需字段，不依赖 pandas 推断空 Schema。

- [ ] 写发布测试 `test_empty_execution_publishes_calendar_without_daily_calls`：仅调用日历接口，0 行表、有 Schema/hash、可重放；写导入测试 `test_empty_fkqt_v2_import_and_tampered_calendar`：合法导入成功，篡改日历、路径越界、缺列、重复 Manifest、错误日期均失败。
- [ ] 在 FKQT cwd 执行 `& $pythonExe -m pytest tests/test_execution_publisher_v2.py -q`；在 JEV cwd 执行 `& $pythonExe -m pytest tests/contract/test_fkqt_execution_manifest_v2.py -q`，确认尚未实现的接口导致失败。
- [ ] 实现发布/读取与 `forward_cli execution --schema-version 2` 显式入口。V1 默认为旧行为；非空 V2 沿用官方价格完整性校验。提交数据前校验全部来源，原子发布，旧结果禁止覆盖。
- [ ] 跑新增测试和原 FKQT execution_publisher/JEV execution_manifest 契约测试；两仓库分别提交 `功能：发布并读取空证券执行 Manifest V2`。

## Task T3：纯现金 NAV 与起始日

需求：R54、R48、R59。

文件：修改 `$jevRoot/src/fkqt_jevinvestor/cli/main.py`、`services/portfolio_service.py`、`persistence/repositories.py`，新增 `tests/integration/test_cash_only_daily.py`；日运行起始日期保存在后续运行配置，不改变旧账户创建 API。

接口：新增 `ExecuteCashOnlyDate(portfolio_id: str, trade_date: date, expected_version: int, start_date: date, calendar_proof: TradingDayProofV1, execution_input_hash: str)`；`PortfolioRepository.execute_cash_only_date(command: ExecuteCashOnlyDate) -> ExecutionBatchResult`。后续 T8 的 `has_outstanding_entitlements(session, portfolio_id)` 接入同事务防空判断。

- [ ] 写 `test_cash_only_advances_nav_and_replays_without_version_change`：初始 1000000.00 现金、0 成交/市值、unit_nav=1，重放新增记录和版本变化均为 0；写 `test_cash_only_rejects_positions_orders_closed_day_and_before_start`；写 `test_cash_only_rejects_unpaid_entitlement` 先以数据库权益 Stub 表达，T8 接真实存储。
- [ ] 执行 `& $pythonExe -m pytest tests/integration/test_cash_only_daily.py -q`，确认新路径缺失失败。
- [ ] CAS、空状态检查、执行输入冲突检查、零成交结果和 NAV 放同一事务；有持仓无订单仍正常估值，不误判纯现金。首日仅在显式 start_date 允许未排期空仓估值，之后检查连续有效交易日和上一决策状态。
- [ ] 定点通过后提交 `功能：补齐现金空仓日净值与幂等`。

## Task T4：候选版本导出与冻结

需求：R27、R55、R59。

文件：新增 `$jevRoot/src/fkqt_jevinvestor/domain/candidate_queue.py`、`ingestion/candidate_queue.py`、`tests/unit/test_candidate_queue.py`、`tests/contract/test_candidate_queue_manifest.py`，修改 CLI 增加 `daily prepare-candidates`。

接口：`CandidateQueueV1(trade_date, source_id, source_version, candidate_limit, ordered_symbols, created_at, content_hash)`；`prepare_candidate_queue(root: Path, trade_date: date, candidate_limit: int) -> CandidateQueueV1`；`CandidateQueueStore.save(queue: CandidateQueueV1) -> Path`。输入读取已验证 FKQT candidate_universe，按唯一连续 rank 提取证券。

- [ ] 写 `test_rank_order_changes_queue_hash`、`test_same_queue_preserves_frozen_file`、`test_duplicate_rank_symbol_and_capacity_fail`、`test_queue_dates_and_source_hash_must_match`；断言 SH/SZ 证券格式、正容量、实际数量不超容量，重复写不同内容拒绝。
- [ ] 执行 `& $pythonExe -m pytest tests/unit/test_candidate_queue.py tests/contract/test_candidate_queue_manifest.py -q`，预期新模型缺失失败。
- [ ] 实现单一来源队列导出；created_at 使用冻结上游抓取时间，不用每次执行时间造成同输入不同哈希。路径为日期/hash，不扫描最新版本；CLI 输出绝对 queue_ref/hash。
- [ ] 定点通过后提交 `功能：冻结并导出有序候选队列版本`。

## Task T5：日运行自动读取队列

需求：R43、R55、R48。

文件：修改 `$jevRoot/src/fkqt_jevinvestor/cli/main.py`，新增 `tests/integration/test_daily_candidate_queue.py`，更新 `docs/runbooks/daily-runner.md`。

接口：`daily close --candidate-queue <absolute-path>`；实际 ordered_symbols 与 limit 必须和当日 Manifest 一致，来源身份/内容哈希进入正式 run_id 及审计。旧环境变量入口保留；双入口同时给出且不一致返回 CANDIDATE_QUEUE_CONFIG_CONFLICT。

- [ ] 写 `test_daily_close_consumes_frozen_queue_without_env_symbols`、`test_queue_and_manifest_mismatch_rejected`、`test_held_only_does_not_change_candidate_capacity`，断言同输入 run_id 相同，顺序变化为新 run_id。
- [ ] 执行 `& $pythonExe -m pytest tests/integration/test_daily_candidate_queue.py -q`，预期 CLI 尚不识别该参数。
- [ ] 将队列选择放在已有 stable_daily_run_id 调用之前，不改 LLM、Jev 或仓位策略。队列外持仓取数据库状态，不依赖候选文件补齐。手册写明旧/新入口和错误码。
- [ ] 定点通过后提交 `功能：日运行消费冻结候选队列`；继续下一计划，不在阶段中途跑全套。

## 自检与交接

| 检查项 | 标准值 | 方法 | 优先级 |
|---|---|---|---|
| 设计覆盖 | R54/R55/R59 对应 T1–T5 | 逐任务编号核对 | 必须 |
| 无假开市 | 空包须有冻结日历证明 | T1/T2 测试 | 必须 |
| 空仓资产连续 | NAV 按有效日期推进，重放不变 | T3 测试 | 必须 |
| 候选一致 | rank、来源、日期、容量均一致 | T4/T5 测试 | 必须 |
| 兼容 | 原 V1 与既有 CLI 行为保持 | 原相关回归 | 必须 |

实施输出为代码、测试和更新后的运行手册；本计划本身不代表实现。公司行为权益的空仓检查在下一计划 T8 完成，完成 T8 前不得启用持续调度。
