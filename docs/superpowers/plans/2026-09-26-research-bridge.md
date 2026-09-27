# 研究接线实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 完成 R65–R69，交付沙耶引擎之外可独立运行和验证的全部研究接线。

**Architecture:** 新增 research 包，离线日块/清单隔离决策与执行，复用既有特征、Jev、仓位、成交。原记录重放严格绑定持仓，实验审计独立于正式数据库。

**Tech Stack:** Python 3.12、Pydantic、SQLite readonly、pytest，零新增依赖。

**Spec:** docs/superpowers/specs/2026-09-26-research-bridge-design.md

## Global Constraints

- 不修改 backtest/、tests/backtest/、旧 domain/backtest.py 契约。
- 不调用真实模型、不启用前向、不推送或合并。
- 每次代码改动重读 REQUIREMENTS.md 并映射 R65–R69。
- 失败只重测失败项；保留现有工作树未跟踪文件。

## Review Focus

- 队列轮换：执行包必须覆盖可能遗留持仓，禁止遗漏旧证券估值。
- 冻结映射可变：加载/消费边界重验 hash，不能仅信任 Pydantic frozen。
- 不同持仓：原记录重放必须拒绝，独立实验缓存必须隔离。
- 模型失败/非法动作：NO_SIGNAL 保护原持仓，禁止不同组静默替代。
- 交易日跨度/公司行为：必须使用冻结日历，旧契约不能表达的权益必须拒绝。

## Task 1: 离线数据集（R65）

Files: research/dataset.py、tests/unit/test_research_dataset.py。
Interfaces: ResearchDayV1 -> ReplayDay；DatasetStore.publish/load；FrozenReplayDataProvider.decision_dates/load_day/warmup_dates。
- [x] 写缺文件、篡改、日期间断、未来特征、预热不足和重复发布测试。
- [x] 跑测试，Expected: 未实现入口失败。
- [x] 实现内容 hash、不可变发布及重验、前视和覆盖校验。
- [x] 跑对应测试，Expected: 全通过；记录结果。

## Task 2: 回测执行与原目标适配（R66）

Files: research/execution.py、research/recorded.py、tests/unit/test_research_execution.py。
Interfaces: ResearchExecutionPort.execute -> BacktestExecutionResult；RecordedTargetV1、FrozenRecordedTargetProvider.build_targets。
- [x] 写真实买卖费用、T+1、缺估值、原持仓不一致及重复执行测试。
- [x] 跑最小失败，Expected: 未实现入口失败。
- [x] 复用原 Ledger 与成交规则，重验输入及公司行为拒绝，缓存按完整输入绑定。
- [x] 跑对应测试，Expected: 全通过；记录结果。

## Task 3: 四组 TargetProvider（R67–R68）

Files: research/strategy.py、research/models.py、research/llm.py、research/audit.py、tests/unit/test_research_strategy.py。
Interfaces: ExperimentTargetProvider.build_targets -> TargetPositionBatch；LLM/Jev 配置与真实 SDK adapter；ArtifactStore。
- [x] 写 A/D 动作、B 无 Jev、C 共享 universe、非法动作、失败持仓保护和缓存隔离测试。
- [x] 跑最小失败，Expected: 未实现入口失败。
- [x] 实现四组互斥路由、版本化阈值、共享仓位及文件审计；拒绝旧组名。
- [x] 跑对应测试，Expected: 全通过；记录结果。

## Task 4: 只读导出、CLI 与可比性（R65/R69）

Files: research/export.py、research/cli.py、research/comparison.py、cli/main.py；tests/integration/test_research_bridge.py。
Interfaces: export_dataset(request, output) -> manifest path；research export/verify/targets；compare_results(tuple[BacktestResult,...])。
- [x] 写真实 SQLite 只读导出与原持仓绑定测试、CLI 和组间不一致拒绝测试。
- [x] 跑最小失败，Expected: 未实现入口失败。
- [x] 完成 request 解析、全量验证后发布、入口注册，显式 allow-live-models 开关。
- [x] 跑对应测试，Expected: 全通过；记录结果。

## Task 5: 交接和最终验收（R69/R33）

Files: docs/runbooks/research-bridge.md、docs/validation/2026-09-27-research-bridge.md、task_plan.md、findings.md、README.md。
- [x] 写完整命令、输入字段、输出、稳定错误与文件清单。
- [x] 一次受影响回归与 Ruff/Pyright；失败只重测失败项。
- [x] 一次独立只读审查，重要问题定点修复并留证。
- [x] 本地提交，不 push；注明引擎联合验收与真实增益尚未验证。
