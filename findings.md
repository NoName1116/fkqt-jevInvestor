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
13. Phase 3 不再让 Jev 输出因子占比；Jev 改为输出候选池风险，以及个股下一交易日盈亏、未来 5 个交易日盈亏、最大不利波动、盈亏不对称性和数据充分度的有限标签概率。
14. Jev 输出属于待校准预测；实际盈亏、最大不利波动和盈亏不对称标签由代码从 D+1 后冻结行情生成，不能进入 D 日正式输入。
15. 候选池状态按正式运行复用一次，个股状态按证券独立评估；持仓状态留给 Phase 4 LLM，不进入 Jev 输入。
16. 第一版彻底删除动态因子权重、权重 Profile 和概率到权重映射，Jev 和 LLM 均不生成连续权重。
17. 前向测试候选容量由每次运行的 `candidate_limit` 显式配置，不设产品级固定默认值；合格数量不足时不以不合格股票补齐。
18. C 组“确定性特征 → Jev 盈亏概率 → LLM 离散动作 → 确定性仓位”是第一版主链和最高优先级；A/B/D 仅用于消融和基线比较。
19. `run_id` 不进入 Jev State、输入哈希或正式结果键；相同冻结输入、Provider、模型和问题版本可跨运行复用，数据库使用独立 RunLink 保留每次正式运行的审计关系。

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

## Phase 2 行情快照与确定性特征（2026-09-21）

1. FKQT 已被隔离为外部数据上游；正式链路只通过不可变 Manifest bundle 或版本化 REST 响应读取数据。
2. Manifest Adapter 验证六类数据的版本、日期、内容哈希、物理 Schema 哈希、行数和路径边界；新增的 `candidate_universe` 是候选池成员和版本的权威来源。读取分区目录单文件时必须设置 `partitioning=None`，避免 PyArrow 自动注入 Hive 分区列。
3. REST Adapter 使用启动层配置的 `httpx.AsyncClient`，业务模块不保存 Base URL、认证凭据或 Client 生命周期。
4. 行情快照保存为 `<snapshot-root>/<decision-date>/<content-hash>.json`；数据库只保存引用、审计元数据和逐项版本化特征。
5. 单票行情特征全部使用 Decimal，并统一量化到 8 位；复权模式混合时价格特征稳定缺失为 `ADJUSTMENT_MODE_MISMATCH`。
6. 横截面百分位采用平均秩，单个有效值为 `0.50000000`，无有效值为 `CROSS_SECTION_EMPTY`，输入顺序不影响结果。
7. Pipeline 固定执行 Provider 获取、全量 Point-in-Time 校验、文件冻结、单票特征、横截面和数据库事务保存；未来 bar 会在数据库写入前拒绝整批。
8. CLI 命令为 `market freeze --date YYYY-MM-DD --symbols A,B --source manifest`；bundle 和快照根目录只从环境配置读取，不接受请求传入任意路径。
9. HTTP 提供 freeze、快照引用查询和特征查询；production 请求 Schema 禁止额外本地路径字段。未注入 Provider 时 freeze 返回 `MARKET_PROVIDER_UNAVAILABLE`，历史查询不受影响。
10. 严格类型检查使用 `pyarrow-stubs==20.0.0.20260819`；未来升级 PyArrow 时需要同步复核类型桩。
11. Phase 2 完整验收首次运行：Ruff `All checks passed!`，Pyright `0 errors, 0 warnings`，Pytest `100 passed, 2 failed, 1 deselected`。两个失败来自旧测试把 Alembic `head` 写死为 `0003` 和 11 张表；Phase 2 当前正确值是 `0004` 和 13 张表。修正后按测试节制要求只重跑两个失败项，结果 `2 passed`。
12. 初次验收时 Alembic 为 `0004_phase2_market_features`；审查修复新增独立 Revision `0005_phase2_audit_hardening`，不改写已推送迁移历史。
13. 非阻断警告共 2 条：Starlette TestClient 使用 AnyIO 旧别名；FastAPI 的 `HTTP_422_UNPROCESSABLE_ENTITY` 常量已弃用。两者均来自第三方调用链。
14. 初次验收时 Contract 文件相对提交 `057e0c6` 无漂移；独立审查随后发现 `ReplayDay` 会把 D+1 执行行情暴露给 `TargetProvider`。标签尚未发布，因此已在候选 v1 内新增 `DecisionReplayDay` 并同步 Contributor 契约，修复提交合并后才能发布标签。
15. 独立整分支审查发现 1 个 Critical、10 个 Important 和 2 个 Minor。Critical/Important 已进入一次集中修复：日频 cutoff 必须达到中国市场 15:00 且带时区；数据库统一写入 UTC；原特征快照哈希持久化；缺行情和陈旧行情输出稳定缺失；补齐 4 个设计特征；固定 Decimal precision/rounding；Manifest 强制冻结候选池和来源审计；交易日历必须逐日完整；TargetProvider 无法访问 D+1 行情。
16. 审查修复直接相关测试分三组运行：单元 15 passed、Adapter Contract 10 passed、Phase 2 集成 10 passed；Ruff 全量通过，Pyright 0 errors/0 warnings。
17. 两个 Minor 延后：Pydantic frozen 模型内部 Mapping 仍可变；快照文件尚未采用临时文件加原子发布。两项不会绕过当前哈希校验，但应在下一次存储加固任务处理。

## Phase 3 Jev 行情盈亏概率（2026-09-21）

1. Phase 3 使用 `JevUniverseStateV1` 与 `JevSymbolStateV1` 两层状态；候选池状态不包含单票历史或账户数据，个股状态不包含 `HELD_ONLY` 标记。
2. 候选容量由每次运行的 `candidate_limit` 显式给出；队列外持仓参与个股评估但不计容量，也不进入候选池横截面聚合。
3. Jev 只回答候选池风险与个股 1 日盈亏、5 日盈亏、5 日回撤、5 日盈亏不对称和数据充分度的 Choice 概率，不再实现因子权重。
4. 未来真实标签由 Decimal 代码从 D+1 开盘和 D+1 至 D+5 冻结行情计算；D+1 成交量或成交额为 0 被视为不可成交，返回 `LABEL_UNAVAILABLE`。
5. TypeSafe 新 Provider 与旧新闻语义 Provider 隔离；契约错误只保存清理后响应哈希，外部异常只公开异常类型。
6. `formal_key` 跨运行复用正式结果，`run_id` 通过独立 RunLink 审计；成功缓存命中不新增 Attempt，失败重试序号递增。
7. Phase 3 新增数据库 Revision `0006_phase3_jev_pnl_probabilities` 和四张表：Evaluation、Attempt、QuestionResult、RunLink。
8. C 组编排按候选池一次、证券排序逐一评估；必要特征缺失、Provider 不可用和响应契约无效都保存无概率失败结果，不退化到其他实验组。
9. 新 Live Contract Probe 使用合成状态，并由 `RUN_LIVE_JEV_TESTS=1` 与 `TYPESAFE_API_KEY` 双门控；默认离线执行不访问外部服务。
