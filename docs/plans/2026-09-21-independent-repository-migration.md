# fkqt-jevInvestor 独立仓库迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建公开 GitHub 仓库 `fkqt-jevInvestor`，把已验收的 Phase 0/1 Python 服务等价迁移为独立包 `fkqt_jevinvestor`，并证明账本、虚拟执行、数据库迁移和 Jev 契约没有行为漂移。

**Architecture:** 新仓库位于 `C:\Users\1\Desktop\FKQTPRO\fkqt-jevInvestor`，不嵌入任何现有 Git 仓库。迁移保留 `ai_signal_*` 数据表、业务规则和 Fixture 结果，只修改包名、服务名、API 根路径和独立仓库配置；远程仓库通过已登录的 GitHub 浏览器会话创建，因为当前主机没有 `gh` CLI。

**Tech Stack:** Python 3.12、FastAPI、Pydantic 2、SQLAlchemy 2 Async、Alembic、SQLite、httpx、typesafe-sdk、pytest、pytest-asyncio、Ruff、Pyright、uv、GitHub Actions

**Spec:** `docs/specs/2026-09-21-market-factor-jev-llm-design.md`

## Global Constraints

- 对应需求：R1、R2、R3、R4、R17、R18、R21、R22、R23、R24、R25、R30、R31、R32、R33。
- Python 版本固定为 `>=3.12,<3.14`。
- 新仓库名固定为 `fkqt-jevInvestor`，Python 包名固定为 `fkqt_jevinvestor`。
- 远程仓库初始可见性固定为 Public；不得在创建时初始化 README、`.gitignore` 或 License。
- 新仓库不得嵌套在 `C:\Users\1\Desktop\FKQTPRO\ai-signal-phase0` 工作树内。
- 不导入 FKQT 的 `agent.*`、旧 ORM、旧配置或旧交易服务。
- 保留 `ai_signal_*` 表名和既有业务精度，避免迁移时同时进行数据库重命名。
- 不复制 `.env`、数据库文件、缓存、`.venv`、API Key 或 Git 元数据。
- 源目录在新仓库完整验收前保持不变，不删除 `ai-signal-lab`。
- 日常任务只运行目标测试；完整离线套件、Ruff、Pyright 和 Alembic 往返只在 Task 6 运行一次。
- 所有提交消息使用中文。

## Review Focus

1. **公开仓库 Secret 泄漏**：源目录中的 `.env`、数据库、缓存和凭据不得进入新 Git 历史；Task 1 的 `test_repository_hygiene.ps1` 固定该行为。
2. **包名残留**：运行时代码不得继续 import `ai_signal_lab`；Task 5 的源码扫描与 import 测试固定该行为。
3. **迁移行为漂移**：同一两日 Fixture 的现金、费用、持仓、NAV 和幂等结果必须保持一致；Task 4 的 `test_migrated_phase1_equivalence` 固定该行为。
4. **数据库断链**：Revision `0001→0002→0003` 必须可升级、重复升级和降级；Task 3 的迁移测试固定该行为。
5. **可见性与分支错误**：远程必须为 Public 且默认分支为 `main`；Task 6 的 GitHub 设置核验固定该行为。

---

## 文件结构锁定

```text
C:\Users\1\Desktop\FKQTPRO\fkqt-jevInvestor\
├── .github\
│   ├── workflows\ci.yml
│   └── pull_request_template.md
├── docs\
│   ├── plans\
│   ├── runbooks\
│   └── specs\
├── migrations\
│   ├── env.py
│   └── versions\
│       ├── 0001_phase0_core.py
│       ├── 0002_phase1_portfolio_execution.py
│       └── 0003_phase1_audit_snapshot.py
├── scripts\test_repository_hygiene.ps1
├── src\fkqt_jevinvestor\
│   ├── api\routes\portfolios.py
│   ├── cli\main.py
│   ├── domain\
│   ├── persistence\
│   ├── providers\
│   └── services\
├── tests\
│   ├── integration\
│   ├── live\
│   └── unit\
├── .env.example
├── .gitignore
├── alembic.ini
├── pyproject.toml
├── README.md
├── REQUIREMENTS.md
├── findings.md
└── task_plan.md
```

### Task 1: 创建公开远程仓库和本地安全骨架

**Files:**
- Create: `C:\Users\1\Desktop\FKQTPRO\fkqt-jevInvestor\.gitignore`
- Create: `C:\Users\1\Desktop\FKQTPRO\fkqt-jevInvestor\scripts\test_repository_hygiene.ps1`
- Create: `C:\Users\1\Desktop\FKQTPRO\fkqt-jevInvestor\README.md`
- Create: `C:\Users\1\Desktop\FKQTPRO\fkqt-jevInvestor\.github\pull_request_template.md`

**Interfaces:**
- Consumes: 已登录 GitHub 的浏览器会话；本地目标目录必须不存在。
- Produces: Public 空远程仓库、`main` 本地分支、安全 `.gitignore` 和首次提交。

- [ ] **Step 1: 验证创建前条件**

运行：

```powershell
Test-Path 'C:\Users\1\Desktop\FKQTPRO\fkqt-jevInvestor'
Get-Command git
Get-Command gh -ErrorAction SilentlyContinue
```

预期：目标路径为 `False`，`git` 存在，`gh` 不存在。若目标路径已存在，停止并核实内容，不覆盖。

- [ ] **Step 2: 通过 GitHub UI 创建远程仓库**

使用当前已登录 GitHub 浏览器：

1. 打开 `https://github.com/new`。
2. Repository name 填写 `fkqt-jevInvestor`。
3. Visibility 选择 `Public`。
4. `Add a README file`、`.gitignore`、License 均保持未选择。
5. 点击 `Create repository`。
6. 记录页面显示的 HTTPS remote URL；禁止猜测 GitHub 用户名。

预期：远程仓库为空且为 Public。

- [ ] **Step 3: 建立本地仓库**

```powershell
New-Item -ItemType Directory -Path 'C:\Users\1\Desktop\FKQTPRO\fkqt-jevInvestor'
git init --initial-branch=main 'C:\Users\1\Desktop\FKQTPRO\fkqt-jevInvestor'
git -C 'C:\Users\1\Desktop\FKQTPRO\fkqt-jevInvestor' remote add origin '<Step 2 页面显示的 HTTPS remote URL>'
```

预期：`git branch --show-current` 输出 `main`，`git remote -v` 只显示 `origin`。

- [ ] **Step 4: 写入仓库卫生失败检查**

创建 `scripts/test_repository_hygiene.ps1`：

```powershell
$requiredTracked = @('.gitignore', 'README.md')
foreach ($required in $requiredTracked) {
    git ls-files --error-unmatch $required 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "缺少必须跟踪的文件: $required"
    }
}

$forbiddenTracked = git ls-files | Where-Object {
    $_ -match '(^|/)(\.env|\.venv|data|cache)(/|$)' -or
    $_ -match '\.(db|sqlite|sqlite3)$'
}
if ($forbiddenTracked) {
    throw "禁止跟踪的文件: $($forbiddenTracked -join ', ')"
}

$secretMatches = git grep -n -I -E 'apikey_[0-9a-f]{16,}|TYPESAFE_API_KEY=[A-Za-z0-9_-]{16,}' -- . ':!scripts/test_repository_hygiene.ps1'
if ($LASTEXITCODE -eq 0) {
    throw "检测到疑似 Secret: $secretMatches"
}
if ($LASTEXITCODE -ne 1) {
    throw "git grep 执行失败，退出码 $LASTEXITCODE"
}
```

- [ ] **Step 5: 运行检查并确认它先失败**

运行：

```powershell
powershell -NoProfile -File .\scripts\test_repository_hygiene.ps1
```

预期：FAIL，因为脚本尚未被 Git 跟踪，当前提交骨架不完整。

- [ ] **Step 6: 写入最小仓库文件**

`.gitignore` 完整内容：

```gitignore
.env
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.mypy_cache/
.pyright/
htmlcov/
.coverage
data/
cache/
*.db
*.sqlite
*.sqlite3
dist/
build/
*.egg-info/
```

`README.md` 完整内容：

```markdown
# fkqt-jevInvestor

独立的 A 股日频 Jev + LLM 模拟决策与实验系统。第一版只生成信号并维护模拟持仓，不连接券商。

当前状态：仓库迁移阶段，产品接口以 `REQUIREMENTS.md` 和 `docs/specs/` 为准。
```

`.github/pull_request_template.md` 完整内容：

```markdown
## 对应需求

- Requirement：R

## 变更内容

-

## 验证证据

- [ ] 目标测试通过
- [ ] 未提交 Secret、数据库或缓存
- [ ] 未修改需求范围外代码
```

- [ ] **Step 7: 跟踪文件后重跑卫生检查**

```powershell
git add .gitignore README.md scripts/test_repository_hygiene.ps1 .github/pull_request_template.md
powershell -NoProfile -File .\scripts\test_repository_hygiene.ps1
```

预期：PASS，无输出，退出码为 0。

- [ ] **Step 8: 提交并推送安全骨架**

```powershell
git commit -m "初始化：创建独立公开仓库骨架"
git push -u origin main
```

预期：远程 `main` 出现首次提交；远程仍为 Public。

### Task 2: 迁移配置、领域契约和 Jev Provider

**Files:**
- Create: `pyproject.toml`
- Create: `uv.lock`
- Create: `.env.example`
- Create: `src/fkqt_jevinvestor/config.py`
- Create: `src/fkqt_jevinvestor/domain/{enums,evidence,factors,market}.py`
- Create: `src/fkqt_jevinvestor/providers/{base,jev,jev_questions}.py`
- Create: `tests/unit/{test_config,test_evidence,test_factor_contracts,test_jev_provider}.py`
- Create: `tests/live/test_jev_live.py`

**Interfaces:**
- Consumes: Phase 0 源文件 `C:\Users\1\Desktop\FKQTPRO\ai-signal-phase0\ai-signal-lab\service`。
- Produces: 可安装包、`Settings`、Jev Provider 契约、离线 Provider 测试和独立 live 门控。

- [ ] **Step 1: 复制经过验收的 Phase 0 文件**

只复制清单内文件，不复制 `.env`、数据或缓存：

```powershell
$sourceRoot = 'C:\Users\1\Desktop\FKQTPRO\ai-signal-phase0\ai-signal-lab\service'
$targetRoot = 'C:\Users\1\Desktop\FKQTPRO\fkqt-jevInvestor'
Copy-Item "$sourceRoot\pyproject.toml" "$targetRoot\pyproject.toml"
Copy-Item "$sourceRoot\uv.lock" "$targetRoot\uv.lock"
Copy-Item "$sourceRoot\.env.example" "$targetRoot\.env.example"
```

按 Files 清单复制 Python 文件到 `src/fkqt_jevinvestor` 和对应测试目录。

- [ ] **Step 2: 写入迁移后包名失败测试**

在 `tests/unit/test_package_identity.py` 写入：

```python
from fkqt_jevinvestor.config import Settings


def test_package_and_environment_prefix_are_independent() -> None:
    settings = Settings(_env_file=None)
    assert settings.environment == "local"
    assert settings.database_url.endswith("data/fkqt_jevinvestor.db")
```

- [ ] **Step 3: 运行测试确认失败**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_package_identity.py -q
```

预期：FAIL，原因是 `fkqt_jevinvestor` 尚不可导入或默认数据库仍为旧名称。

- [ ] **Step 4: 完成机械包名迁移**

对复制文件做以下精确替换：

```text
ai_signal_lab                 -> fkqt_jevinvestor
name = "ai-signal-lab"       -> name = "fkqt-jevinvestor"
AI_SIGNAL_DATABASE_URL        -> JEV_INVESTOR_DATABASE_URL
AI_SIGNAL_ENV                 -> JEV_INVESTOR_ENV
./data/ai_signal_lab.db       -> ./data/fkqt_jevinvestor.db
```

保留 `TYPESAFE_API_KEY`、`TYPESAFE_MODEL` 和 `ai_signal_*` 表名。把项目 description 改为：

```toml
description = "Independent A-share Jev and LLM signal research system"
```

- [ ] **Step 5: 创建环境并安装锁定依赖**

使用已配置的 Codex bundled Python：

```powershell
python -m uv venv --python 3.12
python -m uv sync --all-extras
```

若系统 `python` 不存在，使用当前工作树实际可用的 bundled Python 绝对路径执行同一命令，不安装系统 Python。

- [ ] **Step 6: 运行迁移后的目标测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_package_identity.py tests/unit/test_config.py tests/unit/test_evidence.py tests/unit/test_factor_contracts.py tests/unit/test_jev_provider.py -q
```

预期：全部 PASS；默认不访问网络。

- [ ] **Step 7: 验证 live 测试仍被门控**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/live/test_jev_live.py -q
```

预期：未设置 `RUN_LIVE_JEV_TESTS=1` 时 SKIPPED，不输出 API Key。

- [ ] **Step 8: 提交**

```powershell
git add pyproject.toml uv.lock .env.example src tests
git commit -m "迁移：独立配置与 Jev Provider 契约"
```

### Task 3: 迁移数据库模型与三段 Alembic 历史

**Files:**
- Create: `alembic.ini`
- Create: `migrations/env.py`
- Create: `migrations/versions/0001_phase0_core.py`
- Create: `migrations/versions/0002_phase1_portfolio_execution.py`
- Create: `migrations/versions/0003_phase1_audit_snapshot.py`
- Create: `src/fkqt_jevinvestor/persistence/{base,models,session}.py`
- Create: `tests/integration/test_migrations.py`
- Create: `tests/integration/test_phase1_migration.py`

**Interfaces:**
- Consumes: `fkqt_jevinvestor.persistence.base.Base`。
- Produces: `0003_phase1_audit_snapshot (head)` 和 11 张 `ai_signal_*` 业务表。

- [ ] **Step 1: 复制迁移、模型和现有迁移测试**

复制 Files 清单中的源文件，只把 import 前缀 `ai_signal_lab` 改为 `fkqt_jevinvestor`；Revision ID、表名、列名、约束名和精度不得修改。

- [ ] **Step 2: 添加完整迁移链测试**

在 `tests/integration/test_migration_chain.py` 写入：

```python
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_migrated_revision_chain_is_repeatable_and_reversible(tmp_path: Path) -> None:
    database = tmp_path / "migration-chain.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")

    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert revision == ("0003_phase1_audit_snapshot",)
    assert len({name for name in tables if name.startswith("ai_signal_")}) == 11

    command.downgrade(config, "base")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert not {name for name in tables if name.startswith("ai_signal_")}
```

- [ ] **Step 3: 运行测试确认失败**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_migration_chain.py -q
```

预期：复制或 import 修正未完成时 FAIL。

- [ ] **Step 4: 修正 Alembic 包引用并运行目标测试**

`migrations/env.py` 必须使用：

```python
from fkqt_jevinvestor.persistence import models  # noqa: F401
from fkqt_jevinvestor.persistence.base import Base
```

运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py tests/integration/test_phase1_migration.py tests/integration/test_migration_chain.py -q
```

预期：全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add alembic.ini migrations src/fkqt_jevinvestor/persistence tests/integration
git commit -m "迁移：保留 Phase 0 与 Phase 1 数据库历史"
```

### Task 4: 迁移组合、执行、Repository 与 API

**Files:**
- Create: `src/fkqt_jevinvestor/domain/{execution,portfolio,signals}.py`
- Create: `src/fkqt_jevinvestor/services/{execution_engine,portfolio_service,signal_validator}.py`
- Modify: `src/fkqt_jevinvestor/persistence/repositories.py`
- Create: `src/fkqt_jevinvestor/api/app.py`
- Create: `src/fkqt_jevinvestor/api/routes/portfolios.py`
- Create: `tests/unit/{test_execution_engine,test_portfolio_ledger,test_signal_validator}.py`
- Create: `tests/integration/{test_health_api,test_phase1_two_day_flow,test_portfolio_api,test_portfolio_repository}.py`
- Create: `tests/integration/test_migrated_phase1_equivalence.py`

**Interfaces:**
- Consumes: Task 2 领域基础类型、Task 3 `SessionFactory` 和 ORM。
- Produces: `create_app()`、`PortfolioRepository`、D+1 虚拟执行和 `/api/v1` API。

- [ ] **Step 1: 复制业务源文件与测试**

复制 Files 清单中的既有文件，并只进行包名替换。把 API 根路径从 `/api/ai-signal/v1` 精确改为 `/api/v1`，同步修正 API 测试；领域计算公式、排序、Decimal 精度和错误码不得修改。

- [ ] **Step 2: 写入迁移等价测试**

把原 `test_phase1_two_day_flow.py` 中完整 Fixture 抽成 `run_two_day_fixture()`，返回：

```python
{
    "cash_balance": "795834.7700",
    "total_equity": "999834.7700",
    "sell_commission": "1.2000",
    "stamp_tax": "2.0000",
    "buy_commission": "60.0300",
    "600000.SH.quantity": 400,
    "000001.SZ.quantity": 20000,
    "duplicate_fill_count": 0,
}
```

在 `test_migrated_phase1_equivalence.py` 断言上述字典逐字段相等，并断言 `cash + market_value == total_equity` 的误差不超过 `Decimal("0.01")`。

- [ ] **Step 3: 运行等价测试确认失败**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_migrated_phase1_equivalence.py -q
```

预期：迁移未完成时 FAIL；不得通过修改期望值消除失败。

- [ ] **Step 4: 修正包引用、服务名和 API 元数据**

`create_app()` 的元数据固定为：

```python
app = FastAPI(title="fkqt-jevInvestor", version="0.1.0")
```

存活端点固定为：

```python
@app.get("/api/v1/health/live")
async def live() -> dict[str, str]:
    return {"status": "UP", "service": "fkqt-jevinvestor"}
```

- [ ] **Step 5: 只运行迁移业务目标测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_execution_engine.py tests/unit/test_portfolio_ledger.py tests/unit/test_signal_validator.py tests/integration/test_health_api.py tests/integration/test_phase1_two_day_flow.py tests/integration/test_portfolio_api.py tests/integration/test_portfolio_repository.py tests/integration/test_migrated_phase1_equivalence.py -q
```

预期：全部 PASS；修复失败后只重跑失败的 node ID。

- [ ] **Step 6: 提交**

```powershell
git add src/fkqt_jevinvestor tests
git commit -m "迁移：等价保留持仓与虚拟执行闭环"
```

### Task 5: 增加 CLI、CI 和协作文档

**Files:**
- Create: `src/fkqt_jevinvestor/cli/main.py`
- Create: `src/fkqt_jevinvestor/__main__.py`
- Modify: `pyproject.toml`
- Create: `tests/unit/test_cli.py`
- Create: `.github/workflows/ci.yml`
- Create: `REQUIREMENTS.md`
- Create: `docs/specs/2026-09-21-market-factor-jev-llm-design.md`
- Create: `docs/plans/2026-09-21-independent-repository-migration.md`
- Create: `docs/plans/2026-09-21-phase-2-market-features.md`
- Create: `docs/contributors/2026-09-21-backtesting-development-contract.md`
- Create: `findings.md`
- Create: `task_plan.md`

**Interfaces:**
- Consumes: `fkqt_jevinvestor.api.app:create_app` 和 Alembic 配置。
- Produces: `fkqt-jevinvestor` console script、Windows/Linux CI 和可持续协作文档。

- [ ] **Step 1: 写入 CLI 失败测试**

```python
from fkqt_jevinvestor.cli.main import main


def test_version_command(capsys) -> None:
    exit_code = main(["version"])
    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "fkqt-jevinvestor 0.1.0"
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli.py -q
```

预期：FAIL，原因是 CLI 模块尚不存在。

- [ ] **Step 3: 实现最小 CLI**

`src/fkqt_jevinvestor/cli/main.py`：

```python
import argparse
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fkqt-jevinvestor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version")
    args = parser.parse_args(argv)
    if args.command == "version":
        print("fkqt-jevinvestor 0.1.0")
        return 0
    return 2
```

`src/fkqt_jevinvestor/__main__.py`：

```python
from fkqt_jevinvestor.cli.main import main

raise SystemExit(main())
```

在 `pyproject.toml` 增加：

```toml
[project.scripts]
fkqt-jevinvestor = "fkqt_jevinvestor.cli.main:main"
```

- [ ] **Step 4: 运行 CLI 测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_cli.py -q
.\.venv\Scripts\python.exe -m fkqt_jevinvestor version
```

预期：测试 PASS，命令输出 `fkqt-jevinvestor 0.1.0`。

- [ ] **Step 5: 写入 CI 工作流**

`.github/workflows/ci.yml`：

```yaml
name: ci

on:
  pull_request:
  push:
    branches: [main]

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
      - run: uv sync --all-extras --locked
      - run: uv run ruff check src tests
      - run: uv run pyright
      - run: uv run pytest -m "not live" -q
      - run: mkdir -p data
      - run: uv run alembic upgrade head
```

- [ ] **Step 6: 复制已批准的需求、设计、计划和发现记录**

把当前工作树中的对应 Markdown 文件复制到新仓库 Files 清单位置，包括回测 Contributor 开发契约。更新文档内路径，使其以新仓库根目录为基准；不得复制已删除的 `PROJECT_PLAN.md`。

- [ ] **Step 7: 扫描旧包名和路径残留**

```powershell
$runtimeHits = rg -n "from ai_signal_lab|import ai_signal_lab|ai-signal-lab/service" src tests migrations pyproject.toml
if ($LASTEXITCODE -eq 0) { throw "存在旧运行时引用: $runtimeHits" }
if ($LASTEXITCODE -ne 1) { throw "rg 执行失败" }
```

预期：无匹配，退出码 1 被脚本解释为成功。

- [ ] **Step 8: 提交**

```powershell
git add .github src tests pyproject.toml REQUIREMENTS.md docs findings.md task_plan.md
git commit -m "工程：增加 CLI、CI 与协作基线"
```

### Task 6: 完整迁移验收、远程设置和交付

**Files:**
- Modify: `task_plan.md`
- Modify: `findings.md`

**Interfaces:**
- Consumes: Tasks 1–5 的完整新仓库。
- Produces: 可克隆、可安装、可迁移、可测试的独立公开仓库和验收证据。

- [ ] **Step 1: 运行仓库卫生检查**

```powershell
powershell -NoProfile -File .\scripts\test_repository_hygiene.ps1
git status --short
```

预期：卫生检查退出码 0；测试前工作树干净。

- [ ] **Step 2: 运行唯一一次完整离线验收**

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pyright
.\.venv\Scripts\python.exe -m pytest -m "not live" -q
```

预期：Ruff 0 错误、Pyright 0 errors、全部离线测试 PASS、live 测试不运行。

- [ ] **Step 3: 验证迁移往返**

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic downgrade base
.\.venv\Scripts\python.exe -m alembic upgrade head
```

预期：最终输出包含 `0003_phase1_audit_snapshot (head)`。

- [ ] **Step 4: 更新验收记录**

在 `task_plan.md` 记录实际测试数、Ruff、Pyright、Alembic 和两日 Fixture 数值；在 `findings.md` 记录源提交 `4210322`、`5e43ea8`、`7ba8221` 和迁移提交哈希。只写实际命令结果，不复制预期值冒充证据。

- [ ] **Step 5: 提交验收记录并推送**

```powershell
git add task_plan.md findings.md
git commit -m "验收：完成独立仓库等价迁移"
git push origin main
```

- [ ] **Step 6: 配置 GitHub 协作保护**

在 GitHub Settings 核验：

1. Repository visibility 为 Public。
2. Default branch 为 `main`。
3. 添加 branch ruleset：目标 `main`，Require a pull request before merging，Required approvals 为 `1`，Require status checks 选择 `verify`。
4. 禁止 force push 和 branch deletion。
5. 沙耶可以直接 Fork 后提交 Pull Request；如需直接推送功能分支，等待用户提供她的 GitHub 用户名后再邀请为 Collaborator，不得猜测账号。

- [ ] **Step 7: 最终只读核验**

```powershell
git status --short
git log --oneline -6
git remote -v
git ls-files | Measure-Object
```

预期：工作树干净；远程只有 `origin`；提交历史包含六个任务提交；没有旧 FKQT 仓库 remote。
