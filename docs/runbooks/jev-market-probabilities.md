# Jev 行情盈亏概率运行手册

日期：2026-09-21

适用版本：`jev-state-v1`、`jev-pnl-questions-v1`、`pnl-label-criteria-v1`

## 1. 目的与边界

本模块把冻结候选队列和 Phase 2 确定性行情特征转换为两层 Jev 输入：一次候选池风险状态和逐证券盈亏状态。Jev 只返回有限标签的概率分布，不返回连续收益率、因子权重、仓位、数量、价格或交易动作。

C 组正式数据流如下：

```text
冻结候选队列 + 冻结行情快照 + Phase 2 确定性特征
  → JevUniverseStateV1（每个正式键一次）
  → JevSymbolStateV1（候选证券与队列外持仓的并集）
  → Jev Choice 概率
  → Phase 4 LLM 离散动作
  → 确定性仓位与虚拟执行
```

Phase 3 到 Jev 概率为止，不产生 `ENTER`、`KEEP`、`EXIT`、`AVOID`，也不连接券商。

## 2. 前置条件

- Python：3.12 或 3.13。
- 项目开发依赖已安装。
- 数据库已升级到 `0006_phase3_jev_pnl_probabilities`。
- 正式输入来自已冻结的候选池、行情快照和 Phase 2 特征。
- 只有 Live Contract Probe（真实契约探测）需要 TypeSafe 网络权限和有效 API Key。

## 3. 环境变量

| 变量 | 必需范围 | 允许值 | 用途 |
|---|---|---|---|
| `RUN_LIVE_JEV_TESTS` | 仅 Live Test | `1` | 显式允许外部调用；未设置时安全跳过 |
| `TYPESAFE_API_KEY` | 仅 Live Test/正式调用 | TypeSafe 发放的 Secret | 创建 SDK Client；不得写入文件、日志或 Git |
| `TYPESAFE_JEV_MODEL` | 可选 | 账号有权访问的模型 ID | Live Test 模型；未设置时使用 `jev-latest` |

环境变量中不得保存数据库密码以外的无关凭据；本项目文件、命令历史、测试输出、异常消息、数据库 JSON 和提交记录都不得出现 API Key。

## 4. 默认离线测试

PowerShell：

```powershell
$env:PYTHONPATH=(Join-Path $PWD 'src')
& '.\.venv\Scripts\python.exe' -m pytest -m "not live" -q
Remove-Item Env:PYTHONPATH
```

预期：全部离线测试通过；不访问 TypeSafe、FKQT、LLM、新闻 API 或券商。

只检查 Live Test 门控而不发请求：

```powershell
Remove-Item Env:RUN_LIVE_JEV_TESTS -ErrorAction SilentlyContinue
& '.\.venv\Scripts\python.exe' -m pytest tests/live/test_jev_market_live.py -q
```

预期：`1 skipped`、`0 failed`。

## 5. 显式 Live Contract Probe

只在确认凭据、账号权限和网络后运行：

```powershell
$env:RUN_LIVE_JEV_TESTS='1'
$env:TYPESAFE_API_KEY='<从密码管理器临时注入>'
$env:TYPESAFE_JEV_MODEL='jev-latest'
& '.\.venv\Scripts\python.exe' -m pytest tests/live/test_jev_market_live.py -q
Remove-Item Env:RUN_LIVE_JEV_TESTS
Remove-Item Env:TYPESAFE_API_KEY
Remove-Item Env:TYPESAFE_JEV_MODEL
```

预期：`1 passed`。测试只发送合成证券 `SYNTHETIC.TEST`，不发送真实账户、持仓或交易建议，不打印 Key、认证头或原始异常正文。

## 6. `candidate_limit` 规则

1. 每次实验或前向运行必须显式传入正整数 `candidate_limit`，产品没有默认容量。
2. `candidate_symbols` 必须去重且数量不得超过 `candidate_limit`。
3. 合格候选少于容量时保留实际数量，不以不合格证券补齐。
4. 候选成员或容量变化必须生成新的候选池版本和哈希。
5. `candidate_actual_size` 只统计候选队列，不统计队列外持仓。

## 7. 队列外持仓规则

正式评估证券集合为 `candidate_symbols ∪ held_symbols`。不在候选队列但仍有持仓的证券由编排层视为 `HELD_ONLY`：

- 必须生成个股 Jev 评估或明确的失败状态，不能静默遗漏。
- 不计入候选池的 median、breadth、dispersion、coverage 和 `candidate_limit`。
- `HELD_ONLY` 标记不序列化进 Jev State，保证同一证券的概率与账户无关。
- Phase 4 最终动作只能是 `KEEP`、`EXIT` 或 `NO_SIGNAL`，不得新开或加仓。

## 8. 问题与标签

候选池级问题：

| 问题 | 标签 |
|---|---|
| `universe_risk_regime` | `RISK_ON`、`NEUTRAL`、`RISK_OFF` |

个股级五个问题：

| 问题 | 标签 |
|---|---|
| `next_session_pnl` | `PROFIT`、`FLAT`、`LOSS` |
| `profitability_5d` | `PROFITABLE`、`FLAT`、`LOSS` |
| `drawdown_risk_5d` | `LOW`、`MEDIUM`、`HIGH` |
| `payoff_asymmetry_5d` | `UPSIDE_DOMINANT`、`BALANCED`、`DOWNSIDE_DOMINANT` |
| `data_sufficiency` | `SUFFICIENT`、`LIMITED`、`INSUFFICIENT` |

每个结果必须包含标签全集、有限 Decimal 概率、概率和误差不超过 `0.000001`，以及按固定标签顺序处理并列后的最高概率标签。

## 9. 真实标签公式

真实标签只在评估阶段由代码使用 D+1 至 D+5 冻结行情生成，不进入 D 日 Jev 输入：

```text
entry_open = open(D+1)
net_return_h = close(D+h) / entry_open - 1 - round_trip_cost_v1
mae_5d = min(0, min(low(D+1..D+5) / entry_open - 1))
mfe_5d = max(0, max(high(D+1..D+5) / entry_open - 1))
```

分类规则：

- 1 日：大于 `0.002` 为 `PROFIT`，小于 `-0.002` 为 `LOSS`，闭区间内为 `FLAT`。
- 5 日：大于 `0.005` 为 `PROFITABLE`，小于 `-0.005` 为 `LOSS`，闭区间内为 `FLAT`。
- MAE：绝对值不超过 `0.02` 为 `LOW`；大于 `0.02` 且不超过 `0.05` 为 `MEDIUM`；超过 `0.05` 为 `HIGH`。
- Payoff：MFE 至少是绝对 MAE 的 `1.5` 倍为 `UPSIDE_DOMINANT`；绝对 MAE 至少是 MFE 的 `1.5` 倍为 `DOWNSIDE_DOMINANT`；其余为 `BALANCED`；两者同为 0 也为 `BALANCED`。
- D+1 不可成交、五个有效交易日不完整、日期映射错误或复权口径不一致时为 `LABEL_UNAVAILABLE`，所有数值和分类字段保持空值。

## 10. 评估状态

| 状态 | 含义 | 概率是否允许 |
|---|---|---|
| `AVAILABLE` | Provider 响应通过完整契约校验 | 必须有完整概率 |
| `PROVIDER_UNAVAILABLE` | 凭据、权限、网络、限流、超时或服务不可用 | 禁止 |
| `CONTRACT_INVALID` | 问题、标签、概率或最高标签不符合契约 | 禁止 |
| `DATA_UNAVAILABLE` | 个股必要特征缺失 | 禁止 |

Repository 内部还使用 `IN_PROGRESS` 表示 Claim 已被一个调用者取得。它不是可供交易决策使用的成功结果。

## 11. 重试、幂等与跨运行复用

- 正式键由 scope、State 输入哈希、Provider、Provider 版本、模型和问题集版本组成；`run_id` 不进入正式键。
- 首次 Claim 返回 `ACQUIRED` 并创建 Attempt 1；只有获得 Claim 的调用者能访问 Provider。
- 同一正式键执行中返回 `IN_PROGRESS`，不得再调用 Provider。
- 成功后返回 `COMPLETE`，直接复用已保存概率，不新增 Attempt。
- 失败后允许再次 Claim，新 Attempt 序号递增，旧 Attempt 保留。
- 每个使用结果的 `run_id` 都写入 RunLink；同一运行重复使用不会产生重复 RunLink。
- 失败 Attempt 不保存 QuestionResult，不切换到无 Jev 实验组，不合成均匀概率。

## 12. 数据库审计查询

查看正式评估：

```sql
SELECT id, formal_key, scope, symbol, decision_date, status,
       provider_name, provider_version, model_id, input_hash,
       state_schema_version, question_set_version, latest_attempt_sequence
FROM ai_signal_jev_evaluation
ORDER BY decision_date, scope, symbol;
```

查看调用尝试：

```sql
SELECT evaluation_id, run_id, sequence, status, started_at, finished_at,
       latency_ms, raw_response_hash, error_code
FROM ai_signal_jev_attempt
ORDER BY evaluation_id, sequence;
```

查看完整概率：

```sql
SELECT evaluation_id, question_id, question_version, criteria_version,
       label_order_json, selected_label, distribution_json
FROM ai_signal_jev_question_result
ORDER BY evaluation_id, question_id;
```

查看跨运行复用：

```sql
SELECT run_id, evaluation_id, created_at
FROM ai_signal_jev_run_link
ORDER BY created_at, run_id, evaluation_id;
```

## 13. Secret 禁止项

以下内容不得进入 Git、数据库、日志、异常、测试快照和问题输入：

- `TYPESAFE_API_KEY` 明文；
- `Authorization` Header；
- SDK Client 的 `repr`；
- 原始上游异常正文；
- 包含凭据的 URL、命令输出或屏幕截图；
- `api_key`、`authorization`、`password`、`secret`、`token` 字段。

Provider 契约错误只保存清理后 canonical response hash。网络错误只映射为稳定状态和错误码。

## 14. 常见错误与恢复

| 错误/状态 | 原因 | 恢复步骤 |
|---|---|---|
| Live Test 为 `skipped` | 两个门控变量未同时设置 | 仅在要进行真实探测时临时设置两者 |
| `PROVIDER_UNAVAILABLE` | Key 无效、无模型权限、网络、限流、超时或服务故障 | 检查账号权限和网络；保持同一正式输入重新 Claim；确认 Attempt 序号递增 |
| `CONTRACT_INVALID` | SDK 契约变化、缺问题、未知标签、非法概率或概率和错误 | 查看 Provider/模型版本和 `raw_response_hash`；更新适配器与契约版本；禁止手工改概率 |
| `DATA_UNAVAILABLE` | 必要 Phase 2 特征缺失 | 查看 State 的 `missing_reasons`；修复上游数据后生成新冻结快照和输入哈希 |
| `POINT_IN_TIME_VIOLATION` | 特征时间晚于决策截止 | 丢弃整次错误输入，重新冻结满足截止时间的数据 |
| `CANDIDATE_TARGET_EXCEEDED` | 候选数超过本次显式容量 | 修复上游队列配置并生成新队列版本；不得截断旧历史快照 |
| 长期 `IN_PROGRESS` | 进程在获得 Claim 后异常退出 | 先核对进程和 Attempt；当前版本不自动抢占，必须由运维确认后按独立恢复流程处理数据库状态 |
| `LABEL_UNAVAILABLE` | D+1 不可成交、未来窗口不足或复权不一致 | 等待完整冻结行情或修复数据；不得填 0 收益 |

## 15. 验收清单

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| 默认 Live 门控 | `1 skipped`、0 failed | 运行第 4 节门控命令 | 必需 |
| 个股问题数 | 5 | Live Test 或 Fake Provider Contract Test | 必需 |
| 概率类型 | 有限 Decimal | Provider Contract Test | 必需 |
| 概率和 | 与 1 的误差不超过 `0.000001` | Provider Contract Test | 必需 |
| 正式键唯一 | `formal_key` 唯一 | Migration Test 和数据库唯一约束 | 必需 |
| 跨运行缓存 | 成功复用不新增 Attempt | Service/Repository Integration Test | 必需 |
| 失败概率 | 0 条 QuestionResult | Repository Integration Test | 必需 |
| Secret | Git 和数据库中无明文 | Secret Scan 与审计查询 | 必需 |

