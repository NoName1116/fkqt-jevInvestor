# C 组真实链路复验与前向启动准备

## 1. 本次目的与范围

继续 R40/R48/R56：排查先前 Jev 个股契约失败，独立验证完整 C_JEV_LLM 链路，准备未来小队列前向账户。不改变模型问题、概率阈值、仓位公式、公司行为口径；不覆盖旧失败，不调用备用模型，不接券商。

历史复验已成功，但不是未来前向，不是历史严格 Point-in-Time 回测，也不能证明策略盈利或模型长期稳定。下一交易日来自 Tushare 冻结日历，不能以自然日推算。

## 2. 前置环境

- JEV：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner`。
- JEV Python：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe`。
- FKQT：`C:/Users/1/Desktop/FKQTPRO/ai-signal-phase0/agent`。
- FKQT Python：`C:/Users/1/Desktop/FKQT-pro/agent/.venv-codex/Scripts/python.exe`。
- 既有凭据加载器读取已经配置的 Tushare、TypeSafe、DeepSeek 凭据；不复制到报告、配置或 Git。
- 固定模型：Jev `jev-latest`；DeepSeek `deepseek-flash`、`reasoning_effort=high`。

## 3. 契约排查实际结果

从旧失败实验 `83dabe63f4cc436e87505e47281e6e0e` 的只读数据库提取两股冻结状态，各请求一次，检查 SDK 原响应和现有严格校验。没有反复请求直到成功，没有变更正式历史记录。

| 问题 | 000001.SZ 概率和/标签/最大概率选择 | 600000.SH 概率和/标签/最大概率选择 |
|---|---|---|
| next_session_pnl | 1.00 / 一致 / 一致 | 1.00 / 一致 / 一致 |
| profitability_5d | 1.00 / 一致 / 一致 | 1.00 / 一致 / 一致 |
| drawdown_risk_5d | 1.00 / 一致 / 一致 | 1.00 / 一致 / 一致 |
| payoff_asymmetry_5d | 1.00 / 一致 / 一致 | 1.00 / 一致 / 一致 |
| data_sufficiency | 1.00 / 一致 / 一致 | 1.00 / 一致 / 一致 |

10 个问题的最大值都没有并列；两股 `_results` 均通过。旧失败只保存响应哈希和通用错误码，无法还原旧概率值，因此根因仍未确定；不能宣称解析器 Bug 已修复。本轮没有修改产品代码或放宽契约。

## 4. 独立完整 C 组实验

- Run ID：`8c12e88255af4a8ca4acadf05c2b8d1c`。
- 实验组合：`validation-8c12e88255af`。
- 日期：2026-09-24；初始模拟资金为原实验默认 1000000 元。
- 模式：REAL_SOURCE_REPLAY；strict_point_in_time=False；is_forward=False；GROSS_V1。
- 数据库：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/8c12e88255af4a8ca4acadf05c2b8d1c/replay.db`。
- 最终状态 SUCCESS；CALENDAR、EXECUTION_PUBLISH、EXECUTION_PREPARE、EXECUTE、CANDIDATE_PREPARE、DECISION_PUBLISH、DECIDE、VERIFY 八阶段全部成功。

直接只读 SQL 核验，而不是只接受 CLI 成功码：

| 记录 | 数量与状态 |
|---|---|
| Jev 候选池 | 1，AVAILABLE |
| Jev 个股 | 2，AVAILABLE |
| Jev 问题结果 | 11 |
| LLM 个股决策 | 2，AVAILABLE，000001.SZ 与 600000.SH 均 AVOID，无错误码 |
| 仓位运行 | 1，决策日 9/24、计划执行日 9/28，总目标仓位 0、现金目标 1 |
| 个股目标 | 2，AVOID、UNCHANGED、目标仓位 0，无阻断码 |
| NAV | 1，估值日 9/24，总权益 1000000 元 |
| 虚拟订单与成交 | 各 0，符合有效 AVOID 而不是模型失败 |

上游冻结日历：9/24 OPEN；9/25、9/26、9/27 CLOSED；9/28 OPEN，pretrade_date=9/24。计划执行日期来自上述实际日历。

对同一输入运行幂等检查时禁止所有外部子进程。NAV、订单、成交、Jev Attempt、LLM Attempt 和仓位运行的全部数据库行前后完全一致，仍 SUCCESS。VERIFY 审计会更新 attempts，这是运行观测，不是新增业务记录。

检查过程中的两条 SQL 曾使用错误列名 trade_date/created_at，随后按实际 Schema 改为 valuation_date/无排序列查询；没有改数据库。首次幂等探针错误地把 VERIFY 审计也纳入不变断言，改为业务记录边界后通过；没有因探针错误修改产品。

## 5. 已准备的真正前向账户

- 组合：`forward-live-c-20260928`。
- 起始日期：2026-09-28，尚未运行该日。
- 模拟初始资金：100000 元。
- 固定有序队列：600000.SH、000001.SZ，容量 2；不新增选股逻辑。
- 独立数据库迁移到 `0009_forward_operations`，账户 version=1。
- 初始化后 NAV、订单、Jev Evaluation 都为 0。
- 配置哈希：`bf3285bc45672d9d34e12780da8fba5bad7ef40a91a436aa0d485161310d8e6c`；运行前改模型、队列或路径会改变正式运行配置，不覆盖历史。
- Webhook=null；没有注册 Windows Task，没有常驻进程。

## 6. 9 月 28 日收盘后手动启动

本段命令仅在 2026-09-28 或之后、目标交易日收盘行情就绪时执行。推荐当日北京时间 21:15 后执行；不能今天把未来日期传给 run。账户已经初始化，不要重复 init，不要删除数据库。

```powershell
Set-Location 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner'
$env:PYTHONPATH='C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src'
$env:JEV_INVESTOR_DATABASE_URL='sqlite+aiosqlite:///C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/forward-live-20260928/forward.db'
& 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe' -m fkqt_jevinvestor forward run --config 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/forward-live-20260928/config.json' --date 2026-09-28
```

预计先冻结独立日历和空仓 NAV，再冻结候选及决策行情，执行 C 组决策和 VERIFY。状态 SUCCESS 才表示该日业务验收通过；RETRY_PENDING 等待返回 next_retry_at 后再执行同一命令，最多按配置尝试 4 次；DEGRADED/FAILED 不能当成通过。

查看同一账户状态：

```powershell
Set-Location 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner'
$env:PYTHONPATH='C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/src'
$env:JEV_INVESTOR_DATABASE_URL='sqlite+aiosqlite:///C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/forward-live-20260928/forward.db'
& 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe' -m fkqt_jevinvestor forward status --config 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/forward-live-20260928/config.json' --date 2026-09-28
& 'C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe' -m fkqt_jevinvestor daily status --trade-date 2026-09-28 --portfolio-id forward-live-c-20260928
```

下一日必须使用冻结交易日历的下一 OPEN 日期。第二个已观察交易日先执行已排期订单并更新 NAV，再产生新决策。没有订单也是合法结果；禁止为了测成交而伪造 ENTER。需要确定性成交路径的证据应使用既有离线测试。

## 7. 自检清单

| 检查项 | 标准值 | 检查方法和结果 | 优先级 |
|---|---|---|---|
| 模型原契约 | 不降低校验 | 10 问 SDK 输出通过原 `_results` | 必须 |
| 实际 C 链路 | 八阶段成功且数据库证据完整 | 本次 SUCCESS，3 Jev/2 LLM/1 sizing/NAV=1 | 必须 |
| 幂等 | 无新业务记录与外部调用 | 禁止 process，全部业务行前后相等 | 必须 |
| 未来账户隔离 | 独立库与固定队列 | version=1、NAV/订单/Jev=0，未访问旧账户 | 必须 |
| 日期来源 | 实际 OPEN 日，不自然日推断 | 冻结 Tushare Calendar 9/28 | 必须 |
| 收益口径 | GROSS_V1，历史不冒充前向 | 模式与限制显式列出 | 必须 |
| 外部写边界 | 无券商、调度未注册 | 只初始化本地模拟库 | 必须 |

## 8. 已知问题与排错

旧 CONTRACT_INVALID 根因没有复现，旧响应无法恢复。本次成功不证明未来稳定；若再次失败，保持原记录和 NO_SIGNAL，禁止用同一正式键覆盖或伪造概率。

永久 FAILED 没有自动重置入口；若出现 Schema/哈希/权益缺失，先保留审计并定位原因，不删 NAV、不跳过交易日、不改被冻结输入。历史当前抓取依旧不能证明过去可用性。

真正前向的首日、第二交易日和持续调度都尚未验证，模型效果和盈利没有证据。Windows 计划任务没有安装，本文件只提供手动启动命令。

## 9. 文件清单

| 文件 | 完整绝对路径 |
|---|---|
| 本交付文档 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/validation/2026-09-26-forward-live-readiness.md` |
| 历史复验数据库 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/8c12e88255af4a8ca4acadf05c2b8d1c/replay.db` |
| 历史复验冻结产物 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/8c12e88255af4a8ca4acadf05c2b8d1c/simulation` |
| 前向配置 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/forward-live-20260928/config.json` |
| 前向固定队列 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/forward-live-20260928/candidates.txt` |
| 前向独立数据库 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/forward-live-20260928/forward.db` |
| 前向产物根目录 | `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/forward-live-20260928/artifacts`，待首日运行生成，不声称已存在 |

data 下为本地运行资料，不加入 Git；文档保存脱敏事实，不包含凭据。
