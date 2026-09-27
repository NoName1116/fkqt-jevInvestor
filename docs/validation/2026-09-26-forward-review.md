# 前向补齐最终独立审查及修复

## 1. 目的与审查范围

检查已确认 R54–R60 的前向补齐实现，不修改 Contributor 回测代码。独立只读审查范围为 JEV `a3e6668..56296d4`、FKQT `b956a70..ba344cb`。初审结论：不建议合并，3 项 Important，0 项 Critical，0 项 Minor。

## 2. 环境与执行边界

JEV 工作树：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner`，Python：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor/.venv/Scripts/python.exe`，显式 PYTHONPATH 指向工作树 src。

FKQT 工作树：`C:/Users/1/Desktop/FKQTPRO/ai-signal-phase0/agent`，Python：`C:/Users/1/Desktop/FKQT-pro/agent/.venv-codex/Scripts/python.exe`，PYTHONPATH 指向 agent。

遵守 R33：最终整套已经运行一次，本次集中修复只运行失败和受影响测试，不重跑全套、不二次委托审查，不追加网络请求。

## 3. 全部阻断项与修复

| 编号 | 需求 | 原问题 | 修复与预期结果 |
|---|---|---|---|
| I1 | R17/R58/R59 | 100 股送 10 股后，EXIT 只卖 100 股却报告 FILLED；剩 10 股永远无法退出 | 零目标卖出允许一次性清空整数尾仓，容量仍限制实际成交；未上市红股继续锁定。容量不足只卖整手，剩余数量按真实目标差计算；买入和普通减仓保持原整手规则 |
| I2 | R57 | 真网络失败被发布端归为永久错误，编排器不重试 | 新增前向专用传输分类：ConnectionError、TimeoutError、Requests 连接/超时进入 NETWORK_TEMPORARY_FAILURE，HTTP 429 进入 HTTP_429；权限、Schema、未知 SDK 错误仍永久失败。日历、公司行为、决策行情和 V2 日历边界使用分类；不改旧 Fixture 采集器 |
| I3 | R47/R59 | 删除外层公司行为哈希而不改 dataset_id，读取器仍接受并跳过权益 | 新 V2/公司行为读取器按发布端原身份公式重算 dataset_id，覆盖 request_params、schema_hash、raw_record_hash；篡改绑定先拒绝 DATASET_MANIFEST_ID_MISMATCH，V1 不变 |

I1 的 5 条测试先失败后通过：110 股完全退出、锁定红股保留 10 股、105 股容量只成交 100 股、单独 10 股尾仓退出、5 股容量不得卖 10 股。

I2 日历两条真实异常类型分类测试先失败后通过；权限永久失败测试保留。补充 HTTP 429/403、决策采集、公司行为采集测试通过；编排器对 DATA_NOT_READY、NETWORK_TEMPORARY_FAILURE、HTTP_429 分别测试有限退避和最终失败。

I3 使用真实 FKQT 测试发布产物，删除公司行为绑定的测试先失败后通过。两条旧公司行为测试原先伪造固定 dataset_id，改为按真实公式构造有效身份后，时间与 Parquet 篡改校验继续通过。

## 4. 自检记录

| 检查项 | 标准值 | 检查方法及结果 | 优先级 |
|---|---|---|---|
| JEV 受影响测试 | 无有效失败 | 首次定点 29 passed/2 failed；两失败为旧 Fixture 身份不合法，修复 Fixture 后与尾仓测试共 7 passed | 必须 |
| FKQT 受影响测试 | 无失败 | 发布/日历/权益/分类 20 passed；新增公司行为网络测试另 1 passed | 必须 |
| 代码质量 | 0 error | 改动文件 Ruff 全通过；Pyright 首轮仅新测试缺日期注解，补全后 0 error | 必须 |
| 兼容性 | V1 与回测不改 | V1 reader 未修改，普通买入/减仓继续整手；回测目录未改 | 必须 |
| 原始审计 | 不覆盖失败 | 真实 CONTRACT_INVALID/DEGRADED 记录保持原样 | 必须 |

## 5. 审查未裁定的全部事项及处理

1. 真实 Provider 当前响应是否合法：审查不发网络请求，不能裁定。保留已观测 CONTRACT_INVALID/DEGRADED，不宣称真实整批成功。
2. 未来连续交易日闭环：尚无未来数据，历史重放不是前向证据。维持未验收状态。
3. Windows 已注册任务与远程 Webhook 实际交付：没有获准启用，代码检查不替代真实运行；继续不注册、不发送。
4. 特定股票缺公司行为字段来自上游缺失还是已完全结算的无关历史：缺真实资料不能裁定；必要活跃权益继续 fail-closed，不推算事实。
5. cash_div 与 cash_div_tax 不一致：两者可能为净额和税前不同口径，不能简单要求相等；继续固定 GROSS_V1。
6. fetched_at 的真实性认证：该字段不属于原发布端身份公式。本次 metadata 身份校验不构成可用时间认证，不宣称历史 strict Point-in-Time 已获证明。

## 6. 已知问题与完整文件清单

框架阻断项已按定点证据修复，不代表 PR 已经获得非作者合并批准。真实模型稳定性和未来跨日验证仍未完成；永久 FAILED 没有自动重置入口。运行步骤见 `C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/runbooks/forward-operations.md`。

完整文件清单：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/validation/2026-09-26-forward-file-manifest.md`。原真实验证报告：`C:/Users/1/Desktop/FKQTPRO/fkqt-jevInvestor-phase5-daily-runner/docs/validation/2026-09-26-forward-operations.md`。
