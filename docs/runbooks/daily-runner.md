# Phase 5 日频前向运行手册

需求：R3、R4、R6、R16—R18、R21、R22、R27、R43、R46—R49。只生成模拟信号与虚拟成交，不连接券商。

## 1. 前置条件

1. Python 3.12、项目依赖和 Alembic 数据库已安装并迁移至当前 head。
2. `JEV_INVESTOR_DATABASE_URL` 指向本系统专用数据库。
3. FKQT 已发布 D 日六类不可变 Manifest；`candidate_universe` 只列候选证券，`daily_bars` 与证券状态须覆盖候选和仍持有的证券。
4. `JEV_INVESTOR_C_GROUP_CANDIDATE_SYMBOLS` 按 FKQT 冻结候选池原顺序填入；数量不得超过 `--candidate-limit`。程序会校验其集合与候选池 Manifest 一致。
5. TypeSafe 与 DeepSeek Secret 临时注入环境变量，不能写入仓库或命令历史。
6. 执行日原始行情文件由可信上游在当日收盘后发布，含开盘价与未复权收盘价，至少覆盖全部当前持仓与到期订单证券。程序不会连接真实行情服务来猜测缺失数据。

## 2. 环境变量

```powershell
$env:JEV_INVESTOR_ENV = 'local'
$env:JEV_INVESTOR_DATABASE_URL = 'sqlite+aiosqlite:///./data/fkqt_jevinvestor.db'
$env:FKQT_MANIFEST_BUNDLE_ROOT = 'C:\data\fkqt-manifest\2026-09-24'
$env:JEV_INVESTOR_MARKET_SNAPSHOT_ROOT = 'data/snapshots'
$env:JEV_INVESTOR_EXECUTION_BUNDLE_ROOT = 'data/execution'
$env:JEV_INVESTOR_C_GROUP_CANDIDATE_SYMBOLS = '600000.SH,000001.SZ'
# TYPESAFE_API_KEY 与 DEEPSEEK_API_KEY 仅从密码管理器注入当前进程。
```

首次创建模拟组合可使用现有 Portfolio API。数据库升级：

```powershell
python -m alembic upgrade head
```

## 3. D 日收盘后冻结与决策

在北京时间 D 日 15:00 后，由外部调度器调用：

```powershell
fkqt-jevinvestor daily close --date 2026-09-24 --portfolio-id paper-main --candidate-limit 80
```

标准输出为 JSON，包含 `run_id`、`signal_batch_id`、`decision_count`、`order_count` 和 `target_batch_hash`。退出码 0 表示编排已完成；退出码 2 表示前置条件、冻结输入或 Provider 失败。失败后先检查 stderr 错误码和数据库 Attempt，修复后重跑同一命令。相同冻结输入及组合版本得到相同 `run_id`；正式模型终态不可被重跑改写。

`daily close` 读取 D 日 Manifest，冻结候选与持仓并集、计算确定性特征、调用 Jev 与 DeepSeek，并生成只对交易日历确认的 D+1 有效的虚拟订单。程序不使用自然日加一。

## 4. D+1 行情包准备

上游原始 JSON 顶层为“证券代码 → `MarketExecutionSnapshot`”。例子是一只证券；实际文件必须完整包含本次需要的全部证券：

```json
{
  "600000.SH": {
    "symbol": "600000.SH",
    "trade_date": "2026-09-25",
    "trading_day_status": "OPEN",
    "trading_status": "TRADING",
    "open_price": "10.00",
    "unadjusted_close": "10.20",
    "daily_amount_cny": "10000000",
    "upper_limit_price": "11.00",
    "lower_limit_price": "9.00",
    "is_initial_no_limit_period": false
  }
}
```

如果证券停牌，`trading_day_status` 仍为 `OPEN`，该证券的 `trading_status` 为 `SUSPENDED`；开盘价可以为 `null`，未复权收盘价仍必须足以给已有持仓估值。先冻结原始文件：

```powershell
fkqt-jevinvestor daily prepare-execution --trade-date 2026-09-25 --raw-file C:\data\fkqt-execution\2026-09-25.json
```

输出 `execution_hash` 与内容寻址 `execution_ref`。`prepare-execution` 不调用外部模型，也不成交。原始文件不进入 Git。

## 5. D+1 收盘后回放开盘虚拟执行

使用上一步输出的完整 `execution_ref`：

```powershell
fkqt-jevinvestor daily execute --trade-date 2026-09-25 --portfolio-id paper-main --execution-bundle data/execution/2026-09-25/<execution_hash>.json
```

`<execution_hash>` 必须替换为上一步返回的 64 位 hash。程序重新验证包内容、日期、证券身份、交易日与持仓/待执行订单覆盖，再调用现有虚拟执行引擎。它使用 D+1 开盘价模拟成交、D+1 收盘价计算 NAV，所以须等完整日线发布后运行，不是开盘即时执行。输出成交数、总资产、单位净值、执行行情 hash 与冻结路径。重复同一命令返回 `fill_count=0`；换包重跑返回 `EXECUTION_INPUT_CONFLICT`。不可成交订单终态失效，不跨日追单。随后再运行 D+1 的 `daily close`。

## 6. 状态与外部告警

```powershell
fkqt-jevinvestor daily status --trade-date 2026-09-25 --portfolio-id paper-main
```

JSON 字段：`decision_run_count`、`decision_run_ids`、`market_snapshot_hashes`、`planned_execution_dates`、`scheduled_pending_order_count`、`pending_order_count`、`execution_complete`、`portfolio_version`、`total_equity`。`scheduled_pending_order_count` 是所查日期收盘决策产生的下一交易日待执行数；`pending_order_count` 是所查日期当天到期的待执行数。外部调度器应在收盘任务退出码非 0、执行任务退出码非 0、或预期执行日 `execution_complete=false` 时报警。本项目不运行常驻进程，也不发送短信或邮件。

## 7. 失败处理

| 错误码 | 含义 | 操作 |
|---|---|---|
| `DAILY_CLOSE_BEFORE_CUTOFF` | D 日未收盘 | 15:00 后重跑 |
| `DAILY_EXECUTE_BEFORE_CLOSE` | 执行日未收盘，完整估值行情尚未就绪 | 当日 15:00 后重跑 |
| `DAILY_PREVIOUS_EXECUTION_REQUIRED` | 当天存在上日决策，但尚未完成当天模拟执行 | 先运行 `daily execute` |
| `DAILY_DECISION_INPUT_CONFLICT` | 同组合/决策日已有不同冻结输入的正式运行 | 保留原运行，不在同日覆盖 |
| `DAILY_EXECUTION_NOT_SCHEDULED` | 该日期无正式计划执行或到期订单 | 核对上日信号的计划执行日 |
| `FKQT_MANIFEST_BUNDLE_ROOT_REQUIRED` | 未提供 Manifest 根目录 | 设置环境变量 |
| `C_GROUP_CANDIDATE_CONFIG_INVALID` | 候选为空、重复或超容量 | 以冻结候选池为准修正 |
| `UNIVERSE_SYMBOL_MISMATCH` | Manifest 候选池与配置不一致 | 发布匹配的冻结候选池 |
| `C_GROUP_PROVIDER_CONFIG_REQUIRED` | 缺少 Jev 或 DeepSeek Secret | 从密码管理器注入 |
| `EXECUTION_RAW_INPUT_INVALID` | 原始执行行情不是合法结构 | 修正 JSON 字段和日期 |
| `EXECUTION_BUNDLE_HASH_MISMATCH` | 冻结行情包被改写 | 恢复原包，不能重算覆盖旧 hash |
| `EXECUTION_BUNDLE_DATE_MISMATCH` | 命令日期与包日期不一致 | 使用正确交易日的包 |
| `EXECUTION_TRADING_DAY_NOT_CONFIRMED` | 没有有效开市证据 | 上游补齐交易日状态 |
| `EXECUTION_CLOSE_PRICE_REQUIRED` | 存在缺失的未复权收盘价，不能计算 NAV | 上游补齐完整收盘行情 |
| `EXECUTION_SYMBOL_COVERAGE_INCOMPLETE` | 缺持仓或订单证券 | 上游补齐该证券行情 |
| `EXECUTION_INPUT_CONFLICT` | 已执行日换用不同包 | 保留原执行记录，另建独立实验而非覆盖 |
| `PORTFOLIO_VERSION_CONFLICT` | 并发修改组合 | 查询状态，停止重复调度，核对执行记录 |

## 8. 自检清单

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| 决策时间 | 北京时间 D 日 15:00 后 | 运行 `daily close`，检查退出码 | 必须 |
| D+1 来源 | 冻结交易日历 | 比较 MarketSnapshot 的 `next_trade_date` 与订单日期 | 必须 |
| 模型重复运行 | 同一 `run_id` | 同输入重复调用，比较 JSON | 必须 |
| 执行行情包 | SHA-256 匹配，持仓与订单全覆盖 | `daily execute` 校验并查询 `execution_ref` | 必须 |
| 重复成交 | 0 条新增 Fill | 同一执行包重复运行并检查 `fill_count=0` | 必须 |
| 资产恒等式 | 误差不超过 0.01 CNY | 查询 NAV 与组合持仓市值 | 必须 |
| 外部券商订单 | 0 条 | 检查接口和网络配置 | 必须 |

## 9. 已知限制

本阶段没有 FKQT 的执行行情 REST Adapter；上游必须提供原始 JSON 文件。`daily close` 当前只编排 C 组，A/B/D 仍属于后续对照实验。`daily status` 是只读状态，不负责主动推送告警。回测接口与沙耶代码未修改。
