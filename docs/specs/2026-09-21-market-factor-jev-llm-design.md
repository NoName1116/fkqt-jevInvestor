# fkqt-jevInvestor 独立架构设计

日期：2026-09-21

状态：用户已确认

适用范围：独立仓库初始化、Phase 0/1 迁移、Phase 2 至 Phase 6

需求基线：`REQUIREMENTS.md` v0.3

## 1. 执行摘要

`fkqt-jevInvestor` 是独立于 FKQT 的纯 Python A 股日频模拟决策系统。它复用 FKQT 提供的候选池、行情、交易日历和证券结构化状态，但不导入 FKQT 内部 Python 模块、不访问 FKQT 可写数据库、不调用旧交易链。

第一版主链路为：确定性行情特征 → Jev 类型化概率 → LLM 离散动作 → 确定性仓位 → 虚拟成交与持仓账本。LLM 只决定 `ENTER/KEEP/EXIT/AVOID`，Jev 只输出概率，所有数值计算和仓位均由代码完成。

第一版只交付 FastAPI、CLI、回测与模拟账本，不开发前端。未来 UI 通过版本化 REST API 接入，可复用 FKQT 前端的视觉和组件，也可以建设独立前端；前端不进入当前纯 Python 仓库边界。

## 2. 仓库与所有权

| 项目 | 决策 |
|---|---|
| GitHub 仓库名 | `fkqt-jevInvestor` |
| 仓库可见性 | Public |
| Python 分发名 | `fkqt-jevinvestor` |
| Python 包名 | `fkqt_jevinvestor` |
| 默认分支 | `main` |
| 第一版语言 | Python 3.12 |
| 协作方式 | Collaborator + Pull Request + Review |
| FKQT 角色 | 外部只读数据上游 |
| 券商角色 | 不存在 |

公开仓库允许外部 Contributor 通过 Fork 提交 Pull Request；需要直接推分支时，仓库所有者再邀请其成为 Collaborator。`main` 禁止直接提交；每项功能使用分支和 Pull Request，自动化检查通过且至少 1 名非作者 Review 后合并。Secret 只通过本地环境变量或 GitHub Actions Secrets 注入。

## 3. 系统边界

```text
┌──────────────────────────── FKQT ────────────────────────────┐
│ 候选池 │ 日线 │ 交易日历 │ 停复牌 │ ST/退市 │ 涨跌停 │ 公司行为 │
└───────────────────────┬─────────────────────────────────────┘
                        │ REST API 或不可变文件快照
                        ▼
┌────────────────── fkqt-jevInvestor ─────────────────────────┐
│ Ingestion Adapter                                            │
│   └─ Schema 校验、Point-in-Time 过滤、内容哈希、不可变落地    │
│                         ▼                                    │
│ Deterministic Feature Engine                                 │
│   └─ 趋势、动量、反转、波动、成交、流动性、跳空、横截面       │
│                         ▼                                    │
│ Jev Probability Provider                                     │
│   └─ 候选池风险、个股方向、趋势延续、成交确认、过热风险        │
│                         ▼                                    │
│ Decision LLM Provider                                        │
│   └─ ENTER / KEEP / EXIT / AVOID / NO_SIGNAL                 │
│                         ▼                                    │
│ Deterministic Position Sizer                                 │
│   └─ 波动率、流动性、相关性、上限、现金、整手                  │
│                         ▼                                    │
│ Virtual Execution + Portfolio Ledger                         │
│   └─ D+1 开盘、费用、滑点、T+1、持仓、NAV                    │
│                         ▼                                    │
│ FastAPI + CLI + Replay + Experiment Report                   │
└──────────────────────────────────────────────────────────────┘
```

### 3.1 明确禁止的耦合

- 不使用 `sys.path` 指向 FKQT。
- 不把 FKQT 安装为 editable package。
- 不从 `agent.*`、旧服务层或旧 ORM 导入类型。
- 不连接 FKQT 的可写数据库。
- 不通过共享内存或进程内单例交换行情。
- 不让 FKQT 调用新系统的持仓写接口。

## 4. FKQT 数据边界

### 4.1 两种传输方式

生产首选 REST；回测、离线开发和灾备使用不可变文件快照。两种方式必须映射到同一领域 Schema，业务层无法感知来源。

```python
class MarketDataProvider(Protocol):
    async def freeze_snapshot(
        self,
        symbols: tuple[str, ...],
        decision_date: date,
        decision_cutoff: datetime,
        lookback_trading_days: int,
    ) -> MarketSnapshot: ...

    async def next_trade_date(self, decision_date: date) -> date: ...
```

### 4.2 上游数据契约

每个响应或文件快照必须包含：

- `schema_version`；
- `source_system`；
- `source_version`；
- `requested_at`；
- `decision_cutoff`；
- `candidate_universe_id`；
- `candidate_universe_hash`；
- `records`；
- `record_count`；
- `content_hash`。

单票日线至少包含 `symbol`、`trade_date`、`open`、`high`、`low`、`close`、`previous_close`、`volume`、`amount_cny` 和 `adjustment_mode`。

证券状态至少包含 `trading_day_status`、`trading_status`、`is_st_or_delisting_risk`、`upper_limit_price`、`lower_limit_price`、`is_initial_no_limit_period`、`corporate_action_status`、`market`、`board` 和 `listing_date`。

### 4.3 快照与离线性

新系统接收数据后先校验 Schema 和哈希，再写入内容寻址的不可变快照。后续特征、模型、回放和回测只读取快照，不重复读取 FKQT。FKQT 不可用时，已冻结交易日仍能完整重放；未冻结交易日返回 `UPSTREAM_UNAVAILABLE`，不得伪造数据。

### 4.4 Point-in-Time

- `trade_date` 不得晚于决策日。
- 证券状态和公司行为的可用时间不得晚于 `decision_cutoff`。
- D+1 由交易日历返回，不允许 `decision_date + 1 day`。
- 快照发现未来记录时整批拒绝，不只删除违规行后继续。
- 横截面排名使用同一个候选池快照，禁止不同证券来自不同候选池版本。

## 5. 确定性行情特征

所有可精确计算的数值均由代码计算。每个特征保存 `feature_code`、`feature_version`、`as_of`、`lookback_window`、`value`、`missing_reason` 和 `source_snapshot_hash`。

| 类别 | 第一版特征 |
|---|---|
| 收益 | `return_1d`、`return_5d`、`return_20d`、`return_60d` |
| 趋势 | `close_vs_ma5`、`close_vs_ma20`、`close_vs_ma60`、`ma5_slope_5d`、`ma20_slope_5d` |
| 波动 | `realized_vol_20d`、`atr_pct_14d`、`downside_vol_20d` |
| 成交 | `volume_ratio_5d_20d`、`amount_ratio_5d_20d`、`turnover_pct` |
| 反转 | `distance_from_20d_high`、`distance_from_20d_low`、`short_term_reversal_3d` |
| 跳空 | `overnight_gap_pct`、`gap_fill_pct` |
| 横截面 | `return_20d_percentile`、`volatility_percentile`、`liquidity_percentile` |

历史不足、非正价格、零成交、复权口径不一致和候选池不完整必须产生稳定缺失原因，不得用零代替。计算结果统一量化为 Decimal 字符串，内容哈希基于字段排序后的 canonical JSON。

## 6. Jev 概率层

Phase 3 详细契约以 `docs/specs/2026-09-21-phase-3-jev-market-state-design.md` 为准。Jev 输入拆为每天复用一次的候选池级 `JevUniverseStateV1` 和按证券调用的个股级 `JevSymbolStateV1`。Jev 读取压缩后的结构化特征，不读取长 K 线数组、新闻正文、持仓、现金或历史模型决策，也不计算技术指标。

候选池级问题：

- `universe_risk_regime`：`RISK_ON/NEUTRAL/RISK_OFF`。

个股级窄问题：

- `direction_regime`：`UP/RANGE/DOWN`；
- `trend_persistence`：`CONTINUE/UNCERTAIN/REVERSE`；
- `volume_confirmation`：`CONFIRMED/AMBIGUOUS/REJECTED`；
- `overheat_risk`：`LOW/MEDIUM/HIGH`；
- `factor_conflict`：`LOW/MEDIUM/HIGH`；
- `data_sufficiency`：`SUFFICIENT/LIMITED/INSUFFICIENT`。

这些概率表达对当前状态及其延续倾向的分类判断，不得解释为真实未来涨跌概率。输出必须保存完整概率分布、问题版本、判定标准版本、模型标识、请求哈希、响应哈希、延迟和状态。凭据缺失、超时或限流记录 `PROVIDER_UNAVAILABLE`；Schema 或概率无效记录 `CONTRACT_INVALID`；必要输入不足记录 `DATA_UNAVAILABLE`。失败状态不得包含合成概率。

Jev 不输出连续因子权重、仓位、收益率、价格或交易动作。动态权重如有需要，只能由版本化确定性代码将候选池状态概率映射到预定义权重 Profile。

## 7. LLM 离散决策

```text
ENTER：当前无仓位，允许建立仓位。
KEEP：当前有仓位，继续保留持仓资格。
EXIT：当前有仓位，目标归零。
AVOID：当前无仓位，继续空仓。
NO_SIGNAL：Provider 失败或输出无效，保持当前状态。
```

无仓位时只接受 `ENTER/AVOID/NO_SIGNAL`；有仓位时只接受 `KEEP/EXIT/NO_SIGNAL`。

```json
{
  "action": "ENTER",
  "thesis": "趋势延续和成交确认一致。",
  "invalidation": "趋势延续转弱且成交确认消失。"
}
```

Schema 中不得出现 `target_position_pct`、`quantity`、`price`、`stop_loss_pct`、`take_profit_pct`、`cash_target_pct` 或任何资金比例字段。同一正式输入哈希只保存一次正式决策；Provider 失败不调用备用模型静默替代。

## 8. 确定性仓位与虚拟执行

仓位引擎根据离散动作、当前组合、波动率、流动性、相关性、单票上限、组合上限、现金和 A 股整手规则计算目标权重和数量。公式必须版本化、确定、可重放。

- `ENTER`：计算非零目标，约束失败可阻止建立仓位。
- `KEEP`：按同一公式重新计算，允许机械性增减。
- `EXIT`：目标为零。
- `AVOID`：目标为零。
- `NO_SIGNAL`：保持现有数量，不触发自动清仓。

风险规则可以阻止动作，但不得反转方向。D 日信号只在交易日历确认的 D+1 开盘尝试一次；应用停牌、涨跌停、T+1、滑点、手续费和容量规则，未成交部分终态失效。

## 9. 数据、审计与重放

每次正式运行保存：

- 候选池版本与哈希；
- 上游快照与内容哈希；
- 决策截止时间和下一交易日；
- 特征值、版本和缺失原因；
- Jev 请求、概率与调用状态；
- LLM 输入哈希、模型、Prompt 版本、原始响应和标准化动作；
- 仓位公式版本、输入哈希、目标权重和数量；
- 决策前持仓；
- 虚拟订单、成交、费用和失败原因。

Record Replay 只读冻结记录，不调用 FKQT、Jev 或 LLM。Model Re-evaluation 使用冻结输入创建新实验分支，不覆盖原结果。Secret、数据库 URL、完整认证头和 SDK 调试堆栈不得写入数据库、日志或 API 响应。

## 10. 四组实验

| 实验 | 决策链 | 目的 |
|---|---|---|
| A | 确定性因子 → LLM → 仓位引擎 | 无 Jev 的 LLM 基线 |
| B | 确定性因子 → Jev → LLM → 仓位引擎 | 测量 Jev 增量 |
| C | 确定性因子 → Jev 概率 → 确定性动作映射 → 仓位引擎 | 测量最终 LLM 增量 |
| D | 确定性因子 → 规则方向 → 仓位引擎 | 纯确定性基线 |

四组固定候选池、行情快照、截止时间、仓位公式、交易成本、执行规则和评估区间。Jev 或 LLM 只有在样本外数据上相对 D 组产生可重复增量才保留。

## 11. API、CLI 与未来前端

第一版 FastAPI 统一使用 `/api/v1`，提供健康检查、上游快照导入、实验运行、运行详情、信号、组合、NAV 和只读重放。CLI 提供同等的导入、运行、重放和验收入口，保证没有前端也能完整操作。

未来前端是 API Consumer，不直接访问数据库或模型 Provider。若复用 FKQT 前端，只复用可独立授权和抽取的 UI 组件及视觉规范，不把旧交易状态管理复制进新系统。若独立开发前端，应放在单独仓库或后续明确批准的 `web/` 工程中；两种方式均不属于第一版。

## 12. 迁移策略

已完成的 Phase 0/1 代码包含 Provider 隔离、Jev SDK 契约、组合、FIFO 持仓批次、虚拟订单、费用、T+1、NAV、幂等和审计快照。新仓库不得重写这些能力，采用“复制后验证”的迁移：

1. 新仓库先建立包结构、配置和迁移基线。
2. 按功能复制 Phase 0/1 Python 文件及测试，修正包名和表命名。
3. 使用原 Fixture 验证相同现金、持仓、成交、费用和 NAV。
4. 验证迁移前后同一输入的关键业务哈希与终态一致。
5. 新仓库验收通过前保留旧 `ai-signal-lab`。
6. 删除旧目录必须由用户在迁移验证后再次确认。

Git 历史不直接混入 FKQT 主仓库历史；新仓库从清晰的初始提交开始，设计文档记录来源提交 `4210322` 和 `5e43ea8`，保留可追溯性。

## 13. 阶段划分

### Phase M：独立仓库与 Phase 0/1 迁移

建立纯 Python 仓库、CI、分支保护说明，迁移既有 Provider、账本、执行、迁移和测试。验收为原 Phase 0/1 Fixture 在新包名下产生一致结果。

### Phase 2：FKQT Adapter 与确定性特征

实现 REST/文件双 Adapter、不可变行情快照、交易日历、证券状态、特征计算、横截面排名、缺失处理和 Point-in-Time 测试。

Phase 2 的第一个交付必须冻结 `backtest-contract-v1`：核心团队提供 `ReplayDay`、`TargetPositionBatch`、`ReplayDataProvider`、`TargetProvider` 和 `ExecutionPort`。该契约合并后，回测 Contributor 可以使用内存 Fixture 并行开发；文件 Adapter 完成后再进行真实快照集成。

### Phase 3：Jev 行情概率

实现候选池级与个股级 Jev 状态、窄问题、概率持久化、失败语义、调用审计和真实凭据门控测试。候选池状态在同一正式运行中只调用一次并供全部个股复用；个股状态按证券独立调用。

### Phase 4：LLM 离散动作与仓位引擎

实现 LLM Provider、版本化 Prompt、离散动作 Schema、确定性仓位公式和虚拟订单转换。

### Phase 5：日频自动运行

实现收盘决策、D+1 执行、运行恢复、幂等重跑和监控。

### Phase 6：A/B/C/D 对照实验

实现四条互斥链、统一样本外数据集、概率校准和交易指标报告。

回测引擎本身不识别 Jev 或 LLM。A/B/C/D 通过不同 `TargetProvider` 接入同一引擎，防止各实验组拥有不同回测实现。

## 14. 回测 Contributor 边界

回测开发由 Contributor 沙耶负责，详细契约位于 `docs/contributors/2026-09-21-backtesting-development-contract.md`。核心团队拥有 `domain/`、`ingestion/`、`services/`、`persistence/` 和已冻结 Protocol；Contributor 拥有 `backtest/`、`tests/backtest/` 和回测运行手册。

Contributor 不直接调用 FKQT、Jev、LLM、FastAPI 或 SQLAlchemy。她实现纯 Python 时间推进、预热期、逐日执行、指标累计、A/B/C/D 批量运行和 canonical JSON 结果。任何契约变更必须先单独提交 Contract PR，不能在回测实现 PR 中顺带修改核心 Entity。

## 15. 错误处理

| 条件 | 行为 |
|---|---|
| FKQT 未连接且无冻结快照 | `UPSTREAM_UNAVAILABLE`，不运行 |
| 上游 Schema 或哈希无效 | 整批拒绝，不部分导入 |
| 快照包含未来数据 | `POINT_IN_TIME_VIOLATION`，整批拒绝 |
| 交易日历未知 | 整批拒绝排期 |
| 必要特征不足 | 证券记录 `DATA_UNAVAILABLE`，不进入模型 |
| Jev 不可用 | B/C 组失败，不伪造概率、不跨组降级 |
| LLM 不可用 | A/B 组返回 `NO_SIGNAL`，保持持仓 |
| 仓位约束无法满足 | 整批仓位方案失败，不部分提交 |
| D+1 不可成交 | 记录终态失败，不跨日追单 |
| 重复正式请求 | 返回已保存结果，不重复调用 Provider |

## 16. 测试与验收

测试分为领域单元测试、上游契约测试、数据库迁移测试、跨两交易日集成测试、记录重放测试、Provider Live Test 和样本外实验。日常任务只运行最小相关测试；阶段结束运行一次完整离线套件、Ruff、Pyright 和 Alembic 升降级。

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| 仓库隔离 | 不导入 FKQT 内部模块 | 依赖扫描与源码搜索 | 必须 |
| 离线重放 | FKQT/Jev/LLM 调用均为 0 | Record Replay 集成测试 | 必须 |
| LLM 仓位字段 | 0 个 | Schema 字段扫描 | 必须 |
| 精确指标计算 | 全部由代码完成 | 单元测试与源码检查 | 必须 |
| 未来数据 | 0 条进入 D 日决策 | Point-in-Time 时间旅行测试 | 必须 |
| 下一执行日 | 交易日历返回值 | 周末与节假日集成测试 | 必须 |
| Jev 正式 Mock | 0 条 | Provider 失败测试 | 必须 |
| 重复正式调用 | 新外部调用 0 次 | 输入哈希幂等测试 | 必须 |
| 方向反转 | 0 次 | 风险阻断契约测试 | 必须 |
| 仓位重放 | 同输入完全相同 | 固定 Fixture 重放测试 | 必须 |
| 资产恒等式 | 误差不超过 0.01 CNY | D+1 执行集成测试 | 必须 |
| Phase 0/1 迁移 | 关键终态与原 Fixture 一致 | 新旧结果对照测试 | 必须 |
| Secret 入库 | 0 个 | Git 历史与配置扫描 | 必须 |
| 回测防前视 | D 决策不读取 D+1 行情 | 时间旅行失败测试 | 必须 |
| 实验公平性 | A/B/C/D 共享数据与执行配置 | 配置哈希一致性测试 | 必须 |

## 17. 目标目录结构

```text
fkqt-jevInvestor/
├── .github/
│   ├── workflows/ci.yml
│   └── pull_request_template.md
├── docs/
│   ├── specs/
│   ├── plans/
│   └── runbooks/
├── migrations/
│   └── versions/
├── src/fkqt_jevinvestor/
│   ├── api/
│   ├── backtest/
│   ├── cli/
│   ├── domain/
│   ├── ingestion/
│   ├── providers/
│   ├── services/
│   └── persistence/
├── tests/
│   ├── contract/
│   ├── integration/
│   ├── live/
│   └── unit/
├── REQUIREMENTS.md
├── findings.md
├── task_plan.md
├── pyproject.toml
└── README.md
```

## 18. 非目标与已知风险

第一版不接券商、不开发前端、不做分钟或 Tick 交易、不建设新闻语义层、不训练模型。Jev 尚无公开 A 股行情预测基准；LLM 离散动作仍可能抖动；横截面结果依赖候选池完整性；复权口径错误会污染特征；仓位公式可能比模型方向更影响收益。上述风险必须通过冻结输入、严格契约、确定性基线和样本外实验暴露，不能靠叙述判断系统有效。
