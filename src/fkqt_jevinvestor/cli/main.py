import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime

from fkqt_jevinvestor.config import Settings
from fkqt_jevinvestor.domain.market_time import market_close
from fkqt_jevinvestor.ingestion.fkqt_manifest import FkqtManifestProvider
from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
from fkqt_jevinvestor.persistence.market_repository import MarketSnapshotRepository
from fkqt_jevinvestor.persistence.session import create_engine, create_session_factory
from fkqt_jevinvestor.services.market_pipeline import MarketPipeline


def decision_cutoff_for_date(decision_date: date) -> datetime:
    return market_close(decision_date)


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
    args = parser.parse_args(argv)
    if args.command == "version":
        print("fkqt-jevinvestor 0.1.0")
        return 0
    if args.command == "market" and args.market_command == "freeze":
        return asyncio.run(_freeze_manifest(args, Settings()))
    return 2
