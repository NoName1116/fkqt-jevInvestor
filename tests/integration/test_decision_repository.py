import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.domain.decision import (
    DecisionAction,
    DecisionEvaluationCommand,
    DecisionEvaluationStatus,
    DecisionEvaluationV1,
)
from fkqt_jevinvestor.persistence.decision_repository import (
    ClaimStatus,
    DecisionClaimConflict,
    DecisionEvaluationRepository,
)
from fkqt_jevinvestor.persistence.models import JevEvaluationRecord
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.services.portfolio_service import CreatePortfolio
from tests.unit.test_decision_contracts import _input  # pyright: ignore[reportPrivateUsage]

SessionFactory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[SessionFactory]:
    database = tmp_path / "decision-repository.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    await asyncio.to_thread(alembic_command.upgrade, config, "head")
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    factory = create_session_factory(engine)
    portfolio_repository = PortfolioRepository(factory)
    await portfolio_repository.create(
        CreatePortfolio(portfolio_id="paper-main", name="Paper")
    )
    now = datetime(2026, 9, 18, 15, tzinfo=UTC)
    async with factory.begin() as session:
        for evaluation_id, scope, symbol in (
            ("jev-universe", "UNIVERSE", None),
            ("jev-symbol", "SYMBOL", "600000.SH"),
        ):
            session.add(
                JevEvaluationRecord(
                    id=evaluation_id,
                    formal_key=("1" if scope == "UNIVERSE" else "2") * 64,
                    scope=scope,
                    symbol=symbol,
                    decision_date=now.date(),
                    decision_cutoff=now,
                    state_json={},
                    input_hash="3" * 64,
                    provider_name="typesafe",
                    provider_version="0.7.0",
                    model_id="jev-market",
                    state_schema_version="jev-state-v1",
                    question_set_version="jev-pnl-questions-v1",
                    status="AVAILABLE",
                    latest_attempt_sequence=1,
                    current_owner_token=None,
                    lease_expires_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
    yield factory
    await engine.dispose()


def _command() -> DecisionEvaluationCommand:
    return DecisionEvaluationCommand(
        decision_input=_input(),
        provider_name="deepseek",
        provider_version="responses-v1",
        model_id="deepseek-flash",
    )


def _result(
    command: DecisionEvaluationCommand,
    evaluation_id: str,
) -> DecisionEvaluationV1:
    now = datetime(2026, 9, 18, 15, 0, 1, tzinfo=UTC)
    return DecisionEvaluationV1(
        evaluation_id=evaluation_id,
        formal_key=command.formal_key,
        input_hash=command.input_hash,
        symbol=command.decision_input.symbol,
        status=DecisionEvaluationStatus.AVAILABLE,
        action=DecisionAction.ENTER,
        thesis="冻结证据一致。",
        invalidation="趋势结构失效。",
        raw_response_text=(
            '{"action":"ENTER","thesis":"冻结证据一致。",'
            '"invalidation":"趋势结构失效。"}'
        ),
        raw_response_hash="4" * 64,
        provider_name=command.provider_name,
        provider_version=command.provider_version,
        model_id=command.model_id,
        prompt_version=command.prompt_version,
        output_schema_version=command.output_schema_version,
        started_at=now,
        finished_at=now,
        latency_ms=1,
    )


@pytest.mark.asyncio
async def test_claim_success_cache_and_recent_action(session_factory: SessionFactory) -> None:
    repository = DecisionEvaluationRepository(session_factory)
    command = _command()
    first_run, second_run = uuid4(), uuid4()

    claim = await repository.claim(first_run, command)
    stored = await repository.record_success(
        claim,
        _result(command, claim.evaluation_id),
    )
    cached = await repository.claim(second_run, command)
    history = await repository.recent_actions("paper-main", "600000.SH")

    assert claim.status is ClaimStatus.ACQUIRED
    assert stored.action is DecisionAction.ENTER
    assert cached.status is ClaimStatus.COMPLETE
    assert cached.existing_result == stored
    assert [(item.decision_date, item.action) for item in history] == [
        (command.decision_input.decision_date, DecisionAction.ENTER)
    ]


@pytest.mark.asyncio
async def test_failed_evaluation_can_be_retried_without_overwriting_attempt(
    session_factory: SessionFactory,
) -> None:
    repository = DecisionEvaluationRepository(session_factory)
    command = _command()
    first = await repository.claim(uuid4(), command)
    now = datetime(2026, 9, 18, 15, 0, 1, tzinfo=UTC)

    failure = await repository.record_failure(
        first,
        status=DecisionEvaluationStatus.PROVIDER_UNAVAILABLE,
        error_code="ConnectionError",
        started_at=now,
        finished_at=now,
    )
    retry = await repository.claim(uuid4(), command)

    assert failure.action is DecisionAction.NO_SIGNAL
    assert retry.status is ClaimStatus.ACQUIRED
    assert retry.attempt_sequence == 2


@pytest.mark.asyncio
async def test_two_repository_instances_allow_only_one_initial_owner(
    session_factory: SessionFactory,
) -> None:
    command = _command()
    left = DecisionEvaluationRepository(session_factory)
    right = DecisionEvaluationRepository(session_factory)

    claims = await asyncio.gather(
        left.claim(uuid4(), command),
        right.claim(uuid4(), command),
    )

    assert sorted(item.status.value for item in claims) == ["ACQUIRED", "IN_PROGRESS"]
    acquired = next(item for item in claims if item.status is ClaimStatus.ACQUIRED)
    assert acquired.owner_token is not None


@pytest.mark.asyncio
async def test_expired_owner_cannot_commit_after_single_takeover(
    session_factory: SessionFactory,
) -> None:
    clock_value = datetime(2026, 9, 18, 15, tzinfo=UTC)
    clock = lambda: clock_value
    original_repository = DecisionEvaluationRepository(
        session_factory,
        lease_duration=timedelta(seconds=1),
        clock=clock,
    )
    command = _command()
    original = await original_repository.claim(uuid4(), command)
    clock_value += timedelta(seconds=2)
    left = DecisionEvaluationRepository(session_factory, clock=clock)
    right = DecisionEvaluationRepository(session_factory, clock=clock)

    takeovers = await asyncio.gather(
        left.claim(uuid4(), command),
        right.claim(uuid4(), command),
    )
    replacement = next(item for item in takeovers if item.status is ClaimStatus.ACQUIRED)

    assert sorted(item.status.value for item in takeovers) == ["ACQUIRED", "IN_PROGRESS"]
    with pytest.raises(DecisionClaimConflict, match="DECISION_CLAIM_OWNERSHIP_LOST"):
        await original_repository.record_success(
            original,
            _result(command, original.evaluation_id),
        )
    completed = await left.record_success(
        replacement,
        _result(command, replacement.evaluation_id),
    )
    assert completed.status is DecisionEvaluationStatus.AVAILABLE


@pytest.mark.asyncio
async def test_result_identity_mismatch_rolls_back_claim(session_factory: SessionFactory) -> None:
    repository = DecisionEvaluationRepository(session_factory)
    command = _command()
    claim = await repository.claim(uuid4(), command)
    mismatched = _result(command, claim.evaluation_id).model_copy(
        update={"input_hash": "f" * 64}
    )

    with pytest.raises(DecisionClaimConflict, match="DECISION_RESULT_IDENTITY_MISMATCH"):
        await repository.record_success(claim, mismatched)

    in_progress = await repository.claim(uuid4(), command)
    assert in_progress.status is ClaimStatus.IN_PROGRESS
