# 行情快照与确定性特征运行手册

日期：2026-09-21

适用版本：Phase 2，数据库 Revision `0005_phase2_audit_hardening`

## 1. 目标与边界

本流程从 FKQT 的不可变 Manifest bundle 或版本化 REST API 读取 A 股日线、交易日历和证券状态，冻结为本地 JSON 快照，计算版本化单票与横截面特征，并把快照引用和逐项特征写入数据库。

系统不写入 FKQT 数据库，不要求回放期间 FKQT 在线，不连接券商。Jev 和 LLM 不参与行情指标计算。

## 2. 前置条件

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| Python | 3.12 | `.\.venv\Scripts\python.exe --version` | 必须 |
| 数据库迁移 | `0005_phase2_audit_hardening (head)` | `.\.venv\Scripts\python.exe -m alembic current` | 必须 |
| Manifest 版本 | `TUSHARE_PRO_V1` | 检查每个 Manifest 的 `dataset_version` | Manifest 必须 |
| 日线成交量单位 | `TUSHARE_100_SHARES` | 检查日线 Manifest 的 `request_params.volume_unit` | Manifest 必须 |
| 决策时间 | D 日 15:00，带时区 | 检查 API 请求或 CLI 固定值 | 必须 |
| 下一交易日 | 由交易日历明确提供 | 检查快照 `next_trade_date` | 必须 |

首次运行：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

## 3. Manifest bundle 目录

bundle 根目录必须包含 `manifests` 目录以及 Manifest 指向的 Parquet 文件。每个交易日必须恰好提供以下六类数据：

```text
<bundle-root>/
├── manifests/
│   ├── <dataset-id-1>.json
│   ├── <dataset-id-2>.json
│   ├── <dataset-id-3>.json
│   ├── <dataset-id-4>.json
│   └── <dataset-id-5>.json
└── datasets/
    └── <Manifest.storage_path 指向的 Parquet 文件>
```

六类 `dataset_type` 必须完整：

| dataset_type | 用途 |
|---|---|
| `candidate_universe` | FKQT 冻结候选池成员、版本和哈希 |
| `trading_calendar` | 判断 D 是否开市并取得 D+1 |
| `security_master` | 市场、板块、上市日期 |
| `security_name_history` | ST 和退市风险状态 |
| `suspension_status` | 停牌状态 |
| `daily_bars` | 未复权 OHLC、成交量、成交额 |

Manifest 的 `as_of_date` 必须等于决策日的 `YYYYMMDD`，`dataset_id` 必须与文件名一致。请求 symbols 必须与 `candidate_universe` 成员完全一致。系统会验证内容哈希、物理 Schema 哈希、行数、路径边界、数据版本以及 D 到 D+1 的逐日历日期覆盖。

## 4. CLI 冻结行情

设置固定配置。CLI 不接受任意 bundle 路径参数，防止 production 请求覆盖本地路径边界。

```powershell
$env:FKQT_MANIFEST_BUNDLE_ROOT = "D:\fkqt-export\20260925"
$env:JEV_INVESTOR_MARKET_SNAPSHOT_ROOT = "data\snapshots"
$env:JEV_INVESTOR_DATABASE_URL = "sqlite+aiosqlite:///./data/fkqt_jevinvestor.db"
```

执行：

```powershell
.\.venv\Scripts\fkqt-jevinvestor.exe market freeze --date 2026-09-25 --symbols 600000.SH,000001.SZ --source manifest
```

成功时标准输出为单行 JSON：

```json
{"content_hash":"<64位哈希>","decision_date":"2026-09-25","feature_symbols":["000001.SZ","600000.SH"],"next_trade_date":"2026-09-28","snapshot_id":"<快照ID>"}
```

CLI 固定使用 Asia/Shanghai 15:00 作为 `decision_cutoff`。执行前必须先完成数据库迁移。

## 5. REST Provider 装配

`FkqtRestProvider` 接收由启动层配置完成的 `httpx.AsyncClient`，认证、Base URL 和超时不写入业务模块：

```python
from pathlib import Path

import httpx

from fkqt_jevinvestor.api.app import create_app
from fkqt_jevinvestor.ingestion.fkqt_rest import FkqtRestProvider
from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
from fkqt_jevinvestor.persistence.market_repository import MarketSnapshotRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.services.market_pipeline import MarketPipeline

engine = create_engine("sqlite+aiosqlite:///./data/fkqt_jevinvestor.db")
session_factory = create_session_factory(engine)
client = httpx.AsyncClient(
    base_url="https://fkqt.example.internal",
    headers={"Authorization": "Bearer <从 Secret Manager 注入>"},
    timeout=httpx.Timeout(30.0),
)
pipeline = MarketPipeline(
    FkqtRestProvider(client),
    MarketSnapshotStore(Path("data/snapshots")),
    MarketSnapshotRepository(session_factory),
)
app = create_app(session_factory=session_factory, market_pipeline=pipeline)
```

启动层负责在应用关闭时关闭 `httpx.AsyncClient` 和 SQLAlchemy Engine。Secret 不得写入仓库、日志或数据库。

## 6. HTTP API

冻结请求：

```http
POST /api/v1/market-snapshots/freeze
Content-Type: application/json

{
  "symbols": ["600000.SH", "000001.SZ"],
  "decision_date": "2026-09-25",
  "decision_cutoff": "2026-09-25T15:00:00+08:00",
  "lookback_trading_days": 61
}
```

请求 Schema 禁止额外字段，因此不能提交 `storage_path`、bundle 路径、数据库 URL 或凭据。

查询快照引用：

```http
GET /api/v1/market-snapshots/{snapshot_id}
```

查询该快照的版本化特征：

```http
GET /api/v1/market-snapshots/{snapshot_id}/features
```

未装配 `MarketPipeline` 时，freeze 返回 HTTP 503 和 `MARKET_PROVIDER_UNAVAILABLE`；历史查询仍然可用。

## 7. 快照与数据库位置

不可变 JSON 路径：

```text
<snapshot-root>/<decision-date>/<content-hash>.json
```

示例：

```text
data/snapshots/2026-09-25/0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef.json
```

数据库表：

| 表 | 保存内容 |
|---|---|
| `ai_signal_market_snapshot` | 决策日、cutoff、D+1、日历完整边界、候选池 ID/哈希、内容哈希、文件路径、Manifest IDs 和来源审计信封 |
| `ai_signal_market_feature` | symbol、特征代码、版本、as-of、lookback、Decimal 值或缺失原因、来源哈希和原特征快照哈希 |

数据库不保存完整日线 JSON 或 Parquet。不得单独删除快照文件；否则只能恢复特征和引用，不能恢复原始行情。

## 8. 离线重放

已冻结数据的重放不得调用 FKQT、Jev 或 LLM：

```python
from datetime import date
from pathlib import Path

from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
from fkqt_jevinvestor.persistence.market_repository import MarketSnapshotRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory

decision_date = date.fromisoformat("2026-09-25")
content_hash = "<64位内容哈希>"
snapshot = MarketSnapshotStore(Path("data/snapshots")).load(decision_date, content_hash)

engine = create_engine("sqlite+aiosqlite:///./data/fkqt_jevinvestor.db")
repository = MarketSnapshotRepository(create_session_factory(engine))
stored = await repository.load_run_inputs(decision_date, content_hash)

assert snapshot.content_hash == stored.reference.content_hash
features = stored.features
await engine.dispose()
```

回测侧把 `snapshot.decision_date`、`snapshot.decision_cutoff`、`snapshot.next_trade_date`、`snapshot.universe_snapshot_hash`、内容哈希和 `features` 组装为冻结的 `ReplayDay`。D+1 执行行情必须来自交易日历确认的执行日快照。

## 9. 错误与排查

| 错误码 | 含义 | 处理 |
|---|---|---|
| `FKQT_MANIFEST_BUNDLE_ROOT_REQUIRED` | CLI 未配置 bundle 根目录 | 设置 `FKQT_MANIFEST_BUNDLE_ROOT` |
| `DATASET_MANIFEST_MISSING` | 缺少 manifests 目录或必需 Manifest | 补齐六类 Manifest |
| `DATASET_MANIFEST_AMBIGUOUS` | 同类数据出现多个 Manifest | 每类只保留当前决策日的唯一 Manifest |
| `DATASET_AS_OF_DATE_MISMATCH` | Manifest 日期不等于 D | 使用对应决策日导出 |
| `DATASET_HASH_MISMATCH` | Parquet 内容与 Manifest 不一致 | 重新完整导出，不修改原文件 |
| `DATASET_SCHEMA_HASH_MISMATCH` | 物理 Schema 漂移 | 核对 FKQT 导出版本 |
| `DATASET_ROW_COUNT_MISMATCH` | 行数与 Manifest 不一致 | 重新导出 bundle |
| `DATASET_STORAGE_PATH_INVALID` | storage_path 越出 bundle 根目录 | 修复为 bundle 内相对路径 |
| `DATASET_VERSION_UNSUPPORTED` | 数据版本不是 `TUSHARE_PRO_V1` | 使用受支持导出版本 |
| `DAILY_VOLUME_UNIT_MISMATCH` | 成交量单位不是 `TUSHARE_100_SHARES` | 修正导出元数据和数据单位 |
| `POINT_IN_TIME_VIOLATION` | 出现未来数据或 cutoff 不属于 D | 拒绝整批，修复上游数据 |
| `NEXT_TRADE_DATE_UNAVAILABLE` | 日历没有明确 D+1 | 补齐交易日历，不用自然日推算 |
| `TRADING_CALENDAR_INCOMPLETE` | D 到 D+1 之间存在日历缺页 | 补齐包含休市日在内的逐日历日期记录 |
| `UNIVERSE_SYMBOL_MISMATCH` | 请求 symbols 不等于冻结候选池成员 | 使用候选池原始成员集合 |
| `SYMBOL_COVERAGE_MISMATCH` | 候选池与行情/状态覆盖不一致 | 补齐所有候选证券 |
| `UPSTREAM_AUTH_FAILED` | REST 认证失败 | 更新 Secret 或权限 |
| `UPSTREAM_RATE_LIMITED` | REST 限流 | 降低调用频率并按上游策略重试 |
| `UPSTREAM_UNAVAILABLE` | REST 网络或 5xx | 保留失败记录，恢复后重新发起正式运行 |
| `UPSTREAM_SCHEMA_INVALID` | REST 响应不符合 v1 Schema | 对齐上游版本 |
| `UPSTREAM_HASH_MISMATCH` | REST 内容哈希校验失败 | 拒绝使用并检查上游序列化 |
| `SNAPSHOT_HASH_CONFLICT` | 同哈希路径已有不同内容 | 停止运行并调查存储损坏 |
| `MARKET_SNAPSHOT_CONFLICT` | 同 snapshot ID 出现不同内容 | 停止运行并调查上游非确定性 |
| `MARKET_SNAPSHOT_NOT_FOUND` | 查询的快照不存在 | 核对 snapshot ID、日期和哈希 |

## 10. 自检清单

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| 文件不可变 | 同内容重复保存不改变文件 | 重复运行 freeze，比较内容哈希 | 必须 |
| 防前视 | 所有 bar 日期 `<= D` | 检查快照 JSON | 必须 |
| D+1 | 来自交易日历 | 检查 `next_trade_date` | 必须 |
| 特征精度 | 8 位 Decimal | 查询 features API | 必须 |
| 缺失语义 | `value` 与 `missing_reason` 恰有一个 | 查询数据库或 API | 必须 |
| 横截面范围 | `0.00000000` 至 `1.00000000` | 查询三类 percentile | 必须 |
| 数据库 Revision | `0005_phase2_audit_hardening (head)` | `alembic current` | 必须 |
| Secret | Git 历史中不存在 | `git grep -n "Bearer "` 并人工核对 | 必须 |

## 11. 已知限制

1. CLI 第一版只直接装配 Manifest Provider；REST Provider 由应用启动层注入。
2. `turnover_pct` 缺少流通股本时记录 `FLOAT_SHARES_UNAVAILABLE`，不以零替代。
3. PyArrow 23.0.1 仍需要开发依赖 `pyarrow-stubs` 辅助严格类型检查。
4. Starlette TestClient 当前会产生 AnyIO 旧别名弃用警告，不影响业务结果。
5. `TargetProvider` 只接收不含 D+1 行情的 `DecisionReplayDay`；完整 `ReplayDay.execution_market` 只能传给 `ExecutionPort`。
6. `backtest-contract-v1` 必须在 Contract PR 经非作者 Review、合并后发布；未发布前 Contributor 不应从临时提交开工。
