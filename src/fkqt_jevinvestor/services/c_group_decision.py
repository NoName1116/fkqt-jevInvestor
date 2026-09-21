import time
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fkqt_jevinvestor.domain.decision import (
    DecisionAction,
    DecisionEvaluationCommand,
    DecisionEvaluationStatus,
    DecisionEvaluationV1,
    DecisionHistoryV1,
    DecisionInputV1,
    DecisionMembership,
    PendingOrderSummaryV1,
)
from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationCommand,
    JevEvaluationStatus,
    JevScope,
)
from fkqt_jevinvestor.domain.market_features import MarketFeatureSnapshot, MarketSnapshot
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.persistence.decision_repository import (
    ClaimStatus,
    DecisionClaim,
    DecisionEvaluationRepository,
)
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository, StoredOrder
from fkqt_jevinvestor.providers.base import (
    DecisionLlmProvider,
    ProviderContractError,
    ProviderUnavailableError,
)
from fkqt_jevinvestor.services.jev_market_service import JevRunEvaluationV1
from fkqt_jevinvestor.services.jev_state_builder import (
    REQUIRED_SYMBOL_FEATURES,
    build_jev_states,
)
from fkqt_jevinvestor.services.position_sizing import (
    PositionSizingConfigV1,
    PositionSizingRunV1,
    build_position_sizing_run,
    to_validated_signal_batch,
)


class CGroupDecisionCommandV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    snapshot: MarketSnapshot
    features: Mapping[str, MarketFeatureSnapshot]
    candidate_symbols: tuple[str, ...]
    candidate_limit: int = Field(gt=0)
    jev: JevRunEvaluationV1
    portfolio: PortfolioState
    total_equity: Decimal = Field(gt=0)
    recent_actions: Mapping[str, tuple[DecisionHistoryV1, ...]]
    pending_orders: Mapping[str, tuple[PendingOrderSummaryV1, ...]]
    provider_name: str = Field(min_length=1)
    provider_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    provider_base_url: str = Field(
        default="https://api.deepseek.com",
        min_length=1,
        max_length=512,
    )
    reasoning_effort: Literal["low", "high"] = "high"
    sizing_config: PositionSizingConfigV1

    @model_validator(mode="after")
    def validate_batch_identity(self) -> "CGroupDecisionCommandV1":
        if len(set(self.candidate_symbols)) != len(self.candidate_symbols):
            raise ValueError("C_GROUP_DUPLICATE_CANDIDATE")
        if len(self.candidate_symbols) > self.candidate_limit:
            raise ValueError("C_GROUP_CANDIDATE_LIMIT_EXCEEDED")
        if any(
            item.decision_date > self.snapshot.decision_date
            for history in self.recent_actions.values()
            for item in history
        ):
            raise ValueError("C_GROUP_HISTORY_POINT_IN_TIME_VIOLATION")
        return self


class CGroupDecisionRunV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    evaluations: tuple[DecisionEvaluationV1, ...]
    sizing_run: PositionSizingRunV1
    stored_batch_id: str
    stored_orders: tuple[StoredOrder, ...]


class CGroupDecisionService:
    def __init__(
        self,
        *,
        provider: DecisionLlmProvider,
        decision_repository: DecisionEvaluationRepository,
        portfolio_repository: PortfolioRepository,
    ) -> None:
        self._provider = provider
        self._decision_repository = decision_repository
        self._portfolio_repository = portfolio_repository

    async def evaluate_run(
        self,
        command: CGroupDecisionCommandV1,
    ) -> CGroupDecisionRunV1:
        self._validate_frozen_inputs(command)
        candidate_set = set(command.candidate_symbols)
        positions = {item.symbol: item for item in command.portfolio.positions}
        symbols = tuple(sorted(candidate_set | positions.keys()))
        evaluations: list[DecisionEvaluationV1] = []

        for symbol in symbols:
            decision_input = DecisionInputV1(
                decision_date=command.snapshot.decision_date,
                decision_cutoff=command.snapshot.decision_cutoff,
                planned_execution_date=command.snapshot.next_trade_date,
                candidate_universe_id=command.snapshot.universe_snapshot_id,
                candidate_universe_hash=command.snapshot.universe_snapshot_hash,
                market_snapshot_hash=command.snapshot.content_hash,
                feature_snapshot=command.features.get(symbol),
                symbol=symbol,
                membership=(
                    DecisionMembership.CANDIDATE
                    if symbol in candidate_set
                    else DecisionMembership.HELD_ONLY
                ),
                universe_jev=command.jev.universe,
                symbol_jev=command.jev.symbols[symbol],
                portfolio=command.portfolio,
                total_equity=command.total_equity,
                position=positions.get(symbol),
                recent_actions=command.recent_actions.get(symbol, ()),
                pending_orders=command.pending_orders.get(symbol, ()),
                allowed_actions=(
                    (DecisionAction.ENTER, DecisionAction.AVOID)
                    if symbol not in positions
                    else (DecisionAction.KEEP, DecisionAction.EXIT)
                ),
            )
            evaluation_command = DecisionEvaluationCommand(
                decision_input=decision_input,
                provider_name=command.provider_name,
                provider_version=command.provider_version,
                model_id=command.model_id,
                provider_base_url=command.provider_base_url,
                reasoning_effort=command.reasoning_effort,
            )
            evaluations.append(
                await self._evaluate_one(command.run_id, evaluation_command)
            )

        evaluation_tuple = tuple(evaluations)
        if any(
            item.status is DecisionEvaluationStatus.IN_PROGRESS
            for item in evaluation_tuple
        ):
            raise RuntimeError("C_GROUP_DECISIONS_IN_PROGRESS")
        sizing_run = build_position_sizing_run(
            run_id=str(command.run_id),
            decision_date=command.snapshot.decision_date,
            planned_execution_date=command.snapshot.next_trade_date,
            portfolio=command.portfolio,
            total_equity=command.total_equity,
            evaluations=evaluation_tuple,
            feature_snapshots=command.features,
            config=command.sizing_config,
        )
        stored = await self._portfolio_repository.save_c_group_signal_batch(
            sizing_run,
            command.sizing_config,
            to_validated_signal_batch(sizing_run),
            command.portfolio,
        )
        return CGroupDecisionRunV1(
            run_id=command.run_id,
            evaluations=evaluation_tuple,
            sizing_run=sizing_run,
            stored_batch_id=stored.batch_id,
            stored_orders=stored.orders,
        )

    @staticmethod
    def _validate_frozen_inputs(command: CGroupDecisionCommandV1) -> None:
        snapshot = command.snapshot
        jev = command.jev
        if jev.run_id != command.run_id:
            raise ValueError("C_GROUP_RUN_ID_MISMATCH")
        if (
            jev.decision_date != snapshot.decision_date
            or jev.decision_cutoff != snapshot.decision_cutoff
            or jev.planned_execution_date != snapshot.next_trade_date
            or jev.candidate_universe_hash != snapshot.universe_snapshot_hash
            or jev.market_snapshot_hash != snapshot.content_hash
        ):
            raise ValueError("C_GROUP_FROZEN_INPUT_MISMATCH")
        required = set(command.candidate_symbols) | {
            item.symbol for item in command.portfolio.positions
        }
        if set(jev.symbols) != required:
            raise ValueError("C_GROUP_JEV_COVERAGE_INCOMPLETE")
        if any(
            feature.symbol != symbol
            or feature.decision_date != snapshot.decision_date
            for symbol, feature in command.features.items()
        ):
            raise ValueError("C_GROUP_FEATURE_IDENTITY_MISMATCH")
        for symbol in required:
            feature = command.features.get(symbol)
            if feature is None:
                continue
            expected_content_hash = sha256_json(
                feature.model_copy(update={"content_hash": ""}).model_dump(mode="json")
            )
            if feature.content_hash != expected_content_hash:
                raise ValueError("C_GROUP_FEATURE_CONTENT_HASH_MISMATCH")
            if set(feature.values) != set(REQUIRED_SYMBOL_FEATURES):
                raise ValueError("C_GROUP_FEATURE_SET_INVALID")
            if any(
                value.source_snapshot_hash != snapshot.content_hash
                for value in feature.values.values()
            ):
                raise ValueError("C_GROUP_FEATURE_SOURCE_MISMATCH")

        universe_state, symbol_states = build_jev_states(
            snapshot=snapshot,
            features=command.features,
            candidate_symbols=command.candidate_symbols,
            held_only_symbols=tuple(sorted(required - set(command.candidate_symbols))),
            candidate_limit=command.candidate_limit,
        )
        expected_universe = JevEvaluationCommand(
            scope=JevScope.UNIVERSE,
            state=universe_state,
            provider_name=jev.universe.provider_name,
            provider_version=jev.universe.provider_version,
            model_id=jev.universe.model_id,
        )
        if jev.universe.input_hash != expected_universe.input_hash:
            raise ValueError("C_GROUP_JEV_INPUT_MISMATCH")
        for symbol in required:
            evaluation = jev.symbols[symbol]
            expected_symbol = JevEvaluationCommand(
                scope=JevScope.SYMBOL,
                state=symbol_states[symbol],
                provider_name=evaluation.provider_name,
                provider_version=evaluation.provider_version,
                model_id=evaluation.model_id,
            )
            if evaluation.input_hash != expected_symbol.input_hash:
                raise ValueError("C_GROUP_JEV_INPUT_MISMATCH")

    async def _evaluate_one(
        self,
        run_id: UUID,
        command: DecisionEvaluationCommand,
    ) -> DecisionEvaluationV1:
        claim = await self._decision_repository.claim(run_id, command)
        if claim.status is ClaimStatus.COMPLETE:
            if claim.existing_result is None:
                raise RuntimeError("DECISION_COMPLETE_RESULT_MISSING")
            return claim.existing_result
        if claim.status is ClaimStatus.IN_PROGRESS:
            existing = await self._decision_repository.load_formal(command.formal_key)
            if existing is None:
                raise RuntimeError("DECISION_IN_PROGRESS_RESULT_MISSING")
            return existing

        decision_input = command.decision_input
        if self._missing_required_data(decision_input):
            now = datetime.now(UTC)
            return await self._decision_repository.record_failure(
                claim,
                status=DecisionEvaluationStatus.DATA_UNAVAILABLE,
                error_code="REQUIRED_DECISION_EVIDENCE_MISSING",
                started_at=now,
                finished_at=now,
            )

        started_at = datetime.now(UTC)
        started = time.perf_counter()
        try:
            result = await self._provider.evaluate(command)
            action = DecisionAction(result.output.action)
            if action not in decision_input.allowed_actions:
                raise ProviderContractError(
                    "DECISION_ACTION_NOT_ALLOWED",
                    response_hash=result.raw_response_hash,
                )
        except ProviderUnavailableError:
            return await self._record_provider_failure(
                claim,
                DecisionEvaluationStatus.PROVIDER_UNAVAILABLE,
                "PROVIDER_UNAVAILABLE",
                started_at,
                started,
            )
        except ProviderContractError as exc:
            return await self._record_provider_failure(
                claim,
                DecisionEvaluationStatus.CONTRACT_INVALID,
                "DECISION_RESPONSE_CONTRACT_INVALID",
                started_at,
                started,
                exc.response_hash,
            )

        finished_at = datetime.now(UTC)
        evaluation = DecisionEvaluationV1(
            evaluation_id=claim.evaluation_id,
            formal_key=command.formal_key,
            input_hash=command.input_hash,
            symbol=decision_input.symbol,
            status=DecisionEvaluationStatus.AVAILABLE,
            action=action,
            thesis=result.output.thesis,
            invalidation=result.output.invalidation,
            raw_response_text=result.raw_response_text,
            raw_response_hash=result.raw_response_hash,
            provider_name=command.provider_name,
            provider_version=command.provider_version,
            model_id=command.model_id,
            provider_base_url=command.provider_base_url,
            reasoning_effort=command.reasoning_effort,
            prompt_version=command.prompt_version,
            output_schema_version=command.output_schema_version,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
            error_code=None,
        )
        return await self._decision_repository.record_success(claim, evaluation)

    @staticmethod
    def _missing_required_data(decision_input: DecisionInputV1) -> bool:
        snapshot = decision_input.feature_snapshot
        return (
            decision_input.universe_jev.status is not JevEvaluationStatus.AVAILABLE
            or decision_input.symbol_jev.status is not JevEvaluationStatus.AVAILABLE
            or snapshot is None
            or set(snapshot.values) != set(REQUIRED_SYMBOL_FEATURES)
            or any(item.value is None for item in snapshot.values.values())
        )

    async def _record_provider_failure(
        self,
        claim: DecisionClaim,
        status: DecisionEvaluationStatus,
        error_code: str,
        started_at: datetime,
        _started: float,
        response_hash: str | None = None,
    ) -> DecisionEvaluationV1:
        return await self._decision_repository.record_failure(
            claim,
            status=status,
            error_code=error_code,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            raw_response_hash=response_hash,
        )
