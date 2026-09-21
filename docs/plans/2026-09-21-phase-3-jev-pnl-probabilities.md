# Phase 3 Jev 盈亏概率 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为默认约 80 只 A 股冻结候选队列实现 C 组优先的 Jev 盈亏概率层，包括候选池/个股输入、真实标签、TypeSafe SDK Adapter、失败审计、持久化、幂等编排和显式 Live Test。

**Architecture:** Phase 2 的不可变行情快照与确定性特征先转换为账户无关的 `JevUniverseStateV1` 和 `JevSymbolStateV1`；TypeSafe Adapter 使用有限 `Choice` 问题返回待校准概率。数据库以正式键协调并发调用并保存 Attempt、正式结果和完整概率；未来真实标签由独立纯函数从 D+1 后冻结行情计算，绝不进入正式预测输入。现有新闻语义 `JevSemanticFactorProvider` 保留，Phase 3 新链使用独立类型和文件。

**Tech Stack:** Python 3.12、Pydantic 2、TypeSafe SDK `typesafe-sdk`、SQLAlchemy 2 Async、Alembic、Decimal、pytest、pytest-asyncio、Ruff、Pyright。

**Spec:** `docs/specs/2026-09-21-phase-3-jev-market-state-design.md`

## Global Constraints

- 映射到需求 R7、R8、R10、R12、R14、R21、R23、R24、R26、R27、R29、R33、R37—R43。
- C 组是第一版主链；Phase 3 优先交付 C 组所需 Jev 契约和真实标签，A/B/D 不得阻塞。
- 默认候选目标为 80 只；合格证券不足时保留实际数量，不补入不合格证券。
- 正式评估集合为候选队列与现有持仓并集；`HELD_ONLY` 只属于编排元数据，不进入 Jev State。
- Jev 只返回有限 `Choice` 概率，不返回连续收益率、因子权重、交易动作、仓位、价格或订单数量。
- 第一版不存在动态因子权重、权重 Profile 或概率到权重映射。
- D 日正式输入不得读取 D+1 及之后行情或真实标签。
- 概率和金额使用 Decimal；数据库、哈希和 JSON 中禁止依赖二进制浮点结果。
- Provider 失败不得生成概率，也不得降级到另一实验组或备用模型。
- 默认测试和 CI 不访问网络；Live Test 必须同时受 `RUN_LIVE_JEV_TESTS=1` 与 `TYPESAFE_API_KEY` 门控。
- 保留旧 `JevSemanticFactorProvider` 的公开行为和测试；删除旧原型不属于本计划。
- 不修改 `src/fkqt_jevinvestor/backtest/`、`tests/backtest/` 或 `backtest-contract-v1` 公共 Entity。
- 遵守 R33：任务内运行最小 RED/GREEN 测试；发现失败后只重跑失败项；完整离线套件、Ruff、Pyright 和迁移往返只在 Task 7 运行一次。

## Review Focus

1. 同一正式键发生两个并发请求时，合理行为是只有取得数据库 Claim 的请求调用 Jev，另一个读取进行中或已保存状态；Task 5 的并发测试固定该行为。
2. 股票掉出 80 只队列但仍有持仓时，合理行为是仍生成个股 Jev 评估且 `HELD_ONLY` 不进入 State；Task 5 的持仓移出队列测试固定该行为。
3. TypeSafe 返回完整标签但包含 NaN、Infinity、负数或概率和越界时，合理行为是整次 `CONTRACT_INVALID` 且保存 0 条正式概率；Task 3 的参数化测试固定该行为。
4. D+1 停牌、无可成交开盘或只有不足 5 个未来交易日时，合理行为是 `LABEL_UNAVAILABLE` 而不是零收益；Task 2 的标签测试固定该行为。
5. 旧新闻语义 Provider 与新行情盈亏 Provider 同时存在时，合理行为是类型、问题集、持久化 operation 和调用入口完全隔离；Task 3 与 Task 7 的兼容测试固定该行为。

---

## 文件结构

| 文件 | 责任 |
|---|---|
| `src/fkqt_jevinvestor/domain/jev_market.py` | Phase 3 输入、概率结果、状态、标签和调用命令的冻结领域类型 |
| `src/fkqt_jevinvestor/providers/jev_market_questions.py` | `Choice` 问题目录、标签顺序、问题和判定标准版本 |
| `src/fkqt_jevinvestor/services/jev_state_builder.py` | 从 Phase 2 快照/特征构造候选池与个股 State |
| `src/fkqt_jevinvestor/services/forward_labels.py` | 从 D+1 后冻结行情生成 1 日/5 日真实标签 |
| `src/fkqt_jevinvestor/providers/jev_market.py` | 新 TypeSafe SDK Adapter、严格响应校验和 Secret 清理 |
| `src/fkqt_jevinvestor/persistence/models.py` | 新增 Evaluation、Attempt、QuestionResult、RunLink ORM 表 |
| `src/fkqt_jevinvestor/persistence/jev_repository.py` | 正式键 Claim、Attempt、概率和幂等读取 |
| `src/fkqt_jevinvestor/services/jev_market_service.py` | 候选池一次调用、个股调用、失败状态与 `HELD_ONLY` 编排 |
| `migrations/versions/0006_phase3_jev_pnl_probabilities.py` | Phase 3 表、唯一约束和可逆迁移 |
| `tests/unit/test_jev_market_contracts.py` | 领域约束与 canonical hash |
| `tests/unit/test_jev_market_questions.py` | 问题全集、Choice-only 与版本约束 |
| `tests/unit/test_jev_state_builder.py` | 80 只边界、聚合、缺失和账户隔离 |
| `tests/unit/test_forward_labels.py` | 1 日/5 日标签、MAE/MFE、费用和不可用语义 |
| `tests/unit/test_jev_market_provider.py` | SDK 映射、严格概率校验、Provider 失败和旧 Provider 隔离 |
| `tests/integration/test_jev_repository.py` | 迁移、Claim、Attempt、正式概率、失败原子性和重放 |
| `tests/integration/test_jev_market_service.py` | 候选池复用、个股隔离、持仓并集和无数据短路 |
| `tests/integration/test_phase3_migration.py` | `0006` 表结构、约束与 downgrade |
| `tests/live/test_jev_market_live.py` | 显式启用的新行情问题真实 SDK 契约测试 |
| `docs/runbooks/jev-market-probabilities.md` | 本地离线运行、Live Test、失败代码与审计查询 |

### Task 1: 冻结 Phase 3 领域契约与 Jev 问题目录

**Requirements:** R8、R23、R37—R41

**Files:**
- Create: `src/fkqt_jevinvestor/domain/jev_market.py`
- Create: `src/fkqt_jevinvestor/providers/jev_market_questions.py`
- Test: `tests/unit/test_jev_market_contracts.py`
- Test: `tests/unit/test_jev_market_questions.py`

**Interfaces:**
- Consumes: `MarketFeatureSnapshot`、`SecurityTradeState`、`typesafe_sdk.Choice`。
- Produces: `JevScope`、`JevEvaluationStatus`、`JevStateHeaderV1`、`JevUniverseStateV1`、`JevSymbolStateV1`、`JevQuestionResultV1`、`JevEvaluationV1`、`JevEvaluationCommand`、`QUESTION_DEFINITIONS`、`build_jev_market_questions()`。

- [ ] **Step 1: 编写领域契约失败测试**

在 `tests/unit/test_jev_market_contracts.py` 写入测试，至少固定：概率标签必须完整、和为 `1.000000`、未知标签拒绝、`AVAILABLE` 必须有结果、失败状态不得有结果、市场范围不得带 symbol、个股范围必须带 symbol、canonical hash 不受 Mapping 插入顺序影响。

```python
def test_question_result_requires_exact_distribution() -> None:
    with pytest.raises(ValueError, match="JEV_PROBABILITY_LABELS_INVALID"):
        JevQuestionResultV1(
            question_id="profitability_5d",
            question_version="profitability-5d-v1",
            criteria_version="pnl-label-criteria-v1",
            label_order=("PROFITABLE", "FLAT", "LOSS"),
            distribution={"PROFITABLE": Decimal("0.7"), "LOSS": Decimal("0.3")},
            selected_label="PROFITABLE",
        )
```

- [ ] **Step 2: 运行领域契约测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_jev_market_contracts.py -q`

Expected: collection FAIL，原因是 `fkqt_jevinvestor.domain.jev_market` 尚不存在。

- [ ] **Step 3: 实现冻结领域类型**

在 `domain/jev_market.py` 使用 `ConfigDict(frozen=True)`，固定以下枚举与签名：

```python
class JevScope(StrEnum):
    UNIVERSE = "UNIVERSE"
    SYMBOL = "SYMBOL"


class JevEvaluationStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    AVAILABLE = "AVAILABLE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    CONTRACT_INVALID = "CONTRACT_INVALID"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


class JevStateHeaderV1(BaseModel):
    model_config = ConfigDict(frozen=True)
    decision_date: date
    decision_cutoff: datetime
    candidate_universe_id: str
    candidate_universe_hash: str = Field(min_length=64, max_length=64)
    candidate_target_size: int = Field(default=80, eq=80)
    candidate_actual_size: int = Field(ge=0, le=80)
    market_snapshot_hash: str = Field(min_length=64, max_length=64)
    feature_set_version: str
    state_schema_version: Literal["jev-state-v1"] = "jev-state-v1"


class JevUniverseStateV1(BaseModel):
    model_config = ConfigDict(frozen=True)
    header: JevStateHeaderV1
    metrics: Mapping[str, Decimal]
    coverage: Mapping[str, int | Decimal | tuple[str, ...]]


class JevSymbolStateV1(BaseModel):
    model_config = ConfigDict(frozen=True)
    header: JevStateHeaderV1
    symbol: str
    security: Mapping[str, str | bool | int | None]
    features: Mapping[str, Decimal]
    missing_reasons: tuple[str, ...]


class JevQuestionResultV1(BaseModel):
    model_config = ConfigDict(frozen=True)
    question_id: str
    question_version: str
    criteria_version: str
    label_order: tuple[str, ...]
    distribution: Mapping[str, Decimal]
    selected_label: str


class JevEvaluationCommand(BaseModel):
    model_config = ConfigDict(frozen=True)
    scope: JevScope
    state: JevUniverseStateV1 | JevSymbolStateV1
    provider_name: str
    provider_version: str
    model_id: str
    question_set_version: Literal["jev-pnl-questions-v1"] = "jev-pnl-questions-v1"

    @property
    def input_hash(self) -> str:
        return sha256_json(self.state.model_dump(mode="json"))

    @property
    def formal_key(self) -> str:
        return sha256_json({
            "scope": self.scope.value,
            "input_hash": self.input_hash,
            "provider_name": self.provider_name,
            "provider_version": self.provider_version,
            "model_id": self.model_id,
            "question_set_version": self.question_set_version,
        })


class JevEvaluationV1(BaseModel):
    model_config = ConfigDict(frozen=True)
    evaluation_id: str
    formal_key: str = Field(min_length=64, max_length=64)
    scope: JevScope
    symbol: str | None
    status: JevEvaluationStatus
    results: tuple[JevQuestionResultV1, ...]
    provider_name: str
    provider_version: str
    model_id: str
    state_schema_version: str
    question_set_version: str
    input_hash: str = Field(min_length=64, max_length=64)
    raw_response_hash: str | None = Field(default=None, min_length=64, max_length=64)
    started_at: datetime
    finished_at: datetime
    latency_ms: int = Field(ge=0)
    error_code: str | None = None
```

`JevQuestionResultV1` 用 Decimal 校验有限值、`0 <= p <= 1`、标签全集、总和容差 `0.000001`、最高概率标签和并列时 `label_order` 的稳定顺序。`JevEvaluationV1` 校验 scope/symbol 和 status/results 的互斥关系。`JevEvaluationCommand.input_hash` 由 State canonical payload 计算，`formal_key` 由 `scope + input_hash + provider_name + provider_version + model_id + question_set_version` 计算，不包含 `run_id`。哈希统一调用 `ingestion.canonical.sha256_json()`，State 输出使用排序后的 JSON-compatible Mapping。

- [ ] **Step 4: 运行领域契约测试并确认 GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_jev_market_contracts.py -q`

Expected: PASS，0 failed。

- [ ] **Step 5: 编写 Jev 问题目录失败测试**

在 `tests/unit/test_jev_market_questions.py` 断言候选池恰好 1 个问题、个股恰好 5 个问题、全部是 `Choice`、标签顺序和版本常量逐项一致，并断言不存在 `Score`、因子权重或直接交易动作问题。

```python
def test_symbol_questions_are_choice_only_and_complete() -> None:
    questions = build_jev_market_questions(JevScope.SYMBOL)
    assert tuple(questions) == (
        "next_session_pnl",
        "profitability_5d",
        "drawdown_risk_5d",
        "payoff_asymmetry_5d",
        "data_sufficiency",
    )
    assert all(isinstance(question, Choice) for question in questions.values())
```

- [ ] **Step 6: 运行问题目录测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_jev_market_questions.py -q`

Expected: collection FAIL，原因是 `jev_market_questions` 尚不存在。

- [ ] **Step 7: 实现版本化 Choice 问题目录**

定义不可变 `QuestionDefinition`，每项包含 `question_id`、`question_version`、`criteria_version`、`label_order` 和英文 `instructions`。固定候选池 `universe_risk_regime`，个股固定 `next_session_pnl`、`profitability_5d`、`drawdown_risk_5d`、`payoff_asymmetry_5d`、`data_sufficiency`。所有 `criteria` 显式写出标签含义，不使用开放文本答案。

- [ ] **Step 8: 运行问题目录测试并确认 GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_jev_market_questions.py -q`

Expected: PASS，0 failed。

- [ ] **Step 9: 提交 Task 1**

```powershell
git add src/fkqt_jevinvestor/domain/jev_market.py src/fkqt_jevinvestor/providers/jev_market_questions.py tests/unit/test_jev_market_contracts.py tests/unit/test_jev_market_questions.py
git commit -m "功能：冻结 Jev 盈亏概率领域契约"
```

### Task 2: 构造 80 只候选状态并生成未来真实标签

**Requirements:** R10、R12、R14、R27、R29、R37、R38、R42、R43

**Files:**
- Create: `src/fkqt_jevinvestor/services/jev_state_builder.py`
- Create: `src/fkqt_jevinvestor/services/forward_labels.py`
- Test: `tests/unit/test_jev_state_builder.py`
- Test: `tests/unit/test_forward_labels.py`

**Interfaces:**
- Consumes: Task 1 State 类型、`MarketSnapshot`、`MarketFeatureSnapshot`、`DailyBar`。
- Produces: `build_jev_states(...) -> tuple[JevUniverseStateV1, Mapping[str, JevSymbolStateV1]]`、`ForwardPnlLabelsV1`、`build_forward_pnl_labels(...) -> ForwardPnlLabelsV1`。

- [ ] **Step 1: 编写 State Builder 失败测试**

覆盖：81 个候选时报 `CANDIDATE_TARGET_EXCEEDED`；60 个合格候选保持 60 不补齐；候选池 median、advance ratio、above-MA20 ratio 和 coverage 使用 Decimal；必要特征缺失的证券保留稳定 `missing_reasons`；`held_only_symbols` 加入个股 State 但不改变候选池聚合；任何特征 `as_of > decision_cutoff` 报 `POINT_IN_TIME_VIOLATION`；输出 State 中不存在 news、cash、position、cost、PnL、future label 和 `HELD_ONLY` 字段。

```python
def test_held_only_symbol_is_evaluated_but_not_in_universe_metrics() -> None:
    universe, symbols = build_jev_states(
        snapshot=snapshot_for(("A", "B", "HELD")),
        features=features_for(("A", "B", "HELD")),
        candidate_symbols=("A", "B"),
        held_only_symbols=("HELD",),
    )
    assert universe.header.candidate_actual_size == 2
    assert set(symbols) == {"A", "B", "HELD"}
    assert "HELD_ONLY" not in symbols["HELD"].model_dump_json()
```

- [ ] **Step 2: 运行 State Builder 测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_jev_state_builder.py -q`

Expected: collection FAIL，原因是 `jev_state_builder` 尚不存在。

- [ ] **Step 3: 实现确定性 State Builder**

`build_jev_states` 必须验证候选去重、排序、最多 80、快照覆盖、证券状态覆盖和时间截止。候选池聚合只使用候选证券，不使用 `HELD_ONLY`。median 使用排序后 Decimal 中位数；离散度使用 Decimal sample standard deviation；所有输出量化到 `Decimal("0.00000001")`。必要个股特征固定为设计文档第 4.3 节列出的 Phase 2 特征，不从原始 K 线在此重新计算。

- [ ] **Step 4: 运行 State Builder 测试并确认 GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_jev_state_builder.py -q`

Expected: PASS，0 failed。

- [ ] **Step 5: 编写未来真实标签失败测试**

覆盖 1 日 `±0.002` 边界、5 日 `±0.005` 边界、MAE `0.02/0.05` 边界、MFE/MAE `1.5` 边界、二者为 0、round-trip cost、交易日不足、D+1 无可成交开盘、停牌、日期不连续映射和复权口径不一致。

```python
def test_missing_d1_open_returns_label_unavailable() -> None:
    labels = build_forward_pnl_labels(
        decision_date=date(2026, 9, 18),
        future_bars=(),
        round_trip_cost=Decimal("0.001"),
        source_snapshot_hash="a" * 64,
    )
    assert labels.status == ForwardLabelStatus.LABEL_UNAVAILABLE
    assert labels.missing_reason == "D1_EXECUTION_BAR_UNAVAILABLE"
```

- [ ] **Step 6: 运行标签测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_forward_labels.py -q`

Expected: collection FAIL，原因是 `forward_labels` 尚不存在。

- [ ] **Step 7: 实现 Decimal 标签生成器**

实现：

```python
class ForwardLabelStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    LABEL_UNAVAILABLE = "LABEL_UNAVAILABLE"


class ForwardPnlLabelsV1(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: ForwardLabelStatus
    decision_date: date
    d1_date: date | None
    d5_date: date | None
    next_session_pnl: str | None
    profitability_5d: str | None
    drawdown_risk_5d: str | None
    payoff_asymmetry_5d: str | None
    net_return_1d: Decimal | None
    net_return_5d: Decimal | None
    mae_5d: Decimal | None
    mfe_5d: Decimal | None
    criteria_version: Literal["pnl-label-criteria-v1"] = "pnl-label-criteria-v1"
    source_snapshot_hash: str = Field(min_length=64, max_length=64)
    missing_reason: str | None = None


def build_forward_pnl_labels(
    *,
    decision_date: date,
    future_bars: tuple[DailyBar, ...],
    round_trip_cost: Decimal,
    source_snapshot_hash: str,
) -> ForwardPnlLabelsV1: ...
```

只接受按交易日升序的 D+1 至 D+5 五根有效日线。`net_return_h`、MAE、MFE 和标签规则逐项照设计文档第 5.3 节实现；无效输入返回 `LABEL_UNAVAILABLE` 和稳定 `missing_reason`，不返回零数值。Label Entity 保存 `criteria_version="pnl-label-criteria-v1"`、源行情哈希和 D+1/D+5 日期。

- [ ] **Step 8: 运行标签测试并确认 GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_forward_labels.py -q`

Expected: PASS，0 failed。

- [ ] **Step 9: 提交 Task 2**

```powershell
git add src/fkqt_jevinvestor/services/jev_state_builder.py src/fkqt_jevinvestor/services/forward_labels.py tests/unit/test_jev_state_builder.py tests/unit/test_forward_labels.py
git commit -m "功能：构造 Jev 行情状态与未来盈亏标签"
```

### Task 3: 实现隔离的 TypeSafe Jev 行情 Provider

**Requirements:** R8、R23、R24、R39、R40

**Files:**
- Create: `src/fkqt_jevinvestor/providers/jev_market.py`
- Modify: `src/fkqt_jevinvestor/providers/base.py`
- Test: `tests/unit/test_jev_market_provider.py`
- Test: `tests/unit/test_jev_provider.py`

**Interfaces:**
- Consumes: Task 1 State、问题目录和本文件定义的 `SystemOneMarketClient.system_one(...)`。
- Produces: `JevMarketProvider` Protocol、`TypeSafeJevMarketProvider.evaluate(command) -> JevEvaluationV1`。

- [ ] **Step 1: 编写 Provider 失败测试**

测试 Fake Client 收到 canonical State、scope 对应的问题全集和配置模型；正常响应转换为 Decimal 完整分布；selected label 不在标签中、缺问题、未知问题、缺标签、未知标签、负数、NaN、Infinity、概率和超容差、错误最高标签全部抛 `ProviderContractError`；契约异常携带清理后的 `response_hash` 但不携带原始响应正文；网络异常只暴露异常类名；旧 `JevSemanticFactorProvider` 测试继续通过。

- [ ] **Step 2: 运行 Provider 测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_jev_market_provider.py -q`

Expected: collection FAIL，原因是 `providers.jev_market` 尚不存在。

- [ ] **Step 3: 实现新 Provider Protocol 与 SDK Adapter**

在 `providers/base.py` 新增：

```python
class JevMarketProvider(Protocol):
    async def evaluate(self, command: JevEvaluationCommand) -> JevEvaluationV1: ...
    async def health(self) -> ProviderHealth: ...
```

在 `providers/jev_market.py` 定义 SDK 最小边界并实现 Adapter：

```python
class SystemOneMarketClient(Protocol):
    async def system_one(
        self,
        state: dict[str, Any],
        questions: dict[str, Any],
        *,
        model: str,
    ) -> Any: ...
```

`TypeSafeJevMarketProvider` 构造函数接收 `SystemOneMarketClient`、`model`、`provider_version`。`from_api_key()` 使用 `SecretStr.get_secret_value()` 仅创建 Client，不保存明文。把 `ProviderContractError` 扩展为向后兼容的 `response_hash: str | None` 属性，现有只传 message 的调用仍有效；Validation/标签/概率错误携带清理后响应的哈希，不携带响应正文。SDK 未知异常映射为 `ProviderUnavailableError(type(exc).__name__)`。原始响应哈希基于清理后的 canonical payload；不得保存 SDK repr、认证头或 Secret。

- [ ] **Step 4: 运行新旧 Provider 测试并确认 GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_jev_market_provider.py tests/unit/test_jev_provider.py -q`

Expected: PASS，0 failed；旧新闻语义测试行为不变。

- [ ] **Step 5: 提交 Task 3**

```powershell
git add src/fkqt_jevinvestor/providers/base.py src/fkqt_jevinvestor/providers/jev_market.py tests/unit/test_jev_market_provider.py tests/unit/test_jev_provider.py
git commit -m "功能：接入 Jev 行情盈亏概率 Provider"
```

### Task 4: 建立 Phase 3 数据库迁移与 Repository

**Requirements:** R21、R22、R24、R40

**Files:**
- Modify: `src/fkqt_jevinvestor/persistence/models.py`
- Create: `src/fkqt_jevinvestor/persistence/jev_repository.py`
- Create: `migrations/versions/0006_phase3_jev_pnl_probabilities.py`
- Create: `tests/integration/test_phase3_migration.py`
- Create: `tests/integration/test_jev_repository.py`

**Interfaces:**
- Consumes: Task 1 `JevEvaluationV1` 和 State canonical payload。
- Produces: `JevEvaluationRepository.claim(...)`、`record_success(...)`、`record_failure(...)`、`load_formal(...)`、`list_attempts(...)`。

- [ ] **Step 1: 编写迁移失败测试**

测试升级到 head 后存在：

```text
ai_signal_jev_evaluation
ai_signal_jev_attempt
ai_signal_jev_question_result
ai_signal_jev_run_link
```

断言 `ai_signal_jev_evaluation.formal_key` 唯一，Attempt 的 `(evaluation_id, sequence)` 唯一，QuestionResult 的 `(evaluation_id, question_id)` 唯一，RunLink 的 `(run_id, evaluation_id)` 唯一；断言 downgrade 到 `0005_phase2_audit_hardening` 后四表消失而 Phase 2 表保留。

- [ ] **Step 2: 运行迁移测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_phase3_migration.py -q`

Expected: FAIL，四张 Phase 3 表不存在。

- [ ] **Step 3: 实现 ORM 与 `0006` 迁移**

`JevEvaluationRecord` 保存正式键、scope、symbol、日期、截止时间、清理后的 canonical `state_json`、输入/版本哈希、当前状态、最新 attempt、创建/更新时间；`JevAttemptRecord` 保存触发调用的 `run_id`、序号、Provider/模型版本、开始结束时间、延迟、状态、响应哈希和错误码；`JevQuestionResultRecord` 保存问题/标准版本、label order、selected label 和 Decimal 字符串概率 JSON；`JevRunLinkRecord` 保存每个正式运行实际使用的 evaluation。所有外键和唯一约束显式命名，downgrade 按反向依赖顺序删除。

- [ ] **Step 4: 运行迁移测试并确认 GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_phase3_migration.py -q`

Expected: PASS，0 failed。

- [ ] **Step 5: 编写 Repository 失败测试**

覆盖：首次 Claim 返回 `ACQUIRED`；相同正式键第二次返回 `IN_PROGRESS` 或已保存结果；跨 `run_id` 缓存命中时不新增 Attempt 但新增 RunLink；失败 Attempt 无 QuestionResult；成功原子写入全部问题；部分问题写入失败整笔回滚；失败后可重试且序号递增；成功后不可重新 Claim；读取结果逐字段恢复 Decimal；两个并发 Claim 只有一个 `ACQUIRED`；Secret 字符串不出现在任一 JSON/错误字段。

- [ ] **Step 6: 运行 Repository 测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_jev_repository.py -q`

Expected: collection FAIL，原因是 `jev_repository` 尚不存在。

- [ ] **Step 7: 实现 Claim 与原子持久化**

固定接口：

```python
class ClaimStatus(StrEnum):
    ACQUIRED = "ACQUIRED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"


class JevClaim(BaseModel):
    model_config = ConfigDict(frozen=True)
    status: ClaimStatus
    evaluation_id: str
    formal_key: str = Field(min_length=64, max_length=64)
    attempt_id: str | None
    attempt_sequence: int | None = Field(default=None, ge=1)
    existing_result: JevEvaluationV1 | None = None


class JevEvaluationRepository:
    async def claim(self, run_id: UUID, command: JevEvaluationCommand) -> JevClaim: ...
    async def record_success(self, claim: JevClaim, result: JevEvaluationV1) -> JevEvaluationV1: ...
    async def record_failure(self, claim: JevClaim, result: JevEvaluationV1) -> JevEvaluationV1: ...
    async def load_formal(self, formal_key: str) -> JevEvaluationV1 | None: ...
```

Claim 使用数据库唯一键和事务处理竞争；IntegrityError 后读取现有行，不把竞争当服务错误。缓存命中为当前 `run_id` 幂等写入 RunLink。只有 `AVAILABLE` 可以写 QuestionResult；失败状态清空既有概率。State payload 仅保存清理后的 canonical JSON，错误码使用稳定枚举，不保存异常消息。

- [ ] **Step 8: 运行 Repository 测试并确认 GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_jev_repository.py -q`

Expected: PASS，0 failed。

- [ ] **Step 9: 提交 Task 4**

```powershell
git add src/fkqt_jevinvestor/persistence/models.py src/fkqt_jevinvestor/persistence/jev_repository.py migrations/versions/0006_phase3_jev_pnl_probabilities.py tests/integration/test_phase3_migration.py tests/integration/test_jev_repository.py
git commit -m "功能：持久化 Jev 盈亏概率与调用审计"
```

### Task 5: 编排 C 组优先的候选池与个股评估

**Requirements:** R7、R24、R26、R27、R28、R37、R40、R43

**Files:**
- Create: `src/fkqt_jevinvestor/services/jev_market_service.py`
- Create: `tests/integration/test_jev_market_service.py`

**Interfaces:**
- Consumes: Task 2 `build_jev_states`、Task 3 `JevMarketProvider`、Task 4 Repository。
- Produces: `JevMarketEvaluationService.evaluate_run(...) -> JevRunEvaluationV1`。

- [ ] **Step 1: 编写服务失败测试**

覆盖：候选池 State 每个 run 只调用一次；每个候选和每个队列外持仓各调用一次；`HELD_ONLY` 不出现在 Provider State；相同正式输入重跑外部调用为 0；并发相同 run 只有 Claim 获得者调用 Provider；必要特征缺失记录 `DATA_UNAVAILABLE` 且调用为 0；Provider 异常记录 `PROVIDER_UNAVAILABLE`；契约异常记录 `CONTRACT_INVALID` 并保存响应哈希；任一失败不伪造概率、不切换实验组；Phase 3 输出中不存在 `ENTER/KEEP/EXIT/AVOID`；返回结果覆盖全部评估证券且顺序稳定。

```python
@pytest.mark.asyncio
async def test_same_formal_run_calls_universe_once_and_reuses_result() -> None:
    first = await service.evaluate_run(command)
    second = await service.evaluate_run(command)
    assert provider.universe_calls == 1
    assert provider.symbol_calls == len(
        set(command.candidate_symbols) | set(command.held_symbols)
    )
    assert first == second
```

- [ ] **Step 2: 运行服务测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_jev_market_service.py -q`

Expected: collection FAIL，原因是 `jev_market_service` 尚不存在。

- [ ] **Step 3: 实现评估服务**

定义以下编排类型：

```python
class JevRunCommandV1(BaseModel):
    model_config = ConfigDict(frozen=True)
    run_id: UUID
    snapshot: MarketSnapshot
    features: Mapping[str, MarketFeatureSnapshot]
    candidate_symbols: tuple[str, ...]
    held_symbols: tuple[str, ...] = ()
    provider_name: str
    provider_version: str
    model_id: str


class JevRunEvaluationV1(BaseModel):
    model_config = ConfigDict(frozen=True)
    run_id: UUID
    universe: JevEvaluationV1
    symbols: Mapping[str, JevEvaluationV1]
```

服务先建立 State，再按 `UNIVERSE`、排序后的 symbol 顺序处理。每项先 Claim：`COMPLETE` 直接加载；`IN_PROGRESS` 返回已存在状态且不重复调用；`ACQUIRED` 才调用 Provider。捕获 `ProviderUnavailableError` 与 `ProviderContractError` 并转换为不带概率的领域结果，然后写 Attempt。不得捕获并吞掉编程错误。

- [ ] **Step 4: 运行服务测试并确认 GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_jev_market_service.py -q`

Expected: PASS，0 failed。

- [ ] **Step 5: 提交 Task 5**

```powershell
git add src/fkqt_jevinvestor/services/jev_market_service.py tests/integration/test_jev_market_service.py
git commit -m "功能：编排候选池与个股 Jev 盈亏评估"
```

### Task 6: 增加真实 SDK 门控测试与运行手册

**Requirements:** R23、R24、R29、R32、R33

**Files:**
- Create: `tests/live/test_jev_market_live.py`
- Create: `docs/runbooks/jev-market-probabilities.md`
- Modify: `findings.md`
- Modify: `task_plan.md`

**Interfaces:**
- Consumes: Task 1 问题目录和 Task 3 Provider。
- Produces: 可显式执行的真实 Jev Contract Probe 和完整运维说明。

- [ ] **Step 1: 编写 Live Test 门控**

使用合成、不含真实账户或证券建议的数据构造 `JevSymbolStateV1`。未同时设置 `RUN_LIVE_JEV_TESTS=1` 和 `TYPESAFE_API_KEY` 时 `pytest.skip`；启用时调用新 Provider，断言状态为 `AVAILABLE`、5 个问题齐全、所有概率为有限 Decimal 且和为 1。测试不得打印 Key、请求认证头或原始异常正文。

- [ ] **Step 2: 运行默认 Live Test 并确认安全跳过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/live/test_jev_market_live.py -q`

Expected: `1 skipped`，0 failed，外部调用 0 次。

- [ ] **Step 3: 编写完整运行手册**

`docs/runbooks/jev-market-probabilities.md` 必须完整写出：环境变量、默认离线测试命令、显式 Live Test PowerShell 命令、80 只队列规则、队列外持仓规则、C 组数据流、五个问题与标签、真实标签公式、四种状态、重试/幂等行为、数据库审计查询、Secret 禁止项、常见错误和恢复步骤。不得记录实际 API Key。

- [ ] **Step 4: 更新持久记录**

在 `findings.md` 记录实现期事实，在 `task_plan.md` 增加 Phase 3 Tasks 1—7 状态栏；不得改写旧 Phase 0—2 验收结果。

- [ ] **Step 5: 运行文档与默认门控最小检查**

Run: `.\.venv\Scripts\python.exe -m pytest tests/live/test_jev_market_live.py -q`

Expected: `1 skipped`，0 failed。

- [ ] **Step 6: 提交 Task 6**

```powershell
git add tests/live/test_jev_market_live.py docs/runbooks/jev-market-probabilities.md findings.md task_plan.md
git commit -m "文档：补充 Jev 盈亏概率运行与验收说明"
```

### Task 7: Phase 3 最终验收与兼容检查

**Requirements:** R29、R32、R33，以及本计划全部需求

**Files:**
- Modify only if a verification failure maps to an existing requirement and receives its own RED/GREEN evidence.

**Interfaces:**
- Consumes: Tasks 1—6 全部交付。
- Produces: Phase 3 完整验证证据、最终迁移 head 和干净工作区。

- [ ] **Step 1: 运行一次完整离线测试**

Run: `.\.venv\Scripts\python.exe -m pytest -m "not live" -q`

Expected: 全部离线测试 PASS，0 failed，live 测试未执行。

若出现失败，只修复映射到 R7、R8、R10、R12、R14、R21、R23、R24、R26、R27、R29、R33、R37—R43 的问题；先新增或确认失败测试，再只重跑失败项。不要再次运行完整套件。

- [ ] **Step 2: 运行 Ruff**

Run: `.\.venv\Scripts\python.exe -m ruff check src tests migrations`

Expected: `All checks passed!`

- [ ] **Step 3: 运行 Pyright**

Run: `.\.venv\Scripts\python.exe -m pyright`

Expected: `0 errors`。

- [ ] **Step 4: 运行 Alembic 往返**

使用项目内 `.pytest_cache` 下的唯一临时 SQLite URL 执行完整往返，不触碰默认 `data/fkqt_jevinvestor.db`：

```powershell
$phase3Db = ".pytest_cache/phase3-final-$([guid]::NewGuid().ToString('N')).db"
$env:PHASE3_DB_URL = "sqlite+aiosqlite:///$($phase3Db.Replace('\\','/'))"
.\.venv\Scripts\python.exe -c "import os; from alembic import command; from alembic.config import Config; c=Config('alembic.ini'); c.set_main_option('sqlalchemy.url', os.environ['PHASE3_DB_URL']); command.upgrade(c, 'head'); command.downgrade(c, '0005_phase2_audit_hardening'); command.upgrade(c, 'head'); command.current(c)"
Remove-Item Env:PHASE3_DB_URL
```

Expected: 最终输出 `0006_phase3_jev_pnl_probabilities (head)`。

- [ ] **Step 5: 扫描 Secret 与旧接口兼容性**

Run: `git grep -n -I -E "apikey_[A-Za-z0-9_]+|Authorization: Bearer|TYPESAFE_API_KEY=" -- . ":(exclude)uv.lock"`

Expected: 0 个实际 Secret；仅允许环境变量名称和文档占位说明，不允许赋值。

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_jev_provider.py tests/unit/test_jev_market_provider.py tests/unit/test_backtest_contracts.py -q`

Expected: PASS，旧语义 Provider、新行情 Provider和回测契约同时可用。

- [ ] **Step 6: 更新阶段验收记录并提交**

把真实命令、通过数量、Ruff/Pyright 结果和最终 migration head 写入 `task_plan.md` 与 `findings.md`。只记录实际输出，不推测 Live Test 结果。

```powershell
git add task_plan.md findings.md
git commit -m "验收：完成 Phase 3 Jev 盈亏概率层"
```

## 完成后的阶段关系

| 能力 | Phase 3 完成后 | 还需要 |
|---|---|---|
| Jev 盈亏概率离线生成与审计 | 可用 | 有效 API Key 才能跑真实 Provider |
| 真实盈亏标签与 Jev 校准数据准备 | 可用 | Phase 6 汇总校准指标 |
| 沙耶的纯回测引擎 | 可并行开发 | 她的回测实现 PR |
| C 组真实策略回测 | 尚不可完整运行 | Phase 4 LLM、确定性仓位和 C 组 `TargetProvider` |
| C 组手工单日前向 Probe | 尚不可形成正式动作 | Phase 4 |
| C 组自动日频前向测试 | 尚不可运行 | Phase 4 + Phase 5 调度、恢复与监控 |
| A/B/C/D 正式对照报告 | 尚不可运行 | Phase 6 |

## 自检清单

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| 需求映射 | R7、R8、R10、R12、R14、R21、R23、R24、R26、R27、R29、R33、R37—R43 全覆盖 | 对照每个 Task 的 Requirements | 必须 |
| C 组优先 | C 组 Jev 契约先完成，A/B/D 无专属实现阻塞 | Task 1—5 文件清单 | 必须 |
| 候选目标 | 默认 80，少于 80 不补齐 | Task 2/5 测试 | 必须 |
| 队列外持仓 | 全部评估且 `HELD_ONLY` 不进 Jev State | Task 2/5 测试 | 必须 |
| 未来数据 | 正式输入读取 D+1 标签 0 次 | Task 2/5 时间测试 | 必须 |
| 因子配比 | 实现文件和问题数量 0 | 问题目录及源码扫描 | 必须 |
| 概率完整性 | 标签全集、有限 Decimal、和为 1 | Task 1/3 测试 | 必须 |
| 失败伪造概率 | 0 条 | Task 3/4/5 测试 | 必须 |
| 正式调用幂等 | 同正式键成功调用最多 1 次 | Task 4/5 并发测试 | 必须 |
| Secret | Git、日志、DB 中实际 Secret 0 个 | Task 7 扫描和测试 | 必须 |
| 旧 Provider | 行为不变 | Task 3/7 兼容测试 | 必须 |
| 回测契约 | 公共 Entity 改动 0 个 | Task 7 `test_backtest_contracts.py` | 必须 |
| 阶段验收 | 离线测试、Ruff、Pyright、Alembic 全通过 | Task 7 | 必须 |

## 已知问题

1. Jev Early Access 的模型与 SDK 行为可能变化；版本和响应哈希可以识别漂移，但不能阻止第三方服务变化。
2. 80 只股票逐日个股调用会产生明显调用量；正式键缓存和候选池一次复用可以避免重跑成本，但首次历史批量重评仍需单独预算。
3. `0.2%` 与 `0.5%` 盈亏平坦区间是 `pnl-label-criteria-v1` 的研究假设，不代表已经验证的最优阈值；修改必须提升版本，不能覆盖历史标签。
4. Phase 3 只生成 Jev 概率和真实标签，不包含 LLM 动作、仓位或 C 组 Target；首次真实 C 组回测仍依赖 Phase 4。
