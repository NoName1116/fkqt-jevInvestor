import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from fkqt_jevinvestor.domain.backtest import (
    BacktestConfig,
    DecisionReplayDay,
    ExperimentArm,
    TargetPosition,
    TargetPositionBatch,
)
from fkqt_jevinvestor.domain.backtest import DecisionAction as TargetAction
from fkqt_jevinvestor.domain.decision import (
    DecisionAction,
    DecisionEvaluationStatus,
    DecisionEvaluationV1,
)
from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationCommand,
    JevEvaluationStatus,
    JevEvaluationV1,
    JevScope,
    JevSymbolStateV1,
)
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.providers.base import (
    JevMarketProvider,
    ProviderContractError,
    ProviderUnavailableError,
)
from fkqt_jevinvestor.providers.diagnostics import normalize_usage, sanitize_diagnostic
from fkqt_jevinvestor.research.audit import (
    AuditStore,
    ExperimentArtifact,
    FrozenJevCall,
    LlmCall,
    verify,
)
from fkqt_jevinvestor.research.dataset import FrozenReplayDataProvider
from fkqt_jevinvestor.research.models import (
    ResearchLlmProvider,
    ResearchLlmRequest,
    ResearchStrategyConfig,
)
from fkqt_jevinvestor.research.recorded import reject_legacy_rights
from fkqt_jevinvestor.services.jev_state_builder import REQUIRED_SYMBOL_FEATURES, build_jev_states
from fkqt_jevinvestor.services.position_sizing import (
    POSITION_SIZING_VERSION,
    build_position_sizing_run,
)

_ARMS = {
    ExperimentArm.A_RULE,
    ExperimentArm.B_LLM,
    ExperimentArm.C_JEV_LLM,
    ExperimentArm.D_JEV_DIRECT,
}


class ExperimentTargetProvider:
    def __init__(
        self,
        replay: FrozenReplayDataProvider,
        audit_root: Path,
        strategy: ResearchStrategyConfig,
        *,
        llm: ResearchLlmProvider | None = None,
        jev: JevMarketProvider | None = None,
        replay_only: bool = False,
    ) -> None:
        self.replay, self.strategy = replay, strategy
        self.audit = AuditStore(audit_root)
        self.llm, self.jev = llm, jev
        self.replay_only = replay_only

    async def build_targets(
        self, config: BacktestConfig, day: DecisionReplayDay, portfolio: PortfolioState
    ) -> TargetPositionBatch:
        reject_legacy_rights(portfolio)
        if config.experiment_arm not in _ARMS:
            raise ValueError("RESEARCH_ARM_UNSUPPORTED")
        if type(day) is not DecisionReplayDay:
            raise ValueError("RESEARCH_DECISION_VIEW_REQUIRED")
        source = self.replay.research_day(day.decision_date)
        if (
            config.dataset_id != self.replay.manifest.dataset_id
            or config.dataset_hash != self.replay.manifest.dataset_hash
            or config.sizing_version != POSITION_SIZING_VERSION
            or config.execution_policy_version != "execution-v1"
            or day != source.replay_day().decision_view()
        ):
            raise ValueError("RESEARCH_FROZEN_INPUT_MISMATCH")
        if not config.start_date <= day.decision_date <= config.end_date:
            raise ValueError("RESEARCH_DATE_OUTSIDE_CONFIG")
        symbols = tuple(
            sorted(set(source.queue.ordered_symbols) | {p.symbol for p in portfolio.positions})
        )
        if not set(symbols).issubset(day.features):
            raise ValueError("RESEARCH_FEATURE_COVERAGE_INCOMPLETE")
        history = self._history(config, portfolio, day)
        identity: dict[str, Any] = {
            "config": config.model_dump(mode="json"),
            "strategy": self.strategy.model_dump(mode="json"),
            "day_hash": sha256_json(day),
            "portfolio": portfolio.model_dump(mode="json"),
            "history": history,
        }
        key = sha256_json(identity)
        path = self.audit.path("experiments", key)
        if path.exists():
            return self._load(path, identity)
        if self.replay_only:
            raise ValueError("RESEARCH_EXPERIMENT_NOT_FROZEN")
        for existing_path in (self.audit.root / "experiments").glob("*.json"):
            existing = ExperimentArtifact.model_validate_json(
                existing_path.read_text(encoding="utf-8")
            )
            verify(existing)
            if (
                existing.identity["config"] == identity["config"]
                and existing.identity["strategy"] == identity["strategy"]
                and existing.identity["portfolio"]["portfolio_id"] == portfolio.portfolio_id
                and existing.targets.decision_date > day.decision_date
            ):
                raise ValueError("RESEARCH_HISTORY_BACKFILL_FORBIDDEN")
        with self.audit.claim("experiments", key):
            if path.exists():
                return self._load(path, identity)
            universe: JevEvaluationV1 | None = None
            symbol_jev: dict[str, JevEvaluationV1] = {}
            jev_keys: list[str] = []
            if config.experiment_arm in {ExperimentArm.C_JEV_LLM, ExperimentArm.D_JEV_DIRECT}:
                u_state, s_states = build_jev_states(
                    snapshot=source.snapshot,
                    features=day.features,
                    candidate_symbols=source.queue.ordered_symbols,
                    candidate_limit=source.queue.candidate_limit,
                    held_only_symbols=tuple(
                        sorted(set(symbols) - set(source.queue.ordered_symbols))
                    ),
                )
                u_command = JevEvaluationCommand(
                    scope=JevScope.UNIVERSE,
                    state=u_state,
                    provider_name="typesafe",
                    provider_version=self.strategy.jev_version,
                    model_id=self.strategy.jev_model,
                )
                universe = await self._jev(
                    u_command,
                    bool(
                        u_state.header.candidate_actual_size == 0
                        or any(v is None for v in u_state.metrics.values())
                    ),
                )
                jev_keys.append(u_command.formal_key)
                for symbol in symbols:
                    command = JevEvaluationCommand(
                        scope=JevScope.SYMBOL,
                        state=s_states[symbol],
                        provider_name="typesafe",
                        provider_version=self.strategy.jev_version,
                        model_id=self.strategy.jev_model,
                    )
                    symbol_jev[symbol] = await self._jev(
                        command, bool(s_states[symbol].missing_reasons)
                    )
                    jev_keys.append(command.formal_key)
            evaluations: list[DecisionEvaluationV1] = []
            calls: list[LlmCall] = []
            for symbol in symbols:
                held = portfolio.position(symbol) is not None
                allowed = (
                    (DecisionAction.KEEP, DecisionAction.EXIT)
                    if held
                    else (DecisionAction.ENTER, DecisionAction.AVOID)
                )
                features = day.features[symbol]
                status = DecisionEvaluationStatus.AVAILABLE
                error: str | None = None
                result = None
                action = DecisionAction.NO_SIGNAL
                payload = {
                    "symbol": symbol,
                    "decision_date": day.decision_date.isoformat(),
                    "decision_cutoff": day.decision_cutoff.isoformat(),
                    "planned_execution_date": day.planned_execution_date.isoformat(),
                    "market_snapshot_hash": day.market_snapshot_hash,
                    "feature_snapshot": features.model_dump(mode="json"),
                    "membership": "CANDIDATE"
                    if symbol in source.queue.ordered_symbols
                    else "HELD_ONLY",
                    "portfolio": portfolio.model_dump(mode="json"),
                    "total_equity": str(portfolio.total_equity),
                    "recent_actions": history.get(symbol, []),
                    "pending_orders": [],
                    "allowed_actions": [a.value for a in allowed],
                }
                if config.experiment_arm is ExperimentArm.C_JEV_LLM:
                    assert universe is not None
                    payload.update(
                        universe_jev=universe.model_dump(mode="json"),
                        symbol_jev=symbol_jev[symbol].model_dump(mode="json"),
                    )
                if not set(REQUIRED_SYMBOL_FEATURES).issubset(features.values) or any(
                    value is None or not value.is_finite()
                    for value in (features.values[c].value for c in REQUIRED_SYMBOL_FEATURES)
                ):
                    status, error = (
                        DecisionEvaluationStatus.DATA_UNAVAILABLE,
                        "RESEARCH_REQUIRED_FEATURE_MISSING",
                    )
                elif config.experiment_arm in {
                    ExperimentArm.C_JEV_LLM,
                    ExperimentArm.D_JEV_DIRECT,
                } and (
                    universe is None
                    or universe.status is not JevEvaluationStatus.AVAILABLE
                    or symbol_jev[symbol].status is not JevEvaluationStatus.AVAILABLE
                ):
                    status, error = (
                        DecisionEvaluationStatus.DATA_UNAVAILABLE,
                        "RESEARCH_JEV_UNAVAILABLE",
                    )
                elif config.experiment_arm is ExperimentArm.A_RULE:
                    momentum = features.values["return_20d"].value
                    assert momentum is not None
                    action = (
                        (DecisionAction.EXIT if momentum < 0 else DecisionAction.KEEP)
                        if held
                        else (DecisionAction.ENTER if momentum > 0 else DecisionAction.AVOID)
                    )
                elif config.experiment_arm is ExperimentArm.D_JEV_DIRECT:
                    assert universe is not None
                    action = self._direct(universe, symbol_jev[symbol], held)
                    if action is DecisionAction.NO_SIGNAL:
                        status, error = (
                            DecisionEvaluationStatus.DATA_UNAVAILABLE,
                            "RESEARCH_JEV_INSUFFICIENT",
                        )
                else:
                    request = ResearchLlmRequest(
                        experiment_arm=config.experiment_arm,
                        symbol=symbol,
                        decision_date=day.decision_date,
                        decision_cutoff=day.decision_cutoff,
                        allowed_actions=allowed,
                        payload=payload,
                        model_config_snapshot=self.strategy,
                    )
                    try:
                        if self.llm is None:
                            raise ProviderUnavailableError("RESEARCH_LLM_NOT_CONFIGURED")
                        result = await self.llm.evaluate(request)
                        action = DecisionAction(result.output.action)
                        if (
                            action not in allowed
                            or hashlib.sha256(result.raw_response_text.encode()).hexdigest()
                            != result.raw_response_hash
                        ):
                            raise ProviderContractError(
                                "RESEARCH_LLM_RESPONSE_INVALID",
                                response_hash=result.raw_response_hash,
                            )
                    except ProviderUnavailableError:
                        status, error, action = (
                            DecisionEvaluationStatus.PROVIDER_UNAVAILABLE,
                            "PROVIDER_UNAVAILABLE",
                            DecisionAction.NO_SIGNAL,
                        )
                    except (ProviderContractError, ValidationError) as exc:
                        status, error, action = (
                            DecisionEvaluationStatus.CONTRACT_INVALID,
                            "RESEARCH_LLM_RESPONSE_INVALID",
                            DecisionAction.NO_SIGNAL,
                        )
                        result = None
                        calls.append(
                            LlmCall(
                                request=request.model_dump(mode="json"),
                                status=status.value,
                                response_hash=exc.response_hash
                                if isinstance(exc, ProviderContractError)
                                else None,
                                usage=normalize_usage(exc.usage)
                                if isinstance(exc, ProviderContractError)
                                else None,
                            )
                        )
                    else:
                        calls.append(
                            LlmCall(
                                request=request.model_dump(mode="json"),
                                status=status.value,
                                response_hash=result.raw_response_hash,
                                usage=normalize_usage(result.usage),
                            )
                        )
                    if status is DecisionEvaluationStatus.PROVIDER_UNAVAILABLE:
                        calls.append(
                            LlmCall(
                                request=request.model_dump(mode="json"),
                                status=status.value,
                                response_hash=None,
                                usage=None,
                            )
                        )
                now = datetime.now(UTC)
                evaluation_key = sha256_json({"key": key, "symbol": symbol, "payload": payload})
                raw = (
                    result.raw_response_text
                    if result is not None
                    else (
                        '{"action":"'
                        + action.value
                        + '","thesis":"版本化确定性基线。","invalidation":"下一次冻结决策。"}'
                    )
                )
                evaluations.append(
                    DecisionEvaluationV1(
                        evaluation_id=f"research-{evaluation_key[:24]}",
                        formal_key=evaluation_key,
                        input_hash=sha256_json(payload),
                        symbol=symbol,
                        status=status,
                        action=action,
                        thesis=result.output.thesis
                        if result is not None
                        else "版本化确定性基线。"
                        if status is DecisionEvaluationStatus.AVAILABLE
                        else None,
                        invalidation=result.output.invalidation
                        if result is not None
                        else "下一次冻结决策。"
                        if status is DecisionEvaluationStatus.AVAILABLE
                        else None,
                        raw_response_text=raw
                        if status is DecisionEvaluationStatus.AVAILABLE
                        else None,
                        raw_response_hash=hashlib.sha256(raw.encode()).hexdigest()
                        if status is DecisionEvaluationStatus.AVAILABLE
                        else None,
                        provider_name="deepseek"
                        if config.experiment_arm in {ExperimentArm.B_LLM, ExperimentArm.C_JEV_LLM}
                        else "rule",
                        provider_version=self.strategy.llm_version,
                        model_id=self.strategy.llm_model,
                        provider_base_url=self.strategy.llm_base_url,
                        reasoning_effort=self.strategy.reasoning_effort,
                        prompt_version=self.strategy.prompt_version,
                        output_schema_version="decision-output-v1",
                        started_at=now,
                        finished_at=now,
                        latency_ms=0,
                        error_code=error,
                    )
                )
            sizing = build_position_sizing_run(
                run_id=config.run_id,
                decision_date=day.decision_date,
                planned_execution_date=day.planned_execution_date,
                portfolio=portfolio,
                total_equity=portfolio.total_equity,
                evaluations=evaluations,
                feature_snapshots=day.features,
                config=self.strategy.sizing,
            )
            targets = TargetPositionBatch(
                decision_date=day.decision_date,
                planned_execution_date=day.planned_execution_date,
                experiment_arm=config.experiment_arm,
                input_hash=sizing.input_hash,
                targets=tuple(
                    TargetPosition(
                        symbol=t.symbol,
                        action=TargetAction(t.requested_action.value),
                        target_position_pct=t.target_position_pct,
                        sizing_version=sizing.sizing_version,
                        sizing_input_hash=sizing.input_hash,
                    )
                    for t in sizing.targets
                ),
            )
            self.audit.save(
                "experiments",
                key,
                ExperimentArtifact(
                    identity=identity,
                    evaluations=tuple(evaluations),
                    llm_calls=tuple(calls),
                    jev_call_keys=tuple(jev_keys),
                    targets=targets,
                ),
            )
            return targets

    @staticmethod
    def _load(path: Path, identity: dict[str, Any]) -> TargetPositionBatch:
        artifact = ExperimentArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        verify(artifact)
        if artifact.identity != identity:
            raise ValueError("RESEARCH_AUDIT_IDENTITY_MISMATCH")
        return artifact.targets

    def _history(
        self, config: BacktestConfig, portfolio: PortfolioState, day: DecisionReplayDay
    ) -> dict[str, list[dict[str, str]]]:
        history: dict[str, list[dict[str, str]]] = {}
        dates_seen: set[date] = set()
        for path in sorted((self.audit.root / "experiments").glob("*.json")):
            artifact = ExperimentArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            verify(artifact)
            prior = artifact.identity["config"]
            if (
                prior == config.model_dump(mode="json")
                and artifact.identity["strategy"] == self.strategy.model_dump(mode="json")
                and artifact.identity["portfolio"]["portfolio_id"] == portfolio.portfolio_id
                and artifact.targets.decision_date < day.decision_date
            ):
                if artifact.targets.decision_date in dates_seen:
                    raise ValueError("RESEARCH_HISTORY_BRANCH_AMBIGUOUS")
                dates_seen.add(artifact.targets.decision_date)
                for ev in artifact.evaluations:
                    history.setdefault(ev.symbol, []).append(
                        {
                            "decision_date": artifact.targets.decision_date.isoformat(),
                            "action": ev.action.value,
                            "audit_hash": artifact.content_hash,
                        }
                    )
        return {
            s: sorted(h, key=lambda item: item["decision_date"])[-5:] for s, h in history.items()
        }

    async def _jev(self, command: JevEvaluationCommand, missing: bool) -> JevEvaluationV1:
        path = self.audit.path("jev", command.formal_key)
        if path.exists():
            call = FrozenJevCall.model_validate_json(path.read_text(encoding="utf-8"))
            verify(call)
            if call.command != command:
                raise ValueError("RESEARCH_JEV_CACHE_MISMATCH")
            return call.evaluation.model_copy(
                update={"usage": call.usage, "diagnostic": call.diagnostic}
            )
        with self.audit.claim("jev", command.formal_key):
            now = datetime.now(UTC)
            diagnostic = None
            usage = None
            response_hash = None
            status = (
                JevEvaluationStatus.DATA_UNAVAILABLE
                if missing
                else JevEvaluationStatus.PROVIDER_UNAVAILABLE
            )
            try:
                if missing or self.jev is None:
                    raise ProviderUnavailableError("RESEARCH_JEV_NOT_CONFIGURED")
                result = await self.jev.evaluate(command)
                JevEvaluationV1.model_validate(result.model_dump())
                if (
                    result.formal_key != command.formal_key
                    or result.input_hash != command.input_hash
                    or result.scope != command.scope
                    or result.model_id != command.model_id
                    or result.provider_version != command.provider_version
                    or result.symbol
                    != (
                        command.state.symbol
                        if isinstance(command.state, JevSymbolStateV1)
                        else None
                    )
                ):
                    raise ProviderContractError("RESEARCH_JEV_IDENTITY_MISMATCH")
            except ProviderUnavailableError as exc:
                diagnostic = (
                    sanitize_diagnostic(exc.diagnostic) if exc.diagnostic is not None else None
                )
            except (ProviderContractError, ValidationError) as exc:
                status = JevEvaluationStatus.CONTRACT_INVALID
                if isinstance(exc, ProviderContractError):
                    diagnostic = (
                        sanitize_diagnostic(exc.diagnostic) if exc.diagnostic is not None else None
                    )
                    usage, response_hash = normalize_usage(exc.usage), exc.response_hash
            else:
                self.audit.save(
                    "jev",
                    command.formal_key,
                    FrozenJevCall(
                        command=command,
                        evaluation=result,
                        usage=normalize_usage(result.usage),
                        diagnostic=sanitize_diagnostic(result.diagnostic)
                        if result.diagnostic is not None
                        else None,
                    ),
                )
                return result
            result = JevEvaluationV1(
                evaluation_id=f"research-jev-{command.formal_key[:20]}",
                formal_key=command.formal_key,
                scope=command.scope,
                symbol=command.state.symbol
                if isinstance(command.state, JevSymbolStateV1)
                else None,
                status=status,
                results=(),
                provider_name=command.provider_name,
                provider_version=command.provider_version,
                model_id=command.model_id,
                state_schema_version=command.state.header.state_schema_version,
                question_set_version=command.question_set_version,
                input_hash=command.input_hash,
                raw_response_hash=response_hash,
                started_at=now,
                finished_at=datetime.now(UTC),
                latency_ms=0,
                error_code=status.value,
            )
            self.audit.save(
                "jev",
                command.formal_key,
                FrozenJevCall(
                    command=command, evaluation=result, usage=usage, diagnostic=diagnostic
                ),
            )
            return result

    def _direct(
        self, universe: JevEvaluationV1, symbol: JevEvaluationV1, held: bool
    ) -> DecisionAction:
        distributions = {r.question_id: r.distribution for r in symbol.results}
        threshold = self.strategy.probability_threshold
        if distributions["data_sufficiency"]["SUFFICIENT"] < threshold:
            return DecisionAction.NO_SIGNAL
        negative = (
            universe.results[0].distribution["RISK_OFF"] >= threshold
            or distributions["profitability_5d"]["LOSS"] >= threshold
            or distributions["drawdown_risk_5d"]["HIGH"] >= threshold
        )
        if negative:
            return DecisionAction.EXIT if held else DecisionAction.AVOID
        if held:
            return DecisionAction.KEEP
        positive = (
            distributions["profitability_5d"]["PROFITABLE"] >= threshold
            and distributions["next_session_pnl"]["PROFIT"] >= self.strategy.next_profit_threshold
        )
        return DecisionAction.ENTER if positive else DecisionAction.AVOID
