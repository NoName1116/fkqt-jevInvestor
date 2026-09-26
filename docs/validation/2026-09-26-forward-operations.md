# 前向补齐事项 2–6 验收事实

日期：2026-09-26。需求 R54–R60，兼顾 R14/R15/R24/R25/R44/R46–R53。功能分支 JevInvestor `feature/forward-operations`，FKQT `feature/forward-operations-publisher`。未推送、未合并、未启用长期调度。

## 1. 实施范围

| 事项 | 产出 | 当前验收边界 |
|---|---|---|
| 2 | 独立日历 V2、纯现金 NAV | 空仓及存续权益保护、连续 NAV 定点通过 |
| 3 | 冻结有序 Queue、版本/容量/来源哈希贯穿 | 自动传递并入正式运行键，不新增选股 |
| 4 | 权限探针、双 Provider、独立小账户、三模式报告 | 9/9 合成契约通过；真实个股模型链路仍降级 |
| 5 | 一次性运行、租约、有限退避、恢复、outbox、PS 脚本 | 离线定点通过；没有注册或持续运行 |
| 6 | GROSS_V1 登记/EX/PAY/LIST、FIFO、原子事务 | 生命周期、周末、崩溃、零碎股拒绝通过 |

## 2. 真实调用记录

| Run ID | 模式 | 结果 |
|---|---|---|
| 1270e12aa96f4948aa72ae29fcae86d5 | PROVIDER_CONTRACT | 9/9 可访问，但旧 deepseek-chat 不算 R44 验收 |
| ab42b7c4c5284dbe86a608f70f674493 | PROVIDER_CONTRACT | 7 Tushare + Jev jev-latest + DeepSeek deepseek-flash 全部 AVAILABLE |
| c754f7c5df494467b65a8e956258ce6a | REAL_SOURCE_REPLAY | 9/24 真实来源，NAV=1；两股缺换手率，NO_SIGNAL。旧 VERIFY 显示 SUCCESS 的结论已被后续 SQL 推翻，保留原审计 |
| 83dabe63f4cc436e87505e47281e6e0e | REAL_SOURCE_REPLAY | 补流通股本后 20/20 必需特征齐；UNIVERSE AVAILABLE、两 SYMBOL CONTRACT_INVALID、两 LLM NO_SIGNAL；正确 DEGRADED，NAV=1，无订单 |

真实报告与独立数据库绝对根为 `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/data/validation/<Run ID>/`，Git 忽略。所有历史运行 strict_point_in_time=False、is_forward=False、GROSS_V1。

Tushare 9/25 探针行数：daily=0、trade_cal=1、stock_basic=1、namechange=3、suspend_d=0、stk_limit=0、dividend=78。空列表为 EMPTY_NOT_PROOF，不是覆盖完整证明。新增 daily_basic 的真实发布成功，提供 9/24 流通股本；未用免费替代数据或推算假股本。

后续单股诊断请求再次返回可通过的五问响应，但不更新原失败记录、不据此把整批改成成功。原 CONTRACT_INVALID 的稳定码为 JEV_RESPONSE_CONTRACT_INVALID，尚无证据可以保证真实个股响应长期满足契约；不放宽概率/标签校验。

LIVE_FORWARD 的未来日期状态由离线测试验证为 LIVE_FORWARD_PENDING。真正未来跨两个已观察交易日的闭环尚未发生，持续调度未启用；不把历史重放称为前向或严格回测。

## 3. 自检记录

| 检查项 | 标准值 | 实际检查方法与结果 | 优先级 |
|---|---|---|---|
| JEV 离线全量 | 无有效失败 | 首次 368 passed、2 failed、4 deselected；两失败及新增 API 测试定点 3 passed，未重复全套 | 必须 |
| FKQT 发布回归 | 无失败 | 六文件 24 passed | 必须 |
| Ruff/Pyright | 0 error | 首轮 23/15 问题；受影响文件定点修复与复验记录继续补充 | 必须 |
| 迁移往返 | 空库安全、非零资产拒降级 | migration-chain 定点通过 0009→0007→0009 和 base，拒降级补验中 | 必须 |
| 调度 XML | 5 分钟/105 分钟/IgnoreNew | 内存 Export-ScheduledTask 断言通过，未注册 | 必须 |
| Secret | 暂存/交付无真实值 | 最终扫描待执行，不打印命中值 | 必须 |
| 真实模型 | 无伪造成功 | 合成契约通过，真实个股失败明确 DEGRADED | 必须 |
| Contributor 边界 | 不改沙耶回测实现 | 最终 diff 路径核验待执行 | 必须 |

首次离线两失败：`test_migrated_revision_chain_is_repeatable_and_reversible` 的旧 0007 断言；`test_execute_next_day_returns_orders_fills_snapshot_and_nav` 的 NAV 零字段兼容差异。已修，不删除测试或降低断言。

## 4. 已知问题和下一步

1. 真实个股 Jev 返回仍可能不合约，C 组不能称已通过完整实盘资料模型验收；正式系统按 NO_SIGNAL/DEGRADED 保持持仓，不静默换模型。
2. 没有未来第二交易日资料，因此没有验证真实跨日订单成交；已有相关离线执行测试不替代未来观察。
3. 历史资料当前抓取不能证明历史可用性，不能用于严格历史 Point-in-Time 收益结论。
4. Windows Interactive 任务只在当前用户登录时运行，注销/关机/睡眠无持续运行保证。
5. 永久 FAILED 没有自动重置入口；不能覆盖冻结输入、删除权益或跳过 NAV 缺口来恢复。
6. 第三方 Starlette/AnyIO 与 422 名称弃用告警保留，本阶段不升级无关依赖。

完整配置、依赖、初始化、运行、查看、安装、卸载和停止条件见 `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/runbooks/forward-operations.md`。
