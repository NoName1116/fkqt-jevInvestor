import json
import re
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from datetime import date
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot, TradingDayStatus
from fkqt_jevinvestor.ingestion.canonical import canonical_json, sha256_json


class ExecutionBundleV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "execution-bundle-v1"
    trade_date: date
    snapshots: Mapping[str, MarketExecutionSnapshot]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requires_origin: bool = False
    source_manifest_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_manifest_content_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )

    @classmethod
    def create(
        cls,
        trade_date: date,
        snapshots: Mapping[str, MarketExecutionSnapshot],
        *,
        requires_origin: bool = False,
        source_manifest_id: str | None = None,
        source_manifest_content_hash: str | None = None,
    ) -> "ExecutionBundleV1":
        if requires_origin != (source_manifest_id is not None):
            raise ValueError("EXECUTION_SOURCE_CONFLICT")
        if requires_origin != (source_manifest_content_hash is not None):
            raise ValueError("EXECUTION_SOURCE_CONFLICT")
        payload: dict[str, object] = {
            "schema_version": "execution-bundle-v1",
            "trade_date": trade_date.isoformat(),
            "snapshots": {
                symbol: snapshot.model_dump(mode="json")
                for symbol, snapshot in sorted(snapshots.items())
            },
            "content_hash": "",
        }
        if requires_origin:
            payload["requires_origin"] = True
            payload["source_manifest_id"] = source_manifest_id
            payload["source_manifest_content_hash"] = source_manifest_content_hash
        bundle = cls(
            trade_date=trade_date,
            snapshots=snapshots,
            content_hash=sha256_json(payload),
            requires_origin=requires_origin,
            source_manifest_id=source_manifest_id,
            source_manifest_content_hash=source_manifest_content_hash,
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
        payload = self.model_dump(
            mode="json",
            exclude=(
                {"requires_origin", "source_manifest_id", "source_manifest_content_hash"}
                if not self.requires_origin else None
            ),
        )
        payload["content_hash"] = ""
        if sha256_json(payload) != self.content_hash:
            raise ValueError("EXECUTION_BUNDLE_HASH_MISMATCH")


def load_execution_bundle(path: Path) -> ExecutionBundleV1:
    try:
        bundle = ExecutionBundleV1.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("EXECUTION_BUNDLE_NOT_FOUND") from exc
    origin = path.with_suffix(".origin.json")
    if not origin.exists():
        if bundle.requires_origin:
            raise ValueError("EXECUTION_SOURCE_MISSING")
        return bundle
    try:
        raw_provenance: object = json.loads(origin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("EXECUTION_SOURCE_CONFLICT") from exc
    if not isinstance(raw_provenance, dict):
        raise ValueError("EXECUTION_SOURCE_CONFLICT")  # noqa: TRY004
    provenance = cast(dict[str, object], raw_provenance)
    if provenance.get("execution_hash") != bundle.content_hash:
        raise ValueError("EXECUTION_SOURCE_CONFLICT")
    if provenance.get("source_type") == "MANUAL_JSON":
        if bundle.requires_origin or set(provenance) != {"execution_hash", "source_type"}:
            raise ValueError("EXECUTION_SOURCE_CONFLICT")
        return bundle
    if not bundle.requires_origin or provenance.get("source_type") != "FKQT_TUSHARE_MANIFEST":
        raise ValueError("EXECUTION_SOURCE_CONFLICT")
    source_id = provenance.get("source_manifest_id")
    source_hash = provenance.get("source_manifest_content_hash")
    if (
        set(provenance) != {
            "execution_hash", "source_type", "source_manifest_id",
            "source_manifest_content_hash",
        }
        or not isinstance(source_id, str)
        or not isinstance(source_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", source_id) is None
        or re.fullmatch(r"[0-9a-f]{64}", source_hash) is None
        or source_id != bundle.source_manifest_id
        or source_hash != bundle.source_manifest_content_hash
    ):
        raise ValueError("EXECUTION_SOURCE_CONFLICT")
    return bundle


class ExecutionBundleStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, bundle: ExecutionBundleV1) -> Path:
        bundle.verify(bundle.trade_date, set(bundle.snapshots))
        if bundle.requires_origin != (bundle.source_manifest_id is not None):
            raise ValueError("EXECUTION_SOURCE_CONFLICT")
        if bundle.source_manifest_id is not None and bundle.source_manifest_content_hash is None:
            raise ValueError("EXECUTION_SOURCE_HASH_REQUIRED")
        if bundle.source_manifest_id is None and bundle.source_manifest_content_hash is not None:
            raise ValueError("EXECUTION_SOURCE_CONFLICT")
        destination = self.root / bundle.trade_date.isoformat() / f"{bundle.content_hash}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        serialized = f"{canonical_json(bundle.model_dump(
            mode='json',
            exclude=(
                {'requires_origin', 'source_manifest_id', 'source_manifest_content_hash'}
                if not bundle.requires_origin else None
            ),
        ))}\n"
        created = False
        try:
            with destination.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
            created = True
        except FileExistsError:
            if destination.read_text(encoding="utf-8") != serialized:
                raise ValueError("EXECUTION_BUNDLE_STORE_CONFLICT") from None
        origin = destination.with_suffix(".origin.json")
        if bundle.source_manifest_id is not None:
            if not created and not origin.exists():
                raise ValueError("EXECUTION_SOURCE_CONFLICT")
            source = {
                'execution_hash': bundle.content_hash,
                'source_type': 'FKQT_TUSHARE_MANIFEST',
                'source_manifest_id': bundle.source_manifest_id,
                'source_manifest_content_hash': bundle.source_manifest_content_hash,
            }
        else:
            source = {'execution_hash': bundle.content_hash, 'source_type': 'MANUAL_JSON'}
        provenance = f"{canonical_json(source)}\n"
        try:
            with origin.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(provenance)
        except FileExistsError:
            if origin.read_text(encoding="utf-8") != provenance:
                raise ValueError("EXECUTION_SOURCE_CONFLICT") from None
        return destination
