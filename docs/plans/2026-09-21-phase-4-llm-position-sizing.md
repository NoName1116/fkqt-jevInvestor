# Phase 4 LLM 离散动作与确定性仓位 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用 DeepSeek V4.1 Flash 将 Phase 2 确定性特征和 Phase 3 Jev 概率转换为受限离散动作，再由版本化 Decimal 代码生成可重放目标权重和现有虚拟执行信号。

**Architecture:** 每只证券构造独立 `DecisionInputV1`，经 Provider Protocol 调用 DeepSeek OpenAI-compatible Responses API，并以 Pydantic 严格裁决动作。正式决策使用数据库 Claim/CAS 保存；完整动作集合一次性进入 `position-sizing-v1`，输出正式 `DecisionSignalBatchV1`、仓位审计和冻结 `TargetPositionBatch`，现有 Phase 1 执行链只通过内部兼容 Adapter 消费。

**Tech Stack:** Python 3.12、Pydantic 2、OpenAI Python SDK 2.x、DeepSeek V4.1 Flash (`deepseek-flash`)、SQLAlchemy 2 Async、Alembic、Decimal、pytest、pytest-asyncio、Ruff、Pyright。

**Spec:** `docs/specs/2026-09-21-phase-4-llm-position-sizing-design.md`

## Global Constraints

- 映射需求 R2—R4、R6、R7、R9、R12、R15、R16、R18—R29、R32—R36、R40—R45。
- 用户指定 Native 执行：主 Agent 在隔离 worktree 中逐任务实现，阶段结束由独立 reviewer 做整分支审查。
- 默认模型必须为 `deepseek-flash`，对应当前 DeepSeek V4.1 Flash；默认 `reasoning_effort=high`，实际 model ID 写入审计。
- LLM 只允许输出 `ENTER/KEEP/EXIT/AVOID`，不得输出 `NO_SIGNAL`、confidence、概率、收益率、价格、数量、仓位或资金比例。
- `NO_SIGNAL` 只由系统在 Provider、契约或必要输入失败时生成，并保持当前持仓。
- 正式决策集合必须等于冻结候选队列与现有持仓证券的并集；`HELD_ONLY` 不得 `ENTER`。
- 每票独立正式调用；同一正式键只有数据库 Claim owner 可以调用 Provider。
- 不自动修复非法 JSON，不自动切换备用模型，不执行隐式重试。
- 目标权重只能由 `position-sizing-v1` 使用 Decimal 计算；输入顺序不得改变输出。
- 单票上限 `0.10000000`、正常输入总仓位上限 `0.80000000`、最低现金 `0.20000000`。
- 决策前已超限时阻止新增风险，不自动卖出没有 `EXIT` 或有效 `KEEP` 调整的持仓。
- Phase 1 兼容 `confidence` 固定为 `Decimal(0)`，语义为 `NOT_PROVIDED`，不得参与执行或评估。
- 默认测试和 CI 不访问网络；Live Test 同时受 `RUN_LIVE_DECISION_LLM_TESTS=1` 和 `DEEPSEEK_API_KEY` 门控。
- 不修改 `src/fkqt_jevinvestor/backtest/` 与 `tests/backtest/`；只对核心 `domain/backtest.py` 做向后兼容枚举加法。
- 遵守 R33：任务内先跑最小 RED/GREEN；首次阶段全量验收后只重跑失败项与受影响项。

## Review Focus

1. DeepSeek 返回合法 JSON 但动作不属于当前 `allowed_actions` 时，必须保存 `CONTRACT_INVALID` 并标准化为 `NO_SIGNAL`；Task 3 Provider 和 Task 7 服务测试覆盖。
2. 同一正式键两个 worker 同时请求或过期 owner 与接管 owner 同时提交时，数据库 CAS 必须只允许一个终态；Task 5 并发测试覆盖。
3. 决策前组合已经超过 80% 时，合理行为是阻止新增 `ENTER`，不是自动清仓或把 `KEEP` 压成 0；Task 4 组合测试覆盖。
4. `HELD_ONLY`、Jev 单票失败和候选队列不足配置容量同时出现时，每票必须有显式结果且模型调用数准确；Task 7 集成测试覆盖。
5. 回测冻结重放不得调用 FKQT、Jev、LLM、FastAPI 或 SQLAlchemy，且不得读取 D+1 行情；Task 8 TargetProvider 测试覆盖。

---

## 文件结构

| 文件 | 责任 |
|---|---|
| `src/fkqt_jevinvestor/domain/backtest.py` | 加入规范实验组名，保留旧成员 |
| `src/fkqt_jevinvestor/domain/decision.py` | Decision 输入、输出、状态、历史、仓位和正式信号领域类型 |
| `src/fkqt_jevinvestor/providers/decision_prompt.py` | 版本化 Prompt、JSON Schema 与 canonical 消息构造 |
| `src/fkqt_jevinvestor/providers/deepseek_decision.py` | DeepSeek/OpenAI-compatible Adapter 与严格响应校验 |
| `src/fkqt_jevinvestor/providers/base.py` | `DecisionLlmProvider` Protocol |
| `src/fkqt_jevinvestor/services/position_sizing.py` | `position-sizing-v1` Decimal 公式、组合约束和 Signal 转换 |
| `src/fkqt_jevinvestor/persistence/models.py` | LLM Evaluation/Attempt/RunLink 与 Sizing Run/Target ORM |
| `src/fkqt_jevinvestor/persistence/decision_repository.py` | Decision Claim、CAS、Attempt、正式结果和历史动作读取 |
| `src/fkqt_jevinvestor/persistence/repositories.py` | Sizing 与现有 Signal/Order 的原子兼容保存 |
| `src/fkqt_jevinvestor/services/c_group_decision.py` | C 组逐票决策、失败隔离、完整覆盖和仓位编排 |
| `src/fkqt_jevinvestor/services/frozen_c_group_target.py` | 从不可变文件读取 `TargetPositionBatch` 的纯重放 Adapter |
| `src/fkqt_jevinvestor/config.py` | DeepSeek Secret、Base URL、模型、reasoning effort、timeout |
| `src/fkqt_jevinvestor/cli/main.py` | 手工 C 组命令与配置错误处理 |
| `migrations/versions/0007_phase4_llm_position_sizing.py` | Phase 4 五表与可逆迁移 |
| `tests/unit/test_backtest_contracts.py` | 实验组规范命名兼容性 |
| `tests/unit/test_decision_contracts.py` | Decision Schema、状态机、禁止字段和 hash |
| `tests/unit/test_decision_prompt.py` | Prompt 版本、边界和 JSON Schema |
| `tests/unit/test_deepseek_decision_provider.py` | SDK 请求、响应、失败与 Secret 清理 |
| `tests/unit/test_position_sizing.py` | 公式、组合约束、动作转换与确定性 |
| `tests/integration/test_decision_repository.py` | Claim/CAS、Attempt、幂等和重放 |
| `tests/integration/test_c_group_decision.py` | C 组完整调用链和原子 Signal 保存 |
| `tests/integration/test_phase4_migration.py` | `0007` 结构、约束和 downgrade |
| `tests/unit/test_frozen_c_group_target.py` | 文件重放、哈希和防 D+1 泄露 |
| `tests/live/test_deepseek_decision_live.py` | 显式启用的 DeepSeek V4.1 Flash 契约 Probe |
| `docs/runbooks/c-group-decisions.md` | 配置、离线运行、Live Probe、错误码和审计查询 |

### Task 1: 修正实验组规范命名

**Requirements:** R26、R34—R36、R45

**Files:**
- Modify: `src/fkqt_jevinvestor/domain/backtest.py`
- Modify: `tests/unit/test_backtest_contracts.py`
- Modify: `docs/contributors/2026-09-21-backtesting-development-contract.md`

**Interfaces:**
- Consumes: 现有 `ExperimentArm` 与 `TargetPositionBatch`。
- Produces: `ExperimentArm.A_RULE/B_LLM/C_JEV_LLM/D_JEV_DIRECT`；旧四个成员继续可导入。

- [ ] **Step 1: 编写规范命名失败测试**

在 `tests/unit/test_backtest_contracts.py` 增加：

```python
def test_experiment_arm_has_canonical_names_without_removing_legacy_names() -> None:
    assert ExperimentArm.A_RULE.value == "A_RULE"
    assert ExperimentArm.B_LLM.value == "B_LLM"
    assert ExperimentArm.C_JEV_LLM.value == "C_JEV_LLM"
    assert ExperimentArm.D_JEV_DIRECT.value == "D_JEV_DIRECT"
    assert ExperimentArm.A_LLM.value == "A_LLM"
    assert ExperimentArm.B_JEV_LLM.value == "B_JEV_LLM"
    assert ExperimentArm.C_JEV_DIRECT.value == "C_JEV_DIRECT"
    assert ExperimentArm.D_RULE.value == "D_RULE"
```

- [ ] **Step 2: 运行单个测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_backtest_contracts.py::test_experiment_arm_has_canonical_names_without_removing_legacy_names -q`

Expected: FAIL，`ExperimentArm.A_RULE` 不存在。

- [ ] **Step 3: 做纯加法枚举修复**

向 `ExperimentArm` 加入四个规范成员，不删除或改变旧成员的值：

```python
class ExperimentArm(StrEnum):
    A_RULE = "A_RULE"
    B_LLM = "B_LLM"
    C_JEV_LLM = "C_JEV_LLM"
    D_JEV_DIRECT = "D_JEV_DIRECT"
    A_LLM = "A_LLM"
    B_JEV_LLM = "B_JEV_LLM"
    C_JEV_DIRECT = "C_JEV_DIRECT"
    D_RULE = "D_RULE"
```

- [ ] **Step 4: 更新 Contributor 文档映射表并运行该测试**

文档必须把规范名映射到 R26，并把旧名标记为只读兼容，不允许新结果使用。再次运行 Task 1 测试，Expected: PASS。

- [ ] **Step 5: 提交独立契约修复**

```powershell
git add src/fkqt_jevinvestor/domain/backtest.py tests/unit/test_backtest_contracts.py docs/contributors/2026-09-21-backtesting-development-contract.md
git commit -m "契约：补充规范实验组命名"
```

### Task 2: 冻结 Decision 领域契约与 Prompt

**Requirements:** R7、R9、R12、R21—R23、R25、R27、R28、R41、R43

**Files:**
- Create: `src/fkqt_jevinvestor/domain/decision.py`
- Create: `src/fkqt_jevinvestor/providers/decision_prompt.py`
- Test: `tests/unit/test_decision_contracts.py`
- Test: `tests/unit/test_decision_prompt.py`

**Interfaces:**
- Consumes: `MarketFeatureSnapshot`、`JevEvaluationV1`、`PortfolioState`、`sha256_json()`。
- Produces: `DecisionMembership`、`DecisionAction`、`DecisionEvaluationStatus`、`DecisionInputV1`、`DecisionModelOutputV1`、`DecisionEvaluationCommand`、`DecisionEvaluationV1`、`DecisionSignalBatchV1`、`build_decision_messages()`。

- [ ] **Step 1: 编写 Decision Schema RED 测试**

覆盖以下精确行为：模型输出拒绝 `NO_SIGNAL`；拒绝 `confidence`、`target_position_pct`、`quantity` 和额外字段；空仓只接受 `ENTER/AVOID`；持仓只接受 `KEEP/EXIT`；`HELD_ONLY` 只接受 `KEEP/EXIT`；`input_hash` 不包含 run ID 且不受 Mapping 顺序影响；未来 `as_of` 和未来 Jev 完成时间拒绝。

核心断言：

```python
def test_model_output_forbids_numeric_trade_fields() -> None:
    with pytest.raises(ValidationError):
        DecisionModelOutputV1.model_validate(
            {
                "action": "ENTER",
                "thesis": "输入证据一致。",
                "invalidation": "输入证据失效。",
                "target_position_pct": "0.1",
            }
        )
```

- [ ] **Step 2: 运行 Decision 测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_decision_contracts.py -q`

Expected: collection FAIL，`fkqt_jevinvestor.domain.decision` 不存在。

- [ ] **Step 3: 实现冻结领域类型**

`DecisionModelOutputV1` 必须使用 Literal 排除 `NO_SIGNAL`：

```python
class DecisionModelOutputV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["ENTER", "KEEP", "EXIT", "AVOID"]
    thesis: str = Field(min_length=1, max_length=240)
    invalidation: str = Field(min_length=1, max_length=240)
```

`DecisionEvaluationV1` 对 `AVAILABLE` 和失败状态执行互斥校验；失败结果必须是 `NO_SIGNAL`、不得带模型文本；成功结果必须有 raw response hash 和完整文本。`formal_key` 固定包含 input/provider/model/prompt/schema 版本。

- [ ] **Step 4: 编写 Prompt RED 测试**

断言 system prompt 包含“不得计算仓位”“只从 allowed_actions 选择”“Jev 未校准”；JSON Schema 只有 action/thesis/invalidation；user 消息等于 canonical `DecisionInputV1` JSON；Prompt 不出现 API Key、目标权重和 D+1 行情。

- [ ] **Step 5: 实现 `decision-prompt-v1`**

定义：

```python
DECISION_PROMPT_VERSION = "decision-prompt-v1"
DECISION_OUTPUT_SCHEMA_VERSION = "decision-output-v1"
DECISION_MAX_OUTPUT_CHARS = 4096
```

`build_decision_messages()` 返回有序 system/user 消息，`decision_output_json_schema()` 返回 `additionalProperties: false` 的 JSON Schema。

- [ ] **Step 6: 运行 Task 2 两个测试文件并提交**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_decision_contracts.py tests/unit/test_decision_prompt.py -q`

Expected: PASS。

```powershell
git add src/fkqt_jevinvestor/domain/decision.py src/fkqt_jevinvestor/providers/decision_prompt.py tests/unit/test_decision_contracts.py tests/unit/test_decision_prompt.py
git commit -m "功能：冻结 LLM 离散决策契约"
```

### Task 3: 实现 DeepSeek V4.1 Flash Provider

**Requirements:** R23、R25、R32、R44

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/fkqt_jevinvestor/config.py`
- Modify: `src/fkqt_jevinvestor/providers/base.py`
- Create: `src/fkqt_jevinvestor/providers/deepseek_decision.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_deepseek_decision_provider.py`

**Interfaces:**
- Consumes: `DecisionEvaluationCommand`、Prompt 消息和 JSON Schema。
- Produces: `DecisionLlmProvider.evaluate()`、`DeepSeekDecisionProvider`、DeepSeek Settings。

- [ ] **Step 1: 编写配置和 Provider RED 测试**

配置断言：默认 model 为 `deepseek-flash`、reasoning effort 为 `high`、timeout 为 30、Key 为 `SecretStr`。Provider Fake 覆盖合法输出、空内容、超过 4096 字符、拒答、截断、非法 JSON、额外字段、非法状态动作和网络异常；失败异常正文不得包含 Fake Secret。

请求断言必须精确包含：

```python
assert call["model"] == "deepseek-flash"
assert call["reasoning_effort"] == "high"
assert call["text"]["format"]["type"] == "json_schema"
assert call["text"]["format"]["name"] == "decision_output_v1"
```

- [ ] **Step 2: 运行 Provider 测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_deepseek_decision_provider.py tests/unit/test_config.py -q`

Expected: import FAIL 或默认配置断言 FAIL。

- [ ] **Step 3: 添加并锁定 SDK**

在 `pyproject.toml` 添加 `openai>=2,<3`，使用 uv 更新 lock。不得引入 LangChain、Instructor 或 PydanticAI。

- [ ] **Step 4: 扩展 Settings 与 Protocol**

加入：

```python
deepseek_api_key: SecretStr | None
deepseek_base_url: str = "https://api.deepseek.com"
deepseek_model: str = "deepseek-flash"
deepseek_reasoning_effort: Literal["low", "high"] = "high"
deepseek_timeout_seconds: float = Field(default=30, gt=0)
```

`DecisionLlmProvider` 只暴露领域命令和领域结果，不暴露 OpenAI SDK 类型。

- [ ] **Step 5: 实现 Provider**

使用注入 Client 的 `responses.create()`；Provider 不从环境读取 Key。解析顺序固定为响应状态、拒答/截断、单一 output text、长度、SHA-256、JSON、Pydantic、allowed action。所有 SDK 异常映射为不含正文的 `ProviderUnavailableError(type(exc).__name__)`。

- [ ] **Step 6: 运行 Task 3 测试并提交**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_deepseek_decision_provider.py tests/unit/test_config.py -q`

Expected: PASS。

```powershell
git add pyproject.toml uv.lock src/fkqt_jevinvestor/config.py src/fkqt_jevinvestor/providers/base.py src/fkqt_jevinvestor/providers/deepseek_decision.py tests/unit/test_config.py tests/unit/test_deepseek_decision_provider.py
git commit -m "功能：接入 DeepSeek V4.1 离散决策 Provider"
```

### Task 4: 实现 `position-sizing-v1` 与正式信号 Adapter

**Requirements:** R3、R4、R18—R21、R28、R41

**Files:**
- Create: `src/fkqt_jevinvestor/services/position_sizing.py`
- Test: `tests/unit/test_position_sizing.py`
- Modify: `tests/unit/test_execution_engine.py`

**Interfaces:**
- Consumes: 完整 `DecisionEvaluationV1` 集合、`PortfolioState`、Phase 2 feature snapshots。
- Produces: `PositionSizingConfigV1`、`PositionSizingRunV1`、`SizedTargetV1`、`DecisionSignalBatchV1`、`to_validated_signal_batch()`。

- [ ] **Step 1: 编写公式 RED 测试**

使用固定 Decimal Fixture 覆盖：波动率 floor、流动性 clamp、10% 单票 cap、1% 最低 ENTER、低流动性阻断、`EXIT=0`、`AVOID=0`、`NO_SIGNAL` 保持、`KEEP` 不归零、输入顺序稳定和八位量化。

公式断言示例：

```python
assert size_one(
    realized_vol_20d=Decimal("0.02000000"),
    liquidity_percentile=Decimal("0.50000000"),
    config=PositionSizingConfigV1(),
) == Decimal("0.05000000")
```

- [ ] **Step 2: 运行单元测试并确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_position_sizing.py -q`

Expected: collection FAIL，模块不存在。

- [ ] **Step 3: 实现局部 Decimal context 和单票公式**

禁止修改全局 Decimal context。`PositionSizingConfigV1` 固定设计文档中的八个参数并提供 canonical hash。

- [ ] **Step 4: 实现组合分配**

按 symbol 排序；先保留 `NO_SIGNAL`；剩余预算按 raw target 比例缩放；量化残差归现金。加入超限输入测试：已有 85% `NO_SIGNAL` 时所有新 `ENTER` 为 `BLOCKED/PREEXISTING_GROSS_LIMIT_EXCEEDED`，原持仓保持 85%。

- [ ] **Step 5: 实现正式信号和兼容 Adapter**

`DecisionSignalBatchV1` 保存真实 `sizing_version`；内部 Adapter 生成完整覆盖的 `FixtureSignalBatch`，固定：

```python
confidence=Decimal(0)
factor_codes=()
evidence_ids=()
fixture_version=f"decision-{sizing_run.sizing_version}"
```

被阻断的空仓 `ENTER` 转成执行层 `AVOID`，但 `SizedTargetV1.requested_action` 必须保留 `ENTER` 和阻断码。测试证明执行引擎不会为它创建订单。

- [ ] **Step 6: 运行 Task 4 测试并提交**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_position_sizing.py tests/unit/test_execution_engine.py -q`

Expected: PASS。

```powershell
git add src/fkqt_jevinvestor/services/position_sizing.py tests/unit/test_position_sizing.py tests/unit/test_execution_engine.py
git commit -m "功能：实现确定性仓位与正式信号转换"
```

### Task 5: 持久化 Decision Claim 与 Sizing 审计

**Requirements:** R21、R22、R25、R28、R32

**Files:**
- Create: `migrations/versions/0007_phase4_llm_position_sizing.py`
- Modify: `src/fkqt_jevinvestor/persistence/models.py`
- Create: `src/fkqt_jevinvestor/persistence/decision_repository.py`
- Modify: `src/fkqt_jevinvestor/persistence/repositories.py`
- Test: `tests/integration/test_phase4_migration.py`
- Test: `tests/integration/test_decision_repository.py`

**Interfaces:**
- Consumes: Decision command/result、Sizing run、兼容 ValidatedSignalBatch。
- Produces: `DecisionEvaluationRepository.claim/record_success/record_failure/load_formal/recent_actions` 与 `PortfolioRepository.save_c_group_signal_batch()`。

- [ ] **Step 1: 编写迁移 RED 测试**

断言 `0007` 新增五表、正式键/attempt/run link/target 唯一约束、owner 和 lease 字段；downgrade 到 `0006` 后五表消失且 Phase 3 四表仍存在。

- [ ] **Step 2: 运行迁移测试确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_phase4_migration.py -q`

Expected: FAIL，Alembic head 仍为 `0006`。

- [ ] **Step 3: 实现 Migration 与 ORM**

建立：

```text
ai_signal_llm_decision_evaluation
ai_signal_llm_decision_attempt
ai_signal_llm_decision_run_link
ai_signal_position_sizing_run
ai_signal_position_target
```

Evaluation 必须包含 `portfolio_id`、Jev evaluation IDs、state JSON/hash、raw response text/hash、当前 owner/lease。Raw response 使用有长度上限的 Text，失败结果不得写 model text。

- [ ] **Step 4: 编写 Repository RED 测试**

覆盖首次 Claim、成功缓存、失败重试、两个独立 Repository 同时 Claim、过期 lease 双 worker 抢占、旧 owner 与 replacement 同时提交、身份不匹配回滚、最近五次正式动作顺序。

- [ ] **Step 5: 实现数据库 CAS Repository**

接管和 completion 的条件 `UPDATE` 必须同时包含 evaluation ID、`IN_PROGRESS`、attempt sequence、owner token 和 lease。以 `rowcount == 1` 作为唯一所有权裁决，进程内 Lock 不能作为正确性依据。

- [ ] **Step 6: 实现 Sizing + Signal 原子保存**

`PortfolioRepository.save_c_group_signal_batch()` 在同一 Session transaction 中写入 sizing run、全部 position target、正式 SignalBatch/Signal、决策前 Portfolio snapshot 和 VirtualOrder。任一 target 或 order 冲突必须整批回滚。

- [ ] **Step 7: 运行 Task 5 测试并提交**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_phase4_migration.py tests/integration/test_decision_repository.py tests/integration/test_portfolio_repository.py -q`

Expected: PASS。

```powershell
git add migrations/versions/0007_phase4_llm_position_sizing.py src/fkqt_jevinvestor/persistence/models.py src/fkqt_jevinvestor/persistence/decision_repository.py src/fkqt_jevinvestor/persistence/repositories.py tests/integration/test_phase4_migration.py tests/integration/test_decision_repository.py tests/integration/test_portfolio_repository.py
git commit -m "持久化：保存 LLM 决策与仓位审计"
```

### Task 6: 实现 C 组主链服务

**Requirements:** R7、R9、R12、R15、R16、R21—R28、R40—R44

**Files:**
- Create: `src/fkqt_jevinvestor/services/c_group_decision.py`
- Test: `tests/integration/test_c_group_decision.py`

**Interfaces:**
- Consumes: `JevRunEvaluationV1`、snapshot/features、portfolio、历史动作、pending order 摘要、Decision Provider/Repository、Sizer、PortfolioRepository。
- Produces: `CGroupDecisionCommandV1`、`CGroupDecisionRunV1`、`CGroupDecisionService.evaluate_run()`。

- [ ] **Step 1: 编写 C 组 RED 集成测试**

测试 Fixture 包含两个候选、一个 `HELD_ONLY`、一个 Symbol Jev 失败和完整 Portfolio。断言 universe Provider 结果复用、LLM 只调用可用的两票、三票都有结果、`HELD_ONLY` allowed actions 只有 KEEP/EXIT、失败票 NO_SIGNAL 保持、最终 SignalBatch 完整。

- [ ] **Step 2: 运行测试确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_c_group_decision.py -q`

Expected: collection FAIL，service 不存在。

- [ ] **Step 3: 实现输入一致性 Gate**

整批拒绝：decision date/cutoff/execution date、candidate universe hash、market snapshot hash 不一致，或 portfolio ID/version 不匹配。单票短路：feature 缺失、Symbol Jev 非 AVAILABLE、历史/订单摘要日期晚于 cutoff。

- [ ] **Step 4: 实现逐票 Claim 与失败归一化**

按 symbol 排序；COMPLETE 直接读取；IN_PROGRESS 返回已保存进行中记录；ACQUIRED 才调用 Provider。所有失败使用真实 started/finished/latency，Provider 失败不写 thesis/invalidation。

- [ ] **Step 5: 实现全体 Sizing 和原子保存**

只有所有证券都有明确 DecisionEvaluation 后才能调用 Sizer。保存完整 sizing+signal 后返回 run result；中途 DB 错误不得留下部分 target 或 order。

- [ ] **Step 6: 补齐边界测试并运行**

增加：Universe Jev 失败时 LLM 调用 0；相同 run 重放调用 0；候选顺序变化 hash/结果不变；缺一个输出整批拒绝；Provider 非法动作不改写；决策前超限只阻止新增。

Run: `.\.venv\Scripts\python.exe -m pytest tests/integration/test_c_group_decision.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```powershell
git add src/fkqt_jevinvestor/services/c_group_decision.py tests/integration/test_c_group_decision.py
git commit -m "功能：编排 C 组 LLM 决策与仓位"
```

### Task 7: 冻结重放 TargetProvider

**Requirements:** R6、R22、R26、R29、R34—R36、R45

**Files:**
- Create: `src/fkqt_jevinvestor/services/frozen_c_group_target.py`
- Test: `tests/unit/test_frozen_c_group_target.py`
- Modify: `tests/unit/test_backtest_contracts.py`

**Interfaces:**
- Consumes: 内容寻址 JSON 文件和 `DecisionReplayDay`。
- Produces: `FrozenTargetBundleV1`、`FrozenTargetStore`、`FrozenCGroupTargetProvider.build_targets()`。

- [ ] **Step 1: 编写冻结重放 RED 测试**

写入两天 bundle，断言返回 `ExperimentArm.C_JEV_LLM`；请求日期/hash 不匹配拒绝；路径逃逸拒绝；内容篡改拒绝；Provider 类源码和 Fake 调用均证明没有 FKQT/Jev/LLM/FastAPI/SQLAlchemy 访问；`DecisionReplayDay` 不含 execution market。

- [ ] **Step 2: 运行测试确认 RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_frozen_c_group_target.py -q`

Expected: collection FAIL，模块不存在。

- [ ] **Step 3: 实现内容寻址 Store 与 Provider**

路径固定为 `<root>/<dataset_id>/<decision_date>/<content_hash>.json`。保存采用临时文件加 `os.replace()`；加载重新计算 hash 并校验 decision date、market/feature/universe hash 和 sizing version。

- [ ] **Step 4: 运行 Task 7 测试并提交**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_frozen_c_group_target.py tests/unit/test_backtest_contracts.py -q`

Expected: PASS。

```powershell
git add src/fkqt_jevinvestor/services/frozen_c_group_target.py tests/unit/test_frozen_c_group_target.py tests/unit/test_backtest_contracts.py
git commit -m "功能：提供 C 组冻结目标重放"
```

### Task 8: CLI、Live Probe、运行手册与阶段验收

**Requirements:** R2、R21—R25、R28、R32、R33、R44

**Files:**
- Modify: `src/fkqt_jevinvestor/cli/main.py`
- Modify: `tests/unit/test_cli.py`
- Create: `tests/live/test_deepseek_decision_live.py`
- Create: `docs/runbooks/c-group-decisions.md`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `task_plan.md`
- Modify: `findings.md`

**Interfaces:**
- Consumes: Phase 4 Service、Settings、Repositories。
- Produces: `decision run-c-group`、显式 Live Probe 和完整运维文档。

- [ ] **Step 1: 编写 CLI 与 Live Gate RED 测试**

CLI 测试固定必需参数 `--date/--portfolio-id/--candidate-limit`，非正容量拒绝，缺 snapshot/provider 配置返回稳定错误。默认 Live Test 必须 skip 且 Fake 证明外部调用为 0。

- [ ] **Step 2: 实现 CLI 装配**

Client 只在存在 Key 且实际执行决策命令时创建。不得在 `--help`、配置查询、离线重放或普通测试导入阶段连接网络。

- [ ] **Step 3: 编写 Live Probe**

使用一只空仓合成 Decision input，模型必须为环境配置值且默认 `deepseek-flash`；断言只返回四个模型动作、无额外字段、无仓位数值。Probe 不保存虚拟订单。

- [ ] **Step 4: 编写完整运行手册**

文档包含环境变量、数据库迁移、单日命令、Live 开关、五张新表查询、所有稳定错误码、重放流程、Secret 禁止项、Phase 5 停止点和故障排查。`.env.example` 只保存空 Key 与非秘密默认值。

- [ ] **Step 5: 运行最小文档/CLI测试并提交**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli.py tests/live/test_deepseek_decision_live.py -q`

Expected: CLI PASS，Live SKIPPED。

```powershell
git add src/fkqt_jevinvestor/cli/main.py tests/unit/test_cli.py tests/live/test_deepseek_decision_live.py docs/runbooks/c-group-decisions.md .env.example README.md task_plan.md findings.md
git commit -m "文档：补充 C 组决策运行与验收"
```

- [ ] **Step 6: 运行阶段唯一一次完整离线测试**

```powershell
$env:PYTHONPATH = (Join-Path $PWD 'src')
.\.venv\Scripts\python.exe -m pytest -m "not live" -q
```

Expected: 全部 PASS。若出现失败，只修复并重跑失败项，不再第二次运行完整套件。

- [ ] **Step 7: 运行静态、迁移与安全验收**

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pyright --pythonpath ".\.venv\Scripts\python.exe"
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe downgrade 0006_phase3_jev_pnl_probabilities
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe current
git diff --check
```

Expected: Ruff 0、Pyright 0、Alembic 最终 `0007_phase4_llm_position_sizing (head)`、diff check 0。

- [ ] **Step 8: 执行 Secret 与禁止字段扫描**

扫描 `sk-`、Bearer、非空 Key、`target_position_pct/quantity/price/confidence` 是否进入 LLM 输出 Schema。允许仓位字段只存在于 Sizer、Signal 和执行层；Decision model output 必须 0 命中。

- [ ] **Step 9: 独立整分支审查并修复阻断项**

Reviewer 对照 R7、R19—R29、R40—R45 和本计划检查完整 diff。Critical/Important 必须修复并仅定点重测受影响测试；Minor 记录到 `findings.md`。

- [ ] **Step 10: 提交最终验收**

```powershell
git add task_plan.md findings.md
git commit -m "验收：完成 Phase 4 LLM 与确定性仓位"
```

## 预期最终输出

1. DeepSeek V4.1 Flash 逐证券离散动作 Provider。
2. 严格 `ENTER/KEEP/EXIT/AVOID` Schema 与系统 `NO_SIGNAL` 失败语义。
3. 带数据库 CAS 的正式决策审计和历史 Attempt。
4. `position-sizing-v1` 确定性目标权重与组合约束。
5. 正式 `DecisionSignalBatchV1`、现有虚拟执行兼容层和原子持久化。
6. `C_JEV_LLM` 单日 C 组主链。
7. 不调用外部服务的冻结 `TargetProvider`，供回测使用。
8. CLI、Live Probe、运行手册和 Phase 4 验收记录。

## 已知实施风险

| 风险 | 处理 |
|---|---|
| DeepSeek Responses API 与 OpenAI SDK 类型存在兼容差异 | Provider 使用注入 Client Protocol，离线锁定请求 JSON；Live Probe 单独验证真实服务 |
| 逐票调用延迟较高 | Phase 4 保持顺序确定性；Phase 5 才引入有限并发 |
| 已有 Signal 表要求 confidence | 兼容层固定 `0/NOT_PROVIDED`，正式表无该字段，执行与评估禁止读取 |
| 旧实验组枚举与 R26 不一致 | Task 1 纯加法修复，旧成员不删除，新记录只用规范成员 |
| Sizing 与 Signal 跨表部分提交 | Task 5 强制同一 Session transaction 并注入中途故障测试 |
| 决策前组合已超过新上限 | 保持未获退出动作的持仓，阻止新增风险并保存稳定错误码 |
