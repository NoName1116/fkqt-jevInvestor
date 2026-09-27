from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.ingestion.canonical import sha256_json


class TradingDayProofV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["trading-day-proof-v1"] = "trading-day-proof-v1"
    trade_date: date
    is_open: bool
    exchange: Literal["SSE", "SZSE"]
    previous_trade_date: date | None = None
    source_manifest_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(
        cls, trade_date: date, *, is_open: bool, exchange: Literal["SSE", "SZSE"],
        source_manifest_id: str, source_content_hash: str,
        previous_trade_date: date | None = None,
    ) -> "TradingDayProofV1":
        provisional = cls(
            trade_date=trade_date, is_open=is_open, exchange=exchange,
            source_manifest_id=source_manifest_id, source_content_hash=source_content_hash,
            previous_trade_date=previous_trade_date, content_hash="0" * 64,
        )
        payload = provisional.model_dump(mode="json")
        payload["content_hash"] = ""
        return provisional.model_copy(update={"content_hash": sha256_json(payload)})

    def verify(self, trade_date: date) -> None:
        if self.trade_date != trade_date:
            raise ValueError("EXECUTION_BUNDLE_DATE_MISMATCH")
        if not self.is_open:
            raise ValueError("EXECUTION_TRADING_DAY_NOT_CONFIRMED")
        if self.previous_trade_date is not None and self.previous_trade_date >= trade_date:
            raise ValueError("TRADING_DAY_PROOF_PREVIOUS_DATE_INVALID")
        payload = self.model_dump(mode="json")
        payload["content_hash"] = ""
        if sha256_json(payload) != self.content_hash:
            raise ValueError("TRADING_DAY_PROOF_HASH_MISMATCH")
