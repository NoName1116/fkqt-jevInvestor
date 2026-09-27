# 2026-09-27 研究接线验收

## 1. 结论与范围

R65–R69 已实现：只读导出不可变数据集与原正式 C 目标、冻结 ReplayProvider、严格原持仓 TargetProvider、FIFO 模拟执行适配、四组独立策略与文件审计、四组可比性检查及完整 CLI。C 仍为主链：确定性特征 → Jev 有限盈亏概率 → LLM 离散动作 → 确定性仓位。Jev 不计算连续盈亏金额，LLM 不生成仓位或数量。

本轮基线 a36537e，分支 feature/forward-operations。交付33份文件，其中22份Python文件。只进行本地提交；不推送、合并、打包、发布、注册调度任务或激活真实前向。未修改 src/fkqt_jevinvestor/backtest/、tests/backtest/ 和旧 domain/backtest.py。

## 2. 环境与前置条件

- 工作树：C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner
- Python：C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe
- 复用原有依赖，零新增依赖；所有本轮测试离线，不要求 API Key。
- 数据源必须已有冻结快照、证券状态、必要日线、特征、候选队列和交易日历确认的 D+1 执行包。导出只读 SQLite，缺失数据不得临时调用上游补造。
- 真实调用 B/C 需要 DeepSeek，C/D 需要 TypeSafe，并且必须显式 --allow-live-models。凭据只放本机未跟踪环境配置，不进入研究 JSON 或 Git。
- 示例源是本机合成测试数据，绝非真实行情或收益证据；新克隆环境不能假定 ignored 源文件存在。

## 3. 需求到实现和输出

| 需求 | 完整实现 | 输出与边界 |
|---|---|---|
| R65 | ResearchDayV1 / DatasetStore / export_dataset | 内容寻址manifest与日块；源库mode=ro/query_only，一致读事务；完整日期链、状态/特征/执行覆盖；成本策略进数据集hash |
| R66 | FrozenReplayDataProvider / FrozenRecordedTargetProvider / ResearchExecutionPort | 旧协议适配；decision_view隔离D+1；原目标严格完整持仓绑定；复制FIFO账本后成交；幂等与序列校验 |
| R67 | ExperimentTargetProvider / ResearchDeepSeekProvider | A趋势基线、B无Jev LLM、C Jev→LLM、D概率规则；统一确定性仓位；失败NO_SIGNAL保护原持仓 |
| R68 | AuditStore / ExperimentArtifact / FrozenJevCall | 配置、完整持仓、日期、历史链寻址；真实请求/响应hash/状态/实际用量；文件不可覆盖，不写正式模型和账户库 |
| R69 | research export/verify/targets/compare与操作手册 | 可执行CLI、沙耶接线、逐日审计旁证、完整仓位参数比较；没有回测日期循环或伪造收益 |

A趋势和D概率阈值只是固定研究基线，不是已验证盈利策略。C/D共享相同Jev正式输入缓存；B真实请求不含Jev。未知模型用量保留null，成交费用与模型费用不混为一项。

## 4. 实际执行与结果

### 4.1 一次受影响回归

完整执行命令：

```powershell
Set-Location 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner'
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
$researchPython='C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe'
& $researchPython -m pytest tests/unit/test_research_dataset.py tests/unit/test_research_execution.py tests/unit/test_research_strategy.py tests/unit/test_research_boundaries.py tests/unit/test_research_comparison.py tests/unit/test_research_llm.py tests/integration/test_research_bridge.py tests/unit/test_backtest_contracts.py tests/unit/test_cli.py tests/unit/test_snapshot_store.py tests/unit/test_frozen_c_group_target.py tests/unit/test_execution_engine.py tests/unit/test_position_sizing.py tests/integration/test_c_group_decision.py -q --basetemp=data/test-runs/research-final-regression
```

实际输出：91 passed in 12.19s。其中33条新增研究验证、58条受影响既有验证。没有重跑完整旧仓库测试套件。覆盖原回测契约、CLI、快照校验、冻结C目标、执行规则、仓位和C主链。

验证范围：不可变发布与篡改拒绝；未来特征、预热不足、日期链间断；队列轮换潜在持仓覆盖；缺证券状态/日线拒绝；原目标持仓绑定；买入费用与FIFO卖出/T+1；买入日和末执行日持有天数；A/D共享仓位；B请求隔离；C候选池1次与个股独立；模型无效响应与非法连续参数拒绝；失败保留HELD_ONLY；冻结缓存和replay-only；配置历史隔离、分支歧义和回填保护；只读源库导出；正式C目标导出及原持仓hash；四组完整参数和审计旁证。

### 4.2 静态检查

```powershell
& $researchPython -m ruff check src/fkqt_jevinvestor/research src/fkqt_jevinvestor/cli/main.py src/fkqt_jevinvestor/ingestion/snapshot_store.py tests/unit/research_fixtures.py tests/unit/test_research_dataset.py tests/unit/test_research_execution.py tests/unit/test_research_strategy.py tests/unit/test_research_boundaries.py tests/unit/test_research_comparison.py tests/unit/test_research_llm.py tests/integration/test_research_bridge.py
& $researchPython -m pyright src/fkqt_jevinvestor/research src/fkqt_jevinvestor/cli/main.py src/fkqt_jevinvestor/ingestion/snapshot_store.py tests/unit/research_fixtures.py tests/unit/test_research_dataset.py tests/unit/test_research_execution.py tests/unit/test_research_strategy.py tests/unit/test_research_boundaries.py tests/unit/test_research_comparison.py tests/unit/test_research_llm.py tests/integration/test_research_bridge.py
git diff --check
git diff --name-only -- src/fkqt_jevinvestor/backtest tests/backtest src/fkqt_jevinvestor/domain/backtest.py
```

实际：Ruff All checks passed；Pyright 0 errors, 0 warnings, 0 informations。22份Python文件纳入检查。git diff --check无错误；Contributor目录与旧契约的diff为空。LF→CRLF只是本机Git换行提示，没有修改全仓换行配置。

### 4.3 真实CLI离线冒烟

```powershell
$researchSource='C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/test-runs/research-recorded-fix2/test_export_formal_targets_pre0/source.db'
$researchBefore=(Get-FileHash -LiteralPath $researchSource -Algorithm SHA256).Hash
& $researchPython -m fkqt_jevinvestor research export --request 'examples/research/export-request.local.json' --output-root 'data/validation/research-bridge-20260927/datasets'
$researchManifest='C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/research-bridge-20260927/datasets/research-fixture/c164abfabc10a1c67931f124b5058d6a6c809bacea9c57a56606777758778a5b/manifest.json'
& $researchPython -m fkqt_jevinvestor research verify --manifest $researchManifest
& $researchPython -m fkqt_jevinvestor research targets --manifest $researchManifest --arm A_RULE --date '2026-09-18' --portfolio 'examples/research/portfolio.json' --strategy-config 'examples/research/strategy.json' --audit-root 'data/validation/research-bridge-20260927/audits'
& $researchPython -m fkqt_jevinvestor research targets --manifest $researchManifest --arm A_RULE --date '2026-09-18' --portfolio 'examples/research/portfolio.json' --strategy-config 'examples/research/strategy.json' --audit-root 'data/validation/research-bridge-20260927/audits' --replay-only
$researchAfter=(Get-FileHash -LiteralPath $researchSource -Algorithm SHA256).Hash
$researchBefore -eq $researchAfter
```

实际结果：
1. EXPORTED，发布manifest、日块、正式目标与索引。
2. VERIFIED，days=1，数据集hash c164abfabc10a1c67931f124b5058d6a6c809bacea9c57a56606777758778a5b。
3. A_RULE：000001.SZ与600000.SH均ENTER，目标比例各0.05000000，执行日2026-09-21。
4. 重放得到完全相同目标；两次sizing input_hash均d7250cdde1448c950c452004cf92517856817b41e41c51b5a1459d65943d11f6。
5. 源库前后SHA256均6DF14A5EF894C278E3AC0B2EFCDFABFEB588A3276D0E559FB3BAE135D3F84703，相等为True。
6. 本轮真实模型调用次数0；没有启用正式前向。

CLI正确模块为 fkqt_jevinvestor。最初文档误写 fkqt_jevinvestor.cli，冒烟报缺 __main__；修正文档后执行上述四条成功。不新增另一个包入口掩盖文档错误。

## 5. 独立审查与修复证据

一份fresh只读审查，4项Important、0 Critical、0 Minor。没有第二次审查或重复全套测试。

| 问题 | 修复 | 定点证据 |
|---|---|---|
| 历史混入其他配置与同日持仓分支 | 完整BacktestConfig/策略隔离；动作携带前序审计hash；同日歧义拒绝；后续冻结后拒绝新增更早历史 | 配置/分支测试RED→GREEN；回填测试1 RED→1 GREEN；最终回归通过 |
| 缺证券状态仍能发布并入场 | 每只特征证券必须有状态和必要日线，缺失在发布前拒绝 | 合法重算hash后的缺状态/日线测试RED→GREEN |
| 买入日持有天数为0，漏最后执行日 | 计数成功执行日期，含当前执行日，与正式NAV口径一致 | 首日/末日测试RED→GREEN；T+1不受影响 |
| 比较只核对仓位版本 | CLI必需审计目录；逐日完整配置/仓位参数相同才AUDIT_VERIFIED；Python缺旁证只DECLARED_CONFIG_ONLY | 完整旁证成功、缺旁证拒绝、不同risk budget拒绝，RED→GREEN |

上述修复首次6个定点测试均失败，修复后6个通过；之后回填测试1失败→1通过。另补齐缺源库表的CLI错误捕获，1失败→1通过，返回脱敏OperationalError/退出2，未创建发布目录。

其他调试记录：原接口DECISION_INPUT并非DECISION；数据库小数精度与原持仓零值的canonical表示不同，必须从原决策持仓恢复current_position_pct再核对目标hash；异步测试中的同步CLI需在线程运行。全部失败项均定点修复重测，不隐去首轮失败。

执行裁定：用户少测要求优先于技能重复整仓验证，最终只跑一次受影响91项；对已实现行为追加验证直接GREEN，不声称所有覆盖都曾RED。保留分支与worktree，不追加集成选择暂停，不清理用户原缓存。

## 6. 自检清单

| 检查项 | 标准值 | 检查方法与实际值 | 优先级 |
|---|---|---|---|
| 受影响回归 | 失败0 | pytest，91/91通过 | 必须 |
| 静态检查 | 错误0、警告0 | Ruff/Pyright，均通过 | 必须 |
| 源数据库变化 | 0 | CLI前后SHA256相同 | 必须 |
| 未来行情进入模型 | 0 | DecisionReplayDay隔离及请求测试 | 必须 |
| B请求Jev字段 | 0 | FakeResponses边界与策略请求验证 | 必须 |
| 失败目标 | NO_SIGNAL保持原持仓 | HELD_ONLY与Provider错误测试通过 | 必须 |
| 历史实验隔离 | 完整配置一致且无歧义 | 配置、分支、回填三种边界通过 | 必须 |
| 持有日 | 首执行日1，第二执行日2 | 首日和末日执行断言通过 | 必须 |
| 实际仓位参数 | 四组完整相同 | audit_root旁证，不一致拒绝 | 必须 |
| Contributor目录变化 | 0 | git diff为空 | 必须 |
| 外部调用/前向启用 | 0 | 全部离线Fake Provider与显式命令审计 | 必须 |
| 真实收益增量 | 尚未验证 | 不使用合成输入作收益声明 | 必须 |

## 7. 已知限制与下一步

1. 沙耶引擎尚未联合接线验收。本项目已预留三条协议实现，不写日期循环或收益聚合；合入后核对预热接口、初始现金状态、daily record签名、结果hash，再做同数据集联合回归。
2. 独立实验从现金组合开始执行；已有聚合持仓没有FIFO lot明细时拒绝，不能伪造成本。原正式目标仅能在完全相同持仓上重放，不可拿原C目标冒充B/D独立实验。
3. 旧回测接口无法完整表达公司行为权益，遇到公司行为事件/未知状态/未清应收或锁定红股明确拒绝。支持权益回测需要独立契约扩展，前向V2实现不受影响。
4. 同一实验必须按日顺序生成目标；多日配置固定。分叉使用不同run_id，禁止在后续冻结后补写过去。进程崩溃遗留单个lock需确认进程已停止后人工处理，禁止批量清缓存重评。
5. 四组比较AUDIT_VERIFIED表示声明和审计参数核对通过，不证明收益统计显著性、源行情真实性或旧引擎聚合正确。模型费用不在成交total_fees中，缺真实用量或价格身份时不可补0。
6. 本轮未调用真实TypeSafe/DeepSeek，未启动连续真实前向，未完成真实样本外四组增量或概率校准实验。真实行情日历与成熟标签齐备后才能评估Jev是否有增益。
7. 本机示例请求引用ignored合成测试源；新克隆环境必须生成自己的测试源或替换成实际冻结路径。API Key和数据库不提交。既有正式前向账户迁移/启用仍按上一轮手册由操作员处理，本轮不自动执行。
8. Ruff/Pyright与91项不是整个项目全部测试；结果只覆盖列出的受影响范围。没有把尚未执行的真实验证、沙耶联合验证或远程合并标为完成。

排错与完整沙耶构造代码见 C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/runbooks/research-bridge.md。稳定错误包含RESEARCH_SECURITY_COVERAGE_INCOMPLETE、RESEARCH_HISTORY_BRANCH_AMBIGUOUS、RESEARCH_HISTORY_BACKFILL_FORBIDDEN、RESEARCH_COMPARISON_AUDIT_INCOMPLETE_OR_AMBIGUOUS、RESEARCH_COMPARISON_STRATEGY_MISMATCH；保留证据、补可信来源或新建实验ID，不能修改已冻结结果让校验通过。

## 8. 完整交付文件清单

| 绝对文件路径 | 用途 |
|---|---|
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/README.md | 需求、设计、操作或验收记录 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/REQUIREMENTS.md | 需求、设计、操作或验收记录 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/findings.md | 需求、设计、操作或验收记录 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/task_plan.md | 需求、设计、操作或验收记录 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/cli/main.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/ingestion/snapshot_store.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/__init__.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/artifacts.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/audit.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/cli.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/comparison.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/dataset.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/execution.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/export.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/llm.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/models.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/recorded.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/strategy.py | 实现 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/unit/research_fixtures.py | 验证 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/unit/test_research_boundaries.py | 验证 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/unit/test_research_comparison.py | 验证 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/unit/test_research_dataset.py | 验证 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/unit/test_research_execution.py | 验证 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/unit/test_research_llm.py | 验证 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/unit/test_research_strategy.py | 验证 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/integration/test_research_bridge.py | 验证 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/runbooks/research-bridge.md | 需求、设计、操作或验收记录 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/superpowers/plans/2026-09-26-research-bridge.md | 需求、设计、操作或验收记录 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/superpowers/specs/2026-09-26-research-bridge-design.md | 需求、设计、操作或验收记录 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/validation/2026-09-27-research-bridge.md | 需求、设计、操作或验收记录 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/examples/research/export-request.local.json | 完整配置 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/examples/research/portfolio.json | 完整配置 |
| C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/examples/research/strategy.json | 完整配置 |

本机生成的5份验收数据（ignored，不纳入Git）：

- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/research-bridge-20260927/datasets/research-fixture/c164abfabc10a1c67931f124b5058d6a6c809bacea9c57a56606777758778a5b/manifest.json
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/research-bridge-20260927/datasets/research-fixture/c164abfabc10a1c67931f124b5058d6a6c809bacea9c57a56606777758778a5b/days/f2493ef49710434b8f90a98381a91c9d234416cca750ed6f9eba393301e40a69.json
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/research-bridge-20260927/datasets/research-fixture/c164abfabc10a1c67931f124b5058d6a6c809bacea9c57a56606777758778a5b/recorded/f1c983d81f368e48488cd9a9af5c40b5cb2d1541218bda6e8a14c0863f497bf9.json
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/research-bridge-20260927/datasets/research-fixture/c164abfabc10a1c67931f124b5058d6a6c809bacea9c57a56606777758778a5b/recorded-index-1ee13491996a9f91790cd191c681ccfe4cac847b369807ee1871fbd6248ea725.json
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/research-bridge-20260927/audits/experiments/63abe0459449c3480910b9af81a9c76386ed365ab74e5e4cee229e4239ac3e7e.json

过程ledger（ignored，保留其他任务目录）：C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/.superpowers/sdd/2026-09-26-research-bridge/progress.md
