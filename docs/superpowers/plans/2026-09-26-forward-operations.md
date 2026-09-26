# 前向编排与真实验证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用外部触发的一次性运行器完成重试、恢复、告警和真实小队列验证。

**Architecture:** 独立 operations 模块只通过 CLI 子进程协调两个仓库，业务状态以数据库记录为准。Windows Task Scheduler 是外部触发来源；Webhook 可选且默认关闭。真实权限与历史重放、真正前向分别报告。

**Tech Stack:** Python 3.12 subprocess、HTTPX、SQLAlchemy、Alembic、PowerShell Task Scheduler、pytest。

**Spec:** `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/superpowers/specs/2026-09-26-forward-operations-design.md`

## Global Constraints

- T10–T14；依赖前两计划 T1–T9，映射 R56/R57/R60 与 R24/R25/R46/R48/R49。
- 不复制 FKQT 源码，不导入其内部包。子进程 shell=False、argv 数组、显式 cwd、绝对解释器路径。
- 密钥只从已有本地配置读取，不写 Git、不输出 Secret 对象；账号权限不购买、不绕过。
- 只生成系统任务安装脚本，不自动注册/启动计划；Webhook 未显式配置时不向外发通知。
- 同组合先执行当日 NAV 再收盘决策，历史缺口按冻结日历顺序恢复，不直接跳到今天。
- 真实历史采集当前 availability 不得伪装为过去可用；不满足 Point-in-Time 的资料只做 plumbing/replay，不报告严格回测或前向。

## Review Focus

1. 子进程错误输出包含 Token/URL 凭据：必须脱敏后入日志，T10/T13 覆盖。
2. 进程死亡而业务已提交：恢复需查询实际 NAV/订单再确定阶段，T11 覆盖。
3. 网络告警发送成功后本地确认失败：事件 ID 固定，接收侧需去重，不承诺分布式 exactly-once，T12 覆盖。
4. 周末与节假日外部任务触发：只报告 CLOSED，不生成订单/NAV，T11 覆盖。
5. 当前真实接口成功但未来日数据未发生：报告 LIVE_FORWARD_PENDING，不替换为历史成功，T13 覆盖。

## Task T10：配置和安全子进程

文件：新增 `$jevRoot/src/fkqt_jevinvestor/operations/__init__.py`、`config.py`、`process.py`、`tests/unit/test_forward_process.py`、`tests/unit/test_forward_operation_config.py`；示例 `config/forward.example.json` 不含真实凭据。

接口：`ForwardOperationConfig(portfolio_id: str, start_date: date, fkqt_cwd: Path, fkqt_python: Path, jev_cwd: Path, jev_python: Path, data_root: Path, candidate_source: Path, candidate_limit: int, readiness_deadline_local: time, max_attempts: int = 4, process_timeout_seconds: int = 300, max_run_seconds: int = 600, webhook_url: SecretStr | None = None)`；ready deadline 按 Asia/Shanghai 当日时间解释，必须显式提供。`run_cli(argv: tuple[str, ...], cwd: Path, *, timeout_seconds: int, env: Mapping[str, str]) -> ProcessOutcome`，后者含 exit_code、safe_stdout、safe_stderr、timed_out、duration_seconds。

- [ ] 写 `test_cli_uses_argv_no_shell_and_explicit_cwd`、`test_timeout_kills_child_and_limits_logs`、`test_secrets_redacted_from_stdout_stderr_config_and_exception`、`test_absolute_paths_and_positive_capacity_required`。日志截断到每流 64 KiB，保留结构化退出结果；配置里不能写 API Key。
- [ ] 运行两个新增 unit 文件，确认接口缺失失败；实现标准库 subprocess 与超时回收，不在代码导入时启动进程。生产命令无凭据 argv，环境按最小白名单继承必要本地配置。
- [ ] 参数校验执行路径与 cwd 存在，不自动安装依赖；Webhook HTTPS，URL 中不得用户名密码，授权 Secret 单独 env 传入。
- [ ] 定点通过后提交 `功能：新增安全的一次性前向进程调用`。

## Task T11：运行状态、租约、重试和恢复

文件：新增 `operations/runner.py`、`persistence/forward_operation_repository.py`、`migrations/versions/0009_forward_operations.py`、`tests/integration/test_forward_operations.py`，修改 models 和 CLI 增加 `forward run --config --date`、`forward status --config --date`。

接口：`ForwardOperationRepository.acquire_lease(portfolio_id: str, *, owner_id: str, now: datetime, lease_seconds: int) -> bool`；`record_stage(...)` 保存唯一 portfolio/date/stage/config_hash、输入哈希、状态、attempts、next_retry_at；`ForwardRunner.run_once(config: ForwardOperationConfig, trade_date: date, now: datetime) -> ForwardRunOutcome`。Outcome 字段 status、completed_stages、error_code、next_retry_at。

阶段：CALENDAR、EXECUTION_PUBLISH、EXECUTION_PREPARE、EXECUTE、CANDIDATE_PREPARE、DECISION_PUBLISH、DECIDE、VERIFY。每阶段的具体 argv 在实现中匹配 T1–T9 新 CLI，测试固定调用顺序。首次运行候选原文件归档，不修改选股内容。

- [ ] 写 `test_execute_and_nav_precede_decision`、`test_closed_day_has_no_business_writes`、`test_lease_prevents_parallel_runs_and_expiry_recovers`、`test_crash_after_nav_commit_does_not_reexecute`、`test_missing_intermediate_day_blocks_current_decision`。Stub 子进程记录 argv，数据库真实持久化阶段。
- [ ] 写 `test_only_transient_errors_retry`：DATA_NOT_READY、HTTP 429、网络临时失败退避 60/300/900 秒，最多 4 次；凭据/Schema/哈希/权益缺失永久失败；deadline 到期仍未齐备报警。可重试返回 code 3 RETRY_PENDING，硬失败 code 2，完成或 CLOSED code 0。
- [ ] 运行该集成文件，确认功能缺失失败；实现 CAS 租约续期、release、数据库唯一约束与业务事实校对。单次总时限 600 秒，不阻塞等待下一 retry 时刻，最晚就绪时限按配置显式给出。
- [ ] 真实 Provider 失败时检查持久化状态，即使业务 CLI 返回 0 但 Provider unavailable，也不得认定健康成功；报告 DEGRADED 并告警，同时遵守 NO_SIGNAL/持仓保持既有语义。
- [ ] 定点通过后提交 `功能：新增前向编排状态与可恢复重试`。

## Task T12：告警 outbox 与外部调度脚本

文件：新增 `operations/alerts.py`、`tests/unit/test_forward_alerts.py`、`tests/integration/test_forward_alert_outbox.py`、`scripts/forward-task.ps1`、`scripts/install-forward-task.ps1`、`scripts/uninstall-forward-task.ps1`、`docs/runbooks/forward-operations.md`；outbox 表和约束在 T11 新迁移定义。

接口：`AlertEvent(event_id, portfolio_id, trade_date, stage, error_code, severity, recovered, created_at)`；`enqueue_alert(session, event) -> None`；`dispatch_outbox(config, *, limit: int = 20) -> AlertDeliverySummary`。事件 ID = portfolio/date/stage/error/episode 的稳定哈希；恢复事件同 episode 单独 ID。Webhook body 不含密钥、持仓明细、原始 Provider 响应。

- [ ] 写 `test_unchanged_error_enqueues_once_and_recovery_once`、`test_webhook_failure_stays_in_outbox`、`test_disabled_webhook_only_writes_local_jsonl`、`test_duplicate_delivery_preserves_event_id`；数据库记录 pending/delivered，HTTP 失败保留重试。
- [ ] 运行新增测试，确认缺实现失败；实现本地 JSONL/退出码与可选 Webhook，明确至少一次投递而非绝对只发送一次；接收侧按 event_id 去重。
- [ ] 脚本参数显式配置绝对路径、隐藏进程、固定 cwd；安装默认工作日 21:15 开始、每 5 分钟一次至 23:00，仅作为触发，日历由运行器判断。任务设置禁止并行实例，失败不无限重启。脚本执行前检查配置与解释器；卸载仅移除指定任务，不删数据库/快照。
- [ ] 验证 PowerShell Parser 无语法错误，测试任务 XML 的路径和触发参数；不执行 Register-ScheduledTask，也不发送外部通知。手册给完整安装、查看、手工运行、卸载命令和预期输出。
- [ ] 定点通过后提交 `功能：加入前向告警与外部调度安装脚本`。

## Task T13：真实权限探针与小队列验收

文件：FKQT 新增 `market_data/permission_probe.py`、`tests/test_permission_probe.py`；JEV 新增 `operations/live_validation.py`、`tests/unit/test_live_validation_report.py`、`tests/live/test_forward_live.py`，复用既有 Jev/DeepSeek live 测试；输出报告放忽略目录 `data/validation/<run_id>/report.json`。

接口：FKQT `probe_permissions(client, *, symbol: str, trade_date: str) -> PermissionProbeReport`；JEV `validate_live(config: ForwardOperationConfig, *, mode: Literal['PROVIDER_CONTRACT', 'REAL_SOURCE_REPLAY', 'LIVE_FORWARD'], trade_date: date) -> LiveValidationReport`；新增 `forward validate --config --mode --date`。只有 --live 显式开关才调用外部。

- [ ] 写 `test_probe_records_each_endpoint_without_token`、`test_missing_key_or_permission_is_not_success`、`test_historical_replay_cannot_claim_forward`、`test_future_session_reports_pending`；逐项字段 endpoint/status/code/latency/source_date/coverage，不打印原响应。
- [ ] 先运行离线新增 tests，确认缺实现失败；实现 daily/trade_cal/stock_basic/namechange/suspend_d/stk_limit/dividend 七项最小请求，不因合法空 namechange/suspension/dividend 响应直接判权限不足，也不因空列表判覆盖完整，保持权限与数据就绪两轴。
- [ ] 核查本地凭据存在性；加载 `.env` 只在进程内，不写环境值至日志；缺任一密钥报告 BLOCKED，继续其他独立探针。已有 Token 缓存如 SDK 创建不作为仓库产物提交。
- [ ] 使用独立模拟账户、显式 2 证券队列运行真实模型契约，再跑已有真实来源可用日的 plumbing 验证。最新抓取历史数据不自动获得历史 Point-in-Time 资格。记录实际模型和权限；不修改默认模型冒充升级。
- [ ] 若尚无可观察的下一交易日，保留 LIVE_FORWARD_PENDING 和待执行日期，禁止把历史回放称为前向。真实 forward 仅从本次冻结后的未来交易日开始，交给经用户启用的外部调度；本次不自行注册长期任务。
- [ ] 完成可运行探针并将缺权限项完整写入报告后提交 `功能：加入真实前向权限与链路验证入口`；Secret 报告只包含状态，不包含值。

## Task T14：阶段最终验收与交付

文件：更新 README、REQUIREMENTS.md 的验收记录、task_plan.md、findings.md、daily-runner 与 forward-operations 手册；输出 `docs/validation/2026-09-26-forward-operations.md`，只写脱敏事实。

- [ ] 在 JEV 执行一次完整离线 `& $pythonExe -m pytest -m 'not live' -q`；FKQT 仅跑本阶段与既有发布器相关测试，不跑无关旧系统全量。
- [ ] 执行 JEV Ruff、Pyright 与 git diff --check；Pyright 显式使用上述共享解释器。修复后只重跑失败项/静态失败文件，记录首次全量和定点复验，不重复全套。
- [ ] 独立临时数据库执行 Alembic 0009 → 0007 → 0009 往返，数据库路径明确在 data/validation 下。降级如果存在非零权益先拒绝，防止把真实新资产静默抹掉；只对可安全空 Fixture 做往返。
- [ ] 扫描暂存 diff 和报告的 API Key/Bearer/Secret 值，不把扫描命中的真实值打印出来；核查无 Contributor 文件改动、旧冻结哈希不变、两个仓库的修改文件清单与需求映射。
- [ ] 最终代码审查后按 R32 提交 PR；需要创建外部 PR 时遵循用户授权，合并必须等待非作者 Review，不为进度绕过保护。
- [ ] 交付完整 CLI 命令、安装前置条件、所有绝对路径、实际测试计数、live 成功/失败/未执行列表及停止条件；不要把外部阻塞当成代码完成，也不要把代码完成当成真实持续运行完成。

## 最终自检

| 检查项 | 标准值 | 检查方法 | 优先级 |
|---|---|---|---|
| 命令安全 | 无 shell 拼接、无 argv 密钥 | T10 测试和 diff 审查 | 必须 |
| 顺序与恢复 | 执行/NAV 在先、决策在后，无重复式业务写入 | T11 故障与租约测试 | 必须 |
| 重试有限 | 4 次上限、显式 deadline | T11 测试 | 必须 |
| 外部写边界 | 未自动安装计划，未发未配置通知 | 脚本/实际调用记录 | 必须 |
| 真实验证 | 七接口与双 Provider 状态完整，三种模式分列 | T13 报告 | 必须 |
| 税前标识 | GROSS_V1 写入每份运行与验收报告 | 报告断言 | 必须 |
| 兼容和迁移 | 旧契约无破坏，0009 空 Fixture 往返成功 | T14 检查 | 必须 |

## 计划自检与执行交接

三个计划 T1–T14 合计覆盖事项 2–6，新增需求 R54–R60 均有任务；Review Focus 每项已落入测试。计划仅包含已批准范围，无新依赖框架、券商、前端或 A/B/D。

建议 Native（当前会话主代理逐项实现），降低跨接口上下文切换；阶段末集中审查。用户审阅计划并确认执行方式后实施；目前功能代码未修改，真实凭据未读取，外部调用未执行。
