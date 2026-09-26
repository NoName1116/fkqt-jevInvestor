from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from fkqt_jevinvestor.operations.config import ForwardOperationConfig
from fkqt_jevinvestor.operations.runner import ForwardRunner
from fkqt_jevinvestor.persistence.decision_repository import DecisionEvaluationRepository
from fkqt_jevinvestor.persistence.forward_operation_repository import ForwardOperationRepository
from fkqt_jevinvestor.persistence.models import NavRecord
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.services.c_group_decision import CGroupDecisionService
from tests.integration import test_c_group_decision as fixtures
from tests.unit.test_forward_operation_config import values

session_factory = fixtures.session_factory


@pytest.mark.asyncio
async def test_verify_rejects_committed_data_unavailable_decisions(session_factory: fixtures.SessionFactory, tmp_path: Path) -> None:
    run_id = uuid4()
    jev = fixtures._jev_run(run_id)  # pyright: ignore[reportPrivateUsage]
    await fixtures._seed_jev(session_factory, jev)  # pyright: ignore[reportPrivateUsage]
    await CGroupDecisionService(provider=fixtures.FakeDecisionProvider(), decision_repository=DecisionEvaluationRepository(session_factory), portfolio_repository=PortfolioRepository(session_factory)).evaluate_run(fixtures._command(run_id, jev))  # pyright: ignore[reportPrivateUsage]
    day = date(2026, 9, 18)
    async with session_factory.begin() as session:
        session.add(NavRecord(id=uuid4().hex, portfolio_id="paper-main", valuation_date=day, total_equity=Decimal(1000000), unit_nav=Decimal(1), daily_return=Decimal(0), cumulative_return=Decimal(0), max_drawdown=Decimal(0)))
    config = ForwardOperationConfig.model_validate({**values(tmp_path), "portfolio_id": "paper-main"})
    runner = ForwardRunner(ForwardOperationRepository(session_factory))
    with pytest.raises(ValueError, match="FORWARD_DATA_DEGRADED"):
        await runner._stage(config, day, "VERIFY", tmp_path, {}, 30)  # pyright: ignore[reportPrivateUsage]
