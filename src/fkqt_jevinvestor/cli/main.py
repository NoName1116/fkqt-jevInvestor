import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from fkqt_jevinvestor.config import Settings
from fkqt_jevinvestor.domain.market_time import market_close
from fkqt_jevinvestor.ingestion.fkqt_manifest import FkqtManifestProvider
from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
from fkqt_jevinvestor.persistence.market_repository import MarketSnapshotRepository
from fkqt_jevinvestor.persistence.decision_repository import DecisionEvaluationRepository
from fkqt_jevinvestor.persistence.jev_repository import JevEvaluationRepository
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.domain.decision import PendingOrderSummaryV1
from fkqt_jevinvestor.services.c_group_decision import (
    CGroupDecisionCommandV1,
    CGroupDecisionService,
)
from fkqt_jevinvestor.services.jev_market_service import (
    JevMarketEvaluationService,
    JevRunCommandV1,
)
from fkqt_jevinvestor.services.market_pipeline import MarketPipeline
from fkqt_jevinvestor.services.position_sizing import PositionSizingConfigV1


def decision_cutoff_for_date(decision_date: date) -> datetime:
    return market_close(decision_date)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("CANDIDATE_LIMIT_MUST_BE_POSITIVE")
    return parsed


async def _freeze_manifest(args: argparse.Namespace, settings: Settings) -> int:
    if settings.fkqt_manifest_bundle_root is None:
        print("FKQT_MANIFEST_BUNDLE_ROOT_REQUIRED", file=sys.stderr)
        return 2
    symbols = tuple(symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip())
    decision_cutoff = decision_cutoff_for_date(args.decision_date)
    engine = create_engine(settings.database_url)
    try:
        pipeline = MarketPipeline(
            FkqtManifestProvider(settings.fkqt_manifest_bundle_root),
            MarketSnapshotStore(settings.market_snapshot_root),
            MarketSnapshotRepository(create_session_factory(engine)),
        )
        snapshot, features = await pipeline.freeze_and_compute(
            symbols,
            args.decision_date,
            decision_cutoff,
        )
        print(
            json.dumps(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "content_hash": snapshot.content_hash,
                    "decision_date": snapshot.decision_date.isoformat(),
                    "next_trade_date": snapshot.next_trade_date.isoformat(),
                    "feature_symbols": sorted(features),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    finally:
        await engine.dispose()


async def _run_c_group(args: argparse.Namespace, settings: Settings) -> int:
    if (
        settings.c_group_snapshot_hash is None
        or settings.c_group_candidate_symbols is None
    ):
        print("C_GROUP_SNAPSHOT_CONFIG_REQUIRED", file=sys.stderr)
        return 2
    if settings.typesafe_api_key is None or settings.deepseek_api_key is None:
        print("C_GROUP_PROVIDER_CONFIG_REQUIRED", file=sys.stderr)
        return 2
    candidate_symbols = tuple(
        symbol.strip().upper()
        for symbol in settings.c_group_candidate_symbols.split(",")
        if symbol.strip()
    )
    if not candidate_symbols or len(candidate_symbols) > args.candidate_limit:
        print("C_GROUP_CANDIDATE_CONFIG_INVALID", file=sys.stderr)
        return 2

    from fkqt_jevinvestor.providers.deepseek_decision import DeepSeekDecisionProvider
    from fkqt_jevinvestor.providers.jev_market import TypeSafeJevMarketProvider

    engine = create_engine(settings.database_url)
    try:
        session_factory = create_session_factory(engine)
        market_repository = MarketSnapshotRepository(session_factory)
        stored_inputs = await market_repository.load_run_inputs(
            args.decision_date,
            settings.c_group_snapshot_hash,
        )
        snapshot = MarketSnapshotStore(settings.market_snapshot_root).load(
            args.decision_date,
            settings.c_group_snapshot_hash,
        )
        if stored_inputs.reference.snapshot_id != snapshot.snapshot_id:
            raise ValueError("C_GROUP_SNAPSHOT_IDENTITY_MISMATCH")
        portfolio_repository = PortfolioRepository(session_factory)
        portfolio = await portfolio_repository.get_state(
            args.portfolio_id,
            as_of=args.decision_date,
        )
        required_symbols = set(candidate_symbols) | {
            position.symbol for position in portfolio.positions
        }
        if not required_symbols.issubset(stored_inputs.features):
            raise ValueError("C_GROUP_FEATURE_COVERAGE_INCOMPLETE")

        run_id = uuid4()
        jev_provider_version = "typesafe-sdk-0.7.0"
        jev_service = JevMarketEvaluationService(
            provider=TypeSafeJevMarketProvider.from_api_key(
                settings.typesafe_api_key,
                settings.typesafe_model,
                jev_provider_version,
            ),
            repository=JevEvaluationRepository(session_factory),
        )
        jev = await jev_service.evaluate_run(
            JevRunCommandV1(
                run_id=run_id,
                snapshot=snapshot,
                features=stored_inputs.features,
                candidate_symbols=candidate_symbols,
                candidate_limit=args.candidate_limit,
                held_symbols=tuple(sorted(required_symbols - set(candidate_symbols))),
                provider_name="typesafe",
                provider_version=jev_provider_version,
                model_id=settings.typesafe_model,
            )
        )
        decision_repository = DecisionEvaluationRepository(session_factory)
        pending = await portfolio_repository.load_pending_orders(
            args.portfolio_id,
            snapshot.next_trade_date,
        )
        pending_by_symbol = {
            symbol: tuple(
                PendingOrderSummaryV1(
                    action=order.action.value,
                    planned_execution_date=order.planned_execution_date,
                    status=order.status.value,
                )
                for order in pending
                if order.symbol == symbol
            )
            for symbol in required_symbols
        }
        recent_actions = {
            symbol: await decision_repository.recent_actions(
                args.portfolio_id,
                symbol,
            )
            for symbol in required_symbols
        }
        total_equity = portfolio.cash_balance + sum(
            (
                position.last_price * position.quantity
                for position in portfolio.positions
            ),
            Decimal(0),
        )
        decision_provider_version = "deepseek-responses-v1"
        decision_service = CGroupDecisionService(
            provider=DeepSeekDecisionProvider.from_api_key(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
                provider_version=decision_provider_version,
                reasoning_effort=settings.deepseek_reasoning_effort,
                timeout_seconds=settings.deepseek_timeout_seconds,
            ),
            decision_repository=decision_repository,
            portfolio_repository=portfolio_repository,
        )
        result = await decision_service.evaluate_run(
            CGroupDecisionCommandV1(
                run_id=run_id,
                snapshot=snapshot,
                features=stored_inputs.features,
                candidate_symbols=candidate_symbols,
                candidate_limit=args.candidate_limit,
                jev=jev,
                portfolio=portfolio,
                total_equity=total_equity,
                recent_actions=recent_actions,
                pending_orders=pending_by_symbol,
                provider_name="deepseek",
                provider_version=decision_provider_version,
                model_id=settings.deepseek_model,
                sizing_config=PositionSizingConfigV1(),
            )
        )
        print(
            json.dumps(
                {
                    "run_id": str(result.run_id),
                    "signal_batch_id": result.stored_batch_id,
                    "decision_count": len(result.evaluations),
                    "order_count": len(result.stored_orders),
                    "target_batch_hash": result.sizing_run.target_batch_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except (RuntimeError, ValueError) as exc:
        message = str(exc)
        print(message if message.isupper() else type(exc).__name__, file=sys.stderr)
        return 2
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fkqt-jevinvestor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version")
    market_parser = subparsers.add_parser("market")
    market_subparsers = market_parser.add_subparsers(dest="market_command", required=True)
    freeze_parser = market_subparsers.add_parser("freeze")
    freeze_parser.add_argument("--date", dest="decision_date", type=date.fromisoformat, required=True)
    freeze_parser.add_argument("--symbols", required=True)
    freeze_parser.add_argument("--source", choices=("manifest",), required=True)
    decision_parser = subparsers.add_parser("decision")
    decision_subparsers = decision_parser.add_subparsers(
        dest="decision_command",
        required=True,
    )
    c_group_parser = decision_subparsers.add_parser("run-c-group")
    c_group_parser.add_argument(
        "--date",
        dest="decision_date",
        type=date.fromisoformat,
        required=True,
    )
    c_group_parser.add_argument("--portfolio-id", required=True)
    c_group_parser.add_argument("--candidate-limit", type=_positive_int, required=True)
    args = parser.parse_args(argv)
    if args.command == "version":
        print("fkqt-jevinvestor 0.1.0")
        return 0
    if args.command == "market" and args.market_command == "freeze":
        return asyncio.run(_freeze_manifest(args, Settings()))
    if args.command == "decision" and args.decision_command == "run-c-group":
        return asyncio.run(_run_c_group(args, Settings()))
    return 2
