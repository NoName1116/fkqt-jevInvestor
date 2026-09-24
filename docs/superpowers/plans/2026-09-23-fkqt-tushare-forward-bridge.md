# FKQT → fkqt-jevInvestor 前向行情桥接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FKQT 将任意显式候选队列、持仓及到期订单证券的 Tushare 日频数据发布成不可变快照，本项目校验后自动生成既有执行行情包。

**Architecture:** FKQT 新增独立前向发布器，复用现有 `TushareClient` 和 `LocalMarketDataStore`，不修改三证券 Fixture。新系统仅以版本化 Manifest/Parquet 文件边界消费，不直连 Tushare；D 日决策和 D+1 执行保持时序隔离。

**Tech Stack:** Python 3.12；FKQT 侧 pandas、pyarrow、DuckDB、pytest；本项目 Pydantic、pyarrow、现有 argparse CLI、pytest、Ruff、Pyright。

**Spec:** `docs/superpowers/specs/2026-09-23-fkqt-tushare-forward-bridge-design.md`

## Global Constraints

- FKQT 是唯一行情采集方；本项目不得调用 Tushare、导入 FKQT 内部模块或写 FKQT 数据库。
- 不修改旧 FKQT 策略、旧模拟账户、LLM Overlay、Forward Test 和三证券 Fixture 的冻结语义。
- 不修改 `src/fkqt_jevinvestor/backtest/`、`tests/backtest/`、`docs/runbooks/backtesting.md`。
- D 日模型输入不读取 D+1 行情；模拟执行仅在 D+1 收盘行情完整后按开盘价回放。
- 权限不足、字段缺失、日期/集合/哈希不一致均显式失败，不补零、不估算涨跌停价、不重复成交。
- 每次代码改动前重读相应仓库 `REQUIREMENTS.md`，在本项目新增 R50—R53、FKQT 新增 R63，提交前核查范围。测试遵循 R33：任务级最小测试，阶段末完整离线验证一次。
- 两个仓库使用各自独立分支与 PR；本计划文档位于本项目仓库。先执行 FKQT 发布端，再执行本项目读取端。

## Review Focus

1. 同日多个候选队列发布到同一根目录时，消费者不能静默选错 Manifest：Task 1 验证按日独立目录及歧义拒绝。
2. 候选队列仅改变顺序但集合不变时，Manifest 内容哈希必须改变：Task 1 验证排名字段进入 Parquet 内容。
3. `stk_limit` 接口缺权或返回空集时，普通交易证券不能被误判为上市初期无涨跌幅：Task 2 验证失败码。
4. 停牌持仓缺当日可审计估值价时不能悄悄沿用旧价格：Task 2 与 Task 3 均验证拒绝。
5. 原手工执行 JSON 入口和历史 `ExecutionBundleV1` 必须继续可用：Task 3 验证双入口同 Schema、旧文件可读。

---

## 文件职责与执行环境

| 仓库 | 文件 | 职责 |
|---|---|---|
| FKQT | `REQUIREMENTS.md` | 新增 R63，锁定新只读发布器范围 |
| FKQT | `agent/market_data/forward_publisher.py` | 任意范围六类决策 Manifest 发布 |
| FKQT | `agent/market_data/execution_publisher.py` | 将现有 Tushare 执行上下文映射为执行 Manifest |
| FKQT | `agent/market_data/forward_cli.py` | 外部调度器可调用的发布命令；输出 Manifest ID 与目录 |
| FKQT | `agent/tests/test_forward_publisher.py` | 决策发布范围、排序、时间与异常测试 |
| FKQT | `agent/tests/test_execution_publisher.py` | 涨跌停、停牌、单位、权限与哈希测试 |
| 本项目 | `REQUIREMENTS.md` | 新增 R50—R53 |
| 本项目 | `src/fkqt_jevinvestor/ingestion/fkqt_execution_manifest.py` | 执行 Manifest 校验、读取与映射 |
| 本项目 | `src/fkqt_jevinvestor/cli/main.py` | 为 `daily prepare-execution` 增加 Manifest 入口，并导出持仓与到期订单证券全集 |
| 本项目 | `tests/contract/test_fkqt_execution_manifest.py` | 跨仓库 Schema、篡改、覆盖、旧入口测试 |
| 本项目 | `tests/integration/test_daily_runner.py` | 发布→冻结→幂等执行时序测试 |
| 本项目 | `docs/runbooks/daily-runner.md`、`.env.example`、`task_plan.md`、`findings.md` | 运行方式、配置和证据 |

FKQT 工作目录基线：`C:/Users/1/Desktop/FKQTPRO/ai-signal-phase0`；本项目已在独立工作树 `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner`。执行前分别确认 `git status --short --branch`、远端和基线提交；FKQT 侧新建独立工作树，不能把两仓库文件提交到同一个 Git 仓库。若原 FKQT 分支已变动，以当前仓库结构和冻结需求为准，记录必要的计划偏差。

### Task 1: FKQT 发布任意范围的六类决策 Manifest

**Files:** FKQT `REQUIREMENTS.md`、`agent/market_data/forward_publisher.py`、`agent/market_data/forward_cli.py`、`agent/tests/test_forward_publisher.py`。

**Interfaces:**
- Consumes: `TushareClient.pro`、`LocalMarketDataStore.ingest_frame(...)`，以及显式 `candidate_symbols: tuple[str, ...]`、`held_symbols: tuple[str, ...]`、`as_of_date: str`、`universe_source: str`。
- Produces: `publish_decision_bundle(client, store, *, as_of_date, candidate_symbols, held_symbols, universe_source) -> dict[str, DatasetManifest]`；六类 Manifest 均在按日独立的 `store.root` 中，候选 Parquet 行含 `symbol`、`rank`、`universe_version`。

- [ ] **Step 1: 锁定需求与写失败测试。** 重读 FKQT `REQUIREMENTS.md`，新增 R63：新发布器支持显式非三证券集合、不改旧交易链、发布六类不可变 Manifest、缺数据拒绝。测试先构造四证券 Fake，其中三只候选、一只仅持仓，断言发布数据覆盖四只、候选只含三只；交换候选顺序后 `candidate_universe.content_hash` 改变；重复同根同日不同范围返回 `DATASET_MANIFEST_AMBIGUOUS` 或要求新独立目录。

```python
def test_forward_publisher_keeps_rank_and_held_only(fake_client, tmp_path):
    store = LocalMarketDataStore(tmp_path / "20260924")
    manifests = publish_decision_bundle(
        fake_client, store, as_of_date="20260924",
        candidate_symbols=("600000.SH", "000001.SZ", "600519.SH"),
        held_symbols=("300750.SZ",), universe_source="manual-queue-v1",
    )
    rows = store.read_frame(manifests["candidate_universe"])
    assert rows["symbol"].tolist() == ["600000.SH", "000001.SZ", "600519.SH"]
    assert len(store.read_frame(manifests["daily_bars"])["ts_code"].unique()) == 4
```

- [ ] **Step 2: 运行失败测试。** `python -m pytest agent/tests/test_forward_publisher.py -q`；预期 `ImportError` 或 `NameError`，证明新发布器尚不存在。
- [ ] **Step 3: 实现最小独立发布器。** 规范化证券代码，拒绝空值/重复/非 A 股格式；按显式队列 rank 产生版本哈希；用现有 `TushareUniverseFixtureIngestion._fetch` 或等价只读 `_fetch` 获取 `trade_cal`、`stock_basic`、`namechange`、`suspend_d`、`daily`；将 `daily` 扩成足够计算特征的已知历史窗口且严格过滤 `trade_date <= as_of_date`。逐数据集调用 `store.ingest_frame`，`daily_bars.request_params.volume_unit="TUSHARE_100_SHARES"`。候选缺历史、日历不覆盖下一有效交易日、查询结果存在未来行、同一根目录已存在异范围 Manifest 时抛稳定 `DataUnavailableError`；不得修改 `TushareUniverseFixtureIngestion`。`forward_cli` 提供 `decision --date YYYYMMDD --candidate-file PATH --held-file PATH --source ID --output-root PATH`，候选文件为有序、每行一个证券代码的 UTF-8 文本，持仓文件同格式；输出包含六个 Manifest ID 的 JSON。

```python
def publish_decision_bundle(
    client: TushareClient,
    store: LocalMarketDataStore,
    *,
    as_of_date: str,
    candidate_symbols: tuple[str, ...],
    held_symbols: tuple[str, ...],
    universe_source: str,
) -> dict[str, DatasetManifest]:
    """仅发布显式范围的六类不可变决策数据；缺数据抛 DataUnavailableError。"""
```

- [ ] **Step 4: 最小测试转绿并提交。** 只运行 `python -m pytest agent/tests/test_forward_publisher.py -q`；预期全部通过。提交 `REQUIREMENTS.md`、发布器及该测试，中文提交说明“发布任意范围的 FKQT 决策行情快照”。

### Task 2: FKQT 发布执行行情 Manifest

**Files:** FKQT `agent/market_data/execution_publisher.py`、`agent/market_data/forward_cli.py`、`agent/tests/test_execution_publisher.py`。

**Interfaces:**
- Consumes: `TushareClient.get_execution_context(symbol, trade_date=...) -> dict` 与 `LocalMarketDataStore.ingest_frame`。
- Produces: `publish_execution_bundle(client, store, *, trade_date: str, symbols: tuple[str, ...]) -> DatasetManifest`；类型 `execution_snapshots`、版本 `TUSHARE_EXECUTION_V1`，每行是 `MarketExecutionSnapshot` 可映射字段，`request_params.symbols` 保存有序规范化全集。

- [ ] **Step 1: 写失败测试。** Fake 为正常证券给出 `latest_open=10`、`unadjusted_close=10.2`、`daily_amount_thousands=10000`、`upper_limit_price=11`、`lower_limit_price=9`、`execution_data_quality=COMPLETE`；断言金额字符串为 `10000000`、交易日一致、来源为 `TUSHARE`。另测 `stk_limit` 缺失、缺权限、停牌无估值、范围缺证券、同日重复与篡改拒绝。

```python
def test_execution_publisher_converts_amount_and_limits(fake_client, tmp_path):
    store = LocalMarketDataStore(tmp_path / "20260925")
    manifest = publish_execution_bundle(
        fake_client, store, trade_date="20260925", symbols=("600000.SH",)
    )
    row = store.read_frame(manifest).iloc[0]
    assert manifest.dataset_version == "TUSHARE_EXECUTION_V1"
    assert row["daily_amount_cny"] == "10000000"
    assert row["upper_limit_price"] == "11"
```

- [ ] **Step 2: 运行失败测试。** `python -m pytest agent/tests/test_execution_publisher.py -q`；预期新模块/函数不存在。
- [ ] **Step 3: 实现精确映射和失败语义。** 将 Tushare 执行上下文的 `latest_open`、`unadjusted_close`、`daily_amount_thousands × Decimal(1000)`、上下限价、有效日与停牌状态转成只含字符串数值的 DataFrame；普通交易证券必须有上下限价；上市初期无涨跌幅仅接受上下文明确 `listing_trading_day_number` 且分类可靠；停牌证券的当日估值缺失则整包失败。`execution_data_quality != COMPLETE`、异常、未来日期和来源不符直接拒绝；发布到独立按日目录，保存 `request_params` 与缺失原因；不修改现有 `get_execution_context` 的旧调用方行为。`forward_cli` 增加 `execution --date YYYYMMDD --symbols-file PATH --output-root PATH`；证券文件是组合持仓与当日到期订单去重后的全集，输出执行 Manifest ID 与路径的 JSON。

```python
def publish_execution_bundle(
    client: TushareClient,
    store: LocalMarketDataStore,
    *,
    trade_date: str,
    symbols: tuple[str, ...],
) -> DatasetManifest:
    """发布 execution_snapshots；必要字段缺失则整包拒绝。"""
```

- [ ] **Step 4: 仅重测本任务并提交。** `python -m pytest agent/tests/test_execution_publisher.py -q`；预期全通过。提交中文说明“发布 Tushare 执行行情 Manifest”。

### Task 3: 本项目读取 FKQT 执行 Manifest 并接入 CLI

**Files:** 本项目 `REQUIREMENTS.md`、`src/fkqt_jevinvestor/ingestion/fkqt_execution_manifest.py`、`src/fkqt_jevinvestor/cli/main.py`、`tests/contract/test_fkqt_execution_manifest.py`。

**Interfaces:**
- Consumes: Task 2 的 `execution_snapshots` Manifest/Parquet，字段与版本精确匹配；本项目现有 `ExecutionBundleV1.create(date, snapshots)` 和 `ExecutionBundleStore.save(bundle)`。
- Produces: `load_fkqt_execution_manifest(root: Path, trade_date: date, required_symbols: set[str]) -> ExecutionBundleV1`；CLI `daily prepare-execution --trade-date DATE --manifest-root PATH`，与原 `--raw-file` 二选一，输出原有 `execution_hash`、`execution_ref`、`symbol_count`；CLI `daily required-symbols --trade-date DATE --portfolio-id ID --output-file PATH` 从持久化持仓与截至当日未完成订单导出一行一证券的 UTF-8 文件。

- [ ] **Step 1: 重读本项目 `REQUIREMENTS.md`，新增 R50—R53 并写失败测试。** R50 为 FKQT-only 来源与双入口兼容，R51 为执行 Schema/哈希/日期/证券覆盖，R52 为停牌/涨跌停/金额缺失 fail-closed，R53 为幂等与 D/D+1 隔离。测试以本项目已有的 PyArrow/hash 测试辅助函数写出符合 FKQT 合同的 Parquet/Manifest，另用 Task 2 生成的固定金样本做跨仓库验证；运行时和测试均不 import FKQT 包。分别断言正常读取、篡改文件、版本错误、`as_of_date` 错误、符号缺失、同日双 Manifest 歧义、停牌缺估值拒绝。原 `--raw-file` 测试继续通过。

```python
def write_execution_manifest_fixture(root, day, symbols):
    import hashlib
    import json
    from pathlib import Path
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = [{
        "symbol": symbol, "trade_date": day, "trading_day_status": "OPEN",
        "trading_status": "TRADING", "open_price": "10", "unadjusted_close": "10.2",
        "daily_amount_cny": "10000000", "upper_limit_price": "11",
        "lower_limit_price": "9", "is_initial_no_limit_period": False,
    } for symbol in symbols]
    table = pa.Table.from_pylist(rows)
    dataset_id = hashlib.sha256(f"execution:{day}".encode()).hexdigest()
    relative = Path("raw/execution_snapshots") / f"as_of_date={day}" / f"{dataset_id}.parquet"
    path = root / relative
    path.parent.mkdir(parents=True)
    pq.write_table(table, path, compression="zstd", version="2.6")
    manifest = {
        "dataset_id": dataset_id, "dataset_type": "execution_snapshots",
        "as_of_date": day, "source": "TUSHARE", "dataset_version": "TUSHARE_EXECUTION_V1",
        "request_params": {"symbols": list(symbols)},
        "fetched_at": "2026-09-25T08:00:00+00:00", "row_count": len(rows),
        "schema_hash": hashlib.sha256(table.schema.serialize().to_pybytes()).hexdigest(),
        "raw_record_hash": "a" * 64,
        "content_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "storage_path": relative.as_posix(),
    }
    directory = root / "manifests"
    directory.mkdir(parents=True)
    (directory / f"{dataset_id}.json").write_text(json.dumps(manifest), encoding="utf-8")

def test_fkqt_execution_manifest_rejects_missing_held_symbol(tmp_path):
    write_execution_manifest_fixture(tmp_path, "20260925", ("600000.SH",))
    with pytest.raises(FkqtBundleError, match="EXECUTION_SYMBOL_COVERAGE_INCOMPLETE"):
        load_fkqt_execution_manifest(tmp_path, date(2026, 9, 25), {"600000.SH", "300750.SZ"})
```

- [ ] **Step 2: 运行失败测试。** `uv run pytest tests/contract/test_fkqt_execution_manifest.py -q`；预期新模块导入失败。
- [ ] **Step 3: 实现读取与 CLI。** 仿照 `FkqtManifestProvider._read_rows` 只读校验 Manifest ID/路径、SHA-256、Parquet Schema/hash/行数、`source=TUSHARE`、版本、日期、数值十进制、字段和证券覆盖。不要从 FKQT 包 import；契约测试可以通过夹具文件而非运行时依赖 FKQT。`argparse` 的 `--raw-file` 与 `--manifest-root` 设为互斥且必选；准备阶段调用读取器时传入空的 `required_symbols`，但仍严格要求 Parquet 实际证券集合与 Manifest `request_params.symbols` 完全相同；`daily execute` 再根据持久化持仓与到期订单验证实际覆盖。`daily required-symbols` 复用 `PortfolioRepository.get_state(portfolio_id, as_of=trade_date)` 和 `load_pending_orders_through`，写出排序、去重的证券全集，不修改持仓/订单；空全集仍输出空文件和 `symbol_count=0`，交由发布端拒绝空证券请求。从 Manifest 成功创建 `ExecutionBundleV1` 后进入原 `ExecutionBundleStore` 路径。已有手工入口及历史内容寻址文件语义不变。

```python
def load_fkqt_execution_manifest(
    root: Path, trade_date: date, required_symbols: set[str]
) -> ExecutionBundleV1:
    """仅从已校验 FKQT Manifest 构造现有执行包。"""
```

- [ ] **Step 4: 最小测试转绿并提交。** `uv run pytest tests/contract/test_fkqt_execution_manifest.py -q`；预期全通过。提交中文说明“接入 FKQT 冻结执行行情”。

### Task 4: 跨仓库契约、运行手册与阶段验收

**Files:** 本项目 `tests/integration/test_daily_runner.py`、`docs/runbooks/daily-runner.md`、`.env.example`、`task_plan.md`、`findings.md`；FKQT 新发布器的使用说明置于 `agent/market_data/` 已有文档位置或新建 `agent/market_data/README.md`。

**Interfaces:**
- Consumes: Task 1 六类 Manifest、Task 2 执行 Manifest、Task 3 `load_fkqt_execution_manifest` 与现有日运行命令。
- Produces: 可复制的 D 发布→D 决策→D+1 发布→D+1 冻结→D+1 执行命令、跨仓库固定夹具和验收证据。

- [ ] **Step 1: 写失败的跨仓库流程测试。** 使用两日冻结夹具，决策日只见 D 数据，执行日先 `prepare-execution --manifest-root` 再 `daily execute`；断言 held-only 被覆盖、同一包重跑 `fill_count=0`、换包 `EXECUTION_INPUT_CONFLICT`。对同目录两个 Manifest 验证拒绝歧义；对旧 `--raw-file` 路径做回归。按现有 `test_daily_runner.py` 的命令测试帮助函数调用下列新入口：

```python
args = Namespace(
    trade_date=date(2026, 9, 25), raw_file=None,
    manifest_root=str(execution_manifest_root),
)
assert _prepare_execution(args, settings) == 0
execution_ref = json.loads(capsys.readouterr().out)["execution_ref"]
assert Path(execution_ref).is_file()
```

- [ ] **Step 2: 运行该测试观察失败。** `uv run pytest tests/integration/test_daily_runner.py -q`；预期新增用例在跨仓库装配或 CLI 入口处失败，原用例保持通过。
- [ ] **Step 3: 完成最小装配及运行手册。** 文档列出 FKQT 发布命令/函数入口、按日目录、Tushare 权限前提、本项目环境变量、D/D+1 命令、标准输出、缺权/缺价/哈希冲突的错误码和恢复方法。更新 `task_plan.md` 与 `findings.md`，准确记录未验证的真实账号权限。不能宣称无人值守运行已完成，除非外部调度和真实数据冒烟都通过。运行示例中的命令必须完整：

```powershell
fkqt-jevinvestor daily required-symbols --trade-date 2026-09-24 --portfolio-id paper-main --output-file C:\data\held-20260924.txt
python -m market_data.forward_cli decision --date 20260924 --candidate-file C:\data\candidate-20260924.txt --held-file C:\data\held-20260924.txt --source fkqt-queue-v1 --output-root C:\data\fkqt-forward\20260924
$env:FKQT_MANIFEST_BUNDLE_ROOT = 'C:\data\fkqt-forward\20260924'
$env:JEV_INVESTOR_C_GROUP_CANDIDATE_SYMBOLS = (Get-Content C:\data\candidate-20260924.txt) -join ','
fkqt-jevinvestor daily close --date 2026-09-24 --portfolio-id paper-main --candidate-limit 80
fkqt-jevinvestor daily required-symbols --trade-date 2026-09-25 --portfolio-id paper-main --output-file C:\data\required-20260925.txt
python -m market_data.forward_cli execution --date 20260925 --symbols-file C:\data\required-20260925.txt --output-root C:\data\fkqt-execution\20260925
$prepared = fkqt-jevinvestor daily prepare-execution --trade-date 2026-09-25 --manifest-root C:\data\fkqt-execution\20260925 | ConvertFrom-Json
fkqt-jevinvestor daily execute --trade-date 2026-09-25 --portfolio-id paper-main --execution-bundle $prepared.execution_ref
```
- [ ] **Step 4: 阶段终验。** FKQT 侧运行 `python -m pytest agent/tests/test_forward_publisher.py agent/tests/test_execution_publisher.py agent/tests/test_point_in_time_data.py -q`；本项目运行 `uv run pytest -q -m "not live"`、`uv run ruff check .`、`uv run pyright`、`uv run alembic heads`、`git diff --check`。读取完整输出与退出码；若 FKQT 现有完整离线套件可在当前环境运行，再执行一次。只对失败项修复并重测；结束时确认两仓库没有 Secret、没有旧交易链或回测目录改动。
- [ ] **Step 5: 提交与交付。** 各仓库分别中文提交、分别推送独立分支并按既有 PR 基线创建 PR；PR 合并仍需 CI 与非作者 Review。交付两个 PR 链接、可执行运行手册、测试证据与真实 Tushare 权限未验证项。

## 实施前置与 Plan B

执行前检查 FKQT 侧 `TushareClient` 账号是否具备 `daily`、`trade_cal`、`stock_basic`、`namechange`、`suspend_d`、`stk_limit` 的调用权限。无真实凭据也可完成并验证离线代码，但不能确认当日实时数据可用。若 `stk_limit` 缺权，不绕过风控门槛；原手工 JSON 入口继续保留，由另一个能提供官方涨跌停数据的可信上游按同契约发布，或等待权限开通。
