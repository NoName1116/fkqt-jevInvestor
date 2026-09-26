# fkqt-jevInvestor

独立的 A 股日频 Jev + LLM 模拟决策与实验系统。第一版只生成信号并维护模拟持仓，不连接券商。

当前状态：Phase 5 已实现 C 组日频编排：冻结行情与确定性特征 → Jev 盈亏概率 → DeepSeek 离散动作 → 确定性仓位 → 模拟信号 → 下一交易日收盘后回放开盘虚拟成交。系统不连接券商，不发送真实订单。

运行入口：

- 行情冻结：`fkqt-jevinvestor market freeze`
- 单日 C 组：`fkqt-jevinvestor decision run-c-group`
- 日频收盘编排：`fkqt-jevinvestor daily close`
- 执行行情冻结：`fkqt-jevinvestor daily prepare-execution`
- 虚拟执行与状态：`fkqt-jevinvestor daily execute`、`fkqt-jevinvestor daily status`
- 运行说明：[`docs/runbooks/c-group-decisions.md`](docs/runbooks/c-group-decisions.md)
- 日频运行说明：[`docs/runbooks/daily-runner.md`](docs/runbooks/daily-runner.md)
- 外部一次性编排：`fkqt-jevinvestor forward run`、`forward status`、`forward validate`。
- 前向、权益和调度完整手册：[`docs/runbooks/forward-operations.md`](docs/runbooks/forward-operations.md)。
- 本阶段实际验收边界：[`docs/validation/2026-09-26-forward-operations.md`](docs/validation/2026-09-26-forward-operations.md)。
- 回测接入：`FrozenCGroupTargetProvider` 只读取内容寻址冻结目标，不调用外部模型或数据库。
- 独立研究接线：`research export/verify/targets/compare`，四组 A/B/C/D、冻结审计、严格原持仓重放与回测执行适配；[完整操作和沙耶接口](docs/runbooks/research-bridge.md)。不包含沙耶的回测引擎，收益有效性尚未验证。

默认决策模型为 `deepseek-flash`，推理强度为 `high`。API Key 只从本地未跟踪 `.env` 或进程环境加载，不得写入 Git、命令历史、日志或数据库。调度脚本未自动安装；真实历史来源重放不等于持续前向，真实 Provider 不合约时保留 NO_SIGNAL 并降级。
