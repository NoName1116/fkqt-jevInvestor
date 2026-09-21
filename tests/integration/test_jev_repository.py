import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationCommand,
    JevEvaluationStatus,
    JevEvaluationV1,
    JevQuestionResultV1,
    JevScope,
    JevStateHeaderV1,
    JevSymbolStateV1,
)
from fkqt_jevinvestor.persistence.jev_repository import (
    ClaimStatus,
    JevClaimConflict,
    JevEvaluationRepository,
)
from fkqt_jevinvestor.persistence.models import (
    JevAttemptRecord,
    JevQuestionResultRecord,
    JevRunLinkRecord,
)
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.providers.jev_market_questions import QUESTION_DEFINITIONS

SessionFactory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[SessionFactory]:
    database = tmp_path / "jev-repository.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    yield create_session_factory(engine)
    await engine.dispose()


def _command(*, security: dict[str, str] | None = None) -> JevEvaluationCommand:
    header = JevStateHeaderV1(
        decision_date=date(2026, 9, 18),
        decision_cutoff=datetime(2026, 9, 18, 15, tzinfo=UTC),
        candidate_universe_id="universe-v1",
        candidate_universe_hash="a" * 64,
        candidate_limit=20,
        candidate_actual_size=1,
        market_snapshot_hash="b" * 64,
        feature_set_version="market-features-v1",
    )
    state = JevSymbolStateV1(
        header=header,
        symbol="600000.SH",
        security=security or {"market": "SSE"},
        features={"return_5d": Decimal("0.01000000")},
        missing_reasons=(),
    )
    return JevEvaluationCommand(
        scope=JevScope.SYMBOL,
        state=state,
        provider_name="typesafe",
        provider_version="typesafe-sdk-test",
        model_id="jev-market-test",
    )


def _question(question_id: str = "profitability_5d") -> JevQuestionResultV1:
    definition = QUESTION_DEFINITIONS[question_id]
    distribution = {label: Decimal(0) for label in definition.label_order}
    distribution[definition.label_order[0]] = Decimal(1)
    return JevQuestionResultV1(
        question_id=question_id,
        question_version=definition.question_version,
        criteria_version=definition.criteria_version,
        label_order=definition.label_order,
        distribution=distribution,
        selected_label=definition.label_order[0],
    )


def _result(
    command_value: JevEvaluationCommand,
    *,
    status: JevEvaluationStatus = JevEvaluationStatus.AVAILABLE,
    results: tuple[JevQuestionResultV1, ...] | None = None,
    error_code: str | None = None,
) -> JevEvaluationV1:
    now = datetime(2026, 9, 18, 15, 0, 1, tzinfo=UTC)
    available_results = results if results is not None else tuple(
        _question(definition.question_id)
        for definition in QUESTION_DEFINITIONS.values()
        if definition.scope is JevScope.SYMBOL
    )
    return JevEvaluationV1(
        evaluation_id=f"jev-{command_value.formal_key[:24]}",
        formal_key=command_value.formal_key,
        scope=command_value.scope,
        symbol="600000.SH",
        status=status,
        results=available_results if status is JevEvaluationStatus.AVAILABLE else (),
        provider_name=command_value.provider_name,
        provider_version=command_value.provider_version,
        model_id=command_value.model_id,
        state_schema_version=command_value.state.header.state_schema_version,
        question_set_version=command_value.question_set_version,
        input_hash=command_value.input_hash,
        raw_response_hash="c" * 64 if status is JevEvaluationStatus.AVAILABLE else None,
        started_at=now,
        finished_at=now,
        latency_ms=5,
        error_code=error_code,
    )


async def _count(session_factory: SessionFactory, model: type[object]) -> int:
    async with session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


@pytest.mark.asyncio
async def test_claim_lifecycle_and_cross_run_cache_link(
    session_factory: SessionFactory,
) -> None:
    repository = JevEvaluationRepository(session_factory)
    command_value = _command()
    first_run, second_run = uuid4(), uuid4()

    first = await repository.claim(first_run, command_value)
    in_progress = await repository.claim(second_run, command_value)
    await repository.record_success(first, _result(command_value))
    complete = await repository.claim(second_run, command_value)

    assert first.status == ClaimStatus.ACQUIRED
    assert first.attempt_sequence == 1
    assert in_progress.status == ClaimStatus.IN_PROGRESS
    assert complete.status == ClaimStatus.COMPLETE
    assert complete.existing_result is not None
    assert await _count(session_factory, JevAttemptRecord) == 1
    assert await _count(session_factory, JevRunLinkRecord) == 2


@pytest.mark.asyncio
async def test_success_round_trips_decimal_questions_atomically(
    session_factory: SessionFactory,
) -> None:
    repository = JevEvaluationRepository(session_factory)
    command_value = _command()
    claim = await repository.claim(uuid4(), command_value)
    expected = _result(command_value)

    await repository.record_success(claim, expected)
    loaded = await repository.load_formal(command_value.formal_key)

    assert loaded is not None
    assert loaded == expected
    assert loaded.results[1].distribution["PROFITABLE"] == Decimal(1)
    assert await _count(session_factory, JevQuestionResultRecord) == 5


@pytest.mark.asyncio
async def test_duplicate_question_rolls_back_entire_success(
    session_factory: SessionFactory,
) -> None:
    repository = JevEvaluationRepository(session_factory)
    command_value = _command()
    claim = await repository.claim(uuid4(), command_value)
    duplicate = _question()
    invalid = _result(command_value).model_copy(update={"results": (duplicate, duplicate)})

    with pytest.raises(ValidationError, match="JEV_DUPLICATE_QUESTION_RESULT"):
        await repository.record_success(claim, invalid)

    assert await _count(session_factory, JevQuestionResultRecord) == 0
    loaded = await repository.load_formal(command_value.formal_key)
    assert loaded is not None
    assert loaded.status == JevEvaluationStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_failure_has_no_probabilities_and_can_retry(
    session_factory: SessionFactory,
) -> None:
    repository = JevEvaluationRepository(session_factory)
    command_value = _command()
    first = await repository.claim(uuid4(), command_value)
    failed = _result(
        command_value,
        status=JevEvaluationStatus.PROVIDER_UNAVAILABLE,
        error_code="CONNECTION_ERROR",
    )

    await repository.record_failure(first, failed)
    retry = await repository.claim(uuid4(), command_value)

    assert retry.status == ClaimStatus.ACQUIRED
    assert retry.attempt_sequence == 2
    assert await _count(session_factory, JevAttemptRecord) == 2
    assert await _count(session_factory, JevQuestionResultRecord) == 0


@pytest.mark.asyncio
async def test_success_cannot_be_reclaimed(session_factory: SessionFactory) -> None:
    repository = JevEvaluationRepository(session_factory)
    command_value = _command()
    claim = await repository.claim(uuid4(), command_value)
    await repository.record_success(claim, _result(command_value))

    later = await repository.claim(uuid4(), command_value)
    assert later.status == ClaimStatus.COMPLETE
    assert later.attempt_id is None
    assert later.attempt_sequence is None
    assert await _count(session_factory, JevAttemptRecord) == 1


@pytest.mark.asyncio
async def test_two_concurrent_claims_have_one_owner(
    session_factory: SessionFactory,
) -> None:
    first_repository = JevEvaluationRepository(session_factory)
    second_repository = JevEvaluationRepository(session_factory)
    command_value = _command()
    claims = await asyncio.gather(
        first_repository.claim(uuid4(), command_value),
        second_repository.claim(uuid4(), command_value),
    )

    assert [claim.status for claim in claims].count(ClaimStatus.ACQUIRED) == 1
    assert [claim.status for claim in claims].count(ClaimStatus.IN_PROGRESS) == 1
    assert await _count(session_factory, JevAttemptRecord) == 1


@pytest.mark.asyncio
async def test_attempt_audit_is_ordered(session_factory: SessionFactory) -> None:
    repository = JevEvaluationRepository(session_factory)
    command_value = _command()
    first = await repository.claim(uuid4(), command_value)
    await repository.record_failure(
        first,
        _result(
            command_value,
            status=JevEvaluationStatus.CONTRACT_INVALID,
            error_code="JEV_RESPONSE_CONTRACT_INVALID",
        ),
    )
    second = await repository.claim(uuid4(), command_value)

    attempts = await repository.list_attempts(command_value.formal_key)
    assert [item.sequence for item in attempts] == [1, 2]
    assert attempts[0].status == JevEvaluationStatus.CONTRACT_INVALID
    assert attempts[1].status == JevEvaluationStatus.IN_PROGRESS
    assert second.attempt_sequence == 2


@pytest.mark.asyncio
async def test_sensitive_state_key_is_rejected_before_persistence(
    session_factory: SessionFactory,
) -> None:
    repository = JevEvaluationRepository(session_factory)
    command_value = _command()
    bad_state = command_value.state.model_copy(
        update={"security": {"market": "SSE", "api_key": "secret-value"}}
    )
    command_value = command_value.model_copy(update={"state": bad_state})

    with pytest.raises(ValueError, match="SENSITIVE_STATE_FIELD_FORBIDDEN"):
        await repository.claim(UUID(int=1), command_value)

    assert await _count(session_factory, JevAttemptRecord) == 0


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 18, 15, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


@pytest.mark.asyncio
async def test_expired_claim_is_reacquired_and_old_owner_cannot_commit(
    session_factory: SessionFactory,
) -> None:
    clock = MutableClock()
    first_repository = JevEvaluationRepository(
        session_factory,
        lease_duration=timedelta(seconds=30),
        clock=clock,
    )
    second_repository = JevEvaluationRepository(
        session_factory,
        lease_duration=timedelta(seconds=30),
        clock=clock,
    )
    command_value = _command()
    first = await first_repository.claim(uuid4(), command_value)
    clock.value += timedelta(seconds=31)

    replacement = await second_repository.claim(uuid4(), command_value)

    assert replacement.status == ClaimStatus.ACQUIRED
    assert replacement.attempt_sequence == 2
    with pytest.raises(JevClaimConflict, match="JEV_CLAIM_NOT_ACTIVE"):
        await first_repository.record_success(first, _result(command_value))


@pytest.mark.asyncio
async def test_repository_rejects_result_metadata_mismatch(
    session_factory: SessionFactory,
) -> None:
    repository = JevEvaluationRepository(session_factory)
    command_value = _command()
    claim = await repository.claim(uuid4(), command_value)
    mismatched = _result(command_value).model_copy(update={"provider_name": "other"})

    with pytest.raises(JevClaimConflict, match="JEV_RESULT_IDENTITY_MISMATCH"):
        await repository.record_success(claim, mismatched)
