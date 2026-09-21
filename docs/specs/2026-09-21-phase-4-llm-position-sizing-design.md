# Phase 4 LLM 离散动作与确定性仓位设计

日期：2026-09-21

状态：待书面审阅

需求映射：R3、R4、R7、R9、R12、R15、R16、R18—R29、R32—R36、R40—R43

上游阶段：Phase 3 Jev 行情盈亏概率层

下游阶段：Phase 5 日频自动运行与 Phase 6 A/B/C/D 对照实验

## 1. 目标

Phase 4 完成 C 组主链的决策与仓位部分：确定性行情特征、Jev 候选池风险概率、Jev 个股盈亏概率和决策前组合状态共同形成逐证券冻结输入；最终决策 LLM 只输出 `ENTER`、`KEEP`、`EXIT` 或 `AVOID`；版本化代码再把离散动作转换为确定性目标权重，并复用现有虚拟订单与 D+1 执行引擎。

LLM 负责方向判断，不负责数值计算。Jev 概率是待校准预测证据，不是仓位参数。交易数量、成交价格、费用、滑点、T+1、涨跌停和成交容量继续由代码计算。

## 2. 非目标

Phase 4 不实现：

- 收盘自动调度、任务恢复、告警或定时通知；
- A/B/C/D 四组批量实验、概率校准或绩效比较；
- 新闻、公告正文、政策或行业事件语义输入；
- 因子权重、概率到权重映射或模型生成连续仓位；
- 真实券商、真实订单、分钟级或 Tick 级交易；
- Web 前端或桌面客户端；
- 自动切换备用模型、自动修复非法模型输出或隐式多次重试。

## 3. 方案选择

第一版使用官方 `openai` Python SDK 的异步 OpenAI-compatible Client，默认连接 DeepSeek。`base_url`、模型名称和超时从启动配置注入；领域层和服务层只依赖 `DecisionLlmProvider` Protocol，不引用 SDK 类型。

不采用 LangChain、PydanticAI 或 Instructor。当前调用是一次严格分类，不需要 Agent、工具或多步工作流。额外框架的自动重试和修复会使正式输入、调用次数和成本发生隐式变化，不利于审计与重放。

DeepSeek 当前支持 JSON Output 和 JSON Schema 输出，但 Provider 仍必须使用本地 Pydantic 重新验证完整响应。Provider 能生成合法 JSON 不等于业务动作合法。

参考资料：

- DeepSeek JSON Output：https://api-docs.deepseek.com/guides/json_mode/
- DeepSeek Responses API：https://api-docs.deepseek.com/api/create-response/
- DeepSeek Models & Pricing：https://api-docs.deepseek.com/quick_start/pricing/
- OpenAI Python SDK Structured Outputs：https://github.com/openai/openai-python/blob/main/helpers.md

## 4. 总体架构

```text
冻结候选池与当前持仓证券并集
                │
                ├── 冻结行情与确定性特征
                ├── Jev 候选池风险概率
                ├── Jev 个股盈亏概率
                ├── 决策前组合与个股持仓
                ├── 最近五次正式动作
                └── 未完成虚拟订单摘要
                ▼
         DecisionInputV1
                │
                ▼
      DecisionLlmProvider Protocol
                │
                ├── DeepSeek Provider
                └── 离线 Fake Provider
                ▼
       ENTER / KEEP / EXIT / AVOID
       失败由系统生成 NO_SIGNAL
                │
                ▼
       动作状态机与输入完整性校验
                │
                ▼
         PositionSizerV1
                │
                ▼
         TargetPositionBatch
                │
                ▼
      现有 Signal / D+1 虚拟执行引擎
```

逐证券各调用一次 LLM。候选池容量由运行命令显式配置，不设产品级默认容量。单票调用允许独立缓存、失败隔离和重放；Phase 5 才增加有限并发调度。

## 5. 领域契约

### 5.1 枚举

```python
class DecisionMembership(StrEnum):
    CANDIDATE = "CANDIDATE"
    HELD_ONLY = "HELD_ONLY"


class DecisionAction(StrEnum):
    ENTER = "ENTER"
    KEEP = "KEEP"
    EXIT = "EXIT"
    AVOID = "AVOID"
    NO_SIGNAL = "NO_SIGNAL"


class DecisionEvaluationStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    AVAILABLE = "AVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    CONTRACT_INVALID = "CONTRACT_INVALID"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class SizingStatus(StrEnum):
    SIZED = "SIZED"
    UNCHANGED = "UNCHANGED"
    BLOCKED = "BLOCKED"
```

模型不得主动返回 `NO_SIGNAL`。该动作只由服务层在 Provider、契约或必要输入失败时生成。

### 5.2 决策输入

`DecisionInputV1` 是逐证券不可变状态，包含：

| 字段 | 内容 |
|---|---|
| `decision_date` | 决策交易日 D |
| `decision_cutoff` | 带时区的 D 日截止时间 |
| `planned_execution_date` | 交易日历给出的 D+1 |
| `candidate_universe_id/hash` | 冻结候选池身份 |
| `market_snapshot_hash` | 冻结行情快照哈希 |
| `feature_snapshot_hash` | 该证券特征快照哈希 |
| `symbol` | 证券代码 |
| `membership` | `CANDIDATE` 或 `HELD_ONLY` |
| `features` | Phase 2 确定性特征、版本、截止时间与缺失原因 |
| `universe_jev` | Phase 3 候选池完整问题概率和 Evaluation ID |
| `symbol_jev` | Phase 3 个股完整问题概率和 Evaluation ID |
| `portfolio` | 决策前现金、冻结现金、总资产、已实现盈亏、组合版本和持仓数量 |
| `position` | 本票数量、可卖数量、成本、当前权重、浮动盈亏和持有交易日；空仓为 `None` |
| `recent_actions` | 本票最近五次正式标准化动作，按时间升序 |
| `pending_orders` | 本票未完成虚拟订单的动作、计划日期和状态摘要 |
| `allowed_actions` | 由代码依据状态机生成的有序动作集合 |
| `input_schema_version` | 固定为 `decision-input-v1` |

`input_hash` 使用 canonical JSON 的 SHA-256。`run_id`、API Key、数据库 URL 和运行进程身份不进入正式输入哈希。

### 5.3 输入禁止项

输入不得包含：

- D+1 或之后行情、Forward Label、已实现未来收益或未来成交状态；
- 新闻正文、开放互联网检索结果或模型自行检索内容；
- Jev 自由文本、历史 LLM 推理过程或未验证自然语言证据；
- 目标权重、目标数量、未来成交价、止盈止损比例；
- API Key、认证头、数据库凭据、SDK 调试堆栈。

### 5.4 模型输出

Provider 响应 Schema 固定为：

```json
{
  "action": "ENTER",
  "thesis": "趋势与成交确认一致，Jev 盈亏分布偏向正向。",
  "invalidation": "趋势结构或成交确认失效。"
}
```

规则：

- `action` 只能为 `ENTER/KEEP/EXIT/AVOID`；
- `thesis` 和 `invalidation` 各为 1—240 个字符；
- `extra="forbid"`，拒绝任何额外字段；
- 禁止输出 confidence、score、probability、收益率、价格、数量、仓位、止盈止损和资金比例；
- 响应正文先计算 SHA-256，再做 JSON 与 Pydantic 校验；
- 空内容、拒答、截断、多个结果、Schema 不匹配或状态机不合法均不得猜测修复。

## 6. 动作状态机

| 证券状态 | 模型允许动作 | 系统失败动作 |
|---|---|---|
| 候选股且空仓 | `ENTER`、`AVOID` | `NO_SIGNAL` |
| 候选股且持仓 | `KEEP`、`EXIT` | `NO_SIGNAL` |
| `HELD_ONLY` 且持仓 | `KEEP`、`EXIT` | `NO_SIGNAL` |

`HELD_ONLY` 不计入 `candidate_limit`，不得 `ENTER`。队列外持仓不得因退出候选池而自动清仓。

模型输出不在 `allowed_actions` 中时记录 `CONTRACT_INVALID`，系统标准化为 `NO_SIGNAL`。代码不得把非法 `ENTER` 推测为 `AVOID`，也不得把非法 `EXIT` 推测为 `KEEP`。

## 7. Prompt 契约

Prompt 版本固定为 `decision-prompt-v1`，分为稳定 system prompt 和 canonical user payload。

System prompt 明确：

1. 模型是离散动作分类器，不是仓位计算器或行情数据源。
2. 只能使用输入中的冻结事实和 Jev 概率。
3. Jev 概率是未校准预测，不得改写、重新计算或宣称为真实胜率。
4. 只能从 `allowed_actions` 选择一个动作。
5. 不得输出输入中不存在的数字、事实、新闻、价格或因果关系。
6. 只输出符合 JSON Schema 的对象。

User payload 是 `DecisionInputV1` 的 canonical JSON。Prompt 不拼接自由格式日志、异常信息或数据库记录文本。

## 8. Provider 边界

```python
class DecisionLlmProvider(Protocol):
    async def evaluate(
        self,
        command: DecisionEvaluationCommand,
    ) -> DecisionProviderResult: ...
```

`DeepSeekDecisionProvider` 使用注入的异步 Client、模型、Provider 版本、Prompt 版本、Schema 版本和超时。启动层从以下环境变量读取配置：

- `DEEPSEEK_API_KEY`：无默认值，使用 `SecretStr`；
- `DEEPSEEK_BASE_URL`：默认 `https://api.deepseek.com`；
- `DEEPSEEK_MODEL`：默认 `deepseek-flash`；旧 `deepseek-chat` 已退役，不得作为默认值；
- `DEEPSEEK_TIMEOUT_SECONDS`：默认 `30`，必须大于 0。

Provider 不读取全局环境变量，不保存 Secret，不记录完整异常正文。SDK 异常统一抛出 `ProviderUnavailableError`；响应契约异常抛出携带清理后响应哈希的 `ProviderContractError`。

离线测试使用 Fake Client，不访问网络。Live Test 同时受 `RUN_LIVE_DECISION_LLM_TESTS=1` 和有效 `DEEPSEEK_API_KEY` 门控。

## 9. 失败语义

| 条件 | 调用行为 | 状态 | 标准化动作 |
|---|---|---|---|
| Universe Jev 非 `AVAILABLE` | 整批不调用 LLM | `DATA_UNAVAILABLE` | 全部 `NO_SIGNAL` |
| Symbol Jev 非 `AVAILABLE` | 该票不调用 LLM | `DATA_UNAVAILABLE` | 该票 `NO_SIGNAL` |
| 必要特征缺失 | 该票不调用 LLM | `DATA_UNAVAILABLE` | 该票 `NO_SIGNAL` |
| 缺 Key、超时、限流、网络或空响应 | 调用失败 | `PROVIDER_UNAVAILABLE` | `NO_SIGNAL` |
| JSON、Schema、额外字段或动作状态非法 | 调用失败 | `CONTRACT_INVALID` | `NO_SIGNAL` |
| 合法输出 | 调用成功 | `AVAILABLE` | 模型离散动作 |

不调用备用模型，不自动重试，不合成 thesis 或 invalidation。失败记录中不伪造模型输出。

## 10. 幂等、并发与重放

正式决策键包含：

```text
input_hash
+ provider_name
+ provider_version
+ model_id
+ prompt_version
+ output_schema_version
```

同一正式键只允许一个 owner 调用外部 Provider。Evaluation 主记录保存当前 owner token、attempt sequence 和 lease；接管、成功提交和失败提交均使用数据库条件 `UPDATE` 做 CAS。旧 owner、过期 owner 和重复 owner 不能写入终态。

成功记录永久复用。失败记录可创建递增 Attempt 重试，但不得覆盖历史 Attempt。`run_id` 通过独立 RunLink 关联正式 Evaluation，不参与正式键。

Record Replay 读取已保存决策和仓位结果，外部调用数必须为 0。Model Re-evaluation 必须使用新的 Provider、模型、Prompt 或实验身份创建独立正式记录，不覆盖原结果。

## 11. 确定性仓位策略

策略版本固定为 `position-sizing-v1`。配置如下：

| 参数 | 值 |
|---|---:|
| `daily_risk_budget` | `0.00200000` |
| `volatility_floor` | `0.01000000` |
| `max_single_position_pct` | `0.10000000` |
| `max_gross_position_pct` | `0.80000000` |
| `min_cash_pct` | `0.20000000` |
| `min_entry_position_pct` | `0.01000000` |
| `liquidity_entry_floor` | `0.20000000` |
| `weight_quantum` | `0.00000001` |

非零原始目标：

```text
volatility_weight =
    daily_risk_budget
    / max(realized_vol_20d, volatility_floor)

liquidity_multiplier =
    clamp(liquidity_percentile, 0.25, 1.00)

raw_target =
    min(
        volatility_weight × liquidity_multiplier,
        max_single_position_pct
    )
```

所有 Decimal 运算使用局部 context、固定 precision 和 `ROUND_HALF_UP`，最终量化到八位小数。

### 11.1 动作处理

- `EXIT`：目标为 0。
- `AVOID`：目标为 0。
- `NO_SIGNAL`：保持当前权重。
- `ENTER`：计算 `raw_target`；必要特征缺失、流动性低于准入线或目标低于最低新建仓位时为 `BLOCKED`，目标为 0，不创建订单。
- `KEEP`：计算 `raw_target`；允许机械性增持或减持。如果数据或组合约束会把目标压为 0，则保持当前权重，禁止变成清仓。

### 11.2 组合约束

1. 先保留全部 `NO_SIGNAL` 当前权重。
2. 计算 `ENTER/KEEP` 原始目标，单票不得超过 10%。
3. 如果保留权重已经达到 80%，阻止新的 `ENTER`，`KEEP` 不得被压到 0。
4. 如果待分配目标超过剩余总仓位预算，按原始目标比例缩放。
5. 比例相同或量化残差按证券代码升序稳定处理。
6. 对未超限的输入，最终持仓权重不超过 80%，现金权重不低于 20%。
7. 所有舍入残差归入现金，现金与全部目标权重之和必须等于 1。

如果决策前组合已经超过 80%，`NO_SIGNAL` 和缺少可执行退出动作时不得为了满足新上限而强制卖出。该次 Sizing Run 记录 `PREEXISTING_GROSS_LIMIT_EXCEEDED`，阻止全部新增风险，并保持未获 `EXIT` 或有效 `KEEP` 调整的既有数量。总仓位上限是新增风险约束，不是绕过 LLM 动作的自动清仓指令。

确定性风控可以阻止 `ENTER` 或保持 `KEEP`，不得把 `ENTER` 反转为 `EXIT`，也不得把 `EXIT` 反转为 `ENTER`。

### 11.3 现有执行动作转换

| 决策动作与目标 | Phase 1 `SignalAction` |
|---|---|
| `ENTER` 且目标大于 0 | `OPEN` |
| `ENTER` 被阻止 | 不创建订单；审计为 `BLOCKED` |
| `KEEP` 且目标大于当前权重 | `ADD` |
| `KEEP` 且目标小于当前权重但大于 0 | `REDUCE` |
| `KEEP` 且目标等于当前权重 | `HOLD` |
| `EXIT` | `CLOSE` |
| `AVOID` | `AVOID` |
| `NO_SIGNAL` 且持仓 | `HOLD` |
| `NO_SIGNAL` 且空仓 | `AVOID` |

Phase 4 只产出目标权重。D+1 实际数量仍由现有执行引擎使用执行日开盘价、总资产、100 股整手、手续费、滑点和成交容量计算。

`DecisionSignalBatchV1` 是正式运行输出，字段包含 portfolio、决策日、执行日、Sizing 版本、候选证券、逐票目标、现金目标和输入哈希。Phase 1 的 `FixtureSignalBatch` 保持不变，只作为现有 Repository/Execution 的内部兼容输入；Phase 4 Adapter 必须逐字段转换并验证哈希，不允许通过本地 Fixture HTTP 路由提交正式信号。

## 12. C 组服务

`CGroupDecisionService.evaluate_run()` 接收：

- `run_id`、portfolio ID、candidate limit；
- 冻结 `MarketSnapshot` 和 Phase 2 features；
- Phase 3 `JevRunEvaluationV1`；
- 决策前 `PortfolioState`；
- 最近动作与未完成订单的冻结摘要；
- LLM Provider 身份；
- `PositionSizingConfigV1`。

服务执行顺序：

1. 校验所有日期、cutoff、candidate universe 和 snapshot hash 一致。
2. 构造“候选队列与当前持仓并集”，标记 `HELD_ONLY`。
3. 验证 Jev universe 和逐票结果属于同一冻结输入。
4. 按证券代码排序逐票 Claim。
5. 必要输入失败时直接记录 `NO_SIGNAL`，不调用 Provider。
6. 合法输入调用 DeepSeek Provider 并保存正式结果。
7. 收集全部证券动作，禁止静默遗漏。
8. 一次性运行确定性仓位策略。
9. 生成正式 `DecisionSignalBatchV1`，再通过无副作用 Adapter 转换为现有 `ValidatedSignalBatch` 兼容结构并保存；正式审计不使用 `fixture_version` 作为产品版本。
10. 返回决策、仓位、阻断原因和 Signal Batch 引用。

任何证券失败都不阻止其他证券完成；但仓位方案必须在完整动作集合上一次性计算和原子保存，禁止只保存部分目标。

## 13. 回测接入

Phase 4 提供 `FrozenCGroupTargetProvider`，实现已冻结的 `TargetProvider` Protocol。它只读取冻结的 Decision 与 Sizing 记录，将其转换为 `TargetPositionBatch`，不得调用 FKQT、Jev、LLM、FastAPI 或 SQLAlchemy Repository。

正式模型评估与回测重放分离：先由 C 组服务创建不可变决策数据集，再由回测引擎重放该数据集。这样同一次回测不会因模型服务状态或模型版本漂移而变化。

回测 Contributor 的 `backtest-contract-v1` 不修改。

## 14. 持久化设计

新增 Alembic Revision `0007_phase4_llm_position_sizing`。

### 14.1 `ai_signal_llm_decision_evaluation`

保存正式键、证券、成员身份、日期、输入 JSON/hash、Provider/模型/Prompt/Schema 版本、状态、标准化动作、thesis、invalidation、原始响应正文/hash、当前 owner、lease、最新 Attempt 序号和时间。

### 14.2 `ai_signal_llm_decision_attempt`

保存 Evaluation、run ID、owner token、序号、Provider/模型、开始/结束时间、延迟、状态、响应哈希和稳定错误码。

### 14.3 `ai_signal_llm_decision_run_link`

保存 run 与正式 Evaluation 的多对多审计关联。

### 14.4 `ai_signal_position_sizing_run`

保存 run、portfolio、决策日、执行日、Sizing 版本、配置 JSON/hash、输入 hash、决策前组合 hash、目标批次 hash、总目标权重、现金目标和创建时间。

### 14.5 `ai_signal_position_target`

逐票保存原始决策、Sizing 状态、原始目标、最终目标、当前权重、转换后的 SignalAction 和稳定阻断码。

正式表不保存 API Key、认证头、数据库 URL 或完整 SDK 异常。原始响应仅保存模型最终 JSON 正文，不保存 Provider 内部调试信息。

## 15. API、CLI 与 Live Probe

Phase 4 增加手工单日入口，不增加调度器：

- Service：`CGroupDecisionService.evaluate_run()`；
- CLI：`decision run-c-group --date YYYY-MM-DD --portfolio-id ID --candidate-limit N`；
- 查询：按 run ID 读取 Decision、Sizing 和 Signal Batch 审计；
- Live Test：单只合成证券、空仓和持仓各一次，验证动作 Schema，不执行虚拟订单。

REST 写入口可在 Phase 5 与运行恢复一起增加，避免 Phase 4 提前形成不完整的自动运行 API。现有只读 Portfolio 与 Market API 不受影响。

## 16. 安全与 Secret

- `DEEPSEEK_API_KEY` 使用 `SecretStr`，只在启动层创建 Client 时解包。
- 请求和响应日志不得包含认证头。
- Provider 异常只公开稳定错误码和异常类型，不保存异常正文。
- Git、Fixture、文档和测试只使用明显占位符。
- 模型响应中的未知字段直接拒绝，不进入持久化业务字段。
- 原始 JSON 正文受长度上限约束，超过限制视为契约无效。

## 17. 测试策略

### 17.1 领域测试

- 输出 Schema 拒绝额外字段、连续数值字段、空文本和 `NO_SIGNAL`；
- 状态机覆盖空仓、持仓和 `HELD_ONLY`；
- Decision input hash 不受 Mapping 顺序和 run ID 影响；
- 任何未来时间、未来标签或禁止字段均被拒绝。

### 17.2 Provider 测试

- 验证模型、JSON Schema、Prompt 版本和 canonical payload；
- 空响应、拒答、截断、非法 JSON、额外字段和非法动作分别失败；
- 网络异常不泄露响应正文；
- Fake Client 全程离线。

### 17.3 仓位测试

- 同一输入逐字段相同；
- 波动率下限、单票上限、流动性准入和最低新建仓位；
- 总仓位 80%、最低现金 20% 和舍入残差；
- 决策前已超限时阻止新增风险但不自动清仓；
- `NO_SIGNAL` 保持、`EXIT` 归零、`KEEP` 不被压到零；
- 输入顺序变化不改变结果；
- 动作到 Phase 1 SignalAction 的完整映射。

### 17.4 持久化与并发测试

- 同一正式键两个 worker 只有一个获得 Claim；
- 过期租约只有一个 worker 接管；
- 旧 owner 与新 owner 不能同时提交；
- 成功缓存命中不产生新外部调用；
- 失败 Attempt 可重试且历史不覆盖；
- 仓位与逐票 Target 同事务提交，故障时全部回滚。

### 17.5 集成与重放测试

- 候选池与持仓并集无遗漏；
- Jev universe 失败时 LLM 调用为 0；
- 单票 Jev 失败只影响该票；
- Provider 失败得到 `NO_SIGNAL` 并保持持仓；
- 重放 Provider 调用为 0；
- `FrozenCGroupTargetProvider` 不读取 D+1 行情；
- 生成的 Signal Batch 可被现有 Validator 和执行引擎接受。

阶段开发遵守 R33：任务内先运行最小失败测试；首次完整离线验收后，只重跑失败项和受影响项。

## 18. 验收清单

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| 模型动作集合 | 仅 4 个模型动作 | Schema 单元测试 | 必须 |
| `NO_SIGNAL` 来源 | 仅系统失败归一化 | Provider/Service 测试 | 必须 |
| 模型仓位字段 | 0 个 | Schema 与 Prompt 扫描 | 必须 |
| 队列外持仓 | 全部覆盖且不可 `ENTER` | 集成测试 | 必须 |
| Provider 降级 | 不调用备用模型 | Fake 调用计数 | 必须 |
| 正式幂等 | 相同输入新增调用 0 次 | Repository 集成测试 | 必须 |
| 并发 owner | 同一时刻 1 个 | CAS 并发测试 | 必须 |
| 单票上限 | `0.10000000` | Sizer 单元测试 | 必须 |
| 总仓位上限 | `0.80000000` | Sizer 组合测试 | 必须 |
| 最低现金 | `0.20000000` | 权重恒等式测试 | 必须 |
| 重放外部调用 | FKQT/Jev/LLM 均为 0 | Replay 集成测试 | 必须 |
| D+1 泄露 | 0 个字段 | 时间旅行测试 | 必须 |
| Secret 入库 | 0 个 | 数据库内容与 Git 扫描 | 必须 |
| 旧功能回归 | Phase 0—3 受影响测试全通过 | 定点回归 | 必须 |

## 19. 已知风险

1. LLM 动作即使 Schema 合法也可能没有样本外增量；Phase 6 必须与确定性基线比较。
2. 逐证券调用会增加延迟和费用；Phase 5 只能增加有限并发，不能改变正式输入或失败隔离语义。
3. DeepSeek 模型服务和结构化输出能力可能升级；Provider 与版本字段必须隔离变化。
4. 仓位参数是第一版实验常量，不代表经过优化；任何调整都必须产生新的 Sizing 版本。
5. `KEEP` 机械调整可能增加换手；样本外报告必须单独展示换手和费用。

## 20. 交付停止点

Phase 4 完成后，可以使用已冻结数据执行单日 C 组决策、生成确定性 Target、转换为现有虚拟信号，并让回测引擎重放冻结结果。系统仍不会自动在每个交易日运行；日频调度、恢复和监控属于 Phase 5。
