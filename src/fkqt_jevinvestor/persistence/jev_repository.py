import asyncio
import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationCommand,
    JevEvaluationStatus,
    JevEvaluationV1,
    JevQuestionResultV1,
    JevScope,
    JevSymbolStateV1,
)
from fkqt_jevinvestor.domain.market_time import as_utc
from fkqt_jevinvestor.persistence.models import (
    JevAttemptRecord,
    JevEvaluationRecord,
    JevQuestionResultRecord,
    JevRunLinkRecord,
)
from fkqt_jevinvestor.providers.jev_market_questions import QUESTION_DEFINITIONS

SessionFactory = async_sessionmaker[AsyncSession]
_SENSITIVE_KEYS = {"api_key", "apikey", "authorization", "password", "secret", "token"}


class ClaimStatus(StrEnum):
    ACQUIRED = "ACQUIRED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETE = "COMPLETE"


class JevClaim(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: ClaimStatus
    evaluation_id: str
    formal_key: str = Field(min_length=64, max_length=64)
    attempt_id: str | None
    attempt_sequence: int | None = Field(default=None, ge=1)
    existing_result: JevEvaluationV1 | None = None


class JevAttemptAudit(BaseModel):
    model_config = ConfigDict(frozen=True)

    attempt_id: str
    evaluation_id: str
    run_id: UUID
    sequence: int
    provider_name: str
    provider_version: str
    model_id: str
    started_at: datetime
    finished_at: datetime | None
    latency_ms: int
    status: JevEvaluationStatus
    raw_response_hash: str | None
    error_code: str | None


class JevRepositoryError(RuntimeError):
    pass


class JevClaimConflict(JevRepositoryError):
    pass


def _stored_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else as_utc(value)


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:32]}"


def _assert_sanitized(value: object) -> None:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        for key, nested in mapping.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                raise ValueError("SENSITIVE_STATE_FIELD_FORBIDDEN")
            _assert_sanitized(nested)
    elif isinstance(value, list | tuple):
        sequence = cast(list[object] | tuple[object, ...], value)
        for nested in sequence:
            _assert_sanitized(nested)


def _validate_result_identity(
    record: JevEvaluationRecord,
    claim: JevClaim,
    result: JevEvaluationV1,
) -> None:
    if claim.status is not ClaimStatus.ACQUIRED or claim.attempt_id is None:
        raise JevClaimConflict("JEV_CLAIM_NOT_ACQUIRED")
    if (
        record.id != claim.evaluation_id
        or record.formal_key != claim.formal_key
        or result.evaluation_id != record.id
        or result.formal_key != record.formal_key
        or result.input_hash != record.input_hash
    ):
        raise JevClaimConflict("JEV_RESULT_IDENTITY_MISMATCH")


def _attempt_audit(record: JevAttemptRecord) -> JevAttemptAudit:
    return JevAttemptAudit(
        attempt_id=record.id,
        evaluation_id=record.evaluation_id,
        run_id=UUID(record.run_id),
        sequence=record.sequence,
        provider_name=record.provider_name,
        provider_version=record.provider_version,
        model_id=record.model_id,
        started_at=_stored_utc(record.started_at),
        finished_at=(
            _stored_utc(record.finished_at) if record.finished_at is not None else None
        ),
        latency_ms=record.latency_ms,
        status=JevEvaluationStatus(record.status),
        raw_response_hash=record.raw_response_hash,
        error_code=record.error_code,
    )


class JevEvaluationRepository:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        self._claim_lock = asyncio.Lock()

    async def claim(self, run_id: UUID, command: JevEvaluationCommand) -> JevClaim:
        state_json = command.state.model_dump(mode="json")
        _assert_sanitized(state_json)
        async with self._claim_lock:
            try:
                async with self._session_factory.begin() as session:
                    record = await session.scalar(
                        select(JevEvaluationRecord).where(
                            JevEvaluationRecord.formal_key == command.formal_key
                        )
                    )
                    if record is None:
                        return await self._create_claim(session, run_id, command, state_json)

                    await self._ensure_run_link(session, run_id, record.id)
                    if record.status == JevEvaluationStatus.AVAILABLE.value:
                        existing = await self._load_record(session, record)
                        return JevClaim(
                            status=ClaimStatus.COMPLETE,
                            evaluation_id=record.id,
                            formal_key=record.formal_key,
                            attempt_id=None,
                            attempt_sequence=None,
                            existing_result=existing,
                        )
                    if record.status == JevEvaluationStatus.IN_PROGRESS.value:
                        return JevClaim(
                            status=ClaimStatus.IN_PROGRESS,
                            evaluation_id=record.id,
                            formal_key=record.formal_key,
                            attempt_id=None,
                            attempt_sequence=None,
                        )
                    return await self._retry_claim(session, run_id, command, record)
            except IntegrityError as exc:
                return await self._claim_after_competition(run_id, command, exc)

    async def record_success(
        self,
        claim: JevClaim,
        result: JevEvaluationV1,
    ) -> JevEvaluationV1:
        if result.status is not JevEvaluationStatus.AVAILABLE:
            raise ValueError("JEV_SUCCESS_STATUS_REQUIRED")
        async with self._session_factory.begin() as session:
            record, attempt = await self._owned_records(session, claim)
            _validate_result_identity(record, claim, result)
            await session.execute(
                delete(JevQuestionResultRecord).where(
                    JevQuestionResultRecord.evaluation_id == record.id
                )
            )
            for item in result.results:
                session.add(
                    JevQuestionResultRecord(
                        id=_stable_id("jevq", record.id, item.question_id),
                        evaluation_id=record.id,
                        question_id=item.question_id,
                        question_version=item.question_version,
                        criteria_version=item.criteria_version,
                        label_order_json=list(item.label_order),
                        selected_label=item.selected_label,
                        distribution_json={
                            label: str(item.distribution[label]) for label in item.label_order
                        },
                    )
                )
            self._finish_records(record, attempt, result)
            await session.flush()
        return result

    async def record_failure(
        self,
        claim: JevClaim,
        result: JevEvaluationV1,
    ) -> JevEvaluationV1:
        if result.status in {
            JevEvaluationStatus.AVAILABLE,
            JevEvaluationStatus.IN_PROGRESS,
        }:
            raise ValueError("JEV_FAILURE_STATUS_REQUIRED")
        if result.error_code is None or not result.error_code.replace("_", "").isalnum():
            raise ValueError("JEV_STABLE_ERROR_CODE_REQUIRED")
        async with self._session_factory.begin() as session:
            record, attempt = await self._owned_records(session, claim)
            _validate_result_identity(record, claim, result)
            await session.execute(
                delete(JevQuestionResultRecord).where(
                    JevQuestionResultRecord.evaluation_id == record.id
                )
            )
            self._finish_records(record, attempt, result)
            await session.flush()
        return result

    async def load_formal(self, formal_key: str) -> JevEvaluationV1 | None:
        async with self._session_factory() as session:
            record = await session.scalar(
                select(JevEvaluationRecord).where(
                    JevEvaluationRecord.formal_key == formal_key
                )
            )
            if record is None:
                return None
            return await self._load_record(session, record)

    async def list_attempts(self, formal_key: str) -> tuple[JevAttemptAudit, ...]:
        async with self._session_factory() as session:
            evaluation_id = await session.scalar(
                select(JevEvaluationRecord.id).where(
                    JevEvaluationRecord.formal_key == formal_key
                )
            )
            if evaluation_id is None:
                return ()
            records = tuple(
                await session.scalars(
                    select(JevAttemptRecord)
                    .where(JevAttemptRecord.evaluation_id == evaluation_id)
                    .order_by(JevAttemptRecord.sequence)
                )
            )
            return tuple(_attempt_audit(record) for record in records)

    async def _create_claim(
        self,
        session: AsyncSession,
        run_id: UUID,
        command: JevEvaluationCommand,
        state_json: dict[str, Any],
    ) -> JevClaim:
        now = datetime.now(UTC)
        evaluation_id = f"jev-{command.formal_key[:24]}"
        record = JevEvaluationRecord(
            id=evaluation_id,
            formal_key=command.formal_key,
            scope=command.scope.value,
            symbol=(
                command.state.symbol
                if isinstance(command.state, JevSymbolStateV1)
                else None
            ),
            decision_date=command.state.header.decision_date,
            decision_cutoff=as_utc(command.state.header.decision_cutoff),
            state_json=state_json,
            input_hash=command.input_hash,
            provider_name=command.provider_name,
            provider_version=command.provider_version,
            model_id=command.model_id,
            state_schema_version=command.state.header.state_schema_version,
            question_set_version=command.question_set_version,
            status=JevEvaluationStatus.IN_PROGRESS.value,
            latest_attempt_sequence=1,
            created_at=now,
            updated_at=now,
        )
        session.add(record)
        attempt_id = _stable_id("jeva", evaluation_id, 1)
        session.add(self._new_attempt(attempt_id, evaluation_id, run_id, 1, command, now))
        await self._ensure_run_link(session, run_id, evaluation_id)
        await session.flush()
        return JevClaim(
            status=ClaimStatus.ACQUIRED,
            evaluation_id=evaluation_id,
            formal_key=command.formal_key,
            attempt_id=attempt_id,
            attempt_sequence=1,
        )

    async def _retry_claim(
        self,
        session: AsyncSession,
        run_id: UUID,
        command: JevEvaluationCommand,
        record: JevEvaluationRecord,
    ) -> JevClaim:
        sequence = record.latest_attempt_sequence + 1
        now = datetime.now(UTC)
        attempt_id = _stable_id("jeva", record.id, sequence)
        session.add(self._new_attempt(attempt_id, record.id, run_id, sequence, command, now))
        record.status = JevEvaluationStatus.IN_PROGRESS.value
        record.latest_attempt_sequence = sequence
        record.updated_at = now
        await session.flush()
        return JevClaim(
            status=ClaimStatus.ACQUIRED,
            evaluation_id=record.id,
            formal_key=record.formal_key,
            attempt_id=attempt_id,
            attempt_sequence=sequence,
        )

    @staticmethod
    def _new_attempt(
        attempt_id: str,
        evaluation_id: str,
        run_id: UUID,
        sequence: int,
        command: JevEvaluationCommand,
        now: datetime,
    ) -> JevAttemptRecord:
        return JevAttemptRecord(
            id=attempt_id,
            evaluation_id=evaluation_id,
            run_id=str(run_id),
            sequence=sequence,
            provider_name=command.provider_name,
            provider_version=command.provider_version,
            model_id=command.model_id,
            started_at=now,
            finished_at=None,
            latency_ms=0,
            status=JevEvaluationStatus.IN_PROGRESS.value,
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
            select(JevRunLinkRecord.id).where(
                JevRunLinkRecord.run_id == run_text,
                JevRunLinkRecord.evaluation_id == evaluation_id,
            )
        )
        if existing is None:
            session.add(
                JevRunLinkRecord(
                    id=_stable_id("jevl", run_text, evaluation_id),
                    run_id=run_text,
                    evaluation_id=evaluation_id,
                    created_at=datetime.now(UTC),
                )
            )

    async def _claim_after_competition(
        self,
        run_id: UUID,
        command: JevEvaluationCommand,
        cause: IntegrityError,
    ) -> JevClaim:
        async with self._session_factory.begin() as session:
            record = await session.scalar(
                select(JevEvaluationRecord).where(
                    JevEvaluationRecord.formal_key == command.formal_key
                )
            )
            if record is None:
                raise JevClaimConflict("JEV_CLAIM_COMPETITION_UNRESOLVED") from cause
            await self._ensure_run_link(session, run_id, record.id)
            if record.status == JevEvaluationStatus.AVAILABLE.value:
                existing = await self._load_record(session, record)
                return JevClaim(
                    status=ClaimStatus.COMPLETE,
                    evaluation_id=record.id,
                    formal_key=record.formal_key,
                    attempt_id=None,
                    existing_result=existing,
                )
            return JevClaim(
                status=ClaimStatus.IN_PROGRESS,
                evaluation_id=record.id,
                formal_key=record.formal_key,
                attempt_id=None,
            )

    @staticmethod
    async def _owned_records(
        session: AsyncSession,
        claim: JevClaim,
    ) -> tuple[JevEvaluationRecord, JevAttemptRecord]:
        if claim.attempt_id is None:
            raise JevClaimConflict("JEV_CLAIM_ATTEMPT_REQUIRED")
        record = await session.get(JevEvaluationRecord, claim.evaluation_id)
        attempt = await session.get(JevAttemptRecord, claim.attempt_id)
        if record is None or attempt is None:
            raise JevClaimConflict("JEV_CLAIM_NOT_FOUND")
        if (
            record.status != JevEvaluationStatus.IN_PROGRESS.value
            or attempt.status != JevEvaluationStatus.IN_PROGRESS.value
            or attempt.sequence != claim.attempt_sequence
        ):
            raise JevClaimConflict("JEV_CLAIM_NOT_ACTIVE")
        return record, attempt

    @staticmethod
    def _finish_records(
        record: JevEvaluationRecord,
        attempt: JevAttemptRecord,
        result: JevEvaluationV1,
    ) -> None:
        record.status = result.status.value
        record.updated_at = as_utc(result.finished_at)
        attempt.started_at = as_utc(result.started_at)
        attempt.finished_at = as_utc(result.finished_at)
        attempt.latency_ms = result.latency_ms
        attempt.status = result.status.value
        attempt.raw_response_hash = result.raw_response_hash
        attempt.error_code = result.error_code

    @staticmethod
    async def _load_record(
        session: AsyncSession,
        record: JevEvaluationRecord,
    ) -> JevEvaluationV1:
        attempt = await session.scalar(
            select(JevAttemptRecord).where(
                JevAttemptRecord.evaluation_id == record.id,
                JevAttemptRecord.sequence == record.latest_attempt_sequence,
            )
        )
        if attempt is None:
            raise JevRepositoryError("JEV_ATTEMPT_NOT_FOUND")
        question_records = tuple(
            await session.scalars(
                select(JevQuestionResultRecord).where(
                    JevQuestionResultRecord.evaluation_id == record.id
                )
            )
        )
        order = {question_id: index for index, question_id in enumerate(QUESTION_DEFINITIONS)}
        question_records = tuple(
            sorted(question_records, key=lambda item: order.get(item.question_id, 10_000))
        )
        results = tuple(
            JevQuestionResultV1(
                question_id=item.question_id,
                question_version=item.question_version,
                criteria_version=item.criteria_version,
                label_order=tuple(item.label_order_json),
                distribution={
                    label: Decimal(value) for label, value in item.distribution_json.items()
                },
                selected_label=item.selected_label,
            )
            for item in question_records
        )
        started_at = _stored_utc(attempt.started_at)
        finished_at = (
            _stored_utc(attempt.finished_at)
            if attempt.finished_at is not None
            else started_at
        )
        return JevEvaluationV1(
            evaluation_id=record.id,
            formal_key=record.formal_key,
            scope=JevScope(record.scope),
            symbol=record.symbol,
            status=JevEvaluationStatus(record.status),
            results=results,
            provider_name=record.provider_name,
            provider_version=record.provider_version,
            model_id=record.model_id,
            state_schema_version=record.state_schema_version,
            question_set_version=record.question_set_version,
            input_hash=record.input_hash,
            raw_response_hash=attempt.raw_response_hash,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=attempt.latency_ms,
            error_code=attempt.error_code,
        )
