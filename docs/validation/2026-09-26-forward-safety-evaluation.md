# 2026-09-26 前向安全与评估验收

## 1. 执行结果

R61–R64四项完成代码实现及离线验证。没有真实Provider请求，没有券商，没有任务注册、push或PR，没有修改沙耶backtest目录。源码位于现有feature/forward-operations隔离工作树。

| 需求 | 交付 | 证据 |
|---|---|---|
| R61 | 北京时间future/15:00前无副作用保护；CLI不发送outbox | 日期测试5通过、真实CLI副本hash不变 |
| R62 | 有界白名单诊断、具体原因、合法SDK usage、可空迁移 | 恶意字段/传输类型/序列化测试，Attempt往返 |
| R63 | 默认预览、lease围栏、CAS、累计次数、旧错误及业务证据审计 | 真实失败恢复、lease被换、过期预览、模型拒绝、NAV冲突 |
| R64 | 只读JSON/Markdown、完整分母、费用覆盖、NAV及成熟概率评分 | 缺预测分母2、成熟Brier1.14、费用4及净收益-0.04 |

依赖沿用Python/SQLAlchemy/Pydantic/Alembic/Decimal/pytest，无新计费服务或训练框架。完整运行命令、前置条件和排错已写入 `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/runbooks/forward-safety-evaluation.md`。

## 2. 实际测试与排错

1. 日期保护首轮3失败/2通过；实现后5通过。
2. 新诊断首轮3失败，随后3通过；Provider和迁移首跑32通过/1失败。原因是excluded审计在Repository重验时丢失，显式保存后失败项通过。
3. 恢复核心4失败后4通过；lease被换的测试先失败，事务围栏后通过。
4. 报告初次缺模块，随后数字3通过；空报告测试先因Fixture起始日期不匹配失败，修Fixture后发现成交表经order关联组合的真实字段，修join后该项通过。
5. 一次受影响批跑103项，102通过/1失败。唯一失败为旧迁移head断言0009，改为0010和29张业务表后只重测该项通过。
6. 独立审查后的新边界定点20项：19通过/1失败。用量已持久化，但缓存读取未带usage，导致正式结果对象相等性失败；缓存旁路审计恢复读取后只重测该项通过，正式serialization依然排除审计。
7. Jev-only FAILED身份、D5单一复权基准、真实FAILED输入哈希保留、EXECUTE业务冲突、完整CNY扣模型费用、端到端成熟评分均取得定点通过证据。
8. 29个改动Python文件最终Ruff全通过，Pyright 0 errors/0 warnings；git diff --check通过；暂存变更高熵apikey/sk模式扫描0命中。

没有重新运行旧完整全仓套件。结论限定上述受影响批跑和修复定点，不把以前368项结果冒充本轮全仓验收。

### 2.1 受影响批跑完整命令

```powershell
Set-Location 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner'
$env:PYTHONPATH='C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src'
& 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe' -m pytest tests/unit/test_forward_evaluation_metrics.py tests/integration/test_forward_evaluation.py tests/unit/test_provider_diagnostics.py tests/integration/test_forward_safety_migration.py tests/integration/test_forward_recovery.py tests/integration/test_forward_date_guard.py tests/unit/test_jev_market_provider.py tests/unit/test_deepseek_decision_provider.py tests/integration/test_jev_repository.py tests/integration/test_decision_repository.py tests/integration/test_c_group_decision.py tests/integration/test_forward_operations.py tests/integration/test_forward_alert_outbox.py tests/integration/test_forward_health.py tests/integration/test_forward_migration_guard.py tests/integration/test_migration_chain.py tests/unit/test_cli.py -q --tb=short --basetemp=data/test-runs/safety-final-affected -p no:cacheprovider
```

以上是已执行命令记录，不建议复制重用现有basetemp；复验应选新的目录，避免Windows pytest目录权限问题。

## 3. 一次独立审查及裁定

审查者只读，未派代理、未联网、未改工作树；没有第二次审查。其指出的Important均纳入本轮范围：

| 问题 | 处理 | 验证 |
|---|---|---|
| Runner失败把input_hash改None，恢复永远拒绝 | Repository未传新hash时保留原hash | 真实Runner失败→恢复→再次失败累计2，重复run不增加 |
| Jev调用后首个LLM前崩溃无法归属 | 模型调用前冻结portfolio/date/run_id/market/完整symbols身份文件 | 只有JevRunLink/Attempt、DECIDE FAILED、零Decision/sizing仍计数 |
| 整批失败日被省略，分母依赖成功记录 | 冻结运行身份和snapshot全集恢复分母；每个stage失败日显式列出 | 两股零决策时expected2/coverage0/missing2 |
| EXECUTE恢复未核对已有业务提交 | 同事务重读NAV、POST_EXECUTION和上游execution hash并纳入preview hash | A/B执行身份冲突直接拒绝 |
| 完整计费下扣模型费用一直未知 | 全部费用完整、CNY、NAV可用才计算，仍称估算 | 初始100、NAV100、模型费用4→-0.04 |

额外自检发现每天前复权基准可能变化，拼接每日单条K线会误造标签；改用D5冻结快照的完整D1–D5窗口，交易日序列仍来自5份冻结日历。测试原LOSS改为正确FLAT后通过。此变更没有修改标签阈值或决策输入。

未判断范围：真实Provider稳定性、线上部署、未执行的完整全仓套件；不以审查席位的口头结论代替执行证据。

## 4. 真实数据副本只读验收

源：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/8c12e88255af4a8ca4acadf05c2b8d1c/replay.db`，仍为0009。

副本：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/safety-evaluation-20260926/report-copy.db`，仅该副本升级0010。

配置：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/safety-evaluation-20260926/config.json`。

既有前向账户：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/forward-live-20260928/forward.db`，仍为0009，未激活或迁移。

CLI future 2099-01-01返回BLOCKED/FORWARD_FUTURE_DATE、退出2；recover默认预览返回拒绝、退出2；报告重复生成成功；上述操作后副本Get-FileHash不变。现有账户和源库只读检查版本，没有业务写入。

最终报告：

- JSON：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/safety-evaluation-20260926/reports/60bcaeefa632e679fc635bce84d4bac9a7c78c0f3546669393fd351df3d8aed2.json`
- Markdown：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/safety-evaluation-20260926/reports/60bcaeefa632e679fc635bce84d4bac9a7c78c0f3546669393fd351df3d8aed2.md`

这是历史来源实验，不是真正前向。模型调用审计为旧记录，usage/新diagnostic缺失为null；两股AVAILABLE/AVOID，完整性2/2；没有成熟窗口，PENDING_LABELS。原运行模式缺新证明为UNVERIFIED；副本操作配置与历史stage hash无法证明一致，所以总体收益MIXED_CONFIGURATION/null。没有把收益0或费用0填入未知字段。

## 5. 自检清单

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| 日期保护 | future和14:59:59阻断；15:00历史可进入 | date_guard及CLI future | 必须 |
| 诊断脱敏 | ≤16KiB，未知自由字段不保存 | provider_diagnostics | 必须 |
| 实际用量 | 合法非负整数；缺失null | SDK Fake及Repository往返 | 必须 |
| 正式兼容 | 审计不进入canonical；缓存保留usage | serialization及cache测试 | 必须 |
| 恢复并发 | 租约被换、stale均拒绝无部分写 | forward_recovery | 必须 |
| 恢复业务 | NAV/execution hash冲突拒绝 | 真实DB冲突Fixture | 必须 |
| 完整分母 | missing计入；未知不报100% | Jev-only失败测试 | 必须 |
| Brier/回撤 | 0.68/0.10手算；成熟3类1.14 | 数值与成熟报告测试 | 必须 |
| 费用收益 | 已知4 CNY，净收益-0.04 | 完整usage价格NAV集成 | 必须 |
| 模式版本 | 旧UNVERIFIED；混合不给整体收益 | Runner时钟证明与历史副本 | 必须 |
| 迁移 | head0010、29表、非空拒降级 | migration_chain/safety_migration | 必须 |
| 只读 | 数据库文件hash不变 | Get-FileHash前后比较 | 必须 |
| 静态及秘密 | Ruff/Pyright零错误；Secret模式0 | 29文件检查及暂存扫描 | 必须 |
| Contributor边界 | backtest目录无改动 | git diff路径清单 | 必须 |

## 6. 已知限制与下一实际步骤

- 旧Jev失败原响应、实际usage和旧Jev-only崩溃身份无法反向补造；关联不明明确显示UNKNOWN。
- 多份后续snapshot身份有歧义、缺完整D5窗口、缺交易日历、预测版本不符时不评分。
- 模型费用只是操作员历史价格的估算，不是账单；USD不自动换成人民币。
- 原始报告数据和数据库只在本机忽略目录，未进入公开Git；高熵格式扫描不是全面Secret安全审计。
- 正式前向首日前需操作员先备份并迁移已有forward.db到0010；本轮不替用户自动执行该账户迁移或启用任务。
- 尚未发生的连续前向、Contributor回测合并、样本外增益仍未被本轮验收证明。

## 7. 完整文件清单

下列36份文件覆盖当前四项源码、测试、需求、计划和完整文档；data产物已在第4节单列，不提交Git。

| 类型 | 绝对路径 |
|---|---|
| 需求/计划/文档 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/REQUIREMENTS.md` |
| 需求/计划/文档 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/runbooks/forward-safety-evaluation.md` |
| 需求/计划/文档 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/superpowers/plans/2026-09-26-forward-safety-evaluation.md` |
| 需求/计划/文档 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/superpowers/specs/2026-09-26-forward-safety-evaluation-design.md` |
| 需求/计划/文档 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/validation/2026-09-26-forward-safety-evaluation.md` |
| 需求/计划/文档 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/findings.md` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/migrations/versions/0010_forward_safety_evaluation.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/cli/main.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/domain/decision.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/domain/jev_market.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/operations/cli.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/operations/evaluation.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/operations/evaluation_labels.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/operations/evaluation_metrics.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/operations/recovery.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/operations/run_identity.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/operations/runner.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/persistence/decision_repository.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/persistence/forward_operation_repository.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/persistence/jev_repository.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/persistence/models.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/providers/base.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/providers/deepseek_decision.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/providers/diagnostics.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/providers/jev_market.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/services/c_group_decision.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src/fkqt_jevinvestor/services/jev_market_service.py` |
| 需求/计划/文档 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/task_plan.md` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/integration/test_decision_repository.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/integration/test_forward_date_guard.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/integration/test_forward_evaluation.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/integration/test_forward_recovery.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/integration/test_forward_safety_migration.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/integration/test_migration_chain.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/unit/test_forward_evaluation_metrics.py` |
| 代码/测试/迁移 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/tests/unit/test_provider_diagnostics.py` |
