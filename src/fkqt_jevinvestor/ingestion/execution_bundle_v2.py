from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from fkqt_jevinvestor.domain.corporate_actions import CorporateActionManifestV1
from fkqt_jevinvestor.domain.forward import TradingDayProofV1
from fkqt_jevinvestor.domain.market import MarketExecutionSnapshot, TradingDayStatus
from fkqt_jevinvestor.ingestion.canonical import sha256_json


class ExecutionBundleV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["execution-bundle-v2"] = "execution-bundle-v2"
    trade_date: date
    snapshots: Mapping[str, MarketExecutionSnapshot]
    calendar_proof: TradingDayProofV1
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requires_origin: Literal[True] = True
    source_manifest_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    corporate_action_manifest_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$",
    )
    corporate_actions: CorporateActionManifestV1 | None = None

    @model_serializer(mode="wrap")
    def serialize_compatible(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        payload = handler(self)
        if self.corporate_actions is None:
            payload.pop("corporate_actions", None)
        return payload

    @classmethod
    def create(
        cls, trade_date: date, snapshots: Mapping[str, MarketExecutionSnapshot], *,
        calendar_proof: TradingDayProofV1, source_manifest_id: str,
        source_manifest_content_hash: str, corporate_action_manifest_hash: str | None = None,
        corporate_actions: CorporateActionManifestV1 | None = None,
    ) -> "ExecutionBundleV2":
        provisional = cls(
            trade_date=trade_date, snapshots=snapshots, calendar_proof=calendar_proof,
            source_manifest_id=source_manifest_id,
            source_manifest_content_hash=source_manifest_content_hash,
            corporate_action_manifest_hash=corporate_action_manifest_hash,
            corporate_actions=corporate_actions,
            content_hash="0" * 64,
        )
        payload = provisional.model_dump(mode="json")
        payload["content_hash"] = ""
        bundle = provisional.model_copy(update={"content_hash": sha256_json(payload)})
        bundle.verify(trade_date, set(snapshots))
        return bundle

    def verify(self, trade_date: date, required_symbols: AbstractSet[str]) -> None:
        if self.trade_date != trade_date:
            raise ValueError("EXECUTION_BUNDLE_DATE_MISMATCH")
        self.calendar_proof.verify(trade_date)
        if self.corporate_action_manifest_hash is not None and self.corporate_actions is None:
            raise ValueError("CORPORATE_ACTION_MANIFEST_REQUIRED")
        if self.corporate_actions is not None:
            self.corporate_actions.verify(trade_date)
            if self.corporate_action_manifest_hash != self.corporate_actions.source_content_hash:
                raise ValueError("CORPORATE_ACTION_HASH_MISMATCH")
            if not set(self.snapshots).issubset(self.corporate_actions.requested_symbols):
                raise ValueError("CORPORATE_ACTION_COVERAGE_INCOMPLETE")
        if not set(required_symbols).issubset(self.snapshots):
            raise ValueError("EXECUTION_SYMBOL_COVERAGE_INCOMPLETE")
        for symbol, snapshot in self.snapshots.items():
            if symbol != snapshot.symbol or snapshot.trade_date != trade_date:
                raise ValueError("EXECUTION_BUNDLE_SYMBOL_IDENTITY_MISMATCH")
            if snapshot.trading_day_status is not TradingDayStatus.OPEN:
                raise ValueError("EXECUTION_TRADING_DAY_NOT_CONFIRMED")
            if snapshot.unadjusted_close is None:
                raise ValueError("EXECUTION_CLOSE_PRICE_REQUIRED")
        payload = self.model_dump(mode="json")
        payload["content_hash"] = ""
        if sha256_json(payload) != self.content_hash:
            raise ValueError("EXECUTION_BUNDLE_HASH_MISMATCH")
