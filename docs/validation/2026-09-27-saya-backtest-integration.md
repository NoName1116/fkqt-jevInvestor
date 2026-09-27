# 2026-09-27 沙耶回测代码审查与合入验收

## 1. 结论

按R70完成沙耶实现与现有研究接线的本地整合。来源为 [SAYAboom/fkqt-jevInvestor 的 feature/backtest-engine](https://github.com/SAYAboom/fkqt-jevInvestor/tree/feature/backtest-engine)，作者最新提交9ef4fb5c53f1d7ba99f0d4573718ea48d7259506；整合前本地HEAD为c14427d。使用no-ff合并保留作者历史，不是复制代码或squash。

完成作者代码审查、预热/哈希/逐日收益签名核对、真实研究端口四组两日联合测试、持仓队列轮换测试及独立只读审查的重要问题修复。最终受影响测试160/160通过，Ruff/Pyright零错误。仅本地开发分支 feature/forward-operations；不推送、不更新GitHub main、不关闭或合并其他PR。

## 2. 环境与前置条件

- 工作树：C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner
- Python：C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe
- 原有依赖不变，无新增安装或打包。
- 所有模型调用在外部Provider边界替换为测试响应；行情为合成冻结数据。真实回测需要完整历史来源与D+1执行包，不能使用合成测试数据证明增益。
- 源SQLite、正式账户、凭据和已有不可变研究数据均不修改。旧未跟踪缓存保留。

## 3. 审查结果与整合裁定

| 项目 | 来源状态 | 整合结果 |
|---|---|---|
| 预热接口 | warmup_dates(before,count)已实现 | 只加载校验，不调用目标/执行；日期链包括预热衔接 |
| 日记录签名 | previous_equity/initial_cash/running_peak_equity/execution | 累计收益与回撤有明确输入，不隐式补写 |
| config_hash | 完整BacktestConfig canonical JSON | 联合测试按SHA256重算一致 |
| result_hash | config/daily_records/summary，排除summary.result_hash | 保留config_hash，无自引用，联合测试重算一致 |
| 初始组合 | run_id作组合编号，现金为initial_cash | 真实研究端口接受并跨日传递后状态 |
| 实验组 | 作者旧枚举 | 三方合并保留旧成员与现有A_RULE/B_LLM/C_JEV_LLM/D_JEV_DIRECT；手册统一当前命名 |
| 候选覆盖 | 作者按features∪held | 可选候选队列接口按queue∪held；旧Provider语义保持 |
| 公司行为 | 旧契约无完整权益表示 | 研究适配继续明确拒绝；没有擅自改权益定义 |

候选覆盖曾在联合测试真实报TARGET_SYMBOL_COVERAGE_MISMATCH：数据集额外保存潜在持仓特征，但当前目标只覆盖候选与实际持仓。不能通过扩大买入候选来让测试通过，也不能修改ReplayDay删除额外特征破坏旧冻结文件。

采用可选CandidateQueueReplayProvider.candidate_symbols(decision_date)：研究Provider返回已冻结queue；通用Engine据此核对queue∪held，旧无此方法Provider继续features∪held。ReplayDay序列化与已有数据集hash不变，旧目标严格重放边界不放宽。

## 4. 独立审查与定点修复

一次fresh只读审查，4 Important、0 Critical、0 Minor；没有第二审查。四类问题已复现，修复前9个定点用例全部失败，修复后9个通过。

| 问题 | 修复 | 验证 |
|---|---|---|
| 日期链缺口仍返回成功 | 预热与正式共用前一日planned_execution_date链，必须等于下一决策日；正式日期须落在配置窗口 | 正式断链和预热→正式断链拒绝；窗口外日期在任何目标调用前拒绝 |
| 目标集合去重吞重复 | 目标数量必须等于唯一证券数量，再比较覆盖 | 同证券两个目标在执行前拒绝，执行调用0 |
| 执行结果错误仍推进状态 | 校验决策/执行日期、组合身份、返回现金、资产恒等式和组合估值 | 日期、组合、总权益、现金和持仓估值五类故障各自拒绝 |
| 重复arm覆盖第一份结果 | 在整批预检实验组唯一性 | 未创建执行器/未调用目标即EXPERIMENT_CONFIG_MISMATCH拒绝 |

旧成功测试曾使用默认执行日期和不同组合编号，预热样本全部指向同一正式日；这些不符合成功样本契约。成功测试局部绑定输入身份并修正冻结日期链，故障guards仍使用原始未绑定FakeExecutionPort，不把故障掩盖掉。

窗口外用例初始使用的FakeReplayProvider会自行过滤窗口，导致实际EMPTY_BACKTEST_WINDOW而非预期错误；换成明确违规Provider后验证DECISION_DATES_OUTSIDE_WINDOW。没有隐藏该测试构造错误。

## 5. 实际验证命令与结果

```powershell
Set-Location 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner'
$env:PYTHONPATH=(Join-Path (Get-Location) 'src')
$sayaPython='C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe'
& $sayaPython -m pytest tests/backtest tests/integration/test_saya_research_integration.py tests/unit/test_research_dataset.py tests/unit/test_research_execution.py tests/unit/test_research_strategy.py tests/unit/test_research_boundaries.py tests/unit/test_research_comparison.py tests/unit/test_research_llm.py tests/integration/test_research_bridge.py tests/unit/test_backtest_contracts.py tests/unit/test_cli.py tests/unit/test_snapshot_store.py tests/unit/test_frozen_c_group_target.py tests/unit/test_execution_engine.py tests/unit/test_position_sizing.py tests/integration/test_c_group_decision.py -q --basetemp=data/test-runs/saya-final-regression
& $sayaPython -m ruff check src/fkqt_jevinvestor/backtest src/fkqt_jevinvestor/research src/fkqt_jevinvestor/domain/backtest.py tests/backtest tests/unit/test_backtest_contracts.py tests/integration/test_saya_research_integration.py
& $sayaPython -m pyright src/fkqt_jevinvestor/backtest src/fkqt_jevinvestor/research src/fkqt_jevinvestor/domain/backtest.py tests/backtest tests/unit/test_backtest_contracts.py tests/integration/test_saya_research_integration.py
git diff --check
git diff --cached --check
```

实际结果：160 passed in 9.54s；Ruff All checks passed；Pyright 0 errors,0 warnings,0 informations；差异检查无错误。作者tests/backtest与契约合入基线60项通过。最终仅运行一次受影响套件，不声称运行了完整仓库全部测试。

四组联合使用真实DatasetStore/FrozenReplayDataProvider、ExperimentTargetProvider、确定性仓位和ResearchExecutionPort FIFO，以及沙耶BacktestEngine/run_experiment_arms。只有LLM/Jev响应为离线假件。每组2条记录，资产恒等式成立；8份独立实验审计逐日核对参数，比较AUDIT_VERIFIED。第二次replay_only得到完全相同四份结果，不再次调用模型。队列轮换后000001.SZ作为HELD_ONLY继续KEEP/EXIT范围，不买入000002.SZ额外潜在证券。

四份合成结果均累计收益0.00789400、成交费用4.6000。此数值只用于确定性接线检查，不是历史真实绩效、模型增益或投资建议。各组result_hash不同，因为配置包含各自run_id和arm。

| 组 | 结果hash |
|---|---|
| A_RULE | 88d046fe7eb18956bffe892273f10a0953c5eea5e107fb5cf32754bea07fd121 |
| B_LLM | 266488ccc2e5c458e97b351c1721e8096332ad225210fd47f6edae9a162944ad |
| C_JEV_LLM | 7410f64a3966a66f1614b0f5f6369284b58acdb2fbbc9eece6edc8605f278bdb |
| D_JEV_DIRECT | 1d8b2ad2bc9a3b27ff76b39af2a743099d9791536d6d8b235bdf558172673e99 |

结果目录（ignored）：C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/test-runs/saya-final-regression/test_saya_engine_four_arms_rea0/
四个文件名：A_RULE.json、B_LLM.json、C_JEV_LLM.json、D_JEV_DIRECT.json；各组原始审计位于同目录audit/experiments/，冻结源位于datasets/。

## 6. 完整自检与已知边界

| 检查项 | 标准值 | 检查方法/结果 | 优先级 |
|---|---|---|---|
| 回归失败 | 0 | 160/160通过 | 必须 |
| 静态错误/警告 | 0 | Ruff/Pyright通过 | 必须 |
| 四组记录 | 每组2，互不共享FIFO | 真实端口联合测试通过 | 必须 |
| 哈希自引用 | 0 | 独立SHA256重算通过 | 必须 |
| 额外证券入场 | 0 | 000002.SZ未产生目标 | 必须 |
| 队列外持仓遗漏 | 0 | HELD_ONLY进入下日决策 | 必须 |
| 重复目标/组 | 执行前拒绝 | guards通过 | 必须 |
| 日期和执行账目 | 违反即拒绝 | guards通过 | 必须 |
| 缓存重评 | 0 | replay_only完整结果相同 | 必须 |
| 作者提交保留 | 9ef4fb5为HEAD祖先 | 提交后git merge-base --is-ancestor验证 | 必须 |
| 真实外部调用 | 0 | 本轮全部Fake Provider边界 | 必须 |

完整未验证范围与裁定：
1. GitHub主仓库没有沙耶实现PR。本轮读取其公开fork分支并本地合并，远程main与现有PR没有改变；后续发布应按团队PR流程执行。
2. 真实TypeSafe/DeepSeek连通性、持续前向、真实历史四组增益、概率校准与样本外表现没有在本轮验证。
3. 未修改的FIFO/费用/仓位核心通过受影响回归，但未对所有金融业务边界重新全面审计。
4. execution_factory必须返回新实例，真实联合接线已做到；故意返回同一实例的自定义工厂不在本轮改成自动复制或并行调度。
5. 年度规模性能与极端长期数值稳定性没有负载测试。数据源逐次重验优先完整性，未擅自增加缓存优化。
6. Sortino沿用已冻结的负收益样本标准差口径，与常规下行RMS定义不同。比较第三方指标前要统一公式，不擅自改指标导致历史hash变化。
7. 公司行为回测仍需扩展旧契约；含权益日期明确拒绝。total_fees为成交费，不含未知模型费用。
8. 不清理用户原有测试缓存或工作树，不升级/启用正式前向账户。此次目录修改由R70明确授权，不扩展到无关模块。

裁定的成本：可选队列接口需要维护两种明确覆盖来源；成功与故障测试假件需要区分；保留旧Sortino需要外部比较前统一口径；只做本地合并意味着GitHub协作者暂时看不到本轮结果；未负载测试意味着不能声称长周期规模验证完成。用户要求连续执行和少测优先，不重复询问集成菜单，也不重复全仓验证。

## 7. 完整文件清单

本轮25份交付文件（含作者原文件与整合修复）：

- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/REQUIREMENTS.md
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/task_plan.md
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/findings.md
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/contributors/2026-09-21-backtesting-development-contract.md
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/runbooks/backtesting.md
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/runbooks/research-bridge.md
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/superpowers/plans/2026-09-27-saya-backtest-integration.md
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/validation/2026-09-27-saya-backtest-integration.md
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/backtest/__init__.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/backtest/engine.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/backtest/metrics.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/backtest/runner.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/backtest/serialization.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/domain/backtest.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/research/dataset.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/backtest/__init__.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/backtest/fixtures.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/backtest/test_engine.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/backtest/test_fixtures.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/backtest/test_metrics.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/backtest/test_runner.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/backtest/test_serialization.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/backtest/test_integration_guards.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/unit/test_backtest_contracts.py
- C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/integration/test_saya_research_integration.py

过程ledger（ignored）：C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/.superpowers/sdd/2026-09-27-saya-backtest-integration/progress.md

手册程序冒烟：已实际执行 `docs/runbooks/backtesting.md` 中的 `offline-rule-demo`，退出码为 0，无真实 API 调用。生成不可变结果：C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/research/demo-results/e7d2ed7a86bbc61fd9023f8f71f7672993b652ab918cbcc7c2cdb02fef0a254f.json。该合成数据结果仅验证接线，不代表真实收益。
完整可运行A组库调用与四组接口见 C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/runbooks/backtesting.md，模型与审计操作见 C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/runbooks/research-bridge.md。
