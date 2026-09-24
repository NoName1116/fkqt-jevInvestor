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
- 回测接入：`FrozenCGroupTargetProvider` 只读取内容寻址冻结目标，不调用外部模型或数据库。

默认决策模型为 `deepseek-flash`，推理强度为 `high`。API Key 只能通过环境变量临时注入，不得写入仓库、命令历史、日志或数据库。
