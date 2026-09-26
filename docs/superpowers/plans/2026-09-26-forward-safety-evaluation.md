# 前向安全与评估实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 用户选择本会话连续实现，不逐项暂停。

**Goal:** 一次性交付已确认R61–R64。

**Architecture:** 在现有Runner/Provider/Repository/CLI上加法扩展。用一份0010迁移保存诊断、用量和恢复审计；报告独立只读，复用已有标签公式。

**Tech Stack:** Python、Pydantic、SQLAlchemy、Alembic、Decimal、pytest，禁止新增调度和训练框架。

**Spec:** docs/superpowers/specs/2026-09-26-forward-safety-evaluation-design.md

## Global Constraints

- 根目录C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner，已有隔离worktree，不创建新的。
- Python C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe；显式PYTHONPATH=工作树src。
- 用户明确不暂停，计划写完直接执行；R33最小RED/GREEN，失败修后只重测失败，末尾一次受影响回归和静态检查，不重复旧全套。
- 不改backtest契约/实现、不调用真实Provider或券商、不注册任务、不push/merge。
- 输出完整文档、自检、全部绝对路径和已知限制。

## Review Focus

1. UTC与北京时间跨日：未来日期不写告警或租约，T1无副作用测试。
2. 恶意SDK未知标签/模型/数值：诊断不能保存Secret，T2注入与上限测试。
3. 预览后并发运行：恢复CAS失败不产生部分审计，T3过期预览/租约测试。
4. 重复恢复与模型重评：幂等且不能重置DECIDE/VERIFY，T3测试。
5. 缺预测/缺标签/缺NAV/价格版本不匹配：不提高成功率或生成免费收益，T4空/缺口/费用/版本测试。

## Task 1：日期保护（R61）

Files: operations/runner.py，tests/integration/test_forward_date_guard.py。
Produces: run_once在业务之前返回BLOCKED/FORWARD_FUTURE_DATE或FORWARD_BEFORE_MARKET_CLOSE。

- [ ] 写未来/14:59:59/15:00/历史/时区跨日测试，假Repository任何调用直接失败，assert error_code与无目录创建。
- [ ] `pytest tests/integration/test_forward_date_guard.py -q`，预期新保护失败。
- [ ] run_once开头以北京时间检查，拒绝时不进入_alert或_run_once；保留历史与无时区语义。
- [ ] 同命令GREEN，提交“新增前向日期无副作用保护”。

## Task 2：诊断、实际用量、0010迁移（R62/R59）

Files: providers/diagnostics.py、jev_market.py、deepseek_decision.py、base.py；domain/jev_market.py、decision.py；services/jev_market_service.py、c_group_decision.py；persistence/models.py、jev_repository.py、decision_repository.py；migrations/versions/0010_forward_safety_evaluation.py；tests/unit/test_provider_diagnostics.py、tests/integration/test_forward_safety_migration.py。
Produces: build_jev_diagnostic(response,command,reason_codes)->dict；safe_usage(response)->dict|None；可空diagnostic_json/usage_json，旧零字段省略；ForwardRecoveryRecord存old-stage快照。

- [ ] 写具体SUM/LABEL/CHOICE失败原因、恶意字段、合法usage/未知usage、旧序列化、诊断上限RED。
- [ ] `pytest tests/unit/test_provider_diagnostics.py -q`，预期缺函数或诊断字段失败。
- [ ] 归一化诊断只保存已知标签、合法有限数值、有界常量；保留原response_hash与旧error_code；在服务和Repository传递保存；usage只接受实际非负整数。
- [ ] 写新迁移空往返、非空审计拒降级测试；运行两测试文件GREEN并提交。

## Task 3：恢复（R63）

Files: operations/recovery.py、persistence/forward_operation_repository.py、operations/runner.py、cli/main.py、operations/cli.py；tests/integration/test_forward_recovery.py。
Consumes: ForwardRecoveryRecord、已有lease与stage。
Produces: RecoveryService.preview(config,day,stage)->dict；commit(config,day,stage,reason,expected_stage_hash,now)->dict。

- [ ] 写真实DB预览无写、commit原错误快照、stale/lease/后续成功/DECIDE/VERIFY拒绝、重复commit幂等、attempts不归零测试RED。
- [ ] `pytest tests/integration/test_forward_recovery.py -q`。
- [ ] 事务内CAS租约与阶段hash，旧快照写不可变审计，置RETRY_PENDING且保留次数；Runner仅消费一份授权，不增加自动无限重试；不改冻结业务或模型。
- [ ] 加recover参数，默认preview；GREEN并提交。

## Task 4：只读报告（R64）

Files: operations/evaluation.py、evaluation_metrics.py、evaluation_labels.py、operations/cli.py、cli/main.py；tests/unit/test_forward_evaluation_metrics.py、tests/integration/test_forward_evaluation.py。
Consumes: 已有Portfolio/NAV/Decision/Sizing/JevAttempt/MarketSnapshot及Task2用量。
Produces: build_report(factory,config,from_date,to_date,pricing=None)->dict；write_report(report,root)->JSON/Markdown路径；标签复用build_forward_pnl_labels。

- [ ] 写收益/回撤/Brier/10桶/费用手算及null/空样本/日期缺口/混版本/未知费用RED。
- [ ] `pytest tests/unit/test_forward_evaluation_metrics.py tests/integration/test_forward_evaluation.py -q`。
- [ ] 完整性分母取冻结snapshot证券身份与held-only，并检查链接；报告全部状态与actions、实际Attempt、诊断覆盖/费用coverage；NAV缺口显式拒绝收益结论。
- [ ] 从后续冻结快照构造5个真实交易日标签，检查identity/版本/截止日，未来未成熟不评分；盈亏四问单列，不为data_sufficiency生成标签。
- [ ] 使用Decimal，内容hash排除生成时间，JSON/Markdown冲突不覆盖；report参数附可选pricing-json，不调用Provider。
- [ ] GREEN并提交。

## Task 5：集成文档和最终验收

Files: docs/runbooks/forward-safety-evaluation.md、docs/validation/2026-09-26-forward-safety-evaluation.md、task_plan.md、findings.md。
- [ ] 运行新增和受影响的Provider/Repository/Runner/迁移/CLI测试一次；失败修后仅重测失败项。
- [ ] Ruff/Pyright新增和改动文件一次，失败定点；Secret扫描和diff --check。
- [ ] 用独立临时库生成完整报告、预览恢复、验证CLI future阻断，不升级或污染真实账户。
- [ ] 一次fresh最终审查，集中修Important/Critical，每项RED/GREEN，不复审；保存所有ruling与knownissues。
- [ ] 保存完整依赖/运行/故障命令、自检、文件清单；只本地提交，不外部集成。
