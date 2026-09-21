# Phase 3 Jev 候选池与个股状态概率设计

日期：2026-09-21

状态：待用户书面复核

需求基线：`REQUIREMENTS.md` v0.3（R7、R8、R10、R12、R14、R21、R23、R24、R26、R29、R33、R37—R41）

上游阶段：Phase 2 行情快照与确定性特征

下游阶段：Phase 4 LLM 离散动作与确定性仓位

## 1. 执行摘要

Phase 3 将原有“Jev 输出因子占比”方案替换为“Jev 输出市场和个股状态的有限标签概率”。Python 继续负责所有可精确计算的指标、归一化、横截面位置和权重映射；Jev 只回答代码难以用单一阈值稳定表达的状态判断问题。

Jev 输出描述的是：在决策日 D 的截止时间内，输入状态更符合哪个有限标签，以及当前趋势更偏向延续、模糊还是反转。它不是经统计校准的未来涨跌概率，也不是未来收益预测。系统必须通过独立样本外回测和概率校准检验判断这些输出是否具有交易增量。

```text
冻结行情与证券状态
        ↓
Python 确定性特征
        ├── 候选池级压缩状态 ──→ JevUniverseStateV1 ──→ 候选池风险概率
        └── 个股级压缩状态 ──→ JevSymbolStateV1 ──→ 个股状态概率
                                                        ↓
                                             Phase 4 决策 LLM
                                                        ↓
                                         ENTER/KEEP/EXIT/AVOID
                                                        ↓
                                           确定性仓位与虚拟执行
```

## 2. 设计目标与非目标

### 2.1 目标

1. 以版本化领域 Entity 冻结 Jev 候选池级和个股级输入。
2. 使用有限标签概率分布表达市场风险与个股状态判断。
3. 同一候选池状态在同一正式运行中只调用一次并供全部个股复用。
4. 保存完整概率、调用状态和版本信息，支持离线重放、样本外校准和模型对比。
5. 在 Provider 失败、响应无效和输入不足时提供明确、不可伪造的失败语义。
6. 保持回测 Contributor 已冻结的 `backtest-contract-v1` 不变。

### 2.2 非目标

1. 不让 Jev 直接输出交易动作。
2. 不让 Jev 输出连续因子权重、仓位、价格或收益率。
3. 不把新闻、公告、政策、行业文本、RAG 或长期记忆引入 Phase 3。
4. 不把持仓、现金、成本、盈亏或历史模型决策放入 Jev 输入。
5. 不在 Phase 3 实现最终决策 LLM、仓位公式或 A/B/C/D 完整实验调度。
6. 不宣称 Jev 概率已经过市场结果校准。

## 3. 职责边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| Phase 2 Feature Engine | 指标、Decimal 量化、横截面排名、缺失原因、输入哈希 | 模糊状态判断、交易动作 |
| Phase 3 Jev Provider | 有限标签状态概率、响应契约校验、调用审计 | 指标计算、权重、仓位、买卖动作 |
| Phase 4 Decision LLM | 结合特征、Jev 概率和持仓输出离散动作 | 连续仓位、价格、订单数量 |
| Position Sizer | 依据离散动作和约束确定仓位与数量 | 改写交易方向、调用模型 |
| Backtest Engine | 消费 `TargetPositionBatch`、推进交易日、计算指标 | 直接调用 Jev 或理解 Jev Schema |

“因子有效性”不属于 Jev 当前状态判断。因子是否有效必须由样本外统计、消融实验和稳定性检验决定。Phase 3 只保留 `factor_conflict`，用于描述当前输入特征是否相互支持，而不是声称某因子具备预测能力。

## 4. 输入设计

### 4.1 共同审计头 `JevStateHeaderV1`

候选池级和个股级输入共用以下字段：

| 字段 | 类型 | 约束 |
|---|---|---|
| `run_id` | UUID | 正式运行标识 |
| `decision_date` | date | 决策交易日 D |
| `decision_cutoff` | datetime | 必须带时区；所有输入均不得晚于该时间 |
| `candidate_universe_id` | str | 冻结候选池标识 |
| `candidate_universe_hash` | str | 64 位小写 SHA-256 |
| `market_snapshot_hash` | str | Phase 2 冻结快照哈希 |
| `feature_set_version` | str | 确定性特征集合版本 |
| `state_schema_version` | Literal | 固定为 `jev-state-v1` |

共同约束：

- 输入使用 canonical JSON 计算 `input_hash`。
- Decimal 以规范化字符串传输，不使用二进制浮点序列化。
- 映射字段按键排序；数组顺序必须由领域规则固定。
- 同一 `input_hash + provider + model + question_set_version` 的正式调用幂等。

### 4.2 候选池级输入 `JevUniverseStateV1`

候选池级输入完全来源于同一候选池快照和 Phase 2 特征的确定性聚合，不要求新增基准指数数据：

| 分类 | 字段 |
|---|---|
| 中心趋势 | `median_return_5d`、`median_return_20d`、`median_close_vs_ma20`、`median_ma20_slope_5d` |
| 候选池宽度 | `advance_ratio`、`above_ma20_ratio`、`positive_return_20d_ratio` |
| 风险 | `median_realized_vol_20d`、`cross_section_return_dispersion`、`downside_breadth_ratio` |
| 成交 | `median_amount_ratio_5d_20d`、`liquid_symbol_ratio` |
| 覆盖 | `eligible_symbol_count`、`missing_symbol_count`、`coverage_ratio`、`missing_reasons` |

候选池级输入不得携带单一证券代码、账户状态和个股历史。它只代表已冻结候选池，不得在输出、API 或报告中写成“全 A 股市场状态”。其正式结果键为：

```text
decision_date + decision_cutoff + candidate_universe_hash
+ market_snapshot_hash + feature_set_version
+ question_set_version + provider + model
```

该键在一个正式运行中最多成功调用一次。重试必须属于同一调用审计链，不得产生两个并列正式结果。

### 4.3 个股级输入 `JevSymbolStateV1`

个股级输入由共同审计头、证券身份和 Phase 2 特征构成：

| 分类 | 字段 |
|---|---|
| 身份 | `symbol`、`market`、`board`、`listing_age_trading_days` |
| 结构状态 | `trading_status`、`is_st_or_delisting_risk`、`is_initial_no_limit_period`、`corporate_action_status`、`adjustment_mode` |
| 趋势与收益 | `return_5d`、`return_20d`、`return_60d`、`close_vs_ma5`、`close_vs_ma20`、`close_vs_ma60`、`ma5_slope_5d`、`ma20_slope_5d` |
| 波动与反转 | `realized_vol_20d`、`atr_pct_14d`、`downside_vol_20d`、`distance_from_20d_high`、`distance_from_20d_low`、`short_term_reversal_3d` |
| 成交与流动性 | `volume_ratio_5d_20d`、`amount_ratio_5d_20d`、`turnover_pct`、`liquidity_percentile` |
| 横截面 | `return_20d_percentile`、`volatility_percentile` |
| 覆盖 | `available_feature_count`、`required_feature_count`、`missing_reasons` |

输入只传递已通过 Phase 2 Point-in-Time 校验的字段。必要特征不足时，不调用 Jev，直接记录 `DATA_UNAVAILABLE`。

### 4.4 明确禁止的输入

- 原始日线、分钟线或 Tick 数组；
- 新闻、公告、政策和行业正文；
- D+1 或更晚的价格、成交、标签和执行结果；
- 账户现金、持仓数量、成本价、当前权重和浮动盈亏；
- LLM 历史动作、Jev 历史响应和策略历史盈亏；
- 目标仓位、订单数量、止盈止损比例和预期收益率。

这些限制使 Jev 结果保持账户无关，可供多个实验组、组合和回测重复使用。

## 5. 问题与输出设计

### 5.1 候选池级问题

| `question_id` | 版本 | 标签 | 含义 |
|---|---|---|---|
| `universe_risk_regime` | `universe-risk-regime-v1` | `RISK_ON`、`NEUTRAL`、`RISK_OFF` | 当前候选池整体状态对承担权益风险的支持程度 |

### 5.2 个股级问题

| `question_id` | 版本 | 标签 | 含义 |
|---|---|---|---|
| `direction_regime` | `direction-regime-v1` | `UP`、`RANGE`、`DOWN` | 当前方向状态最符合哪类 |
| `trend_persistence` | `trend-persistence-v1` | `CONTINUE`、`UNCERTAIN`、`REVERSE` | 当前趋势延续、模糊或反转倾向 |
| `volume_confirmation` | `volume-confirmation-v1` | `CONFIRMED`、`AMBIGUOUS`、`REJECTED` | 量能对当前价格方向的支持程度 |
| `overheat_risk` | `overheat-risk-v1` | `LOW`、`MEDIUM`、`HIGH` | 当前状态发生拥挤或短期回撤的风险级别 |
| `factor_conflict` | `factor-conflict-v1` | `LOW`、`MEDIUM`、`HIGH` | 确定性特征之间的方向冲突程度 |
| `data_sufficiency` | `data-sufficiency-v1` | `SUFFICIENT`、`LIMITED`、`INSUFFICIENT` | 输入是否足以支持本次状态判断 |

第一版只使用 Jev `Choice`。不使用开放文本作为决策输入，不使用连续 `Score` 生成仓位或因子权重。

### 5.3 标准输出 `JevQuestionResultV1`

```json
{
  "question_id": "trend_persistence",
  "question_version": "trend-persistence-v1",
  "criteria_version": "trend-persistence-criteria-v1",
  "distribution": {
    "CONTINUE": "0.610000",
    "UNCERTAIN": "0.270000",
    "REVERSE": "0.120000"
  },
  "selected_label": "CONTINUE"
}
```

约束：

1. 每个标签必须恰好出现一次。
2. 概率必须为有限非负 Decimal，逐项不大于 1。
3. 概率之和必须在契约容差内等于 1；容差由 Provider Adapter 固定，不从模型响应读取。
4. `selected_label` 必须是最高概率标签。
5. 并列最高概率时按问题定义中的标签顺序确定，保证重放确定性。
6. 任何缺标签、未知标签、NaN、Infinity、负值或总和越界均使整个响应成为 `CONTRACT_INVALID`，不得保存部分正式概率。

### 5.4 概率语义

允许的解释：

> “在输入 Schema、问题标准、Provider 和模型版本固定时，Jev 将当前个股状态的 0.61 概率质量分配给 `CONTINUE`。”

禁止的解释：

> “该股票下一交易日有 61% 概率上涨。”

两者只有在后续使用带时间标签的样本外校准证明可映射时才可能建立统计关系。Phase 3 数据模型不预设这种关系。

## 6. Provider 接口

```python
class JevUniverseStateProvider(Protocol):
    async def evaluate_universe(
        self,
        state: JevUniverseStateV1,
    ) -> JevEvaluationV1: ...


class JevSymbolStateProvider(Protocol):
    async def evaluate_symbol(
        self,
        state: JevSymbolStateV1,
    ) -> JevEvaluationV1: ...
```

`JevEvaluationV1` 包含：

- `scope`：`UNIVERSE` 或 `SYMBOL`；
- `symbol`：候选池级为空，个股级必填；
- `status`：`AVAILABLE`、`PROVIDER_UNAVAILABLE`、`CONTRACT_INVALID`、`DATA_UNAVAILABLE`；
- `results`：仅 `AVAILABLE` 时存在；
- `model_id`、`provider_version`；
- `state_schema_version`、`question_set_version`；
- `input_hash`、`raw_response_hash`；
- `started_at`、`finished_at`、`latency_ms`；
- `error_code`：失败状态必填，禁止保存 Secret 和完整 Provider 响应堆栈。

业务服务依赖 Protocol，不依赖 TypeSafe SDK 类型。SDK Adapter 负责将领域输入映射为 Jev `State + Questions`，并把响应转换为领域结果。

## 7. 失败与幂等语义

| 条件 | 状态 | 概率字段 | 后续行为 |
|---|---|---|---|
| 必要输入不足 | `DATA_UNAVAILABLE` | 不存在 | 不调用 Provider |
| API Key 缺失、无权限、超时、限流、网络失败 | `PROVIDER_UNAVAILABLE` | 不存在 | B/C 组记录失败，不跨组降级 |
| 未知标签、缺标签、非法概率、Schema 不匹配 | `CONTRACT_INVALID` | 不存在 | 保存响应哈希和错误码，不保存正式概率 |
| 完整有效响应 | `AVAILABLE` | 完整分布 | 允许进入下游实验 |

正式调用以输入哈希和版本组合幂等。已存在 `AVAILABLE` 结果时直接复用，不再次调用 Provider。失败重试创建新的 Attempt 记录，但只有一次结果能成为该正式键的当前正式结果。

## 8. 持久化与审计

每次 Attempt 至少保存：

- 正式结果键和 `run_id`；
- `scope`、`symbol`、`decision_date`、`decision_cutoff`；
- `provider_name`、`provider_version`、`model_id`；
- `state_schema_version`、`question_set_version`；
- 每个问题的 `question_version` 和 `criteria_version`；
- `input_hash`、`raw_response_hash`；
- 调用状态、错误码、开始时间、结束时间和延迟；
- `AVAILABLE` 时的完整概率分布与派生标签。

原始请求和响应只保存经过 Secret 清理的 canonical JSON 或内容寻址引用。API Key、Authorization Header、SDK 内部凭证和数据库凭据不得进入数据库、日志、异常文本或 API 响应。

## 9. 下游使用方式

### 9.1 B 组

Phase 4 LLM 同时读取确定性特征、候选池 Jev 概率、个股 Jev 概率和持仓状态，输出 `ENTER/KEEP/EXIT/AVOID`。Jev 概率只是输入证据，不强制对应某个动作。

### 9.2 C 组

C 组不让 Jev 直接生成动作，而由版本化确定性映射将概率转为离散动作。映射器可以读取 `direction_regime`、`trend_persistence`、`overheat_risk` 和当前是否持仓，但阈值必须在 Phase 6 实验规范中单独冻结，不能在 Phase 3 Provider 中硬编码。

### 9.3 动态因子权重

如未来实验需要动态权重，Jev 只提供候选池状态概率，Python 使用预定义 Profile 进行确定性混合：

```text
final_weights =
    P(RISK_ON)  × risk_on_profile
  + P(NEUTRAL)  × neutral_profile
  + P(RISK_OFF) × risk_off_profile
```

Profile、映射公式和版本均由代码管理。Jev 不返回任何 Profile 或连续权重。

## 10. 兼容与迁移

现有 `JevSemanticFactorProvider` 属于 Phase 0 新闻/证据语义原型，不再作为 Phase 3 正式主链。迁移采用并行替换：

1. 新建候选池状态和个股状态领域 Entity 与 Provider Protocol。
2. 新建 SDK Adapter，不修改旧 Provider 的公开行为。
3. Phase 3 新服务只依赖新 Protocol。
4. 旧 Provider 和测试暂时保留，用于历史迁移可追溯性。
5. 删除旧 Provider 必须在新主链验收后作为独立需求处理，不在 Phase 3 顺带删除。

`backtest-contract-v1` 不变。Phase 3/4/6 通过核心项目实现的 `TargetProvider` 向回测引擎提供目标批次，回测模块不导入任何 Jev Entity。

## 11. 测试与验收

遵守 R33：每个任务先运行最小失败测试，再运行对应通过测试；修复时只重跑失败项；阶段最终验收才运行一次完整离线套件和静态检查。

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| 候选池调用去重 | 同一正式键最多 1 次成功 Provider 调用 | 并发幂等单元测试 | 必须 |
| 个股隔离 | 每个结果只对应 1 个证券和 1 个输入哈希 | Entity/Repository 测试 | 必须 |
| Point-in-Time | 输入字段时间全部不晚于 `decision_cutoff` | 时间旅行失败测试 | 必须 |
| 禁止输入 | 持仓、现金、新闻、未来标签字段为 0 | Schema 字段扫描 | 必须 |
| 概率完整性 | 标签全集一致，概率和在契约容差内为 1 | Provider 契约测试 | 必须 |
| 非法响应 | 统一为 `CONTRACT_INVALID`，正式概率 0 条 | 失败响应参数化测试 | 必须 |
| Provider 失败 | `PROVIDER_UNAVAILABLE`，伪造概率 0 条 | Fake Client 测试 | 必须 |
| 数据不足 | Provider 调用 0 次 | 服务单元测试 | 必须 |
| 版本审计 | 规定的版本和哈希字段全部非空 | Repository 测试 | 必须 |
| Secret 泄漏 | API Key 和认证头匹配数为 0 | 日志、数据库和 Git 扫描 | 必须 |
| 离线重放 | 外部调用为 0，结果逐字段相同 | Replay 集成测试 | 必须 |
| 回测契约 | `backtest-contract-v1` 公共 Entity 无变化 | API 快照测试 | 必须 |

真实 Jev 调用只作为显式启用的 Live Test，要求有效 `TYPESAFE_API_KEY` 和账户模型权限；默认 CI 不访问网络，也不因缺少凭据失败。

## 12. 已知风险与处理

| 风险 | 影响 | 处理 |
|---|---|---|
| Jev 概率未针对 A 股校准 | 数值可能不对应实际频率 | Phase 6 计算 Brier Score、ECE 和按标签收益分层 |
| Provider 或模型版本漂移 | 同状态输出变化 | 保存全部版本和哈希，禁止覆盖历史结果 |
| 问题文字轻微变化导致分布漂移 | 跨版本不可直接比较 | 每次修改提升 `question_version` 或 `criteria_version` |
| 候选池级状态被每票重复调用 | 成本上升且结果可能不一致 | 候选池正式键唯一约束与调用去重 |
| 把状态概率误读为涨跌概率 | 形成虚假置信度 | Schema 命名、文档、报告均禁止使用 `up_probability` |
| 旧语义 Provider 与新 Provider 混用 | 审计含义不一致 | 不同 Protocol、表记录类型和服务入口 |

## 13. 文件边界

实施阶段预计新增或修改以下责任区域；具体文件名由实施计划锁定：

```text
src/fkqt_jevinvestor/domain/       Jev 输入、输出、状态和概率 Entity
src/fkqt_jevinvestor/providers/    Protocol 与 TypeSafe SDK Adapter
src/fkqt_jevinvestor/services/     市场去重、个股编排、失败语义
src/fkqt_jevinvestor/persistence/  Attempt、正式结果与概率持久化
migrations/versions/               Phase 3 审计表迁移
tests/unit/                        Entity、Provider、服务最小测试
tests/integration/                 持久化、幂等和离线重放测试
tests/live/                        显式启用的真实 Jev 契约测试
```

Phase 3 不修改 `src/fkqt_jevinvestor/backtest/`、`tests/backtest/` 和已冻结的 Contributor 契约。

## 14. 完成定义

Phase 3 只有在以下条件全部满足时才完成：

1. R7、R8、R10、R12、R14、R21、R23、R24、R26、R29、R33、R37—R41 均有代码或测试证据。
2. 候选池级与个股级输入、问题、结果和失败状态均为版本化领域类型。
3. 正式结果完整保存概率分布、版本、哈希和调用状态。
4. Provider 不可用、输入不足和响应无效均不会产生伪造概率。
5. 同一候选池正式键不会按证券重复调用。
6. 默认离线 CI 不需要 API Key；显式 Live Test 能验证真实 SDK 契约。
7. 回测公共契约无变化。
8. 阶段最终验收完成一次完整离线测试、Ruff、Pyright 和 Alembic 升降级。
