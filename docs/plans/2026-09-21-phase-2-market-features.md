# Phase 2 FKQT 快照与确定性行情特征 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在独立 `fkqt-jevInvestor` 仓库中实现 FKQT REST/文件快照 Adapter、Point-in-Time 不可变快照、版本化确定性行情特征，并冻结沙耶可并行开发回测引擎的 `backtest-contract-v1`。

**Architecture:** FKQT 数据先经过 Schema、截止时间和内容哈希校验，再进入内容寻址快照存储；特征引擎只读取冻结快照并输出 Decimal 特征与明确缺失原因。回测通过三个 Protocol 消费快照、目标仓位和执行结果，不依赖 FKQT、模型或数据库实现。

**Tech Stack:** Python 3.12、Pydantic 2、httpx、PyArrow、SQLAlchemy 2 Async、Alembic、FastAPI、pytest、Ruff、Pyright

**Spec:** `docs/specs/2026-09-21-market-factor-jev-llm-design.md`

## Global Constraints

- 对应需求：R5、R6、R11、R12、R13、R14、R15、R16、R21、R22、R27、R28、R30、R33、R34、R35、R36。
- 本计划在独立仓库迁移计划全部验收后执行。
- 不导入 FKQT 内部模块，不连接 FKQT 可写数据库。
- 文件 Adapter 只读取从 FKQT 数据目录复制出的不可变 Manifest + Parquet bundle，不直接读取正在写入的数据目录。
- REST Adapter 固定读取接口契约，不在本计划修改 FKQT 旧交易链。
- 价格、金额、比例和特征值使用 Decimal；不得用二进制 float 作为持久化业务值。
- 日线特征使用同一 `adjustment_mode`；混用时整只证券特征失败。
- D+1 只能来自交易日历；禁止自然日加一。
- 未来记录导致整批快照拒绝，不允许静默裁剪后继续。
- 横截面百分位使用同一候选池快照。
- 日常任务仅运行目标测试；完整套件只在 Task 9 运行一次。

## Review Focus

1. **未来数据伪装成乱序数据**：任何记录晚于 cutoff 都必须整批失败；Task 2 的 `test_future_record_rejects_entire_snapshot` 固定该行为。
2. **Manifest 与 Parquet 被替换**：文件内容哈希不匹配必须失败；Task 3 的 `test_manifest_content_hash_mismatch_is_rejected` 固定该行为。
3. **复权口径混用**：同一证券历史出现 `NONE/QFQ` 混合时不得计算收益；Task 5 的 `test_mixed_adjustment_mode_has_missing_reason` 固定该行为。
4. **横截面并列值不稳定**：并列值使用平均秩且不受输入顺序影响；Task 6 的 `test_percentile_ties_use_average_rank` 固定该行为。
5. **回测并行开发冲突**：沙耶只能依赖冻结 Protocol，不修改核心 Entity；Task 1 的 import 测试、标签和开发契约固定该边界。

---

### Task 1: 冻结行情、特征与回测领域契约

**Files:**
- Create: `src/fkqt_jevinvestor/domain/market_features.py`
- Create: `src/fkqt_jevinvestor/domain/backtest.py`
- Test: `tests/unit/test_market_feature_contracts.py`
- Test: `tests/unit/test_backtest_contracts.py`
- Verify: `docs/contributors/2026-09-21-backtesting-development-contract.md`

**Interfaces:**
- Consumes: 已迁移的 `PortfolioState` 和 `MarketExecutionSnapshot`。
- Produces: `DailyBar`、`SecurityTradeState`、`MarketSnapshot`、`FeatureValue`、`MarketFeatureSnapshot`、`ReplayDay`、`TargetPositionBatch`、三个回测 Protocol。

- [ ] **Step 1: 写入不可变与校验失败测试**

~~~python
def test_daily_bar_rejects_nonpositive_prices() -> None:
    with pytest.raises(ValidationError):
        DailyBar(
            symbol="600000.SH",
            trade_date=date(2026, 9, 18),
            open=Decimal("0"),
            high=Decimal("10.20"),
            low=Decimal("9.80"),
            close=Decimal("10.00"),
            previous_close=Decimal("9.90"),
            volume=Decimal("1000"),
            amount_cny=Decimal("10000"),
            adjustment_mode="NONE",
        )


def test_feature_value_requires_value_xor_missing_reason() -> None:
    with pytest.raises(ValidationError):
        FeatureValue(
            feature_code="return_1d",
            feature_version="market-features-v1",
            as_of=datetime(2026, 9, 18, 15, tzinfo=UTC),
            lookback_window=1,
            value=Decimal("0.01"),
            missing_reason="INSUFFICIENT_HISTORY",
            source_snapshot_hash="a" * 64,
        )
~~~

- [ ] **Step 2: 运行测试确认失败**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_market_feature_contracts.py -q
~~~

预期：FAIL，模块不存在。

- [ ] **Step 3: 实现核心行情与特征 Entity**

`market_features.py` 必须提供：

~~~python
class AdjustmentMode(StrEnum):
    NONE = "NONE"
    QFQ = "QFQ"


class DailyBar(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    trade_date: date
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    previous_close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    amount_cny: Decimal = Field(ge=0)
    adjustment_mode: AdjustmentMode


class SecurityTradeState(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    trade_date: date
    trading_day_status: str
    trading_status: str
    is_st_or_delisting_risk: bool | None
    upper_limit_price: Decimal | None = Field(default=None, gt=0)
    lower_limit_price: Decimal | None = Field(default=None, gt=0)
    is_initial_no_limit_period: bool | None
    corporate_action_status: str
    market: str
    board: str
    listing_date: date | None
    missing_reasons: tuple[str, ...] = ()


class MarketSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    snapshot_id: str
    decision_date: date
    decision_cutoff: datetime
    next_trade_date: date
    universe_snapshot_hash: str = Field(min_length=64, max_length=64)
    daily_bars: Mapping[str, tuple[DailyBar, ...]]
    security_states: Mapping[str, SecurityTradeState]
    source_manifest_ids: tuple[str, ...]
    content_hash: str = Field(min_length=64, max_length=64)


class FeatureValue(BaseModel):
    model_config = ConfigDict(frozen=True)
    feature_code: str
    feature_version: str
    as_of: datetime
    lookback_window: int = Field(gt=0)
    value: Decimal | None
    missing_reason: str | None
    source_snapshot_hash: str = Field(min_length=64, max_length=64)


class MarketFeatureSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    decision_date: date
    values: Mapping[str, FeatureValue]
    content_hash: str = Field(min_length=64, max_length=64)
~~~

为 `FeatureValue` 增加 `model_validator(mode="after")`，严格要求 `value` 与 `missing_reason` 恰好一个非空。

- [ ] **Step 4: 实现回测 Entity 和 Protocol**

按 `docs/contributors/2026-09-21-backtesting-development-contract.md` 第 4 节逐字段实现 `domain/backtest.py`，包括 `BacktestExecutionResult`，不得缩减字段或把 Decimal 改成 float。

- [ ] **Step 5: 写入公开接口测试**

~~~python
from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
    ExecutionPort,
    ReplayDataProvider,
    ReplayDay,
    TargetPositionBatch,
    TargetProvider,
)


def test_backtest_contract_v1_public_names_are_importable() -> None:
    assert BacktestConfig
    assert ReplayDay
    assert TargetPositionBatch
    assert ReplayDataProvider
    assert TargetProvider
    assert ExecutionPort
~~~

- [ ] **Step 6: 运行目标测试并冻结标签**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_market_feature_contracts.py tests/unit/test_backtest_contracts.py -q
git add src/fkqt_jevinvestor/domain tests/unit docs/contributors
git commit -m "契约：冻结行情特征与回测接口 v1"
git tag -a backtest-contract-v1 -m "回测接口 v1"
git push origin main backtest-contract-v1
~~~

预期：全部 PASS；沙耶从该标签创建 `feature/backtest-engine`。

### Task 2: 实现 canonical JSON、快照校验和内容寻址存储

**Files:**
- Create: `src/fkqt_jevinvestor/ingestion/canonical.py`
- Create: `src/fkqt_jevinvestor/ingestion/snapshot_store.py`
- Test: `tests/unit/test_snapshot_store.py`

**Interfaces:**
- Consumes: `MarketSnapshot`。
- Produces: `canonical_json`、`sha256_json`、`MarketSnapshotStore.save/load`。

- [ ] **Step 1: 写入未来数据整批拒绝与幂等测试**

~~~python
def test_future_record_rejects_entire_snapshot(snapshot_with_future_bar, store) -> None:
    with pytest.raises(SnapshotValidationError, match="POINT_IN_TIME_VIOLATION"):
        store.save(snapshot_with_future_bar)
    assert tuple(store.root.rglob("*.json")) == ()


def test_same_snapshot_is_idempotent(valid_snapshot, store) -> None:
    first = store.save(valid_snapshot)
    second = store.save(valid_snapshot)
    assert first == second
    assert len(tuple(store.root.rglob("*.json"))) == 1
~~~

- [ ] **Step 2: 运行测试确认失败**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_snapshot_store.py -q
~~~

预期：FAIL，存储模块不存在。

- [ ] **Step 3: 实现 canonical 序列化**

~~~python
def canonical_json(value: BaseModel | Mapping[str, object]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: BaseModel | Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
~~~

- [ ] **Step 4: 实现存储规则**

`MarketSnapshotStore.save()` 在写文件前检查：

- `decision_cutoff.date() == decision_date`；
- 所有 `bar.trade_date <= decision_date`；
- 所有 state 的 `trade_date == decision_date`；
- `next_trade_date > decision_date`；
- `content_hash` 等于清空自身 hash 字段后的 canonical payload hash；
- 路径为 `data/snapshots/<decision_date>/<content_hash>.json`；
- 同路径不同内容返回 `SNAPSHOT_HASH_CONFLICT`。

- [ ] **Step 5: 运行目标测试并提交**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_snapshot_store.py -q
git add src/fkqt_jevinvestor/ingestion tests/unit/test_snapshot_store.py
git commit -m "功能：实现不可变行情快照存储"
~~~

### Task 3: 实现 FKQT Manifest + Parquet 文件 Adapter

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/fkqt_jevinvestor/ingestion/fkqt_manifest.py`
- Test: `tests/contract/test_fkqt_manifest_provider.py`
- Create: `tests/fixtures/fkqt_bundle/`

**Interfaces:**
- Consumes: FKQT `DatasetManifest` JSON 和内容寻址 Parquet。
- Produces: `FkqtManifestProvider.freeze_snapshot(...) -> MarketSnapshot`。

- [ ] **Step 1: 增加依赖**

在 dependencies 增加 `"pyarrow>=17,<24"`，运行 `python -m uv lock`，不得添加 pandas。

- [ ] **Step 2: 写入哈希破坏测试**

~~~python
async def test_manifest_content_hash_mismatch_is_rejected(tampered_bundle) -> None:
    provider = FkqtManifestProvider(tampered_bundle)
    with pytest.raises(FkqtBundleError, match="DATASET_HASH_MISMATCH"):
        await provider.freeze_snapshot(
            symbols=("600000.SH",),
            decision_date=date(2026, 9, 18),
            decision_cutoff=datetime(2026, 9, 18, 15, 30, tzinfo=SHANGHAI),
            lookback_trading_days=61,
        )
~~~

- [ ] **Step 3: 运行测试确认失败**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/contract/test_fkqt_manifest_provider.py -q
~~~

预期：FAIL，Provider 不存在。

- [ ] **Step 4: 实现现有 Dataset 映射**

支持 `trading_calendar`、`security_master`、`security_name_history`、`suspension_status`、`daily_bars`。使用 `pyarrow.parquet.read_table(path).to_pylist()`，校验 `content_hash`、`schema_hash`、`as_of_date`、`dataset_version` 和 `row_count`。Tushare `amount` 由千元转换为 CNY；`vol` 的上游单位记录为 `TUSHARE_100_SHARES`。

`next_trade_date()` 返回第一个晚于 D 且 `is_open == 1` 的日期；不存在时返回 `NEXT_TRADE_DATE_UNAVAILABLE`。当前 bundle 不包含涨跌停和公司行为时写稳定 missing reason，不猜测数值。

- [ ] **Step 5: 运行目标测试并提交**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/contract/test_fkqt_manifest_provider.py -q
git add pyproject.toml uv.lock src/fkqt_jevinvestor/ingestion tests/contract tests/fixtures
git commit -m "功能：读取 FKQT 不可变行情 Bundle"
~~~

### Task 4: 实现版本化 FKQT REST Adapter

**Files:**
- Create: `src/fkqt_jevinvestor/ingestion/fkqt_rest.py`
- Test: `tests/contract/test_fkqt_rest_provider.py`

**Interfaces:**
- Consumes: `GET /api/v1/market-snapshots/{decision_date}` JSON。
- Produces: 与 Task 3 相同的 `MarketSnapshot`。

- [ ] **Step 1: 写入 MockTransport 契约测试**

测试有效响应、`content_hash` 不匹配、包含 D+1 日线；后两者分别断言 `UPSTREAM_HASH_MISMATCH` 和 `POINT_IN_TIME_VIOLATION`。

- [ ] **Step 2: 运行测试确认失败**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/contract/test_fkqt_rest_provider.py -q
~~~

预期：FAIL，REST Provider 不存在。

- [ ] **Step 3: 实现固定请求**

~~~python
response = await client.get(
    f"/api/v1/market-snapshots/{decision_date.isoformat()}",
    params={
        "symbols": ",".join(sorted(symbols)),
        "cutoff": decision_cutoff.isoformat(),
        "lookback_trading_days": lookback_trading_days,
    },
    headers={"Accept": "application/vnd.fkqt.market-snapshot.v1+json"},
)
~~~

只允许 200；401/403 映射 `UPSTREAM_AUTH_FAILED`，404 映射 `SNAPSHOT_NOT_FOUND`，429 映射 `UPSTREAM_RATE_LIMITED`，5xx 映射 `UPSTREAM_UNAVAILABLE`。错误对象不得包含认证头或原始正文。

- [ ] **Step 4: 运行测试并提交**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/contract/test_fkqt_rest_provider.py -q
git add src/fkqt_jevinvestor/ingestion/fkqt_rest.py tests/contract/test_fkqt_rest_provider.py
git commit -m "功能：实现 FKQT REST 行情快照 Adapter"
~~~

### Task 5: 实现单票确定性特征引擎

**Files:**
- Create: `src/fkqt_jevinvestor/services/market_features.py`
- Test: `tests/unit/test_market_features.py`

**Interfaces:**
- Consumes: 升序 `tuple[DailyBar, ...]` 和 snapshot hash。
- Produces: 不含横截面字段的 `MarketFeatureSnapshot`。

- [ ] **Step 1: 写入固定数列和混合复权测试**

手工构造 61 根 Decimal 日线，逐字段断言 `return_1d`、`return_5d`、`return_20d`、`close_vs_ma20`、`overnight_gap_pct`。`test_mixed_adjustment_mode_has_missing_reason` 断言价格衍生特征的 `value is None` 且 `missing_reason == "ADJUSTMENT_MODE_MISMATCH"`。

- [ ] **Step 2: 运行测试确认失败**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_market_features.py -q
~~~

- [ ] **Step 3: 实现固定公式**

~~~text
return_Nd = close_t / close_t-N - 1
close_vs_maN = close_t / mean(close[t-N+1:t]) - 1
maN_slope_5d = maN_t / maN_t-5 - 1
realized_vol_20d = sample_std(daily_returns_20) * sqrt(252)
downside_vol_20d = sample_std(min(return, 0)) * sqrt(252)
ATR14 = mean(max(high-low, abs(high-prev_close), abs(low-prev_close)))
atr_pct_14d = ATR14 / close_t
volume_ratio_5d_20d = mean(volume_5) / mean(volume_20)
amount_ratio_5d_20d = mean(amount_5) / mean(amount_20)
distance_from_20d_high = close_t / max(high_20) - 1
distance_from_20d_low = close_t / min(low_20) - 1
short_term_reversal_3d = -(close_t / close_t-3 - 1)
overnight_gap_pct = open_t / previous_close_t - 1
gap_fill_pct = (close_t - open_t) / abs(open_t - previous_close_t)
~~~

`turnover_pct` 在流通股本缺失时返回 `FLOAT_SHARES_UNAVAILABLE`。分母为零返回 `ZERO_VOLUME_BASELINE`、`ZERO_AMOUNT_BASELINE` 或 `ZERO_OVERNIGHT_GAP`。有效值量化为 `Decimal("0.00000001")`。

- [ ] **Step 4: 运行测试并提交**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_market_features.py -q
git add src/fkqt_jevinvestor/services/market_features.py tests/unit/test_market_features.py
git commit -m "功能：实现确定性单票行情特征"
~~~

### Task 6: 实现横截面百分位和覆盖率

**Files:**
- Modify: `src/fkqt_jevinvestor/services/market_features.py`
- Test: `tests/unit/test_cross_sectional_features.py`

**Interfaces:**
- Consumes: 同一候选池的单票 Feature mapping。
- Produces: 三个百分位和 `FeatureCoverage`。

- [ ] **Step 1: 写入并列秩测试**

~~~python
def test_percentile_ties_use_average_rank() -> None:
    values = {"A": Decimal("1"), "B": Decimal("1"), "C": Decimal("3")}
    first = percentile_ranks(values)
    second = percentile_ranks(dict(reversed(tuple(values.items()))))
    assert first == second
    assert first == {
        "A": Decimal("0.25000000"),
        "B": Decimal("0.25000000"),
        "C": Decimal("1.00000000"),
    }
~~~

- [ ] **Step 2: 运行测试确认失败**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cross_sectional_features.py -q
~~~

- [ ] **Step 3: 实现百分位**

公式为 `(average_rank - 1) / (valid_count - 1)`；单个有效值返回 `0.5`；无有效值返回 `CROSS_SECTION_EMPTY`。映射：`return_20d→return_20d_percentile`，`realized_vol_20d→volatility_percentile`，`amount_ratio_5d_20d→liquidity_percentile`。

- [ ] **Step 4: 运行测试并提交**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cross_sectional_features.py -q
git add src/fkqt_jevinvestor/services/market_features.py tests/unit/test_cross_sectional_features.py
git commit -m "功能：实现稳定横截面特征排名"
~~~

### Task 7: 持久化行情快照引用和特征值

**Files:**
- Modify: `src/fkqt_jevinvestor/persistence/models.py`
- Create: `src/fkqt_jevinvestor/persistence/market_repository.py`
- Create: `migrations/versions/0004_phase2_market_features.py`
- Test: `tests/integration/test_phase2_migration.py`
- Test: `tests/integration/test_market_repository.py`

**Interfaces:**
- Consumes: `MarketSnapshot`、`MarketFeatureSnapshot`。
- Produces: 幂等 `MarketSnapshotRepository.save_run_inputs/load_run_inputs`。

- [ ] **Step 1: 写入迁移失败测试**

断言升级后存在 `ai_signal_market_snapshot` 和 `ai_signal_market_feature`；唯一约束分别为 `(decision_date, content_hash)` 和 `(market_snapshot_id, symbol, feature_code, feature_version)`。降级到 `0003_phase1_audit_snapshot` 后新表消失，原 11 张表仍存在。

- [ ] **Step 2: 运行测试确认失败**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_phase2_migration.py -q
~~~

- [ ] **Step 3: 实现 Revision 和 Repository**

Snapshot 表保存 ID、decision date、cutoff、next trade date、universe hash、content hash、storage path、source manifests、created at。Feature 表保存 snapshot FK、symbol、feature code/version、as of、lookback、Decimal value、missing reason、source hash。禁止把 Parquet 或完整行情 JSON 写进数据库。

相同 content hash 重复保存不新增行；同一逻辑唯一键但内容不同返回 `MARKET_SNAPSHOT_CONFLICT`，事务不得留下部分 Feature。

- [ ] **Step 4: 运行测试并提交**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_phase2_migration.py tests/integration/test_market_repository.py -q
git add migrations src/fkqt_jevinvestor/persistence tests/integration
git commit -m "持久化：保存行情快照引用与版本化特征"
~~~

### Task 8: 编排冻结、计算、查询与 ReplayDay 构建

**Files:**
- Create: `src/fkqt_jevinvestor/services/market_pipeline.py`
- Create: `src/fkqt_jevinvestor/api/routes/market.py`
- Modify: `src/fkqt_jevinvestor/api/app.py`
- Modify: `src/fkqt_jevinvestor/cli/main.py`
- Test: `tests/integration/test_phase2_market_pipeline.py`
- Test: `tests/integration/test_market_api.py`

**Interfaces:**
- Consumes: `MarketDataProvider`、snapshot store、feature service、repository。
- Produces: `freeze_and_compute()`、只读 API、CLI、`ReplayDay` 构建输入。

- [ ] **Step 1: 写入周末排期和未来数据端到端测试**

Fixture 使用 2026-09-25 周五为 D，Provider 显式返回 2026-09-28 为 D+1；另一个 Fixture 注入 2026-09-26 bar，断言整批失败且数据库行数为 0。

- [ ] **Step 2: 运行测试确认失败**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_phase2_market_pipeline.py -q
~~~

- [ ] **Step 3: 实现编排**

~~~python
class MarketPipeline:
    async def freeze_and_compute(
        self,
        symbols: tuple[str, ...],
        decision_date: date,
        decision_cutoff: datetime,
        lookback_trading_days: int = 61,
    ) -> tuple[MarketSnapshot, Mapping[str, MarketFeatureSnapshot]]: ...
~~~

顺序固定为 Provider 获取 → 全量校验 → 文件保存 → 单票特征 → 横截面 → 单事务保存引用和特征。失败时不得保存数据库部分结果。

- [ ] **Step 4: 增加 API 和 CLI**

~~~text
POST /api/v1/market-snapshots/freeze
GET  /api/v1/market-snapshots/{snapshot_id}
GET  /api/v1/market-snapshots/{snapshot_id}/features
~~~

production 禁用任意本地路径参数。CLI 增加 `market freeze --date YYYY-MM-DD --symbols 600000.SH,000001.SZ --source manifest`。

- [ ] **Step 5: 运行测试并提交**

~~~powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_phase2_market_pipeline.py tests/integration/test_market_api.py -q
git add src/fkqt_jevinvestor tests/integration
git commit -m "功能：完成行情冻结与特征编排"
~~~

### Task 9: Phase 2 最终验收与回测交接

**Files:**
- Create: `docs/runbooks/market-snapshots.md`
- Modify: `task_plan.md`
- Modify: `findings.md`

**Interfaces:**
- Consumes: Tasks 1–8。
- Produces: Phase 2 验收证据、稳定回测 Contract 和沙耶交接材料。

- [ ] **Step 1: 核对标签后的 Contract 未漂移**

~~~powershell
git diff --exit-code backtest-contract-v1 -- src/fkqt_jevinvestor/domain/backtest.py docs/contributors/2026-09-21-backtesting-development-contract.md
~~~

预期：退出码 0。若必须修改，创建 `backtest-contract-v2` 并通知 Contributor，不得移动 v1 标签。

- [ ] **Step 2: 运行唯一一次完整验收**

~~~powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pyright
.\.venv\Scripts\python.exe -m pytest -m "not live" -q
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
~~~

预期：Ruff 0 错误、Pyright 0 errors、全部离线测试 PASS、Alembic 为 `0004_phase2_market_features (head)`。

- [ ] **Step 3: 编写运行手册与实际证据**

运行手册必须包含 Manifest bundle、REST 配置、freeze CLI、API、快照路径、错误码和重放步骤。`task_plan.md` 和 `findings.md` 只记录实际结果和已知 Adapter 限制。

- [ ] **Step 4: 提交、推送并通知 Contributor**

~~~powershell
git add docs task_plan.md findings.md
git commit -m "验收：完成 Phase 2 行情快照与确定性特征"
git push origin main
~~~

给沙耶发送开发契约、`backtest-contract-v1`、clone URL 和 PR 要求。若她已在 v1 开发，不得要求她追随 Phase 2 内部实现文件。
