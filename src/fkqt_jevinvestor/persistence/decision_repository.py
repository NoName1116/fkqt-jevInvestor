import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.domain.decision import (
    DecisionAction,
    DecisionEvaluationCommand,
    DecisionEvaluationStatus,
    DecisionEvaluationV1,
    DecisionHistoryV1,
)
from fkqt_jevinvestor.domain.market_time import as_utc
from fkqt_jevinvestor.persistence.models import (
    DecisionAttemptRecord,
    DecisionEvaluationRecord,
    DecisionRunLinkRecord,
)

SessionFactory = async_sessionmaker[AsyncSession]
_SENSITIVE_KEYS = {"api_key", "apikey", "authorization", "password", "secret", "token"}


class ClaimStatus(StrEnum):
    ACQUIRED = "ACQUIRED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"


class DecisionClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: ClaimStatus
    evaluation_id: str
    formal_key: str = Field(min_length=64, max_length=64)
    attempt_id: str | None
    owner_token: str | None = None
    attempt_sequence: int | None = Field(default=None, ge=1)
    existing_result: DecisionEvaluationV1 | None = None


class DecisionRepositoryError(RuntimeError):
    pass


class DecisionClaimConflict(DecisionRepositoryError):
    pass


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:32]}"


def _stored_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else as_utc(value)


def _assert_sanitized(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in cast(dict[object, object], value).items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                raise ValueError("SENSITIVE_DECISION_FIELD_FORBIDDEN")
            _assert_sanitized(nested)
    elif isinstance(value, list | tuple):
        for nested in cast(list[object] | tuple[object, ...], value):
            _assert_sanitized(nested)


class DecisionEvaluationRepository:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        lease_duration: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("DECISION_LEASE_DURATION_INVALID")
        self._session_factory = session_factory
        self._lease_duration = lease_duration
        self._clock = clock
        self._claim_lock = asyncio.Lock()

    async def claim(
        self,
        run_id: UUID,
        command: DecisionEvaluationCommand,
    ) -> DecisionClaim:
        command = DecisionEvaluationCommand.model_validate(command.model_dump())
        input_json = command.decision_input.model_dump(mode="json")
        _assert_sanitized(input_json)
        async with self._claim_lock:
            try:
                async with self._session_factory.begin() as session:
                    record = await session.scalar(
                        select(DecisionEvaluationRecord).where(
                            DecisionEvaluationRecord.formal_key == command.formal_key
                        )
                    )
                    if record is None:
                        return await self._create_claim(
                            session, run_id, command, input_json
                        )
                    already_linked = await self._has_run_link(
                        session, run_id, record.id
                    )
                    await self._ensure_run_link(session, run_id, record.id)
                    if (
                        record.status != DecisionEvaluationStatus.IN_PROGRESS.value
                        and (
                            record.status == DecisionEvaluationStatus.AVAILABLE.value
                            or already_linked
                        )
                    ):
                        return DecisionClaim(
                            status=ClaimStatus.COMPLETE,
                            evaluation_id=record.id,
                            formal_key=record.formal_key,
                            attempt_id=None,
                            existing_result=await self._load_record(session, record),
                        )
                    if record.status == DecisionEvaluationStatus.IN_PROGRESS.value:
                        now = self._clock()
                        if (
                            record.lease_expires_at is not None
                            and _stored_utc(record.lease_expires_at) <= as_utc(now)
                        ):
                            return await self._retry_claim(
                                session, run_id, command, record
                            )
                        return DecisionClaim(
                            status=ClaimStatus.IN_PROGRESS,
                            evaluation_id=record.id,
                            formal_key=record.formal_key,
                            attempt_id=None,
                        )
                    return await self._retry_claim(session, run_id, command, record)
            except IntegrityError as exc:
                return await self._claim_after_competition(run_id, command, exc)

    async def record_success(
        self,
        claim: DecisionClaim,
        result: DecisionEvaluationV1,
    ) -> DecisionEvaluationV1:
        result = DecisionEvaluationV1.model_validate(result.model_dump())
        if result.status is not DecisionEvaluationStatus.AVAILABLE:
            raise ValueError("DECISION_SUCCESS_STATUS_REQUIRED")
        async with self._session_factory.begin() as session:
            record, attempt = await self._owned_records(session, claim)
            self._validate_result_identity(record, claim, result)
            await self._finish_claim(session, record, claim, result)
            record.action = result.action.value
            record.thesis = result.thesis
            record.invalidation = result.invalidation
            record.raw_response_text = result.raw_response_text
            record.raw_response_hash = result.raw_response_hash
            record.error_code = None
            self._finish_attempt(attempt, result)
            await session.flush()
        return result

    async def record_failure(
        self,
        claim: DecisionClaim,
        *,
        status: DecisionEvaluationStatus,
        error_code: str,
        started_at: datetime,
        finished_at: datetime,
        raw_response_hash: str | None = None,
    ) -> DecisionEvaluationV1:
        if status in {
            DecisionEvaluationStatus.AVAILABLE,
            DecisionEvaluationStatus.IN_PROGRESS,
        }:
            raise ValueError("DECISION_FAILURE_STATUS_REQUIRED")
        if not error_code or not error_code.replace("_", "").isalnum():
            raise ValueError("DECISION_STABLE_ERROR_CODE_REQUIRED")
        async with self._session_factory.begin() as session:
            record, attempt = await self._owned_records(session, claim)
            result = DecisionEvaluationV1(
                evaluation_id=record.id,
                formal_key=record.formal_key,
                input_hash=record.input_hash,
                symbol=record.symbol,
                status=status,
                action=DecisionAction.NO_SIGNAL,
                thesis=None,
                invalidation=None,
                raw_response_text=None,
                raw_response_hash=raw_response_hash,
                provider_name=record.provider_name,
                provider_version=record.provider_version,
                model_id=record.model_id,
                prompt_version=record.prompt_version,
                output_schema_version=record.output_schema_version,
                started_at=started_at,
                finished_at=finished_at,
                latency_ms=max(
                    0, round((finished_at - started_at).total_seconds() * 1000)
                ),
                error_code=error_code,
            )
            await self._finish_claim(session, record, claim, result)
            record.action = DecisionAction.NO_SIGNAL.value
            record.thesis = None
            record.invalidation = None
            record.raw_response_text = None
            record.raw_response_hash = raw_response_hash
            record.error_code = error_code
            self._finish_attempt(attempt, result)
            await session.flush()
        return result

    async def load_formal(self, formal_key: str) -> DecisionEvaluationV1 | None:
        async with self._session_factory() as session:
            record = await session.scalar(
                select(DecisionEvaluationRecord).where(
                    DecisionEvaluationRecord.formal_key == formal_key
                )
            )
            return None if record is None else await self._load_record(session, record)

    async def recent_actions(
        self,
        portfolio_id: str,
        symbol: str,
        limit: int = 5,
    ) -> tuple[DecisionHistoryV1, ...]:
        if limit <= 0 or limit > 5:
            raise ValueError("DECISION_HISTORY_LIMIT_INVALID")
        async with self._session_factory() as session:
            records = tuple(
                await session.scalars(
                    select(DecisionEvaluationRecord)
                    .where(
                        DecisionEvaluationRecord.portfolio_id == portfolio_id,
                        DecisionEvaluationRecord.symbol == symbol,
                        DecisionEvaluationRecord.status
                        == DecisionEvaluationStatus.AVAILABLE.value,
                    )
                    .order_by(DecisionEvaluationRecord.decision_date.desc())
                    .limit(limit)
                )
            )
        return tuple(
            DecisionHistoryV1(
                decision_date=record.decision_date,
                action=DecisionAction(record.action),
            )
            for record in reversed(records)
        )

    async def _create_claim(
        self,
        session: AsyncSession,
        run_id: UUID,
        command: DecisionEvaluationCommand,
        input_json: dict[str, Any],
    ) -> DecisionClaim:
        now = self._clock()
        evaluation_id = f"decision-{command.formal_key[:23]}"
        owner_token = str(uuid4())
        lease_expires_at = now + self._lease_duration
        decision_input = command.decision_input
        record = DecisionEvaluationRecord(
            id=evaluation_id,
            formal_key=command.formal_key,
            portfolio_id=decision_input.portfolio.portfolio_id,
            symbol=decision_input.symbol,
            membership=decision_input.membership.value,
            decision_date=decision_input.decision_date,
            decision_cutoff=as_utc(decision_input.decision_cutoff),
            input_json=input_json,
            input_hash=command.input_hash,
            universe_jev_evaluation_id=decision_input.universe_jev.evaluation_id,
            symbol_jev_evaluation_id=decision_input.symbol_jev.evaluation_id,
            provider_name=command.provider_name,
            provider_version=command.provider_version,
            model_id=command.model_id,
            prompt_version=command.prompt_version,
            output_schema_version=command.output_schema_version,
            status=DecisionEvaluationStatus.IN_PROGRESS.value,
            action=DecisionAction.NO_SIGNAL.value,
            thesis=None,
            invalidation=None,
            raw_response_text=None,
            raw_response_hash=None,
            error_code=None,
            latest_attempt_sequence=1,
            current_owner_token=owner_token,
            lease_expires_at=lease_expires_at,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        attempt_id = _stable_id("decision-attempt", evaluation_id, 1)
        session.add(
            self._new_attempt(
                attempt_id, evaluation_id, run_id, owner_token, 1, command, now
            )
        )
        await self._ensure_run_link(session, run_id, evaluation_id)
        await session.flush()
        return DecisionClaim(
            status=ClaimStatus.ACQUIRED,
            evaluation_id=evaluation_id,
            formal_key=command.formal_key,
            attempt_id=attempt_id,
            owner_token=owner_token,
            attempt_sequence=1,
        )

    async def _retry_claim(
        self,
        session: AsyncSession,
        run_id: UUID,
        command: DecisionEvaluationCommand,
        record: DecisionEvaluationRecord,
    ) -> DecisionClaim:
        sequence = record.latest_attempt_sequence + 1
        now = self._clock()
        owner_token = str(uuid4())
        lease_expires_at = now + self._lease_duration
        conditions = [
            DecisionEvaluationRecord.id == record.id,
            DecisionEvaluationRecord.status == record.status,
            DecisionEvaluationRecord.latest_attempt_sequence
            == record.latest_attempt_sequence,
            DecisionEvaluationRecord.current_owner_token == record.current_owner_token,
        ]
        if record.status == DecisionEvaluationStatus.IN_PROGRESS.value:
            conditions.append(DecisionEvaluationRecord.lease_expires_at <= as_utc(now))
        competition = cast(
            CursorResult[Any],
            await session.execute(
                update(DecisionEvaluationRecord)
                .where(*conditions)
                .values(
                    status=DecisionEvaluationStatus.IN_PROGRESS.value,
                    latest_attempt_sequence=sequence,
                    current_owner_token=owner_token,
                    lease_expires_at=lease_expires_at,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if competition.rowcount != 1:
            return DecisionClaim(
                status=ClaimStatus.IN_PROGRESS,
                evaluation_id=record.id,
                formal_key=record.formal_key,
                attempt_id=None,
            )
        previous = await session.scalar(
            select(DecisionAttemptRecord).where(
                DecisionAttemptRecord.evaluation_id == record.id,
                DecisionAttemptRecord.sequence == record.latest_attempt_sequence,
            )
        )
        if previous is not None and previous.status == DecisionEvaluationStatus.IN_PROGRESS:
            self._expire_attempt(previous, now)
        attempt_id = _stable_id("decision-attempt", record.id, sequence)
        session.add(
            self._new_attempt(
                attempt_id,
                record.id,
                run_id,
                owner_token,
                sequence,
                command,
                now,
            )
        )
        await session.flush()
        return DecisionClaim(
            status=ClaimStatus.ACQUIRED,
            evaluation_id=record.id,
            formal_key=record.formal_key,
            attempt_id=attempt_id,
            owner_token=owner_token,
            attempt_sequence=sequence,
        )

    def _new_attempt(
        self,
        attempt_id: str,
        evaluation_id: str,
        run_id: UUID,
        owner_token: str,
        sequence: int,
        command: DecisionEvaluationCommand,
        now: datetime,
    ) -> DecisionAttemptRecord:
        return DecisionAttemptRecord(
            id=attempt_id,
            evaluation_id=evaluation_id,
            run_id=str(run_id),
            owner_token=owner_token,
            sequence=sequence,
            provider_name=command.provider_name,
            provider_version=command.provider_version,
            model_id=command.model_id,
            started_at=now,
            finished_at=None,
            lease_expires_at=now + self._lease_duration,
            latency_ms=0,
            status=DecisionEvaluationStatus.IN_PROGRESS.value,
            raw_response_hash=None,
            error_code=None,
        )

    async def _ensure_run_link(
        self,
        session: AsyncSession,
        run_id: UUID,
        evaluation_id: str,
    ) -> None:
        run_text = str(run_id)
        existing = await session.scalar(
            select(DecisionRunLinkRecord.id).where(
                DecisionRunLinkRecord.run_id == run_text,
                DecisionRunLinkRecord.evaluation_id == evaluation_id,
            )
        )
        if existing is None:
            session.add(
                DecisionRunLinkRecord(
                    id=_stable_id("decision-link", run_text, evaluation_id),
                    run_id=run_text,
                    evaluation_id=evaluation_id,
                    created_at=self._clock(),
                )
            )

    @staticmethod
    async def _has_run_link(
        session: AsyncSession,
        run_id: UUID,
        evaluation_id: str,
    ) -> bool:
        existing = await session.scalar(
            select(DecisionRunLinkRecord.id).where(
                DecisionRunLinkRecord.run_id == str(run_id),
                DecisionRunLinkRecord.evaluation_id == evaluation_id,
            )
        )
        return existing is not None

    async def _claim_after_competition(
        self,
        run_id: UUID,
        command: DecisionEvaluationCommand,
        cause: IntegrityError,
    ) -> DecisionClaim:
        async with self._session_factory.begin() as session:
            record = await session.scalar(
                select(DecisionEvaluationRecord).where(
                    DecisionEvaluationRecord.formal_key == command.formal_key
                )
            )
            if record is None:
                raise DecisionClaimConflict(
                    "DECISION_CLAIM_COMPETITION_UNRESOLVED"
                ) from cause
            await self._ensure_run_link(session, run_id, record.id)
            if record.status == DecisionEvaluationStatus.AVAILABLE.value:
                return DecisionClaim(
                    status=ClaimStatus.COMPLETE,
                    evaluation_id=record.id,
                    formal_key=record.formal_key,
                    attempt_id=None,
                    existing_result=await self._load_record(session, record),
                )
            return DecisionClaim(
                status=ClaimStatus.IN_PROGRESS,
                evaluation_id=record.id,
                formal_key=record.formal_key,
                attempt_id=None,
            )

    @staticmethod
    async def _owned_records(
        session: AsyncSession,
        claim: DecisionClaim,
    ) -> tuple[DecisionEvaluationRecord, DecisionAttemptRecord]:
        if claim.attempt_id is None:
            raise DecisionClaimConflict("DECISION_CLAIM_ATTEMPT_REQUIRED")
        record = await session.get(DecisionEvaluationRecord, claim.evaluation_id)
        attempt = await session.get(DecisionAttemptRecord, claim.attempt_id)
        if record is None or attempt is None:
            raise DecisionClaimConflict("DECISION_CLAIM_NOT_FOUND")
        if (
            record.status != DecisionEvaluationStatus.IN_PROGRESS.value
            or attempt.status != DecisionEvaluationStatus.IN_PROGRESS.value
            or attempt.sequence != claim.attempt_sequence
            or record.latest_attempt_sequence != claim.attempt_sequence
            or attempt.owner_token != claim.owner_token
            or record.current_owner_token != claim.owner_token
        ):
            raise DecisionClaimConflict("DECISION_CLAIM_OWNERSHIP_LOST")
        return record, attempt

    async def _finish_claim(
        self,
        session: AsyncSession,
        record: DecisionEvaluationRecord,
        claim: DecisionClaim,
        result: DecisionEvaluationV1,
    ) -> None:
        finished = cast(
            CursorResult[Any],
            await session.execute(
                update(DecisionEvaluationRecord)
                .where(
                    DecisionEvaluationRecord.id == record.id,
                    DecisionEvaluationRecord.status
                    == DecisionEvaluationStatus.IN_PROGRESS.value,
                    DecisionEvaluationRecord.latest_attempt_sequence
                    == claim.attempt_sequence,
                    DecisionEvaluationRecord.current_owner_token == claim.owner_token,
                    DecisionEvaluationRecord.lease_expires_at > as_utc(self._clock()),
                )
                .values(
                    status=result.status.value,
                    current_owner_token=None,
                    lease_expires_at=None,
                    updated_at=as_utc(result.finished_at),
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if finished.rowcount != 1:
            raise DecisionClaimConflict("DECISION_CLAIM_OWNERSHIP_LOST")

    @staticmethod
    def _validate_result_identity(
        record: DecisionEvaluationRecord,
        claim: DecisionClaim,
        result: DecisionEvaluationV1,
    ) -> None:
        if (
            claim.status is not ClaimStatus.ACQUIRED
            or result.evaluation_id != record.id
            or result.formal_key != record.formal_key
            or result.input_hash != record.input_hash
            or result.symbol != record.symbol
            or result.provider_name != record.provider_name
            or result.provider_version != record.provider_version
            or result.model_id != record.model_id
            or result.prompt_version != record.prompt_version
            or result.output_schema_version != record.output_schema_version
        ):
            raise DecisionClaimConflict("DECISION_RESULT_IDENTITY_MISMATCH")

    @staticmethod
    def _finish_attempt(
        attempt: DecisionAttemptRecord,
        result: DecisionEvaluationV1,
    ) -> None:
        attempt.started_at = as_utc(result.started_at)
        attempt.finished_at = as_utc(result.finished_at)
        attempt.lease_expires_at = None
        attempt.latency_ms = result.latency_ms
        attempt.status = result.status.value
        attempt.raw_response_hash = result.raw_response_hash
        attempt.error_code = result.error_code

    @staticmethod
    def _expire_attempt(attempt: DecisionAttemptRecord, now: datetime) -> None:
        started = _stored_utc(attempt.started_at)
        finished = as_utc(now)
        attempt.finished_at = finished
        attempt.lease_expires_at = None
        attempt.latency_ms = max(0, round((finished - started).total_seconds() * 1000))
        attempt.status = DecisionEvaluationStatus.PROVIDER_UNAVAILABLE.value
        attempt.error_code = "CLAIM_LEASE_EXPIRED"

    @staticmethod
    async def _load_record(
        session: AsyncSession,
        record: DecisionEvaluationRecord,
    ) -> DecisionEvaluationV1:
        attempt = await session.scalar(
            select(DecisionAttemptRecord).where(
                DecisionAttemptRecord.evaluation_id == record.id,
                DecisionAttemptRecord.sequence == record.latest_attempt_sequence,
            )
        )
        if attempt is None:
            raise DecisionRepositoryError("DECISION_ATTEMPT_NOT_FOUND")
        started_at = _stored_utc(attempt.started_at)
        finished_at = (
            _stored_utc(attempt.finished_at)
            if attempt.finished_at is not None
            else started_at
        )
        return DecisionEvaluationV1(
            evaluation_id=record.id,
            formal_key=record.formal_key,
            input_hash=record.input_hash,
            symbol=record.symbol,
            status=DecisionEvaluationStatus(record.status),
            action=DecisionAction(record.action),
            thesis=record.thesis,
            invalidation=record.invalidation,
            raw_response_text=record.raw_response_text,
            raw_response_hash=record.raw_response_hash,
            provider_name=record.provider_name,
            provider_version=record.provider_version,
            model_id=record.model_id,
            prompt_version=record.prompt_version,
            output_schema_version=record.output_schema_version,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=attempt.latency_ms,
            error_code=record.error_code,
        )
