# C 组单日决策与冻结重放运行手册

适用版本：`decision-input-v1`、`decision-output-v1`、`position-sizing-v1`、数据库 Revision `0007_phase4_llm_position_sizing`。

## 1. 解决的问题

C 组链路读取已冻结的 A 股候选队列、D 日行情与确定性特征，先由 Jev 输出候选池和个股盈亏类别概率，再由 DeepSeek 只选择 `ENTER`、`KEEP`、`EXIT` 或 `AVOID`。代码根据离散动作计算目标权重并生成 D+1 模拟订单。系统不连接券商，不发送真实订单。

失败时系统输出 `NO_SIGNAL`：空仓保持不买，持仓保持原权重。它不会切换备用模型，也不会把 Jev 概率直接映射为仓位。

## 2. 前置条件

1. Python 3.12 环境和项目依赖已经安装。
2. 数据库已存在目标模拟组合。
3. D 日 MarketSnapshot 与特征已由 `market freeze` 冻结并写入文件和数据库。
4. 冻结候选符号列表与 MarketSnapshot 的 `universe_snapshot_hash` 对应。
5. TypeSafe 和 DeepSeek Key 通过进程环境临时注入。

安装与迁移：

```powershell
python -m uv sync --all-groups
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe current
```

预期 Revision：`0007_phase4_llm_position_sizing (head)`。

## 3. 环境变量

| 变量 | 是否必需 | 标准值或用途 |
|---|---:|---|
| `JEV_INVESTOR_DATABASE_URL` | 是 | SQLAlchemy 异步数据库 URL |
| `JEV_INVESTOR_MARKET_SNAPSHOT_ROOT` | 是 | 冻结 MarketSnapshot 根目录，默认 `data/snapshots` |
| `JEV_INVESTOR_C_GROUP_SNAPSHOT_HASH` | 是 | 本次 D 日 MarketSnapshot 的 64 位 SHA-256 |
| `JEV_INVESTOR_C_GROUP_CANDIDATE_SYMBOLS` | 是 | 冻结候选队列，英文逗号分隔，不包含系统自行选股 |
| `TYPESAFE_API_KEY` | 是 | TypeSafe Secret，仅在正式命令或 Live Probe 使用 |
| `TYPESAFE_MODEL` | 否 | 默认 `jev-latest` |
| `DEEPSEEK_API_KEY` | 是 | DeepSeek Secret，仅在正式命令或 Live Probe 使用 |
| `DEEPSEEK_BASE_URL` | 否 | 默认 `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 否 | 默认 `deepseek-flash`，对应当前 DeepSeek V4.1 Flash |
| `DEEPSEEK_REASONING_EFFORT` | 否 | 默认 `high`，允许 `low/high` |
| `DEEPSEEK_TIMEOUT_SECONDS` | 否 | 默认 `30`，必须大于 0 |

PowerShell 临时配置示例：

```powershell
$env:JEV_INVESTOR_DATABASE_URL='sqlite+aiosqlite:///./data/fkqt_jevinvestor.db'
$env:JEV_INVESTOR_MARKET_SNAPSHOT_ROOT='data/snapshots'
$env:JEV_INVESTOR_C_GROUP_SNAPSHOT_HASH='<64位冻结行情哈希>'
$env:JEV_INVESTOR_C_GROUP_CANDIDATE_SYMBOLS='600000.SH,000001.SZ'
$env:TYPESAFE_API_KEY='<从密码管理器临时注入>'
$env:TYPESAFE_MODEL='jev-latest'
$env:DEEPSEEK_API_KEY='<从密码管理器临时注入>'
$env:DEEPSEEK_MODEL='deepseek-flash'
$env:DEEPSEEK_REASONING_EFFORT='high'
```

## 4. 单日运行

```powershell
.\.venv\Scripts\fkqt-jevinvestor.exe decision run-c-group --date 2026-09-25 --portfolio-id paper-main --candidate-limit 2
```

成功时 stdout 是单个 JSON 对象，包含 `run_id`、`signal_batch_id`、`decision_count`、`order_count` 和 `target_batch_hash`。`candidate-limit` 是本次正式运行容量上限；环境中的候选列表超过该值时整批拒绝。

Client 只会在上述决策命令通过冻结输入与 Secret 配置 Gate 后创建。`--help`、`version`、普通导入、离线回测和默认测试不会连接网络。

## 5. DeepSeek Live Probe

默认执行会跳过，外部调用为 0：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/live/test_deepseek_decision_live.py -q
```

显式启用：

```powershell
$env:RUN_LIVE_DEEPSEEK_TESTS='1'
$env:DEEPSEEK_API_KEY='<从密码管理器临时注入>'
$env:DEEPSEEK_MODEL='deepseek-flash'
.\.venv\Scripts\python.exe -m pytest tests/live/test_deepseek_decision_live.py -q
Remove-Item Env:RUN_LIVE_DEEPSEEK_TESTS
Remove-Item Env:DEEPSEEK_API_KEY
```

Probe 使用一只合成空仓证券，只验证四动作 Schema 和无仓位数值字段，不保存决策、目标、Signal 或虚拟订单。

## 6. 五张 Phase 4 审计表

```sql
SELECT id, symbol, membership, decision_date, provider_name, model_id,
       status, action, error_code, input_hash
FROM ai_signal_llm_decision_evaluation
ORDER BY decision_date, symbol;

SELECT evaluation_id, sequence, run_id, provider_name, model_id,
       status, latency_ms, raw_response_hash, error_code
FROM ai_signal_llm_decision_attempt
ORDER BY evaluation_id, sequence;

SELECT run_id, evaluation_id, created_at
FROM ai_signal_llm_decision_run_link
ORDER BY run_id, evaluation_id;

SELECT run_id, portfolio_id, portfolio_version, decision_date,
       sizing_version, config_hash, input_hash, target_batch_hash,
       gross_target_pct, cash_target_pct, run_code, signal_batch_id
FROM ai_signal_position_sizing_run
ORDER BY decision_date, run_id;

SELECT sizing_run_id, decision_evaluation_id, symbol, requested_action,
       sizing_status, current_position_pct, raw_target_position_pct,
       target_position_pct, signal_action, block_code
FROM ai_signal_position_target
ORDER BY sizing_run_id, symbol;
```

## 7. 稳定错误码

| 错误码 | 含义 | 处理 |
|---|---|---|
| `C_GROUP_SNAPSHOT_CONFIG_REQUIRED` | 缺 snapshot hash 或冻结候选列表 | 配置两个 `JEV_INVESTOR_C_GROUP_*` 变量 |
| `C_GROUP_PROVIDER_CONFIG_REQUIRED` | 缺 TypeSafe 或 DeepSeek Key | 从密码管理器临时注入两个 Secret |
| `C_GROUP_CANDIDATE_CONFIG_INVALID` | 候选为空或超过容量 | 修正冻结候选列表或容量 |
| `C_GROUP_FEATURE_COVERAGE_INCOMPLETE` | 候选与持仓并集缺特征 | 重新冻结包含队列外持仓的输入 |
| `C_GROUP_RUN_ID_MISMATCH` | Jev 与决策 Run 不同 | 不得混用运行产物 |
| `C_GROUP_FROZEN_INPUT_MISMATCH` | 日期、cutoff、D+1 或 hash 不一致 | 重新选择同一冻结输入 |
| `C_GROUP_JEV_COVERAGE_INCOMPLETE` | Jev 未覆盖候选与持仓并集 | 完整运行 Jev 阶段 |
| `C_GROUP_FEATURE_IDENTITY_MISMATCH` | 特征 symbol/date 不一致 | 修复冻结特征 |
| `C_GROUP_DUPLICATE_CANDIDATE` | 候选重复 | 清理候选列表 |
| `C_GROUP_CANDIDATE_LIMIT_EXCEEDED` | 候选数超过容量 | 修正列表或容量并创建新运行 |
| `C_GROUP_HISTORY_POINT_IN_TIME_VIOLATION` | 历史动作晚于决策日 | 修复历史快照 |
| `REQUIRED_DECISION_EVIDENCE_MISSING` | Jev 或必要特征不可用 | 该票保存 `NO_SIGNAL`；检查上游失败码 |
| `PROVIDER_UNAVAILABLE` | DeepSeek 超时或不可用 | 保留 `NO_SIGNAL`，新 run 才重试 |
| `DECISION_RESPONSE_CONTRACT_INVALID` | 响应不满足 Schema/allowed actions | 检查 Attempt 的响应 hash，不保存模型正文到失败载荷 |
| `PREEXISTING_GROSS_LIMIT_EXCEEDED` | 决策前总仓位已超上限 | 阻止新增风险，不自动清仓 |
| `C_GROUP_PERSISTENCE_IDENTITY_MISMATCH` | 决策、仓位、Signal 身份不一致 | 拒绝整批保存 |
| `C_GROUP_IDEMPOTENCY_CONFLICT` | 同 run 的冻结输入或目标发生变化 | 创建新 run，禁止覆盖历史 |
| `PORTFOLIO_VERSION_CONFLICT` | 组合版本并发变化 | 重新读取组合并创建新 run |

## 8. 冻结重放

1. 将正式 C 组的目标转换为 `FrozenTargetBundleV1.create(...)`。
2. 使用 `FrozenTargetStore.save()` 写入 `<root>/<dataset_id>/<decision_date>/<content_hash>.json`。
3. 保存每个 decision date 对应的 `content_hash` 清单。
4. 回测构造 `FrozenCGroupTargetProvider(store, dataset_id, content_hashes)`。
5. `build_targets()` 校验 dataset hash、日期、D+1、universe/market/feature hash 和 sizing version 后返回 `ExperimentArm.C_JEV_LLM`。

重放不读取 FKQT，不调用 Jev 或 DeepSeek，不导入 FastAPI 或 SQLAlchemy Repository，也无法访问 `execution_market`。内容被替换后加载会返回 `FROZEN_TARGET_CONTENT_HASH_MISMATCH`。

## 9. Secret 禁止项

- 不得把 `TYPESAFE_API_KEY`、`DEEPSEEK_API_KEY` 写入 `.env.example`、Git、数据库、Fixture、Prompt、异常正文或截图。
- 不得在命令行参数中直接传 Key；进程列表和 Shell 历史可能泄露参数。
- 不得保存 `Authorization`、Bearer Header 或完整外部异常消息。
- 运行结束后清除临时环境变量，必要时轮换已暴露的 Key。

## 10. 故障排查

| 现象 | 检查 | 解决 |
|---|---|---|
| 命令在网络前退出 | stderr 稳定错误码 | 按第 7 节修复配置或冻结输入 |
| 某票 `NO_SIGNAL`，其他票正常 | Evaluation `error_code` 与 Jev 状态 | 修复该票特征/Jev；不要伪造概率 |
| 全部 `NO_SIGNAL` | Universe Jev 与 Provider Attempt | Universe 失败时预期不调用 LLM；Provider 失败时创建新 run 重试 |
| 目标权重为 0 | PositionTarget `block_code` | 检查流动性、最低建仓和组合上限 |
| 重放 hash 不匹配 | 文件名、文件内容、日期清单 | 从可信冻结产物重新发布，不修改原文件 |
| 数据库版本冲突 | Portfolio `version` | 重新读取组合状态并创建新 run |

## 11. Phase 5 停止点

Phase 4 到此只支持人工触发的单日决策、冻结重放和 D+1 模拟订单。自动收盘调度、崩溃恢复、限流并发、监控告警、长期前向队列和 A/B/C/D 批量实验属于 Phase 5；不得把当前 CLI 当成无人值守生产调度器。

## 12. 自检清单

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| 模型动作 | `ENTER/KEEP/EXIT/AVOID` | Live/Provider Schema 测试 | 必须 |
| 系统失败动作 | `NO_SIGNAL` | 查询 Evaluation 状态与 action | 必须 |
| 模型数值交易字段 | 0 个 | 扫描 `DecisionModelOutputV1` | 必须 |
| 队列外持仓覆盖 | 全部 `HELD_ONLY`，只允许 KEEP/EXIT | C 组集成测试 | 必须 |
| 冻结重放外部调用 | 0 次 | Frozen Provider 单元测试 | 必须 |
| 默认 Live 外部调用 | 0 次 | 未设置 `RUN_LIVE_DEEPSEEK_TESTS=1` 时应 skip | 必须 |
| 数据库 Revision | `0007_phase4_llm_position_sizing (head)` | `alembic current` | 必须 |
