import asyncio
import json
import os
import time as clock
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select

from fkqt_jevinvestor.domain.market_time import CHINA_STANDARD_TIME
from fkqt_jevinvestor.operations.alerts import AlertEvent, enqueue_alert
from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.operations.process import ProcessOutcome, run_cli
from fkqt_jevinvestor.persistence.forward_operation_repository import ForwardOperationRepository
from fkqt_jevinvestor.persistence.models import (
    DecisionEvaluationRecord,
    DecisionRunLinkRecord,
    ForwardAlertRecord,
    JevEvaluationRecord,
    PortfolioSnapshotRecord,
    PositionSizingRunRecord,
)
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository

STAGES = ("CALENDAR", "EXECUTION_PUBLISH", "EXECUTION_PREPARE", "EXECUTE", "CANDIDATE_PREPARE", "DECISION_PUBLISH", "DECIDE", "VERIFY")
TRANSIENT = {"DATA_NOT_READY", "HTTP_429", "NETWORK_TEMPORARY_FAILURE", "FORWARD_PROCESS_TIMEOUT"}
BACKOFF = (60, 300, 900)


@dataclass(frozen=True)
class ForwardRunOutcome:
    status: str
    completed_stages: tuple[str, ...]
    error_code: str | None = None
    next_retry_at: datetime | None = None

    @property
    def exit_code(self) -> int:
        return 3 if self.status in {"RETRY_PENDING", "BUSY"} else (0 if self.status in {"SUCCESS", "CLOSED"} else 2)


class ForwardRunner:
    def __init__(self, repository: ForwardOperationRepository, *, process: Callable[..., ProcessOutcome] = run_cli,
                 env: dict[str, str] | None = None) -> None:
        self.repository = repository
        self.process = process
        self.env = env if env is not None else dict(os.environ)

    async def run_once(self, config: ForwardOperationConfig, trade_date: date, now: datetime) -> ForwardRunOutcome:
        outcome = await self._run_once(config, trade_date, now)
        if outcome.error_code in {"FORWARD_PROVIDER_DEGRADED", "FORWARD_DATA_DEGRADED"}:
            outcome = ForwardRunOutcome("DEGRADED", outcome.completed_stages, outcome.error_code)
        if outcome.status in {"FAILED", "DEGRADED"}:
            stage = STAGES[min(len(outcome.completed_stages), len(STAGES) - 1)]
            event = AlertEvent.create(portfolio_id=config.portfolio_id, trade_date=trade_date, stage=stage,
                                      error_code=outcome.error_code or "FORWARD_FAILED", severity="ERROR",
                                      recovered=False, created_at=now, episode=config.content_hash())
            async with self.repository.factory.begin() as session:
                await enqueue_alert(session, event)
        elif outcome.status == "SUCCESS":
            async with self.repository.factory.begin() as session:
                alerts = (await session.scalars(select(ForwardAlertRecord))).all()
                for record in alerts:
                    event = AlertEvent.model_validate(record.event_json)
                    if event.portfolio_id == config.portfolio_id and event.trade_date == trade_date and not event.recovered:
                        failure = AlertEvent.create(portfolio_id=config.portfolio_id, trade_date=trade_date, stage=event.stage,
                                                    error_code=event.error_code, severity="ERROR", recovered=False,
                                                    created_at=event.created_at, episode=config.content_hash())
                        if failure.event_id == event.event_id:
                            await enqueue_alert(session, AlertEvent.create(portfolio_id=config.portfolio_id, trade_date=trade_date,
                                stage=event.stage, error_code=event.error_code, severity="INFO", recovered=True,
                                created_at=now, episode=config.content_hash()))
        return outcome

    async def _run_once(self, config: ForwardOperationConfig, trade_date: date, now: datetime) -> ForwardRunOutcome:
        if now.tzinfo is None:
            raise ValueError("FORWARD_TIMEZONE_REQUIRED")
        if trade_date < config.start_date:
            return ForwardRunOutcome("FAILED", (), "FORWARD_BEFORE_START")
        owner = uuid4().hex
        if not await self.repository.acquire_lease(config.portfolio_id, owner_id=owner, now=now,
                                                  lease_seconds=config.max_run_seconds + 60):
            return ForwardRunOutcome("BUSY", (), "FORWARD_LEASE_BUSY")
        started = clock.monotonic()
        completed: list[str] = []
        outputs: dict[str, dict[str, Any]] = {}
        config_hash = config.content_hash()
        root = config.data_root / config.portfolio_id / trade_date.isoformat()
        root.mkdir(parents=True, exist_ok=True)
        deadline = datetime.combine(trade_date, config.readiness_deadline_local, tzinfo=CHINA_STANDARD_TIME)
        try:
            for stage in STAGES:
                timestamp = now + timedelta(seconds=int(clock.monotonic() - started))
                old = await self.repository.get_stage(config.portfolio_id, trade_date, stage, config_hash)
                if old is not None and old.status == "SUCCESS" and stage != "VERIFY":
                    outputs[stage] = old.outcome_json
                    completed.append(stage)
                    if stage == "CALENDAR" and old.outcome_json.get("is_open") is False:
                        return ForwardRunOutcome("CLOSED", tuple(completed))
                    continue
                if old is not None and old.status == "FAILED":
                    return ForwardRunOutcome("FAILED", tuple(completed), old.error_code)
                if old is not None and old.next_retry_at is not None and timestamp.timestamp() < old.next_retry_at:
                    return ForwardRunOutcome("RETRY_PENDING", tuple(completed), old.error_code,
                                             datetime.fromtimestamp(old.next_retry_at, UTC))
                attempts = (old.attempts if old is not None else 0) + 1
                remaining = config.max_run_seconds - (clock.monotonic() - started)
                if not await self.repository.renew_lease(config.portfolio_id, owner, now=timestamp,
                                                       lease_seconds=config.max_run_seconds + 60):
                    return ForwardRunOutcome("FAILED", tuple(completed), "FORWARD_LEASE_LOST")
                await self.repository.record_stage(config.portfolio_id, trade_date, stage, config_hash,
                    owner_id=owner, now=timestamp, status="RUNNING", attempts=attempts, outcome={},
                    input_hash=config_hash)
                try:
                    if remaining <= 0:
                        raise ValueError("FORWARD_PROCESS_TIMEOUT")
                    output = await self._stage(config, trade_date, stage, root, outputs, int(max(1, remaining)))
                    if stage == "CALENDAR" and output.get("is_open") is not False:
                        nav = await PortfolioRepository(self.repository.factory).list_nav(config.portfolio_id)
                        recorded = {item.valuation_date.isoformat() for item in nav}
                        required = output.get("open_dates")
                        if not isinstance(required, list) or trade_date.isoformat() not in required:
                            raise ValueError("TRADING_CALENDAR_INCOMPLETE")
                        if any(not isinstance(day, str) for day in cast(list[object], required)):
                            raise ValueError("TRADING_CALENDAR_INCOMPLETE")
                        if any(day not in recorded for day in cast(list[str], required) if day < trade_date.isoformat()):
                            raise ValueError("DAILY_EXECUTION_HISTORY_GAP")
                except (OSError, RuntimeError, ValueError) as exc:
                    code = str(exc) if str(exc).isupper() and len(str(exc)) < 128 else "FORWARD_STAGE_FAILED"
                    retry = code in TRANSIENT and attempts < config.max_attempts and timestamp < deadline
                    next_retry = min(timestamp + timedelta(seconds=BACKOFF[min(attempts - 1, 2)]), deadline) if retry else None
                    await self.repository.record_stage(config.portfolio_id, trade_date, stage, config_hash,
                        owner_id=owner, now=timestamp, status="RETRY_PENDING" if retry else "FAILED",
                        attempts=attempts, outcome={}, error_code=code, next_retry_at=next_retry)
                    return ForwardRunOutcome("RETRY_PENDING" if retry else "FAILED", tuple(completed), code, next_retry)
                await self.repository.record_stage(config.portfolio_id, trade_date, stage, config_hash,
                    owner_id=owner, now=now + timedelta(seconds=clock.monotonic() - started), status="SUCCESS",
                    attempts=attempts, outcome=output)
                outputs[stage] = output
                completed.append(stage)
                if stage == "CALENDAR" and output.get("is_open") is False:
                    return ForwardRunOutcome("CLOSED", tuple(completed))
            return ForwardRunOutcome("SUCCESS", tuple(completed))
        finally:
            await self.repository.release_lease(config.portfolio_id, owner)

    async def _call(self, config: ForwardOperationConfig, *, fkqt: bool, args: tuple[str, ...],
                    timeout: int, decision_root: Path | None = None) -> dict[str, Any]:
        cwd = config.fkqt_cwd if fkqt else config.jev_cwd
        interpreter = config.fkqt_python if fkqt else config.jev_python
        module = "market_data.forward_cli" if fkqt else "fkqt_jevinvestor"
        env = {name: value for name, value in self.env.items() if name.upper() in {
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE"
        } or name.startswith(("JEV_INVESTOR_", "TYPESAFE_", "DEEPSEEK_", "TUSHARE_"))}
        for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP"):
            inherited = next((value for key, value in os.environ.items() if key.upper() == name), None)
            if inherited is not None and not any(key.upper() == name for key in env):
                env[name] = inherited
        env["PYTHONPATH"] = str(cwd if fkqt else cwd / "src")
        env["JEV_INVESTOR_MARKET_SNAPSHOT_ROOT"] = str(config.data_root / "snapshots")
        env["JEV_INVESTOR_EXECUTION_BUNDLE_ROOT"] = str(config.data_root / "execution")
        env.pop("JEV_INVESTOR_C_GROUP_CANDIDATE_SYMBOLS", None)
        if decision_root is not None:
            env["FKQT_MANIFEST_BUNDLE_ROOT"] = str(decision_root)
        outcome = await asyncio.to_thread(self.process, (str(interpreter), "-m", module, *args), cwd,
                                          timeout_seconds=min(config.process_timeout_seconds, timeout), env=env)
        if outcome.timed_out:
            raise ValueError("FORWARD_PROCESS_TIMEOUT")
        if outcome.exit_code != 0:
            code = outcome.safe_stderr.strip().splitlines()[-1] if outcome.safe_stderr.strip() else "FORWARD_CLI_FAILED"
            raise ValueError(code if code.isupper() and len(code) < 128 else "FORWARD_CLI_FAILED")
        try:
            output = json.loads(outcome.safe_stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise ValueError("FORWARD_CLI_JSON_INVALID") from exc
        if not isinstance(output, dict):
            raise ValueError("FORWARD_CLI_JSON_INVALID")  # noqa: TRY004 — 外部契约错误使用稳定值错误码。
        return cast(dict[str, Any], output)

    async def _stage(self, config: ForwardOperationConfig, day: date, stage: str, root: Path,
                     outputs: dict[str, dict[str, Any]], timeout: int) -> dict[str, Any]:
        day_text = day.isoformat()
        stage_started = clock.monotonic()

        async def call(fkqt: bool, *args: str) -> dict[str, Any]:
            remaining = int(timeout - (clock.monotonic() - stage_started))
            if remaining <= 0:
                raise ValueError("FORWARD_PROCESS_TIMEOUT")
            return await self._call(config, fkqt=fkqt, args=args, timeout=remaining,
                                    decision_root=root / "decision")
        if stage == "CALENDAR":
            return await call(True, "calendar", "--date", day_text, "--start-date", config.start_date.isoformat(), "--output-root", str(root / "calendar"))
        if stage == "EXECUTION_PUBLISH":
            await call(False, "daily", "required-symbols", "--portfolio-id", config.portfolio_id, "--trade-date", day_text, "--output-file", str(root / "execution-symbols.txt"))
            await call(True, "corporate-actions", "--date", day_text, "--symbols-file", str(root / "execution-symbols.txt"), "--output-root", str(root / "corporate-actions"))
            return await call(True, "execution", "--date", day_text, "--symbols-file", str(root / "execution-symbols.txt"), "--schema-version", "2", "--corporate-action-manifest", str(root / "corporate-actions"), "--output-root", str(root / "execution"))
        if stage == "EXECUTION_PREPARE":
            return await call(False, "daily", "prepare-execution", "--trade-date", day_text, "--manifest-root", str(root / "execution"))
        if stage == "EXECUTE":
            portfolio = PortfolioRepository(self.repository.factory)
            if any(nav.valuation_date == day for nav in await portfolio.list_nav(config.portfolio_id)):
                async with self.repository.factory() as session:
                    snapshots = (await session.scalars(select(PortfolioSnapshotRecord).where(
                        PortfolioSnapshotRecord.portfolio_id == config.portfolio_id,
                        PortfolioSnapshotRecord.snapshot_type == "POST_EXECUTION",
                    ))).all()
                snapshot = next((item for item in snapshots if item.as_of.date() == day), None)
                expected = outputs["EXECUTION_PREPARE"].get("execution_hash")
                if snapshot is None or expected is None or (snapshot.details_json or {}).get("execution_input_hash") != expected:
                    raise ValueError("EXECUTION_INPUT_CONFLICT")
                return {"recovered_from_nav": True, "execution_hash": expected}
            return await call(False, "daily", "execute", "--portfolio-id", config.portfolio_id, "--trade-date", day_text,
                              "--start-date", config.start_date.isoformat(), "--execution-bundle", str(outputs["EXECUTION_PREPARE"]["execution_ref"]))
        if stage == "CANDIDATE_PREPARE":
            raw = config.candidate_source.read_text(encoding="utf-8")
            symbols = tuple(line.strip().upper() for line in raw.splitlines() if line.strip())
            if not symbols or len(set(symbols)) != len(symbols) or len(symbols) > config.candidate_limit:
                raise ValueError("CANDIDATE_QUEUE_CONFIG_CONFLICT")
            path = root / "candidates.txt"
            content = "".join(f"{symbol}\n" for symbol in symbols)
            if path.exists() and path.read_text(encoding="utf-8") != content:
                raise ValueError("CANDIDATE_QUEUE_CONFIG_CONFLICT")
            if not path.exists():
                with path.open("x", encoding="utf-8") as handle:
                    handle.write(content)
            return {"candidate_file": str(path), "candidate_count": len(symbols)}
        if stage == "DECISION_PUBLISH":
            await call(False, "daily", "required-symbols", "--portfolio-id", config.portfolio_id, "--trade-date", day_text, "--output-file", str(root / "held-symbols.txt"))
            return await call(True, "decision", "--date", day_text, "--candidate-file", str(root / "candidates.txt"), "--held-file", str(root / "held-symbols.txt"), "--source", "FROZEN_FORWARD_QUEUE_V1", "--output-root", str(root / "decision"))
        if stage == "DECIDE":
            queue = await call(False, "daily", "prepare-candidates", "--trade-date", day_text, "--candidate-limit", str(config.candidate_limit), "--manifest-root", str(root / "decision"), "--output-root", str(root / "queues"))
            return await call(False, "daily", "close", "--date", day_text, "--portfolio-id", config.portfolio_id,
                              "--candidate-limit", str(config.candidate_limit), "--candidate-queue", str(queue["queue_ref"]))
        if stage == "VERIFY":
            portfolio = PortfolioRepository(self.repository.factory)
            if not any(nav.valuation_date == day for nav in await portfolio.list_nav(config.portfolio_id)):
                raise ValueError("DAILY_PREVIOUS_EXECUTION_REQUIRED")
            async with self.repository.factory() as session:
                runs = (await session.scalars(select(PositionSizingRunRecord).where(
                    PositionSizingRunRecord.portfolio_id == config.portfolio_id, PositionSizingRunRecord.decision_date == day,
                ))).all()
                if not runs:
                    raise ValueError("FORWARD_DECISION_NOT_COMMITTED")
                decisions = (await session.scalars(select(DecisionEvaluationRecord).join(DecisionRunLinkRecord,
                    DecisionRunLinkRecord.evaluation_id == DecisionEvaluationRecord.id).where(
                    DecisionRunLinkRecord.run_id.in_([run.run_id for run in runs])))).all()
                jev_ids = {value for record in decisions for value in (record.universe_jev_evaluation_id, record.symbol_jev_evaluation_id)}
                jev = (await session.scalars(select(JevEvaluationRecord).where(JevEvaluationRecord.id.in_(jev_ids)))).all()
                if not decisions or any(record.status in {"PROVIDER_UNAVAILABLE", "CONTRACT_INVALID", "IN_PROGRESS"} for record in (*decisions, *jev)):
                    raise ValueError("FORWARD_PROVIDER_DEGRADED")
                if any(record.status == "DATA_UNAVAILABLE" for record in (*decisions, *jev)):
                    raise ValueError("FORWARD_DATA_DEGRADED")
            return {"execution_complete": True, "decision_run_count": len(runs), "tax_policy_version": "GROSS_V1"}
        raise ValueError("FORWARD_STAGE_UNKNOWN")
