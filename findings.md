# AI Signal Lab 调研与决策记录

日期：2026-09-21

## 已核实事实

1. 现有 FKQT 的实际交易动作由确定性程序产生。
2. 现有 LLM Overlay 在基线订单入队后执行，只做影子审计，不改变订单。
3. 现有因子契约主要表达单一数值，不足以保存 Jev 的概率分布和置信度。
4. 现有 Java 证据字段使用 `kind` 和 `summary`，Python Overlay Adapter 要求 `supports` 和 `text`，存在接口不一致。
5. TypeSafe 于 2026-09-15 公开 Jev，定位为类型化概率决策模型，当前处于 Early Access。
6. TypeSafe 已公开官方 Python SDK `typesafe-sdk`、官方 TypeScript SDK `@typesafe-ai/sdk`、托管 API 文档和 Noul、Choice、Score 响应结构。
7. TypeSafe 官方工作流强调把复杂任务拆分成 Noul、Choice 和 Score 等独立窄问题，再由代码组合结果。

## 已确认决策

1. 采用独立 GitHub 仓库 `fkqt-jevInvestor`，不再作为 FKQT 子目录继续开发。
2. 第一版为纯 Python FastAPI + CLI，不开发前端。
3. 第一版运行 A 股日频，收盘决策、下一交易日开盘虚拟成交。
4. 不连接券商，只生成信号并维护模拟持仓。
5. LLM 只输出 `ENTER/KEEP/EXIT/AVOID` 离散动作，不输出仓位、数量或价格。
6. Jev 读取代码计算的压缩行情特征，只输出类型化概率。
7. 仓位和数量由版本化确定性仓位引擎计算。
8. 第一版不建设新闻、公告、政策和行业事件语义采集层。
9. FKQT 只作为 REST 或不可变文件快照上游，不是 Python 依赖。
10. 正式接收的 FKQT 数据必须冻结到新系统，离线回放不得要求 FKQT 在线。
11. 已验收的 Phase 0/1 代码迁入新仓库，不重新实现。
12. 新系统不复用现有 `LlmOverlayShadowService`。

## 独立仓库架构修订（2026-09-21）

1. GitHub 仓库名为 `fkqt-jevInvestor`，Python 包名为 `fkqt_jevinvestor`。
2. 第一版只交付 FastAPI、CLI、回测、实验和虚拟账本；前端延期。
3. 复用 FKQT 前端仅代表未来可复用 UI 组件与视觉规范，不复制旧交易状态管理。
4. GitHub 仓库公开；外部 Contributor 可通过 Fork + Pull Request 协作，需要直接推分支时再授予 Collaborator。`main` 只接受 Pull Request，并要求自动化检查和非作者 Review。
5. 旧 `ai-signal-lab` 在新仓库迁移验收完成前保留；删除需要迁移后再次确认。
6. 回测模块由 Contributor 沙耶负责；她在独立仓库迁移完成且 `backtest-contract-v1` 标签发布后开始开发。
7. 回测引擎只依赖 `ReplayDataProvider`、`TargetProvider` 和 `ExecutionPort`，不直接依赖 FKQT、Jev、LLM、FastAPI 或 SQLAlchemy。
8. 沙耶只修改 `backtest/`、`tests/backtest/` 和回测运行手册；核心 Entity 变更必须使用独立 Contract PR。

## 规划假设

1. 第一版候选池来自 FKQT 已配置股票池。
2. 默认初始模拟资金为人民币 1,000,000 元，实验创建时可覆盖。
3. 现有 FKQT 能够提供行情、交易日历、股票基础信息和虚拟成交规则的只读能力。
4. Jev 正式集成前能够获得有效 `TYPESAFE_API_KEY` 和账号模型权限。
5. 最终决策 LLM 支持严格结构化输出或可通过独立 Schema Validator 验证。

## 已知风险

1. Jev 仍处于 Early Access，API 契约和服务可用性可能变化。
2. 不保存新闻全文会限制未来重新推理的完全复现能力。
3. 第三方语义 API 的授权、保留期限和限流可能约束缓存策略。
4. LLM 拥有最终决策权会产生高换手、仓位集中和风格漂移风险；由于系统不接券商，这些风险被限制在模拟实验范围内。
5. 组合级上下文可能增长过快，必须通过候选池限制、字段裁剪和版本化摘要控制输入大小。

## 外部资料

- https://typesafe.ai/blog/introducing-system-one-models-and-jev
- https://typesafe.ai/
- https://evals.typesafe.ai/
- https://typesafe.ai/legal/mca

## Phase 0 实施发现

1. 依赖解析实际锁定 `typesafe-sdk v0.7.0`，官方异步入口为 `AsyncTypeSafeClient.system_one()`。
2. Jev 的 8 个窄问题可在一次 `system_one()` 请求中批量提交；业务层通过 `SemanticFactorProvider` 隔离 SDK 类型。
3. SDK 响应必须严格校验 Choice 标签、概率和与八因子完整性，不能对未知标签或缺失结果做静默归一化。
4. 默认离线套件使用 Fake Client，不访问 TypeSafe 网络；实际结果为 20 passed、1 deselected。
5. Live Test 受 `RUN_LIVE_JEV_TESTS=1` 和 `TYPESAFE_API_KEY` 双重门控。2026-09-20 未提供 Key，因此 Live Test 未运行。
6. 当前 Windows 主机没有系统级 Python 和 uv；已使用 Codex bundled Python 3.12.14 与 `python.exe -m uv` 完成安装和验收。
7. 当前 Windows 全局 pytest 临时目录受 ACL 限制；项目内 `.pytest_cache/<任务名>` 可稳定承载测试临时文件。
8. Phase 0 数据库 Revision 为 `0001_phase0_core`，两张业务表均使用 `ai_signal_` 前缀。
9. Starlette `TestClient` 当前产生 1 个 AnyIO 旧别名弃用 warning；这是第三方兼容提示，不影响 Phase 0 测试结果。

## Phase 1 实施发现

1. Phase 1 数据库 Revision 为 `0002_phase1_portfolio_execution`，新增 9 张表，与 Phase 0 的 2 张表合计 11 张 `ai_signal_*` 业务表。
2. 组合、持仓、持仓批次、Signal Fixture、虚拟订单、虚拟成交、不可变快照和 NAV 均保存在独立数据域，没有引用旧 FKQT 业务表。
3. D+1 执行按 `CLOSE`、`REDUCE`、`OPEN`、`ADD` 排序；卖出实际释放的现金才可进入买入资金预检。
4. 买入资金不足时整批买单拒绝，不按比例缩仓；卖出失败时不存在的计划卖出金额不会被当作可用现金。
5. A 股整手、T+1、滑点、佣金、卖出印花税、停牌、涨跌停、容量和 D+2 过期均已有确定性测试。
6. `PARTIALLY_FILLED` 在 Phase 1 是终态，剩余数量不会跨日追单。
7. Repository 使用组合 `version` 做乐观并发控制；两个并发执行请求只写入一组 Fill，冲突方返回 `PORTFOLIO_VERSION_CONFLICT`。
8. 数据库事务中途失败会回滚现金、持仓、订单和成交，不留下部分写入。
9. HTTP API 对金额使用 Decimal 字符串，数据库 URL、SQL、Secret 和堆栈不会进入错误响应。
10. Fixture 信号路由只在 `AI_SIGNAL_ENV=local` 或 `test` 环境挂载；production 环境返回 404。
11. 两交易日闭环实测：D-1 历史持仓 `600000.SH:800@10`，D 日提交 `REDUCE + OPEN`，D+1 先卖 400 股，再买入 `000001.SZ` 20,000 股。
12. 闭环执行后现金为 795,834.77 CNY，总资产为 999,834.77 CNY，现金加持仓市值与总资产一致。
13. 完整离线套件真实结果为 72 passed、1 deselected；被排除的是需要外部凭证和网络的 Jev live test。
14. 本地主数据库已升级并确认处于 `0002_phase1_portfolio_execution (head)`。
15. Phase 1 初版曾使用 `decision_date + 1 calendar day`；Task 8 已改为 Fixture 必须显式携带 `planned_execution_date`。正式运行仍必须由交易日历提供下一有效交易日。
16. Phase 1 仍未交付最终决策 LLM Provider、Jev 实际调用与因子持久化、新闻 REST Provider、真实 FKQT 行情 Adapter、券商接入和真实订单。
# Task 8 最终审查修复（2026-09-21）

- Fixture 现在必须显式携带 `planned_execution_date`；服务不再用自然日推算下一交易日，交易日未知时请求无法通过契约。
- 执行前同时校验行情映射键、证券代码和 `trade_date`，禁止未来或错日行情参与资产估值和成交。
- Repository 重建账本时恢复完整 NAV 历史，连续执行日的 `daily_return` 和 `max_drawdown` 使用真实历史基线。
- 首次无订单日仍会完成估值、快照和 NAV；重复调用返回同一日历史结果。
- 查询执行订单覆盖 `planned_execution_date <= trade_date`，错过执行日的订单写入 `EXPIRED/SIGNAL_EXPIRED`。
- Fixture 信号提交和 Fixture 行情执行端点均只在 `local/test` 暴露，production 返回 404。
- Fixture 幂等检查早于当前持仓校验；已成交后重提原请求仍返回原批次。
- 历史重放按请求交易日精确读取 `POST_EXECUTION` 快照，不再混入最新日期。
- SQLite 输入资金上限收紧为 `9,999,999,999.9999`，明确规避超出安全精度范围的金额；生产数据库仍建议使用原生精确 `NUMERIC`。
- 组合状态读取支持 `as_of`，T+1 可卖数量按批次日期计算，持有交易日按已保存 NAV 日期计算。
- 候选池和现有持仓都必须有显式信号，遗漏返回 `SIGNAL_COVERAGE_INCOMPLETE`。
- 保存 SignalBatch 时原子写入 `DECISION_INPUT` 持仓明细快照及哈希，并通过外键关联批次。
- 最终离线验收：完整运行得到 `74 passed, 1 failed, 1 deselected`；唯一失败是旧测试使用错日行情，修正测试数据后该失败项单独重跑 `1 passed`。因此有效结果为 75 个离线测试全部通过、1 个 live 测试未运行。
- Ruff：`All checks passed!`。Pyright 显式使用项目解释器：`0 errors, 0 warnings, 0 informations`。Alembic：`0003_phase1_audit_snapshot (head)`。

## 独立仓库迁移验收（2026-09-21）

- 源基线提交：`4210322`、`5e43ea8`、`7ba8221`。
- 迁移提交：`1ff159a`、`5894591`、`dc262ae`、`0cef947`、`0111c9f`。
- Ruff：`All checks passed!`。
- Pyright：`0 errors, 0 warnings, 0 informations`。
- Pytest：`79 passed, 1 deselected, 2 warnings`；live 测试未运行。
- Alembic：`upgrade head → downgrade base → upgrade head` 完成，最终为 `0003_phase1_audit_snapshot (head)`。
- 两日 Fixture：现金 `795834.7700`，总资产 `999834.7700`，卖出佣金 `1.2000`，印花税 `2.0000`，买入佣金 `60.0300`，持仓数量 `400/20000`，重复成交 `0`。
