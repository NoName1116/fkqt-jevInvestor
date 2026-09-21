from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationCommand,
    JevEvaluationStatus,
    JevEvaluationV1,
    JevScope,
    JevSymbolStateV1,
    JevUniverseStateV1,
)
from fkqt_jevinvestor.domain.market_features import MarketFeatureSnapshot, MarketSnapshot
from fkqt_jevinvestor.persistence.jev_repository import (
    ClaimStatus,
    JevClaim,
    JevEvaluationRepository,
)
from fkqt_jevinvestor.providers.base import (
    JevMarketProvider,
    ProviderContractError,
    ProviderUnavailableError,
)
from fkqt_jevinvestor.services.jev_state_builder import build_jev_states


class JevRunCommandV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    snapshot: MarketSnapshot
    features: Mapping[str, MarketFeatureSnapshot]
    candidate_symbols: tuple[str, ...]
    candidate_limit: int = Field(gt=0)
    held_symbols: tuple[str, ...] = ()
    provider_name: str
    provider_version: str
    model_id: str


class JevRunEvaluationV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    universe: JevEvaluationV1
    symbols: Mapping[str, JevEvaluationV1]


class JevMarketEvaluationService:
    def __init__(
        self,
        *,
        provider: JevMarketProvider,
        repository: JevEvaluationRepository,
    ) -> None:
        self._provider = provider
        self._repository = repository

    async def evaluate_run(self, command: JevRunCommandV1) -> JevRunEvaluationV1:
        universe_state, symbol_states = build_jev_states(
            snapshot=command.snapshot,
            features=command.features,
            candidate_symbols=command.candidate_symbols,
            held_only_symbols=command.held_symbols,
            candidate_limit=command.candidate_limit,
        )
        universe_command = self._evaluation_command(
            command,
            JevScope.UNIVERSE,
            universe_state,
        )
        universe = await self._evaluate_one(
            run_id=command.run_id,
            command=universe_command,
            missing_required_data=False,
        )

        symbol_results: dict[str, JevEvaluationV1] = {}
        for symbol in sorted(symbol_states):
            state = symbol_states[symbol]
            symbol_command = self._evaluation_command(command, JevScope.SYMBOL, state)
            symbol_results[symbol] = await self._evaluate_one(
                run_id=command.run_id,
                command=symbol_command,
                missing_required_data=bool(state.missing_reasons),
            )
        return JevRunEvaluationV1(
            run_id=command.run_id,
            universe=universe,
            symbols=symbol_results,
        )

    @staticmethod
    def _evaluation_command(
        run_command: JevRunCommandV1,
        scope: JevScope,
        state: JevUniverseStateV1 | JevSymbolStateV1,
    ) -> JevEvaluationCommand:
        return JevEvaluationCommand(
            scope=scope,
            state=state,
            provider_name=run_command.provider_name,
            provider_version=run_command.provider_version,
            model_id=run_command.model_id,
        )

    async def _evaluate_one(
        self,
        *,
        run_id: UUID,
        command: JevEvaluationCommand,
        missing_required_data: bool,
    ) -> JevEvaluationV1:
        claim = await self._repository.claim(run_id, command)
        if claim.status is ClaimStatus.COMPLETE:
            if claim.existing_result is None:
                raise RuntimeError("JEV_COMPLETE_RESULT_MISSING")
            return claim.existing_result
        if claim.status is ClaimStatus.IN_PROGRESS:
            existing = await self._repository.load_formal(command.formal_key)
            if existing is None:
                raise RuntimeError("JEV_IN_PROGRESS_RESULT_MISSING")
            return existing

        if missing_required_data:
            failure = self._failure_result(
                claim=claim,
                command=command,
                status=JevEvaluationStatus.DATA_UNAVAILABLE,
                error_code="REQUIRED_FEATURE_MISSING",
                response_hash=None,
            )
            return await self._repository.record_failure(claim, failure)

        try:
            result = await self._provider.evaluate(command)
        except ProviderUnavailableError:
            failure = self._failure_result(
                claim=claim,
                command=command,
                status=JevEvaluationStatus.PROVIDER_UNAVAILABLE,
                error_code="PROVIDER_UNAVAILABLE",
                response_hash=None,
            )
            return await self._repository.record_failure(claim, failure)
        except ProviderContractError as exc:
            failure = self._failure_result(
                claim=claim,
                command=command,
                status=JevEvaluationStatus.CONTRACT_INVALID,
                error_code="JEV_RESPONSE_CONTRACT_INVALID",
                response_hash=exc.response_hash,
            )
            return await self._repository.record_failure(claim, failure)
        return await self._repository.record_success(claim, result)

    @staticmethod
    def _failure_result(
        *,
        claim: JevClaim,
        command: JevEvaluationCommand,
        status: JevEvaluationStatus,
        error_code: str,
        response_hash: str | None,
    ) -> JevEvaluationV1:
        now = datetime.now(UTC)
        return JevEvaluationV1(
            evaluation_id=claim.evaluation_id,
            formal_key=command.formal_key,
            scope=command.scope,
            symbol=(
                command.state.symbol if command.scope is JevScope.SYMBOL else None
            ),
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
            finished_at=now,
            latency_ms=0,
            error_code=error_code,
        )

