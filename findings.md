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
10. Phase 3 首次完整离线验收为 `192 passed, 1 failed, 2 deselected`；唯一失败是旧迁移链测试仍把 Alembic head 和业务表数量固定为 Phase 2 的 `0005/13`。
11. 旧迁移断言更新为 Phase 3 的 `0006/17` 后，只重跑该失败项得到 `1 passed`；有效离线结果为 193 项通过、0 项有效失败、2 个 live 测试未执行。
12. Ruff 首次发现 21 个 Phase 3 风格问题，19 个由安全机械修复完成，2 个手工修正为 `pairwise` 和明确 `IntegrityError`；重跑结果为 `All checks passed!`。
13. Pyright 在隔离 worktree 中必须显式使用父工作区解释器；最终结果为 `0 errors, 0 warnings, 0 informations`。
14. Alembic 独立临时 SQLite 完成 `upgrade head → downgrade 0005 → upgrade head`，最终为 `0006_phase3_jev_pnl_probabilities (head)`。
15. Secret 扫描未发现实际凭据；命中项仅为空 `.env.example`、扫描正则文本和运行手册中的密码管理器占位符。
16. 旧语义 Provider、新行情 Provider和回测契约兼容测试为 `20 passed`；静态修正涉及的标签、Repository 和编排定点回归为 `37 passed`。
17. 独立 Phase 3 审查发现费用版本、问题集完整性、State 白名单、Jev 边界 Point-in-Time、真实标签交易日/可成交性、空候选池、Claim lease 和失败延迟审计问题；全部映射到 R15/R16/R22/R39/R40/R42 并进入阻断修复。
18. `pnl-label-criteria-v1` 现在固定 `round-trip-cost-v1 = 0.00100000`，标签实体保存成本、成本版本、行情哈希和交易日历哈希；D+1 至 D+5 必须匹配冻结交易日历，D+1 必须通过停牌与一字涨跌停可成交校验。
19. `AVAILABLE` 结果按 scope 强制完整问题集和冻结问题契约；Jev State 使用字段白名单，Provider 在外部调用前重新验证 State。
20. Jev Builder 在独立入口再次校验决策截止、来源审计、日线日期和证券状态日期；空候选池或必要聚合缺失记录 `DATA_UNAVAILABLE`，不调用 Provider。
21. Attempt 增加 lease 与 owner token；过期后新 owner 使用递增序号接管，旧 owner 无法提交。Provider 失败保存真实开始、结束和延迟，不再固定为 0。
22. 独立审查修复后的 Phase 3 定向回归为 `98 passed`；回测契约另行定点回归为 `2 passed`。
23. 审查修复后 Ruff 为 `All checks passed!`，Pyright 为 `0 errors, 0 warnings, 0 informations`，Alembic 临时库再次完成 `0006 → 0005 → 0006` 并停在 `0006_phase3_jev_pnl_probabilities (head)`。
24. TypeSafe SDK 依赖从宽泛的 `<1` 收紧为已验证的 `typesafe-sdk==0.7.0`，并新增真实 `SystemOneResponse`/`ChoiceAnswer` 离线契约测试。
25. 第二轮独立审查指出 State 只限制“不得多字段”但未限制“不得少字段”；现已要求 Universe metrics/coverage 与 Symbol security 字段全集完整，并要求缺失特征与 `missing_reasons` 一一对应，特征计数与实际字段数一致。
26. Evaluation 主记录现保存当前 owner token 和租约截止时间；过期接管、成功提交和失败提交都使用数据库条件 `UPDATE` 作为 CAS 裁决。两个 worker 并发抢占时只有一个获得新 Attempt，租约已过期的旧 owner 无法同时写入终态。
27. 第二轮修复定点验证：State/Provider/Repository 受影响测试 44 项通过；Repository、C 组 Service 与 Phase 3 迁移回归 19 项通过；新增过期租约并发测试 3 项通过。Ruff 全通过，Pyright 0 errors/0 warnings。
28. 最终复核补充发现 Universe coverage 可能字段齐全但数值矛盾；现已要求计数非负、计数和等于候选实际数量、覆盖率处于 0—1 且与计数一致、缺失数量与缺失原因相互一致，空候选池只能使用 `coverage_ratio=None`。相关 State/Builder/Provider/Service 定点回归 57 项通过。

## Phase 4 LLM 离散动作与确定性仓位设计（2026-09-21）

1. 用户确认首个真实决策 LLM Provider 使用 DeepSeek；业务边界保持 OpenAI-compatible，Provider Base URL 与模型可替换。
2. DeepSeek 官方 JSON Output 要求请求显式声明 JSON，官方 Responses API 还支持 JSON Schema；本系统仍以本地 Pydantic 作为最终业务契约裁决，避免把传输格式合法误当作动作合法。
3. OpenAI 官方 Python SDK 提供 Pydantic Structured Outputs parsing；第一版直接使用异步兼容 Client，不引入 LangChain、PydanticAI 或 Instructor 的自动 Agent/修复层。
4. 正式动作逐证券独立调用，模型只输出 `ENTER/KEEP/EXIT/AVOID`；`NO_SIGNAL` 只由系统失败归一化产生。
5. 仓位版本固定为 `position-sizing-v1`，使用波动率和流动性确定目标，并应用 10% 单票、80% 总仓位与 20% 最低现金约束；决策前已超限时只阻止新增风险，不绕过 LLM 自动清仓。
6. 2026-09-21 复核官方模型列表确认 `deepseek-chat` 已于 2026-07-24 退役；第一版默认模型必须使用当前有效的 `deepseek-flash`，并保存实际 model ID。
7. 外部资料：https://api-docs.deepseek.com/guides/json_mode/、https://api-docs.deepseek.com/api/create-response/、https://api-docs.deepseek.com/quick_start/pricing/、https://github.com/openai/openai-python/blob/main/helpers.md。
8. 用户明确要求优先使用最强的 DeepSeek 4.1 系列能力；Phase 4 因此冻结默认 `deepseek-flash` 与 `reasoning_effort=high`，并保留模型配置和实际调用模型审计字段。
9. 现有 `ExperimentArm` 的 A/B/C/D 名称与 R26 含义相反；Phase 4 使用纯加法加入规范枚举，保留旧成员供 Contributor 代码读取，新记录只写规范名。
10. 现有 Phase 1 Signal 表强制要求 `confidence`，但 LLM 不得输出或合成数值置信度；兼容 Adapter 固定写 `Decimal(0)` 表示 `NOT_PROVIDED`，正式 Decision/Sizing 契约不包含该字段。
11. DeepSeek Responses API 的推理强度字段是 `reasoning={"effort": "high"}`；`reasoning_effort` 仅是配置名，不直接作为请求字段发送。
12. C 组在调用 LLM 前验证 decision date、cutoff、D+1、候选池 hash、行情 hash 和 Jev 全证券覆盖；Jev 或特征失败会保存正式 `DATA_UNAVAILABLE/NO_SIGNAL`，不会调用备用模型。
13. Decision 正式键的成功或失败终态均不可变并可跨 `run_id` 复用；只有过期的 `IN_PROGRESS` 租约允许接管。显式模型重评必须改变 Provider/模型配置身份或冻结输入，不能靠更换 `run_id` 覆盖旧历史。
14. 仓位计算固定为 `position-sizing-v1`，LLM 输出契约没有仓位、数量、价格或置信度字段；现有 Signal 兼容层中的 `confidence=0` 仅表示未提供，正式决策和仓位表不读取它。
15. C 组冻结回测文件使用 `<root>/<dataset_id>/<decision_date>/<content_hash>.json`，写入采用临时文件加原子替换，加载时重新计算内容哈希并验证数据集、日期、三类输入 hash 和仓位版本。
16. 单日 CLI 需要显式配置冻结 snapshot hash 和候选证券列表；候选列表不会由 LLM、Jev 或本地排序算法自行构造。缺少冻结输入或 Provider Secret 时，在创建 Client 前以稳定错误退出。
17. Phase 4 首轮独立审查发现 9 个 Important：Jev 完成时间误判、IN_PROGRESS 落信号、失败重试改写历史、跨 run 唯一键冲突、冻结输入绑定不足、仓位取整越界、SDK 隐式重试、Provider 配置未入正式键、历史日期读取当前持仓。现已全部修复并加入定向测试。
18. 仓位缩放统一使用 `ROUND_DOWN`；缩放后低于最低开仓权重的 `ENTER` 变为 `BLOCKED/MIN_ENTRY_POSITION_AFTER_SCALING`，总仓位不超过 80%，现金不低于 20%。
19. 不同 run 但相同正式内容复用同一个内容寻址 SignalBatch、Signal 和 VirtualOrder，同时分别保存 PositionSizingRun 与 PositionTarget 审计记录。
20. 当前冻结目标 Store 可由 Python 直接使用，但数据库到冻结 Target bundle 的一键导出 CLI 尚未实现，已作为 Phase 5 已知非阻断项记录。
21. Phase 2 正式快照包含 23 项特征，Jev 只要求其中 20 项；C 组 Gate 必须做必需集合子集检查，并只以必需特征缺值作为 LLM 阻断条件。可选 `gap_fill_pct` 缺失不应拒绝合法输入。
22. Phase 4 最终独立复核基于 HEAD `7ab7193` 给出 `Ready to merge: Yes`；原 9 个 Important 全部关闭，无剩余阻断项。

## Phase 5 日频运行（2026-09-23）

1. 现有 C 组单日 CLI 使用随机 `run_id`，不能直接作为无人值守的幂等入口；新 `daily close` 将组合、日期、冻结快照、候选池、容量、组合版本与 Provider 配置映射为稳定 UUID。
2. 现有 Phase 1 执行引擎需要 D+1 开盘价和当日未复权收盘价；因此前向虚拟成交须在 D+1 收盘数据齐备后回放，不能宣称开盘即时执行。
3. 现有组合执行在重复 NAV 日期返回历史结果，但原本不绑定执行行情输入；Phase 5 把执行包 hash 写入 `POST_EXECUTION` 快照的 `details_json`，同日换包重跑拒绝。
4. FKQT Manifest 的候选池只列候选证券，而 C 组还要覆盖队列外持仓；Provider 现将候选池成员校验与全量行情证券范围分离，保留候选顺序审计。
5. 执行原始行情文件先结构校验并生成内容寻址包；`daily execute` 再核对持仓、到期订单、日期、证券身份、开市状态和 hash，并冻结审计副本。
6. 当前阶段仍依赖外部调度器与可信上游执行日 JSON；没有券商连接、实时开盘下单、内部告警发送或 A/B/D 批量实验。
7. 独立 Review 修复前完整离线测试为 `287 passed, 3 deselected`；修复后最终为 `289 passed, 3 deselected`。Ruff 全通过，Pyright `0 errors, 0 warnings`，Alembic head 仍为 `0007_phase4_llm_position_sizing`，本阶段未增加数据库迁移。
8. Secret 扫描无实际 Key 或长 Bearer Token 命中；两条 warning 分别来自 Starlette/AnyIO 和 FastAPI 的第三方弃用提示。
9. 独立 Review 指出执行时间/日期、缺收盘价、历史重跑覆盖、决策输入变更与状态查询五类阻断或重要问题；修复采用收盘门禁、计划执行日查询、收盘价必填、已执行日先查 NAV、模型调用前比较稳定 run_id，以及决策日/执行日待办数分列。
10. 复核又发现状态字段按日期跨组合混入快照与订单；最终改为通过本组合该日 `run_id` 关联决策输入、通过本次 `signal_batch_id` 关联虚拟订单。独立审查确认所有报告项关闭；最终离线测试仍为 `289 passed, 3 deselected`，Ruff/Pyright 全通过。

## FKQT → 本项目 Tushare 前向行情桥接（2026-09-23）

1. FKQT 既有三证券 Fixture 不适合动态候选池；新发布器独立接受有序候选与队列外持仓，六类决策数据复用原 Manifest/Parquet 存储契约，候选 rank 进入内容哈希。
2. 执行行情由 FKQT 的 `get_execution_context` 提供，`daily.amount` 的千元单位须乘 1000 转为 CNY；官方 `stk_limit` 缺权限或缺字段时不能推算涨跌停价。
3. 本项目只读 FKQT 文件边界，不导入 FKQT 代码或直连 Tushare；`daily prepare-execution` 保留原手工 JSON 入口，同时新增 `--manifest-root`，两者互斥。
4. 执行包保留 `ExecutionBundleV1` 原内容哈希；来源 Manifest ID 与内容哈希写入同目录不可变 `.origin.json`，避免修改历史包 Schema 与幂等键。
5. `daily required-symbols` 从指定日期的组合持仓和截至该日待执行订单导出排序去重的全集；执行时再次根据持久化状态验证覆盖，不依赖发布者声称的集合。
6. 现有账本不能处理当日公司行为，发布器明确拒绝，不能把拆股、送股或派息误记为纯价格盈亏。停牌缺当日估值、正常交易缺开盘/收盘/成交额/涨跌停价同样拒绝。
7. FKQT 测试发布器生成的决策与执行 Manifest 已在本项目真实读取，不是手写格式的单仓库模拟。审查修复后完整离线测试 `307 passed, 3 deselected`，FKQT 相关测试 `27 passed`，Ruff/Pyright/Alembic 均通过。
8. 未验证真实账号权限与 Tushare 当日数据可用时间；外部调度、候选文件来源、现金空仓日空证券执行包和主动告警仍是前向值守前置事项。
9. 独立审查指出正常交易证券缺 D 日日线仍能发布旧价格，现按停牌记录逐证券验证唯一且完整的 D 日 bar；正常交易缺当日 bar 返回 `DECISION_DAY_BAR_MISSING`，不生成快照。
10. 六份决策 Manifest 改为同级暂存、逐份回读校验、完整目录整体发布；中途失败无可见半套输入。读取端按 `rank`、Manifest 请求列表与配置逐项核对候选顺序。
11. 前向决策 Manifest 的 `data_cutoff` 为交易日 15:00 的行情观察截止点，`fetched_at` 独立记录实际抓取时间。两者无法证明 Tushare 后续未修订历史数据，故不能把此文件桥接宣称为严格历史 point-in-time 数据源。
12. 执行包来源侧文件现在对手工与 FKQT 路径均不可变；加载时验证来源侧文件，执行时把 `source_manifest_id` 写入 `POST_EXECUTION` 审计并用于同日幂等冲突判断。旧无来源侧文件的手工包仍可读取。
13. 本项目契约测试加入 FKQT 发布器 FakeClient 实际生成的固定 Manifest/Parquet 金样本；运行时仍不导入 FKQT 包。
