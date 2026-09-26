# 前向公司行为账本 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 C 组加入税前现金分红和送转的可审计权益账本，不让除权除息破坏净值。

**Architecture:** FKQT 发布冻结事件，JEV 从登记日持仓冻结权益，以独立事件阶段记录实现幂等；同一事务更新权益、FIFO Lot、成交和 NAV。旧回测契约不变，有新权益时旧导出必须拒绝有损转换。

**Tech Stack:** Python 3.12、Decimal、Pydantic、SQLAlchemy、Alembic、PyArrow、pytest。

**Spec:** `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/superpowers/specs/2026-09-26-forward-operations-design.md`

## Global Constraints

- 实施顺序 T6–T9，依赖基础计划 T1–T5；映射 R58–R60、R12、R21、R48。
- 固定 GROSS_V1，现金采用税前每股值；不宣称税后收益。金额 0.01 CNY ROUND_HALF_UP，FIFO 成本 0.00000001。
- 配股、换股、零碎股缺实际分配资料、缺登记日历史均整日停止。不能推算或悄悄舍弃权益。
- 新红股取得日继承原 Lot，上市日单独限制可卖；未支付分红不进入可用买入资金。
- 不编辑旧 Alembic revision，不改变 backtest-contract-v1，不修改沙耶目录。
- 工作区与 `$pythonExe` 使用基础计划已定义绝对路径；所有文件路径按对应仓库根展开。

## Review Focus

1. 登记后卖光仍有派息权利：保留权益证券，不依赖当前持仓，T6/T7 覆盖。
2. 除息后新买股票：不得拿到此前登记分红，T7 覆盖。
3. 红股上市日并非取得次日：不能沿用普通 T+1 解锁，T7 覆盖。
4. 日内途中崩溃并重跑：收入、数量、NAV 不能重复，T8/T9 覆盖。
5. FIFO Lot 被数据库替换重建：权益引用须保持稳定快照，不依赖会变的 Lot ID，T8 覆盖。

## Task T6：公司行为 Manifest

文件：FKQT 新增 `market_data/corporate_action_publisher.py`、`tests/test_corporate_action_publisher.py`，修改 forward_cli；JEV 新增 `src/fkqt_jevinvestor/domain/corporate_actions.py`、`ingestion/fkqt_corporate_action_manifest.py`、`tests/contract/test_corporate_action_manifest.py`。

接口：`CorporateActionEventV1(event_id, symbol, source_version, available_at, record_date, ex_date, pay_date, share_list_date, cash_per_share_gross, bonus_ratio, tax_policy_version)`；`CorporateActionManifestV1(trade_date, requested_symbols, coverage_complete, events, source_manifest_id, source_content_hash, content_hash)`；FKQT `publish_corporate_actions(client, store, *, as_of_date: str, symbols: tuple[str, ...]) -> DatasetManifest`；JEV `load_corporate_actions(root: Path, trade_date: date, cutoff: datetime) -> CorporateActionManifestV1`。

- [ ] 写 `test_implemented_events_require_dates_gross_amount_and_full_coverage`、`test_pagination_and_empty_response_are_not_silently_complete`、`test_future_disclosure_and_manifest_tamper_fail`：每批上游达到返回上限时需继续分页或判为不完整；有现金必须 pay_date，有送转必须 share_list_date；缺字段非零不允许转零。
- [ ] 两仓库运行各自新增文件，确认发布/读取接口缺失失败。
- [ ] FKQT 通过独立采集函数调用 dividend，完整覆盖证券事件，不改旧 get_execution_context 的语义。实现日期与非负 Decimal 校验、实施状态过滤、来源哈希、稳定事件 ID。仅日期级披露信息不能被当成精确盘中时间；真实抓取可用时间作为首次冻结 availability，历史复盘若缺可信时间不得宣称严格 Point-in-Time。
- [ ] 通过新增契约后，两仓库分别提交 `功能：冻结分红送转公司行为事件`。

## Task T7：纯领域权益与 FIFO

文件：新增 JEV `src/fkqt_jevinvestor/services/corporate_action_ledger.py`、`tests/unit/test_corporate_action_ledger.py`；修改 `services/portfolio_service.py`、`domain/portfolio.py`、`domain/decision.py`、`services/position_sizing.py`、`services/jev_state_builder.py` 中读取组合字段的兼容分支。

接口：`EntitlementV1(event_id, symbol, record_date, lot_allocations, entitled_quantity, cash_receivable, bonus_quantity, paid, shares_listed)`；`freeze_entitlement(event: CorporateActionEventV1, ledger: PortfolioLedger, record_date: date) -> EntitlementV1`；`apply_entitlement_stage(ledger: PortfolioLedger, entitlement: EntitlementV1, event: CorporateActionEventV1, *, stage: str, effective_date: date) -> EntitlementV1`。

Lot 新增 `available_on: date | None = None`，原 acquired_on < trade_date 的 T+1 规则仍成立，且 available_on 不为空时要求 trade_date >= available_on。Portfolio/快照新增零默认 receivable/dividend_income；不移除旧字段，新字段不得进入 Jev 输入。

- [ ] 写 `test_cash_dividend_receivable_then_payment`：1000 股每股 0.20，除息日应收 200.00，可用现金不变；派息日现金加 200.00，应收归零，总权益不因支付重复上升。写 `test_sold_after_record_remains_entitled_and_bought_after_record_is_not`。
- [ ] 写 `test_bonus_maintains_total_cost_and_respects_listing`：1000 股成本 10000.00000000、送转比例 0.1，1100 股总成本不变，上市前新增 100 不可卖；分摊成本余数放最后一个 Lot 保持总和精确。写 `test_fractional_bonus_stops_instead_of_rounding`：1001 × 0.1 为非整数，拒绝。写 `test_nav_and_sizing_include_receivable_but_not_spendable_cash`。
- [ ] 运行 `& $pythonExe -m pytest tests/unit/test_corporate_action_ledger.py -q`，确认新领域接口缺失失败。
- [ ] 实现权益阶段及资产公式；bonus 采用登记 Lot 数量而非除权日现仓。登记至除权之间无法证明历史时拒绝，卖出后可产生新锁定红股持仓。C 组持仓输入以新总资产算权重；模型仍不输出数值。保留旧纯无权益快照哈希分支，不将全零新字段写入旧 canonical payload。
- [ ] 通过新增测试、旧 portfolio_ledger/position_sizing/jev_state_builder 定点回归后提交 `功能：加入分红应收与锁定红股账本`。

## Task T8：持久化与事务

文件：新增 `migrations/versions/0008_forward_corporate_actions.py`、`src/fkqt_jevinvestor/persistence/corporate_action_repository.py`、`tests/integration/test_corporate_action_persistence.py`；修改 models/repositories 和旧模型输入/快照持久化适配。

表：`ai_signal_corporate_action_event`、`ai_signal_corporate_entitlement`、`ai_signal_corporate_stage`。事件按来源版本不可变；权益按 portfolio/event 唯一，stage 按 portfolio/event/stage 唯一。权益保存完整登记 Lot 的取得日期、登记数量、成本分配与内容哈希，不外键引用会被 replace_positions 删除的 Lot。Lot 新增 nullable available_on；组合与快照增加 0 默认应收及分红收入。

接口：`CorporateActionRepository.freeze_record_date(session: AsyncSession, portfolio_id: str, event: CorporateActionEventV1, ledger: PortfolioLedger) -> EntitlementV1`；`apply_due_stages(session, portfolio_id, trade_date, ledger) -> tuple[str, ...]`；`has_outstanding_entitlements(session, portfolio_id) -> bool`；`required_symbols(session, portfolio_id) -> set[str]`。不得在这些方法内另开事务或 commit。

- [ ] 写 `test_entitlements_survive_lot_replacement_and_sellout`、`test_stage_replay_preserves_cash_quantity_and_income`、`test_crash_rolls_back_entitlements_fills_nav_and_version`、`test_pending_entitlement_blocks_cash_only`；写旧账户迁移后所有新增金额为 0、available_on 为 null 的断言。
- [ ] 定点执行该集成文件，预期缺表/接口失败；实现迁移与 Repository，接入原 execute_trade_date 同事务，不复制执行引擎。
- [ ] 更新 ledger restore/save、get_state、快照 JSON、NAV restore，确保重启后应收、红股限制、dividend_income 全部保留。T3 的 Stub 改真实数据库断言。
- [ ] 定点通过后提交 `功能：持久化公司行为权益及原子阶段记录`。迁移完整往返留到最终阶段验收；此任务只验新增迁移能建库及旧库升级。

## Task T9：日执行接入与安全边界

文件：修改 execution_engine、PortfolioRepository、daily CLI、V2 Manifest 消费；新增 `tests/integration/test_daily_corporate_actions.py`、`tests/unit/test_corporate_action_compatibility.py`；更新 daily-runner 手册，不修改 backtest 契约。

接口：`daily prepare-execution --corporate-action-manifest <absolute-path>`；ExecuteTradeDate 新增可选 corporate_actions/source_hash，旧调用默认空。required-symbols 输出当前持仓、到期订单、未上市红股和存续现金权益证券的并集。

- [ ] 写 `test_ex_date_expires_old_orders_before_open`：受影响订单终态 `CORPORATE_ACTION_ORDER_EXPIRED`，无该单成交，其他证券沿用旧执行逻辑；写 `test_record_after_execution_pay_and_list_before_execution`，固定登记在收盘、支付/解锁在当日成交前。
- [ ] 写 `test_nontrading_pay_date_settles_on_next_run_with_original_effective_date`、`test_missing_record_day_or_fractional_event_halts_whole_day`、`test_legacy_export_rejects_outstanding_rights`；有权益时旧 frozen_c_group_target 导出不得损失资产，旧纯账户回归结果不变。
- [ ] 运行新增文件，确认未接入时失败；实现端到端时序，完整事件列表 hash 绑定 V2，缺事件覆盖或版本冲突整事务回滚。原无事件 V1 路径保持。
- [ ] 定点通过后提交 `功能：日频执行接入分红送转并保持旧契约安全`；完成后可进入运行编排计划。

## 自检与已知限制

| 检查项 | 标准值 | 方法 | 优先级 |
|---|---|---|---|
| 权益正确 | 登记后卖光仍支付，除息后买入不获旧权益 | T7/T8 | 必须 |
| 股票限制 | 红股上市前不可卖；成本总和精确 | T7/T9 | 必须 |
| 原子幂等 | 故障后 0 部分提交，重放 0 重复收入 | T8/T9 | 必须 |
| 旧契约 | 无权益旧输入结果不变，有权益旧导出拒绝 | T9 | 必须 |
| 税务标识 | 所有新运行/报告为 GROSS_V1 | T6/T9 | 必须 |

零碎股、配股、换股、历史权益不完整属于明确停止条件，并非支持完成；文档必须保留该限制。实现后给沙耶提供新增权益兼容说明，不自动改她的分支。
