# fkqt-jevInvestor 当前任务计划

日期：2026-09-21

当前阶段：独立仓库迁移已完成，下一步是 Phase 2 Task 1 固化回测协作契约

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
| P20 | 冻结 `backtest-contract-v1` | Phase 2 Task 1 | 沙耶可并行开发的稳定标签 | 待开始 |

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

## 当前停止点

Phase 0/1 已等价迁移到 `C:\Users\1\Desktop\FKQTPRO\fkqt-jevInvestor`。迁移验收结果为 Ruff 0 错误、Pyright 0 errors/0 warnings、Pytest 79 passed/1 deselected，Alembic 往返后为 `0003_phase1_audit_snapshot (head)`。下一步完成 Phase 2 Task 1 并推送 `backtest-contract-v1` 标签后，沙耶可通过 Fork + Pull Request 开始回测开发。
# Phase 1 Task 8 最终状态（2026-09-21）

- 状态：完成。
- 代码审查阻断项：已完成一次集中修复。
- 离线测试：75 项有效通过；Jev live 测试随后使用有效凭据单独通过。
- 静态检查：Ruff 通过，Pyright 0 错误。
- 数据库迁移：`0003_phase1_audit_snapshot (head)`。
- 交付边界：只生成信号和维护模拟持仓，不接入券商。
