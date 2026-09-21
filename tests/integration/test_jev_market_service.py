import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fkqt_jevinvestor.domain.enums import ProviderStatus
from fkqt_jevinvestor.domain.jev_market import (
    JevEvaluationCommand,
    JevEvaluationStatus,
    JevEvaluationV1,
    JevQuestionResultV1,
    JevScope,
    JevSymbolStateV1,
)
from fkqt_jevinvestor.domain.market_features import (
    AdjustmentMode,
    DailyBar,
    FeatureValue,
    MarketFeatureSnapshot,
    MarketSnapshot,
    SecurityTradeState,
)
from fkqt_jevinvestor.persistence.jev_repository import JevEvaluationRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.providers.base import (
    ProviderContractError,
    ProviderHealth,
    ProviderUnavailableError,
)
from fkqt_jevinvestor.providers.jev_market_questions import QUESTION_DEFINITIONS
from fkqt_jevinvestor.services.jev_market_service import (
    JevMarketEvaluationService,
    JevRunCommandV1,
)
from fkqt_jevinvestor.services.jev_state_builder import REQUIRED_SYMBOL_FEATURES

SessionFactory = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[SessionFactory]:
    database = tmp_path / "jev-service.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database.as_posix()}")
    await asyncio.to_thread(alembic_command.upgrade, config, "head")
    engine = create_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    yield create_session_factory(engine)
    await engine.dispose()


class FakeProvider:
    def __init__(
        self,
        *,
        failure: Exception | None = None,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.failure = failure
        self.gate = gate
        self.universe_calls = 0
        self.symbol_calls = 0
        self.states: list[str] = []

    async def evaluate(self, command: JevEvaluationCommand) -> JevEvaluationV1:
        if command.scope is JevScope.UNIVERSE:
            self.universe_calls += 1
        else:
            self.symbol_calls += 1
        self.states.append(command.state.model_dump_json())
        if self.gate is not None and command.scope is JevScope.UNIVERSE:
            await self.gate.wait()
        if self.failure is not None:
            raise self.failure
        now = datetime(2026, 9, 18, 15, 0, 1, tzinfo=UTC)
        results: list[JevQuestionResultV1] = []
        for definition in QUESTION_DEFINITIONS.values():
            if definition.scope is not command.scope:
                continue
            distribution = {
                label: Decimal(0) for label in definition.label_order
            }
            distribution[definition.label_order[0]] = Decimal(1)
            results.append(
                JevQuestionResultV1(
                    question_id=definition.question_id,
                    question_version=definition.question_version,
                    criteria_version=definition.criteria_version,
                    label_order=definition.label_order,
                    distribution=distribution,
                    selected_label=definition.label_order[0],
                )
            )
        return JevEvaluationV1(
            evaluation_id=f"jev-{command.formal_key[:24]}",
            formal_key=command.formal_key,
            scope=command.scope,
            symbol=(
                command.state.symbol
                if isinstance(command.state, JevSymbolStateV1)
                else None
            ),
            status=JevEvaluationStatus.AVAILABLE,
            results=tuple(results),
            provider_name=command.provider_name,
            provider_version=command.provider_version,
            model_id=command.model_id,
            state_schema_version=command.state.header.state_schema_version,
            question_set_version=command.question_set_version,
            input_hash=command.input_hash,
            raw_response_hash="d" * 64,
            started_at=now,
            finished_at=now,
            latency_ms=1,
            error_code=None,
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider="fake",
            status=ProviderStatus.AVAILABLE,
            checked_at=datetime.now(UTC),
        )


def _bar(symbol: str) -> DailyBar:
    return DailyBar(
        symbol=symbol,
        trade_date=date(2026, 9, 18),
        open=Decimal(10),
        high=Decimal("10.2"),
        low=Decimal("9.8"),
        close=Decimal("10.1"),
        previous_close=Decimal(10),
        volume=Decimal(1000),
        amount_cny=Decimal(10000),
        adjustment_mode=AdjustmentMode.QFQ,
    )


def _run_command(
    *,
    run_id: UUID | None = None,
    missing_symbol: str | None = None,
) -> JevRunCommandV1:
    symbols = ("B", "A", "HELD")
    cutoff = datetime(2026, 9, 18, 15, tzinfo=UTC)
    snapshot = MarketSnapshot(
        snapshot_id="snapshot-1",
        decision_date=date(2026, 9, 18),
        decision_cutoff=cutoff,
        next_trade_date=date(2026, 9, 21),
        calendar_complete_through=date(2026, 9, 30),
        universe_snapshot_id="universe-1",
        universe_snapshot_hash="a" * 64,
        daily_bars={symbol: (_bar(symbol),) for symbol in symbols},
        security_states={
            symbol: SecurityTradeState(
                symbol=symbol,
                trade_date=date(2026, 9, 18),
                trading_day_status="OPEN",
                trading_status="TRADING",
                is_st_or_delisting_risk=False,
                upper_limit_price=Decimal(11),
                lower_limit_price=Decimal(9),
                is_initial_no_limit_period=False,
                corporate_action_status="NONE",
                market="SSE",
                board="MAIN",
                listing_date=date(2020, 1, 1),
            )
            for symbol in symbols
        },
        source_manifest_ids=("manifest-1",),
        source_audits=(),
        content_hash="b" * 64,
    )
    features: dict[str, MarketFeatureSnapshot] = {}
    for symbol in symbols:
        values: dict[str, FeatureValue] = {}
        for index, code in enumerate(REQUIRED_SYMBOL_FEATURES, start=1):
            is_missing = symbol == missing_symbol and code == "return_60d"
            values[code] = FeatureValue(
                feature_code=code,
                feature_version="market-features-v1",
                as_of=cutoff,
                lookback_window=20,
                value=None if is_missing else Decimal(index) / Decimal(100),
                missing_reason="INSUFFICIENT_HISTORY" if is_missing else None,
                source_snapshot_hash="b" * 64,
            )
        features[symbol] = MarketFeatureSnapshot(
            symbol=symbol,
            decision_date=date(2026, 9, 18),
            values=values,
            content_hash="c" * 64,
        )
    return JevRunCommandV1(
        run_id=run_id or uuid4(),
        snapshot=snapshot,
        features=features,
        candidate_symbols=("B", "A"),
        candidate_limit=20,
        held_symbols=("HELD",),
        provider_name="typesafe",
        provider_version="typesafe-sdk-test",
        model_id="jev-market-test",
    )


def _service(
    session_factory: SessionFactory,
    provider: FakeProvider,
) -> JevMarketEvaluationService:
    return JevMarketEvaluationService(
        provider=provider,
        repository=JevEvaluationRepository(session_factory),
    )


@pytest.mark.asyncio
async def test_same_formal_run_calls_once_then_reuses_all_results(
    session_factory: SessionFactory,
) -> None:
    provider = FakeProvider()
    service = _service(session_factory, provider)
    run_command = _run_command()

    first = await service.evaluate_run(run_command)
    second = await service.evaluate_run(run_command)

    assert provider.universe_calls == 1
    assert provider.symbol_calls == 3
    assert first == second
    assert tuple(first.symbols) == ("A", "B", "HELD")
    assert len(first.symbols) == 3
    assert all("HELD_ONLY" not in state for state in provider.states)
    payload = first.model_dump_json()
    for action in ("ENTER", "KEEP", "EXIT", "AVOID"):
        assert action not in payload


@pytest.mark.asyncio
async def test_missing_required_feature_records_data_unavailable_without_symbol_call(
    session_factory: SessionFactory,
) -> None:
    provider = FakeProvider()
    service = _service(session_factory, provider)

    result = await service.evaluate_run(_run_command(missing_symbol="B"))

    assert result.symbols["B"].status == JevEvaluationStatus.DATA_UNAVAILABLE
    assert result.symbols["B"].results == ()
    assert result.symbols["B"].error_code == "REQUIRED_FEATURE_MISSING"
    assert provider.universe_calls == 1
    assert provider.symbol_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "status", "response_hash"),
    [
        (
            ProviderUnavailableError("ConnectionError"),
            JevEvaluationStatus.PROVIDER_UNAVAILABLE,
            None,
        ),
        (
            ProviderContractError("JEV_RESPONSE_CONTRACT_INVALID", "e" * 64),
            JevEvaluationStatus.CONTRACT_INVALID,
            "e" * 64,
        ),
    ],
)
async def test_provider_failure_is_audited_without_fake_probabilities(
    session_factory: SessionFactory,
    failure: Exception,
    status: JevEvaluationStatus,
    response_hash: str | None,
) -> None:
    provider = FakeProvider(failure=failure)
    service = _service(session_factory, provider)

    result = await service.evaluate_run(_run_command())

    evaluations = (result.universe, *result.symbols.values())
    assert all(item.status == status for item in evaluations)
    assert all(item.results == () for item in evaluations)
    assert all(item.raw_response_hash == response_hash for item in evaluations)


@pytest.mark.asyncio
async def test_concurrent_same_run_only_claim_owner_calls_provider(
    session_factory: SessionFactory,
) -> None:
    gate = asyncio.Event()
    provider = FakeProvider(gate=gate)
    service = _service(session_factory, provider)
    run_command = _run_command()

    owner = asyncio.create_task(service.evaluate_run(run_command))
    while provider.universe_calls == 0:
        await asyncio.sleep(0)
    follower = await service.evaluate_run(run_command)
    assert follower.universe.status == JevEvaluationStatus.IN_PROGRESS
    assert provider.universe_calls == 1
    assert provider.symbol_calls == 3

    gate.set()
    completed = await owner
    assert completed.universe.status == JevEvaluationStatus.AVAILABLE
    assert provider.universe_calls == 1
    assert provider.symbol_calls == 3
