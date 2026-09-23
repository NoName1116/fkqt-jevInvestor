from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot, TradingDayStatus
from fkqt_jevinvestor.ingestion.canonical import canonical_json, sha256_json


class ExecutionBundleV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "execution-bundle-v1"
    trade_date: date
    snapshots: Mapping[str, MarketExecutionSnapshot]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$", exclude=True)
    source_manifest_content_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude=True
    )

    @classmethod
    def create(
        cls,
        trade_date: date,
        snapshots: Mapping[str, MarketExecutionSnapshot],
    ) -> "ExecutionBundleV1":
        payload = {
            "schema_version": "execution-bundle-v1",
            "trade_date": trade_date.isoformat(),
            "snapshots": {
                symbol: snapshot.model_dump(mode="json")
                for symbol, snapshot in sorted(snapshots.items())
            },
            "content_hash": "",
        }
        bundle = cls(
            trade_date=trade_date,
            snapshots=snapshots,
            content_hash=sha256_json(payload),
        )
        bundle.verify(trade_date, set(snapshots))
        return bundle

    def verify(self, trade_date: date, required_symbols: AbstractSet[str]) -> None:
        if self.schema_version != "execution-bundle-v1":
            raise ValueError("EXECUTION_BUNDLE_SCHEMA_UNSUPPORTED")
        if self.trade_date != trade_date:
            raise ValueError("EXECUTION_BUNDLE_DATE_MISMATCH")
        if not self.snapshots or any(
            snapshot.trading_day_status is not TradingDayStatus.OPEN
            for snapshot in self.snapshots.values()
        ):
            raise ValueError("EXECUTION_TRADING_DAY_NOT_CONFIRMED")
        if any(snapshot.unadjusted_close is None for snapshot in self.snapshots.values()):
            raise ValueError("EXECUTION_CLOSE_PRICE_REQUIRED")
        if any(
            symbol != snapshot.symbol or snapshot.trade_date != trade_date
            for symbol, snapshot in self.snapshots.items()
        ):
            raise ValueError("EXECUTION_BUNDLE_SYMBOL_IDENTITY_MISMATCH")
        if not set(required_symbols).issubset(self.snapshots):
            raise ValueError("EXECUTION_SYMBOL_COVERAGE_INCOMPLETE")
        payload = self.model_dump(mode="json")
        payload["content_hash"] = ""
        if sha256_json(payload) != self.content_hash:
            raise ValueError("EXECUTION_BUNDLE_HASH_MISMATCH")


def load_execution_bundle(path: Path) -> ExecutionBundleV1:
    try:
        return ExecutionBundleV1.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("EXECUTION_BUNDLE_NOT_FOUND") from exc


class ExecutionBundleStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, bundle: ExecutionBundleV1) -> Path:
        bundle.verify(bundle.trade_date, set(bundle.snapshots))
        destination = self.root / bundle.trade_date.isoformat() / f"{bundle.content_hash}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        serialized = f"{canonical_json(bundle)}\n"
        try:
            with destination.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
        except FileExistsError:
            if destination.read_text(encoding="utf-8") != serialized:
                raise ValueError("EXECUTION_BUNDLE_STORE_CONFLICT") from None
        if bundle.source_manifest_id is not None:
            if bundle.source_manifest_content_hash is None:
                raise ValueError("EXECUTION_SOURCE_HASH_REQUIRED")
            origin = destination.with_suffix(".origin.json")
            provenance = f"{canonical_json({
                'execution_hash': bundle.content_hash,
                'source_type': 'FKQT_TUSHARE_MANIFEST',
                'source_manifest_id': bundle.source_manifest_id,
                'source_manifest_content_hash': bundle.source_manifest_content_hash,
            })}\n"
            try:
                with origin.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(provenance)
            except FileExistsError:
                if origin.read_text(encoding="utf-8") != provenance:
                    raise ValueError("EXECUTION_SOURCE_CONFLICT") from None
        return destination
