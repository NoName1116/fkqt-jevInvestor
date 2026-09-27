# FKQT → fkqt-jevInvestor 前向行情桥接设计

日期：2026-09-23。状态：用户已批准，实施中。目标：把现有 Phase 5 的手工行情文件步骤改为由 FKQT/Tushare 发布、由本项目验证并冻结的可审计数据链；不改沙耶负责的回测目录。

## 1. 需求与边界

对应现有需求 R3、R5、R6、R11—R13、R15—R18、R21、R27、R31—R33、R43、R46—R49。实施前在 `REQUIREMENTS.md` 增补本桥接的编号需求，所有代码改动逐项对应编号。

第一阶段目标是让指定交易日的候选队列和现有持仓获得完整、冻结的决策行情，并让下一有效交易日获得完整、冻结的模拟执行行情。FKQT 是唯一行情采集方；本项目不得直接调用 Tushare、导入 FKQT 内部模块或写 FKQT 数据库。执行仍是 D+1 收盘后用开盘价回放，不是开盘实时成交。不接券商，不改旧 FKQT 策略、旧模拟账户、LLM Overlay、Forward Test 或 Contributor 回测实现。

当前事实：FKQT 的 `TushareUniverseFixtureIngestion` 将证券范围固定为三只测试股票；它发布的六类 Manifest 没有 `stk_limit` 的涨跌停价格。本项目的 `daily close` 已读取六类 `TUSHARE_PRO_V1` Manifest；`daily prepare-execution` 只接受人工指定的 JSON；模拟执行在普通交易日缺涨跌停价时返回 `PRICE_LIMIT_DATA_UNAVAILABLE`。FKQT 的 `TushareClient.get_execution_context` 已具备读取 `daily`、`stk_limit`、`suspend_d` 等能力，但尚未发布本项目可消费的不可变执行数据。

## 2. 方案比较与选定

| 方案 | 优点 | 问题 | 结论 |
|---|---|---|---|
| 本项目直连 Tushare | 接线短 | 违反 R5，形成第二套凭据、采集与审计链 | 不选 |
| 继续手工 JSON | 无 FKQT 改动 | 易漏持仓和涨跌停字段，不适合持续前向测试 | 仅保留为离线导入入口 |
| FKQT 发布版本化不可变快照，本项目只读 | 复用 FKQT 客户端和现有冻结/哈希边界 | 需要两仓库协调 Schema | 选定 |

## 3. 数据流与组件

```text
外部调度器 + 显式候选队列 + 组合持仓/到期订单证券
    ↓
FKQT 新的通用只读发布命令 → Tushare daily / trade_cal / stock_basic /
                            namechange / suspend_d / stk_limit
    ↓ 写入版本化 Manifest + Parquet/JSON，不可变、含哈希和抓取时间
fkqt-jevInvestor FKQT 文件 Adapter → 校验 Schema、来源、日期、集合与哈希
    ├─ D 日六类决策数据 → 原有 MarketSnapshot → daily close
    └─ D+1 执行数据 → ExecutionBundleV1 → 原有 daily execute
```

FKQT 发布器为新增独立模块和命令，不改三证券 Fixture 的冻结语义。调用方提供交易日、按顺序排列的候选队列、当前持仓与到期订单证券的并集，以及候选队列来源标识。发布器对符号去重、规范化并保存请求集合；候选顺序必须进入候选池版本哈希，队列外持仓仅补行情，不计入 `candidate_limit`。发布数据范围由显式输入决定，不在 FKQT 内部默默扩成全市场或退回三证券 Fixture。

本项目的读取端继续支持 Phase 5 的手工 `--raw-file` 作为离线导入；新增从 FKQT 冻结目录读取执行 Manifest 的入口，输出同一 `ExecutionBundleV1`，之后复用现有哈希验证、内容寻址保存和幂等执行。调用路径和来源 Manifest ID 写入审计，不因新的入口覆盖历史冻结包。

## 4. 版本化数据契约

决策侧保留六类数据集：`candidate_universe`、`trading_calendar`、`security_master`、`security_name_history`、`suspension_status`、`daily_bars`。新增通用范围发布器产出与当前 `FkqtManifestProvider` 兼容的 Manifest；`dataset_version` 保持 `TUSHARE_PRO_V1` 仅当字段与单位语义确实不变。`daily_bars` 仍保留未复权 `open`、`close`、`pre_close`、`vol`、`amount`，其中 Tushare `amount` 千元在消费者端转换成人民币元。六个 Manifest 的 `as_of_date` 必须等于 D 日；任何必须数据缺失不得以三证券旧 Fixture 填补。

执行侧新增单独的 `execution_snapshots` Manifest，版本 `TUSHARE_EXECUTION_V1`，`as_of_date` 等于 D+1。其内容对每个所需证券给出：`symbol`、`trade_date`、`trading_day_status`、`trading_status`、`open_price`、`unadjusted_close`、`daily_amount_cny`、`upper_limit_price`、`lower_limit_price`、`is_initial_no_limit_period`、来源与缺失原因。价格与金额用十进制字符串序列化；不允许二进制浮点改变哈希。Manifest 沿用 `dataset_id`、`source`、`dataset_version`、`request_params`、`fetched_at`、`row_count`、`schema_hash`、`raw_record_hash`、`content_hash`、`storage_path`；记录 Tushare endpoint、请求证券全集、交易日和数据截止时间。文件不可覆盖：同内容返回原引用，冲突内容拒绝。

字段映射：`daily.open` → `open_price`；`daily.close` → `unadjusted_close`；`daily.amount × 1000` → `daily_amount_cny`；`stk_limit.up_limit/down_limit` → 涨跌停价；`trade_cal` → 有效交易日；`suspend_d` 与日线记录共同确认交易状态。上市初期无涨跌幅阶段须由可靠交易日历和上市日规则确定并记录规则版本；不能从缺失的 `stk_limit` 行反推为无涨跌幅。对停牌持仓，必须有可审计的当日估值价；不能自动用上次价格替代当日 `unadjusted_close`。权限不足、接口报错、歧义数据和缺失估值均显式失败。

## 5. 时间、完整性与恢复

FKQT 只在 D 或 D+1 当日行情实际发布后生成相应包，并把 `fetched_at`、数据截止时间写入 Manifest。D 日模型输入只读取截止时间不晚于 D 日决策截止点的数据。D+1 包绝不进入 D 日 Jev/LLM 输入；只在 D+1 执行、NAV 与事后标签中使用。

执行前，本项目从持久化组合与订单计算所需证券全集，检验 Manifest 请求范围和执行记录覆盖全集；检验日期、交易日、证券身份、价格、单位、Schema/内容哈希与上游来源。已有同日 NAV 时只接受原 execution hash；相同输入重跑不新增成交。若 Tushare 当天尚未完整发布、权限不够、停牌估值缺失或校验失败，整次执行不改变持仓/NAV，返回稳定错误码，等待新的可信发布；不得生成假价格或回退手工填值。

FKQT 发布器不会改变已有正式结果。新 `dataset_version` 或规则版本须以显式新版本引入；历史 `ExecutionBundleV1` 保持可读。外部调度器仍负责触发和告警，不在本次设计内增加常驻进程。

## 6. 验收与测试

1. 用非三证券、同时含队列外持仓的冻结夹具验证发布范围；候选顺序改变会改变候选池 hash，持仓证券只增加行情覆盖。
2. 使用 FKQT 已有 `get_execution_context` 的测试替身验证 `daily`、`stk_limit`、`suspend_d` 字段和千元到元的精确换算；无接口权限、缺涨跌停价、错误交易日与缺估值分别失败。
3. 用跨仓库契约夹具验证 FKQT 产物可由本项目读取，并得到与手工入口同 Schema 的 `ExecutionBundleV1`；篡改任一记录必然哈希失败。
4. 复测 D 日决策不读取 D+1 包、执行覆盖持仓与到期订单、重跑不重复成交、同日不同包报冲突。
5. 只运行受影响的单元/契约/集成测试；阶段收尾再运行完整离线套件、Ruff、Pyright 与迁移检查。真实 Tushare 权限和当日数据只能在具备凭据的环境单独做冒烟验证，离线测试不得依赖网络。

## 7. 实施边界与前提

第一子项目仅交付 FKQT → 本项目的前向行情桥，不包含 Phase 6 A/B/C/D 对照实验。FKQT 通用发布器需要可用的 Tushare `daily`、`stk_limit`、`suspend_d` 权限；未验证权限前不得宣称实盘前向测试已经无人值守。若 `stk_limit` 权限不足，保留现有导入入口供经过审计的其他可信上游发布相同契约；绝不计算“估计涨跌停价”替代官方字段。

预期改动限于 FKQT 新发布模块及其测试、本项目行情 ingestion/CLI 入口及其测试、版本化契约和运行手册。`src/fkqt_jevinvestor/backtest/`、`tests/backtest/`、`docs/runbooks/backtesting.md` 与旧 FKQT 交易行为不改。发布与接收均不写入 Secret 或完整认证头。
