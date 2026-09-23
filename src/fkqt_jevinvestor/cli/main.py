import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

from fkqt_jevinvestor.config import Settings
from fkqt_jevinvestor.domain.decision import PendingOrderSummaryV1
from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot
from fkqt_jevinvestor.domain.market_time import market_close
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.ingestion.execution_bundle import (
    ExecutionBundleStore,
    ExecutionBundleV1,
    load_execution_bundle,
)
from fkqt_jevinvestor.ingestion.fkqt_manifest import FkqtManifestProvider
from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
from fkqt_jevinvestor.persistence.decision_repository import DecisionEvaluationRepository
from fkqt_jevinvestor.persistence.jev_repository import JevEvaluationRepository
from fkqt_jevinvestor.persistence.market_repository import MarketSnapshotRepository
from fkqt_jevinvestor.persistence.models import MarketSnapshotRecord, PositionSizingRunRecord
from fkqt_jevinvestor.persistence.repositories import PortfolioRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.services.c_group_decision import (
    CGroupDecisionCommandV1,
    CGroupDecisionService,
)
from fkqt_jevinvestor.services.jev_market_service import (
    JevMarketEvaluationService,
    JevRunCommandV1,
)
from fkqt_jevinvestor.services.market_pipeline import MarketPipeline
from fkqt_jevinvestor.services.portfolio_service import ExecuteTradeDate
from fkqt_jevinvestor.services.position_sizing import PositionSizingConfigV1


def decision_cutoff_for_date(decision_date: date) -> datetime:
    return market_close(decision_date)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("CANDIDATE_LIMIT_MUST_BE_POSITIVE")
    return parsed


def stable_daily_run_id(
    portfolio_id: str,
    decision_date: date,
    snapshot_hash: str,
    candidate_symbols: tuple[str, ...],
    candidate_limit: int,
    portfolio_version: int,
    provider_identity: str = "",
) -> UUID:
    identity = sha256_json({
        "portfolio_id": portfolio_id,
        "decision_date": decision_date.isoformat(),
        "snapshot_hash": snapshot_hash,
        "candidate_symbols": list(candidate_symbols),
        "candidate_limit": candidate_limit,
        "portfolio_version": portfolio_version,
        "provider_identity": provider_identity,
    })
    return uuid5(NAMESPACE_URL, f"fkqt-jevinvestor:daily:v1:{identity}")


def _prepare_execution(args: argparse.Namespace, settings: Settings) -> int:
    try:
        raw = Path(args.raw_file).read_text(encoding="utf-8")
        snapshots = TypeAdapter(dict[str, MarketExecutionSnapshot]).validate_json(raw)
        bundle = ExecutionBundleV1.create(args.trade_date, snapshots)
        path = ExecutionBundleStore(settings.execution_bundle_root).save(bundle)
    except (OSError, RuntimeError, ValueError, ValidationError):
        print("EXECUTION_RAW_INPUT_INVALID", file=sys.stderr)
        return 2
    print(json.dumps({
        "trade_date": args.trade_date.isoformat(),
        "execution_hash": bundle.content_hash,
        "execution_ref": str(path),
        "symbol_count": len(bundle.snapshots),
    }, ensure_ascii=False, sort_keys=True))
    return 0


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


async def _run_c_group(
    args: argparse.Namespace,
    settings: Settings,
    *,
    snapshot_hash: str | None = None,
    candidate_symbols_override: tuple[str, ...] | None = None,
    stable_run: bool = False,
) -> int:
    selected_hash = snapshot_hash or settings.c_group_snapshot_hash
    selected_candidates = candidate_symbols_override or tuple(
        symbol.strip().upper()
        for symbol in (settings.c_group_candidate_symbols or "").split(",")
        if symbol.strip()
    )
    if (
        selected_hash is None
        or not selected_candidates
    ):
        print("C_GROUP_SNAPSHOT_CONFIG_REQUIRED", file=sys.stderr)
        return 2
    if settings.typesafe_api_key is None or settings.deepseek_api_key is None:
        print("C_GROUP_PROVIDER_CONFIG_REQUIRED", file=sys.stderr)
        return 2
    candidate_symbols = selected_candidates
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
            selected_hash,
        )
        snapshot = MarketSnapshotStore(settings.market_snapshot_root).load(
            args.decision_date,
            selected_hash,
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

        run_id = (
            stable_daily_run_id(
                args.portfolio_id,
                args.decision_date,
                selected_hash,
                candidate_symbols,
                args.candidate_limit,
                portfolio.version,
                f"{settings.typesafe_model}:{settings.deepseek_base_url}:"
                f"{settings.deepseek_model}:{settings.deepseek_reasoning_effort}",
            )
            if stable_run else uuid4()
        )
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
                as_of=args.decision_date,
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
                provider_base_url=settings.deepseek_base_url,
                reasoning_effort=settings.deepseek_reasoning_effort,
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


async def _daily_close(args: argparse.Namespace, settings: Settings) -> int:
    if datetime.now().astimezone() < decision_cutoff_for_date(args.decision_date):
        print("DAILY_CLOSE_BEFORE_CUTOFF", file=sys.stderr)
        return 2
    if settings.fkqt_manifest_bundle_root is None:
        print("FKQT_MANIFEST_BUNDLE_ROOT_REQUIRED", file=sys.stderr)
        return 2
    candidates = tuple(
        symbol.strip().upper()
        for symbol in (settings.c_group_candidate_symbols or "").split(",")
        if symbol.strip()
    )
    if not candidates or len(candidates) != len(set(candidates)) or len(candidates) > args.candidate_limit:
        print("C_GROUP_CANDIDATE_CONFIG_INVALID", file=sys.stderr)
        return 2
    if settings.typesafe_api_key is None or settings.deepseek_api_key is None:
        print("C_GROUP_PROVIDER_CONFIG_REQUIRED", file=sys.stderr)
        return 2
    engine = create_engine(settings.database_url)
    try:
        session_factory = create_session_factory(engine)
        portfolio_repository = PortfolioRepository(session_factory)
        portfolio = await portfolio_repository.get_state(
            args.portfolio_id, as_of=args.decision_date
        )
        async with session_factory() as session:
            prior_decision = await session.scalar(
                select(PositionSizingRunRecord.id).where(
                    PositionSizingRunRecord.portfolio_id == args.portfolio_id,
                    PositionSizingRunRecord.planned_execution_date == args.decision_date,
                ).limit(1)
            )
        due_orders = await portfolio_repository.load_pending_orders(
            args.portfolio_id, args.decision_date
        )
        if (prior_decision is not None or due_orders) and not any(
            nav.valuation_date == args.decision_date
            for nav in await portfolio_repository.list_nav(args.portfolio_id)
        ):
            raise ValueError("DAILY_PREVIOUS_EXECUTION_REQUIRED")
        symbols = tuple(sorted(set(candidates) | {item.symbol for item in portfolio.positions}))
        pipeline = MarketPipeline(
            FkqtManifestProvider(
                settings.fkqt_manifest_bundle_root,
                candidate_symbols=candidates,
            ),
            MarketSnapshotStore(settings.market_snapshot_root),
            MarketSnapshotRepository(session_factory),
        )
        snapshot, _ = await pipeline.freeze_and_compute(
            symbols, args.decision_date, decision_cutoff_for_date(args.decision_date)
        )
        expected_run_id = stable_daily_run_id(
            args.portfolio_id,
            args.decision_date,
            snapshot.content_hash,
            candidates,
            args.candidate_limit,
            portfolio.version,
            f"{settings.typesafe_model}:{settings.deepseek_base_url}:"
            f"{settings.deepseek_model}:{settings.deepseek_reasoning_effort}",
        )
        async with session_factory() as session:
            existing_runs = tuple((await session.scalars(
                select(PositionSizingRunRecord).where(
                    PositionSizingRunRecord.portfolio_id == args.portfolio_id,
                    PositionSizingRunRecord.decision_date == args.decision_date,
                )
            )).all())
        if existing_runs and any(
            item.run_id != str(expected_run_id) for item in existing_runs
        ):
            raise ValueError("DAILY_DECISION_INPUT_CONFLICT")
    except (OSError, RuntimeError, ValueError) as exc:
        message = str(exc)
        print(message if message.isupper() else type(exc).__name__, file=sys.stderr)
        return 2
    finally:
        await engine.dispose()
    return await _run_c_group(
        args,
        settings,
        snapshot_hash=snapshot.content_hash,
        candidate_symbols_override=candidates,
        stable_run=True,
    )


async def _daily_execute(args: argparse.Namespace, settings: Settings) -> int:
    if datetime.now().astimezone() < decision_cutoff_for_date(args.trade_date):
        print("DAILY_EXECUTE_BEFORE_CLOSE", file=sys.stderr)
        return 2
    try:
        bundle = load_execution_bundle(Path(args.execution_bundle))
        engine = create_engine(settings.database_url)
        try:
            session_factory = create_session_factory(engine)
            repository = PortfolioRepository(session_factory)
            state = await repository.get_state(args.portfolio_id)
            completed = any(
                item.valuation_date == args.trade_date
                for item in await repository.list_nav(args.portfolio_id)
            )
            required: set[str] = set()
            if not completed:
                due = await repository.load_pending_orders(
                    args.portfolio_id, args.trade_date
                )
                open_orders = await repository.load_pending_orders_through(
                    args.portfolio_id, args.trade_date
                )
                async with session_factory() as session:
                    scheduled = await session.scalar(
                        select(PositionSizingRunRecord.id).where(
                            PositionSizingRunRecord.portfolio_id == args.portfolio_id,
                            PositionSizingRunRecord.planned_execution_date == args.trade_date,
                        ).limit(1)
                    )
                if not due and scheduled is None:
                    raise ValueError("DAILY_EXECUTION_NOT_SCHEDULED")
                required = {item.symbol for item in state.positions if item.quantity > 0}
                required.update(item.symbol for item in open_orders)
            bundle.verify(args.trade_date, required)
            frozen_path = ExecutionBundleStore(settings.execution_bundle_root).save(bundle)
            result = await repository.execute_trade_date(
                ExecuteTradeDate(
                    portfolio_id=args.portfolio_id,
                    trade_date=args.trade_date,
                    expected_version=state.version,
                    market_snapshots=bundle.snapshots,
                    execution_input_hash=bundle.content_hash,
                )
            )
            print(json.dumps({
                "portfolio_id": args.portfolio_id,
                "trade_date": args.trade_date.isoformat(),
                "execution_hash": bundle.content_hash,
                "execution_ref": str(frozen_path),
                "fill_count": len(result.fills),
                "total_equity": str(result.nav.total_equity),
                "nav": str(result.nav.unit_nav),
            }, ensure_ascii=False, sort_keys=True))
            return 0
        finally:
            await engine.dispose()
    except (OSError, RuntimeError, ValueError) as exc:
        message = str(exc)
        print(message if message.isupper() else type(exc).__name__, file=sys.stderr)
        return 2


async def _daily_status(args: argparse.Namespace, settings: Settings) -> int:
    engine = create_engine(settings.database_url)
    try:
        session_factory = create_session_factory(engine)
        repository = PortfolioRepository(session_factory)
        state = await repository.get_state(args.portfolio_id)
        nav = next(
            (item for item in await repository.list_nav(args.portfolio_id)
             if item.valuation_date == args.trade_date),
            None,
        )
        pending = await repository.load_pending_orders(args.portfolio_id, args.trade_date)
        async with session_factory() as session:
            decisions = tuple((await session.scalars(
                select(PositionSizingRunRecord).where(
                    PositionSizingRunRecord.portfolio_id == args.portfolio_id,
                    PositionSizingRunRecord.decision_date == args.trade_date,
                )
            )).all())
            snapshots = tuple((await session.scalars(
                select(MarketSnapshotRecord.content_hash).where(
                    MarketSnapshotRecord.decision_date == args.trade_date
                )
            )).all())
        scheduled_dates = tuple(sorted({item.planned_execution_date for item in decisions}))
        scheduled_pending_count = 0
        for planned_date in scheduled_dates:
            scheduled_pending_count += len(await repository.load_pending_orders(
                args.portfolio_id, planned_date
            ))
        print(json.dumps({
            "portfolio_id": args.portfolio_id,
            "trade_date": args.trade_date.isoformat(),
            "portfolio_version": state.version,
            "decision_run_count": len(decisions),
            "decision_run_ids": sorted(item.run_id for item in decisions),
            "market_snapshot_hashes": sorted(set(snapshots)),
            "planned_execution_dates": [item.isoformat() for item in scheduled_dates],
            "pending_order_count": len(pending),
            "scheduled_pending_order_count": scheduled_pending_count,
            "execution_complete": nav is not None,
            "total_equity": str(nav.total_equity) if nav else None,
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
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
    daily_parser = subparsers.add_parser("daily")
    daily_subparsers = daily_parser.add_subparsers(dest="daily_command", required=True)
    prepare_parser = daily_subparsers.add_parser("prepare-execution")
    prepare_parser.add_argument("--trade-date", type=date.fromisoformat, required=True)
    prepare_parser.add_argument("--raw-file", required=True)
    close_parser = daily_subparsers.add_parser("close")
    close_parser.add_argument("--date", dest="decision_date", type=date.fromisoformat, required=True)
    close_parser.add_argument("--portfolio-id", required=True)
    close_parser.add_argument("--candidate-limit", type=_positive_int, required=True)
    execute_parser = daily_subparsers.add_parser("execute")
    execute_parser.add_argument("--trade-date", type=date.fromisoformat, required=True)
    execute_parser.add_argument("--portfolio-id", required=True)
    execute_parser.add_argument("--execution-bundle", required=True)
    status_parser = daily_subparsers.add_parser("status")
    status_parser.add_argument("--trade-date", type=date.fromisoformat, required=True)
    status_parser.add_argument("--portfolio-id", required=True)
    args = parser.parse_args(argv)
    if args.command == "version":
        print("fkqt-jevinvestor 0.1.0")
        return 0
    if args.command == "market" and args.market_command == "freeze":
        return asyncio.run(_freeze_manifest(args, Settings()))
    if args.command == "decision" and args.decision_command == "run-c-group":
        return asyncio.run(_run_c_group(args, Settings()))
    if args.command == "daily" and args.daily_command == "close":
        return asyncio.run(_daily_close(args, Settings()))
    if args.command == "daily" and args.daily_command == "prepare-execution":
        return _prepare_execution(args, Settings())
    if args.command == "daily" and args.daily_command == "execute":
        return asyncio.run(_daily_execute(args, Settings()))
    if args.command == "daily" and args.daily_command == "status":
        return asyncio.run(_daily_status(args, Settings()))
    return 2
