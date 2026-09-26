import asyncio
import json
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict

from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.operations.process import ProcessOutcome, run_cli
from fkqt_jevinvestor.operations.runner import ForwardRunner
from fkqt_jevinvestor.persistence.forward_operation_repository import ForwardOperationRepository
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio

ENDPOINTS = ("daily", "trade_cal", "stock_basic", "namechange", "suspend_d", "stk_limit", "dividend")
Mode = Literal["PROVIDER_CONTRACT", "REAL_SOURCE_REPLAY", "LIVE_FORWARD"]


class LiveValidationReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    run_id: str
    mode: Mode
    trade_date: date
    status: str
    probes: tuple[dict[str, Any], ...]
    strict_point_in_time: Literal[False] = False
    is_forward: bool = False
    tax_policy_version: Literal["GROSS_V1"] = "GROSS_V1"
    pending_execution_date: date | None = None
    report_ref: str
    replay_outcome: dict[str, Any] | None = None


def _env_paths(cwd: Path) -> tuple[Path, ...]:
    paths = [cwd / ".env", cwd.parent / ".env"]
    for root in (cwd, cwd.parent):
        marker = root / ".git"
        if marker.is_file():
            text = marker.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir: "):
                gitdir = (root / text[8:]).resolve()
                common_file = gitdir / "commondir"
                if common_file.is_file():
                    common = (gitdir / common_file.read_text(encoding="utf-8").strip()).resolve()
                    main = common.parent
                    paths.extend((main / cwd.relative_to(root) / ".env", main / ".env"))
    return tuple(dict.fromkeys(paths))


def credential_environment(config: ForwardOperationConfig) -> dict[str, str]:
    env = dict(os.environ)
    allowed = {"TUSHARE_TOKEN", "TYPESAFE_API_KEY", "TYPESAFE_MODEL", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL", "DEEPSEEK_REASONING_EFFORT"}
    for cwd in (config.jev_cwd, config.fkqt_cwd):
        for path in _env_paths(cwd):
            if path.is_file():
                for name, value in dotenv_values(path).items():
                    if name in allowed and value and not env.get(name):
                        env[name] = value
    return env


async def validate_live(config: ForwardOperationConfig, *, mode: Mode, trade_date: date, live: bool = False,
                        process: Callable[..., ProcessOutcome] = run_cli, env: dict[str, str] | None = None) -> LiveValidationReport:
    started = time.monotonic()
    run_id = uuid4().hex
    root = config.jev_cwd / "data" / "validation" / run_id
    root.mkdir(parents=True, exist_ok=True)
    report_path = root / "report.json"
    environment = (env if env is not None else credential_environment(config)) if live else {}
    if live:
        environment = {**environment, "DEEPSEEK_MODEL": config.deepseek_model,
                       "DEEPSEEK_REASONING_EFFORT": config.deepseek_reasoning_effort,
                       "TYPESAFE_MODEL": config.typesafe_model}
    probes: list[dict[str, Any]] = []
    missing_code = "MISSING_CREDENTIAL" if live else "LIVE_OPT_IN_REQUIRED"
    if live and environment.get("TUSHARE_TOKEN"):
        outcome = await asyncio.to_thread(process, (str(config.fkqt_python), "-m", "market_data.forward_cli", "permission-probe", "--date", trade_date.isoformat(), "--symbol", "600000.SH"),
                                          config.fkqt_cwd, timeout_seconds=min(config.process_timeout_seconds, config.max_run_seconds),
                                          env={**environment, "PYTHONPATH": str(config.fkqt_cwd)})
        try:
            payload: dict[str, Any] = json.loads(outcome.safe_stdout.strip().splitlines()[-1]) if outcome.exit_code == 0 else {}
            rows: list[dict[str, Any]] = payload.get("endpoints", [])
            if len(rows) != 7 or {row["endpoint"] for row in rows} != set(ENDPOINTS):
                raise ValueError("PROBE_SCHEMA_INVALID")
            for row in rows:
                probes.append({name: row[name] for name in ("endpoint", "status", "code", "coverage", "latency_ms", "source_date", "row_count")})
        except (ValueError, KeyError, TypeError, IndexError):
            probes.extend({"endpoint": endpoint, "status": "BLOCKED", "code": "PROBE_PROCESS_FAILED"} for endpoint in ENDPOINTS)
    else:
        probes.extend({"endpoint": endpoint, "status": "BLOCKED", "code": missing_code} for endpoint in ENDPOINTS)
    for endpoint, key, file, switch in (
        ("typesafe", "TYPESAFE_API_KEY", "test_jev_market_live.py", "RUN_LIVE_JEV_TESTS"),
        ("deepseek", "DEEPSEEK_API_KEY", "test_deepseek_decision_live.py", "RUN_LIVE_DEEPSEEK_TESTS"),
    ):
        probe: dict[str, Any] = {"endpoint": endpoint, "status": "BLOCKED", "code": missing_code,
                                 "model_id": environment.get("TYPESAFE_MODEL", "jev-latest") if endpoint == "typesafe" else environment.get("DEEPSEEK_MODEL", "deepseek-flash")}
        remaining = int(config.max_run_seconds - (time.monotonic() - started))
        if live and environment.get(key) and remaining > 0:
            env_for_process = {**environment, "PYTHONPATH": str(config.jev_cwd / "src"), switch: "1",
                               "TYPESAFE_JEV_MODEL": environment.get("TYPESAFE_MODEL", "jev-latest")}
            outcome = await asyncio.to_thread(process, (str(config.jev_python), "-m", "pytest", str(config.jev_cwd / "tests" / "live" / file), "-q", "-p", "no:cacheprovider"),
                                              config.jev_cwd, timeout_seconds=min(config.process_timeout_seconds, remaining), env=env_for_process)
            passed = outcome.exit_code == 0 and re.search(r"\b[1-9]\d* passed\b", outcome.safe_stdout) is not None
            probe.update(status="AVAILABLE" if passed else "PROVIDER_UNAVAILABLE", code="OK" if passed else "PROVIDER_CONTRACT_FAILED",
                         latency_ms=int(outcome.duration_seconds * 1000))
        elif remaining <= 0:
            probe.update(code="VALIDATION_TIMEOUT")
        probes.append(probe)
    status = "SUCCESS" if all(probe["status"] == "AVAILABLE" for probe in probes) else "BLOCKED"
    pending = None
    replay = None
    if mode == "LIVE_FORWARD":
        if trade_date >= datetime.now(UTC).date():
            pending = trade_date
            if status == "SUCCESS":
                status = "LIVE_FORWARD_PENDING"
        else:
            status = "BLOCKED"
            replay = {"error_code": "HISTORICAL_DATE_CANNOT_CLAIM_FORWARD"}
    if mode == "REAL_SOURCE_REPLAY" and status == "SUCCESS":
        if config.candidate_limit > 2 or trade_date >= datetime.now(UTC).date():
            status = "BLOCKED"
            replay = {"error_code": "REPLAY_REQUIRES_PAST_DATE_AND_SMALL_QUEUE"}
        else:
            remaining = int(config.max_run_seconds - (time.monotonic() - started))
            discovered = await asyncio.to_thread(process,
                (str(config.fkqt_python), "-m", "market_data.forward_cli", "available-day", "--before-date", trade_date.isoformat(), "--symbols-file", str(config.candidate_source)),
                config.fkqt_cwd, timeout_seconds=max(1, min(config.process_timeout_seconds, remaining)),
                env={**environment, "PYTHONPATH": str(config.fkqt_cwd)})
            try:
                if discovered.exit_code != 0:
                    raise ValueError("AVAILABLE_SOURCE_DAY_NOT_FOUND")
                actual_day = date.fromisoformat(json.loads(discovered.safe_stdout.strip().splitlines()[-1])["source_date"])
                if actual_day > trade_date:
                    raise ValueError("AVAILABLE_SOURCE_DAY_INVALID")
            except (ValueError, KeyError, IndexError, TypeError):
                report = LiveValidationReport(run_id=run_id, mode=mode, trade_date=trade_date, status="BLOCKED",
                    probes=tuple(probes), report_ref=str(report_path), replay_outcome={"error_code": "AVAILABLE_SOURCE_DAY_NOT_FOUND"})
                report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
                return report
            database = root / "replay.db"
            url = f"sqlite+aiosqlite:///{database.as_posix()}"
            migration = Config(str(config.jev_cwd / "alembic.ini"))
            migration.set_main_option("script_location", str(config.jev_cwd / "migrations"))
            migration.set_main_option("sqlalchemy.url", url)
            await asyncio.to_thread(command.upgrade, migration, "head")
            engine = create_engine(url)
            try:
                factory = create_session_factory(engine)
                isolated = config.model_copy(update={"portfolio_id": f"validation-{run_id[:12]}", "start_date": actual_day,
                                                    "data_root": root / "simulation", "webhook_url": None,
                                                    "max_run_seconds": max(1, int(config.max_run_seconds - (time.monotonic() - started)))})
                await PortfolioRepository(factory).create(CreatePortfolio(portfolio_id=isolated.portfolio_id, name="Real source replay"))
                result = await ForwardRunner(ForwardOperationRepository(factory), env={**environment, "JEV_INVESTOR_DATABASE_URL": url}).run_once(
                    isolated, actual_day, datetime.now(UTC))
                replay = {"status": result.status, "error_code": result.error_code, "completed_stages": result.completed_stages,
                           "portfolio_id": isolated.portfolio_id, "database_ref": str(database), "actual_source_date": actual_day.isoformat()}
                status = "SUCCESS" if result.status == "SUCCESS" else "BLOCKED"
            finally:
                await engine.dispose()
    report = LiveValidationReport(run_id=run_id, mode=mode, trade_date=trade_date, status=status,
                                  probes=tuple(probes), pending_execution_date=pending, report_ref=str(report_path), replay_outcome=replay)
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
