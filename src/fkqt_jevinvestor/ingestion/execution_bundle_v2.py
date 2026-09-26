from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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

    @classmethod
    def create(
        cls, trade_date: date, snapshots: Mapping[str, MarketExecutionSnapshot], *,
        calendar_proof: TradingDayProofV1, source_manifest_id: str,
        source_manifest_content_hash: str, corporate_action_manifest_hash: str | None = None,
    ) -> "ExecutionBundleV2":
        provisional = cls(
            trade_date=trade_date, snapshots=snapshots, calendar_proof=calendar_proof,
            source_manifest_id=source_manifest_id,
            source_manifest_content_hash=source_manifest_content_hash,
            corporate_action_manifest_hash=corporate_action_manifest_hash,
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
        if not required_symbols.issubset(self.snapshots):
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
