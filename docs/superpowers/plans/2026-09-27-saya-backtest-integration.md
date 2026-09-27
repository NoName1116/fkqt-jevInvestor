# 沙耶回测合入实施计划

> **For agentic workers:** 使用 superpowers:executing-plans，在当前已隔离工作树执行；用户已明确授权合入，沿用连续执行，不在任务间暂停。

**Goal:** R70，保留沙耶作者历史，完成与现有研究适配器的离线联合验收。

**Architecture:** 合并已审查的 9ef4fb5，保留新旧枚举兼容。复用 BacktestEngine/run_experiment_arms 与 research 三端口，四组各自独立 FIFO 执行器与持仓。

**Tech Stack:** 原 Python/Pydantic/pytest/Ruff/Pyright，零新增依赖。

**Spec:** REQUIREMENTS.md R70，docs/contributors/2026-09-21-backtesting-development-contract.md，docs/runbooks/research-bridge.md。

## Global Constraints

- 不覆盖用户缓存或已冻结研究数据；不调用真实模型、启动前向、推送或远程合并。
- 只修经联合测试确认的接线缺陷；旧 Protocol 与实验组兼容成员保留。
- 用户少测要求优先：修复只重测失败项，最终受影响套件仅运行一次。

## Review Focus

- 预热只校验，不调用目标/执行；日期链不得断裂。
- 候选证券与当前持仓集合和沙耶覆盖检查一致；潜在持仓只作数据覆盖，不自动买入。
- 各组独立组合与 FIFO；持仓进入下一日真实决策，不共享账本。
- 哈希无自引用，逐日资产、收益、回撤可由冻结结果重算。
- 旧枚举在合并时不能删除；手册统一 A_RULE/B_LLM/C_JEV_LLM/D_JEV_DIRECT。

## Task 1：作者代码审查与安全合并

- [x] 核对fork来源、提交、实际差异和三条已补契约。
- [x] git merge --no-ff --no-commit 9ef4fb5，保留现有枚举，处理真实冲突。
- [x] 运行沙耶 tests/backtest 与契约测试作为合入基线，记录结果。

## Task 2：真实端口联合验证与修复

- [x] 新增 tests/integration/test_saya_research_integration.py，真实冻结数据集、真实执行与仓位、Fake LLM/Jev 请求边界。
- [x] 两日四组执行及AUDIT_VERIFIED；按SHA256重算config/result hash，检查首/末日持仓年龄和缓存重放。
- [x] 验证额外数据证券不自动入场、队列外持仓不遗漏；必要修复映射R70。
- [x] 对失败项修复并只重测失败项。

## Task 3：交付与本地合并提交

- [x] 更新沙耶手册旧命名与研究接线示例；保留历史验收的时点语义。
- [x] 一次受影响回归、Ruff/Pyright、差异和凭据检查。
- [x] docs/validation/2026-09-27-saya-backtest-integration.md记录完整审查结论、结果、限制、绝对文件清单。
- [x] 本地merge commit，验证沙耶提交是HEAD祖先；不推送。
