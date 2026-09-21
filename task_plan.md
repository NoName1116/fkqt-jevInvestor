# fkqt-jevInvestor 当前任务计划

日期：2026-09-21

当前阶段：Phase 3 Jev 行情盈亏概率层已完成，下一步是 Phase 4 LLM 离散动作与确定性仓位集成

| 步骤 | 内容 | 输入 | 输出 | 状态 |
|---|---|---|---|---|
| P1 | 核实现有 FKQT 与 LLM 决策链 | FKQT 源码与设计文档 | 当前架构结论 | 已完成 |
| P2 | 确认新系统目标与边界 | 用户确认内容 | 独立信号系统范围 | 已完成 |
| P3 | 比较仓库与服务组织方案 | 三种方案 | 历史同仓方案，已被 P14 替代 | 已完成 |
| P4 | 建立新工作区与规划基线 | 已确认方案 | 四份规划文件 | 已完成 |
| P5 | 用户审阅项目规划 | `PROJECT_PLAN.md` | 修改意见或明确批准 | 已完成 |
| P6 | 编写 Phase 0 实施计划 | 批准后的规划 | 逐文件、逐测试计划 | 已完成 |
| P7 | 用户选择执行方式 | Phase 0 实施计划 | 执行授权 | 已完成 |
| P8 | 实施 Phase 0 | 批准的实施计划 | 契约、骨架、迁移和测试 | 已完成 |
| P9 | 设计 Phase 1 持仓与虚拟执行闭环 | Phase 0 基线与需求 R2/R3/R6/R10-R14/R21/R24 | 已确认设计与实施计划 | 已完成 |
| P10 | 实施 Phase 1 领域与执行规则 | 固定 Signal Fixture | Validator、执行引擎、FIFO 账本 | 已完成 |
| P11 | 实施 Phase 1 持久化与 API | Phase 1 领域模型 | 迁移、Repository、本地 Fixture API | 已完成 |
| P12 | 两交易日闭环验收 | 历史持仓、REDUCE + OPEN、D+1 行情 | 先卖后买、持仓、NAV、资产恒等式 | 已完成 |
| P13 | 重构模型责任边界 | 用户纠偏 | 行情因子、Jev 概率、LLM 离散动作、确定性仓位设计 | 已完成 |
| P14 | 改为独立纯 Python 仓库 | 用户确认 | `fkqt-jevInvestor` 独立架构设计与需求 v0.2 | 已完成 |
| P15 | 用户审阅独立仓库设计 | 书面设计 | 用户明确批准 | 已完成 |
| P16 | 编写迁移与 Phase 2 实施计划 | 已批准设计 | 两份逐文件、逐测试、逐提交计划 | 已完成 |
| P17 | 冻结回测 Contributor 契约 | 用户指定沙耶负责回测 | Entity、Protocol、文件所有权和验收规范 | 已完成 |
| P18 | 用户审阅实施计划并选择执行方式 | 两份实施计划 | 批准或修改意见 | 已完成 |
| P19 | 创建公开 GitHub 仓库并迁移 | 批准后的迁移计划 | 独立仓库与 Phase 0/1 等价基线 | 已完成 |
| P20 | 冻结 `backtest-contract-v1` | Phase 2 Task 1 | 契约提交与 PR 已完成；标签待非作者 Review 和合并后发布 | 进行中 |
| P21 | 实施 Phase 2 行情快照与确定性特征 | Tasks 2–8 | Adapter、不可变存储、特征、横截面、持久化、API、CLI | 已完成 |
| P22 | Phase 2 最终验收与运行手册 | Task 9 | 全量静态检查、离线测试、迁移状态和运行手册 | 已完成 |
| P23 | 冻结 Phase 3 Jev 盈亏概率设计 | 用户确认 C 组优先 | 可配置候选队列、候选池风险、个股盈亏概率、真实标签、失败和审计契约 | 已完成 |
| P24 | 编写 Phase 3 实施计划 | 已确认 Phase 3 设计 | C 组优先的逐文件、逐测试、逐提交计划 | 已完成 |
| P25 | 实施 Phase 3 Tasks 1—5 | 已确认实施计划 | 契约、State、真实标签、Provider、持久化与 C 组编排 | 已完成 |
| P26 | 实施 Phase 3 Task 6 | Phase 3 核心实现 | Live 门控测试、运行手册和持久记录 | 已完成 |
| P27 | Phase 3 Task 7 最终验收 | Phase 3 全部分支改动 | 全量离线测试、静态检查、迁移往返、Secret 扫描和独立审查 | 已完成 |

## Phase 0 验收记录

- Ruff：0 错误。
- Pyright：0 errors、0 warnings。
- Pytest：20 passed、1 deselected、0 failed、0 errors。
- Alembic：`0001_phase0_core (head)`。
- TypeSafe SDK：锁定 `typesafe-sdk v0.7.0`。
- Live Test：有效凭据下已运行，结果为 `1 passed in 1.42s`。
- 已知非阻断项：Starlette `TestClient` 触发 1 个 AnyIO 弃用 warning，来源为第三方依赖。

## Phase 1 验收记录

- Ruff：0 错误。
- Pyright：0 errors、0 warnings。
- Pytest：75 项离线测试有效通过、0 项有效失败；live 测试独立门控。
- Alembic：`0003_phase1_audit_snapshot (head)`。
- 业务表：11 张，全部使用 `ai_signal_` 前缀。
- 两交易日闭环：历史 `600000.SH` 持仓在 D+1 先卖出 400 股，再买入 `000001.SZ` 20,000 股。
- 成交费用：卖出佣金 1.20 CNY、印花税 2.00 CNY；买入佣金 60.03 CNY。
- 执行后现金：795,834.77 CNY；总资产：999,834.77 CNY；资产恒等式误差不超过 0.01 CNY。
- 幂等重放：第二次执行新增成交数为 0。
- 外部调用：0 次；未调用 Jev、LLM、新闻 API 或券商。
- Live Test：有效凭据下已运行，结果为 `1 passed in 1.42s`。
- 已知非阻断项：Starlette `TestClient` 触发 1 个 AnyIO 弃用 warning，来源为第三方依赖。

## Phase 2 停止点（历史）

Phase 2 核心实现和独立整分支审查修复已完成。首次完整验收得到 Ruff 0 错误、Pyright 0 errors/0 warnings、Pytest 100 passed/2 failed/1 deselected；两个旧迁移测试修正后单独得到 2 passed。审查修复覆盖防前视、UTC 往返、特征哈希、缺行情覆盖、Windows 时区、遗漏特征、冻结候选池、来源审计、日历完整性、回测决策视图和 Decimal context；直接相关测试 35 项通过。数据库目标 Revision 更新为 `0005_phase2_audit_hardening (head)`。下一步是推送修复、更新 Contract PR，获得非作者 Review 并合并，随后发布不可移动的 `backtest-contract-v1` 标签。
# Phase 1 Task 8 最终状态（2026-09-21）

- 状态：完成。
- 代码审查阻断项：已完成一次集中修复。
- 离线测试：75 项有效通过；Jev live 测试随后使用有效凭据单独通过。
- 静态检查：Ruff 通过，Pyright 0 错误。
- 数据库迁移：`0003_phase1_audit_snapshot (head)`。
- 交付边界：只生成信号和维护模拟持仓，不接入券商。

## Phase 3 验收记录

- 核心交付：候选池/个股双层 Jev State、确定性未来盈亏标签、TypeSafe 行情 Provider、四表审计持久化和 C 组优先编排。
- 完整离线测试首次运行：`192 passed, 1 failed, 2 deselected`；唯一失败为旧迁移 head 断言。
- 失败项修正后定点重跑：`1 passed`；有效离线测试为 193 项通过、0 项有效失败、2 个 live 测试未执行。
- Ruff：`All checks passed!`。
- Pyright：`0 errors, 0 warnings, 0 informations`；隔离 worktree 显式使用父工作区 Python 解释器。
- Alembic：临时库完成 `0006 → 0005 → 0006` 往返，最终为 `0006_phase3_jev_pnl_probabilities (head)`。
- Secret 扫描：实际 API Key、Bearer Token 和凭据赋值 0 个；只存在空变量、扫描正则与文档占位符。
- 兼容测试：旧语义 Provider、新行情 Provider和回测契约合计 `20 passed`。
- 静态修正定点回归：未来标签、Jev Repository 和 C 组编排合计 `37 passed`。
- 默认 Live Contract Probe：`1 skipped`，未发生外部调用；本阶段不推测真实 Provider 结果。
- 独立审查：首次结论为 `No`，1 个 Critical 与 7 个 Important 均已修复；修复后 Phase 3 定向回归 `98 passed`，回测契约 `2 passed`。
- 审查修复后静态与迁移复验：Ruff 全通过，Pyright 0 错误/0 警告，Alembic 最终仍为 `0006_phase3_jev_pnl_probabilities (head)`。
- 当前停止点：Phase 3 只产出 Jev 概率和校准标签，尚不产出交易动作；正式 C 组回测和前向信号仍需 Phase 4 的 LLM 离散动作与确定性仓位层。
- 第二轮独立审查修复：State v1 改为完整字段契约，Evaluation 租约改为数据库 CAS；定点回归与静态检查通过，等待审查者最终复核。
