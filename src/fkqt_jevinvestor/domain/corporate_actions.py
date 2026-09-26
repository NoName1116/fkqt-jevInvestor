from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fkqt_jevinvestor.ingestion.canonical import sha256_json


class CorporateActionEventV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    symbol: str = Field(pattern=r"^\d{6}\.(SH|SZ)$")
    source_version: str
    available_at: datetime
    record_date: date
    ex_date: date
    pay_date: date | None
    share_list_date: date | None
    cash_per_share_gross: Decimal = Field(ge=0, allow_inf_nan=False)
    bonus_ratio: Decimal = Field(ge=0, allow_inf_nan=False)
    tax_policy_version: Literal["GROSS_V1"] = "GROSS_V1"

    @model_validator(mode="after")
    def validate_dates(self) -> "CorporateActionEventV1":
        if self.available_at.tzinfo is None:
            raise ValueError("CORPORATE_ACTION_AVAILABILITY_REQUIRED")
        if (self.record_date >= self.ex_date
                or (self.cash_per_share_gross > 0 and (self.pay_date is None or self.pay_date < self.ex_date))
                or (self.bonus_ratio > 0 and (self.share_list_date is None or self.share_list_date < self.ex_date))):
            raise ValueError("CORPORATE_ACTION_DATES_INCOMPLETE")
        return self


class EntitlementLotV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    acquired_on: date
    quantity: int = Field(ge=0)
    cost: Decimal = Field(ge=0)
    bonus_quantity: int = Field(ge=0)


class EntitlementV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: str
    symbol: str
    record_date: date
    lot_allocations: tuple[EntitlementLotV1, ...]
    entitled_quantity: int = Field(ge=0)
    cash_receivable: Decimal = Field(ge=0)
    bonus_quantity: int = Field(ge=0)
    ex_applied: bool = False
    paid: bool = False
    shares_listed: bool = False


class CorporateActionManifestV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    trade_date: date
    requested_symbols: tuple[str, ...]
    coverage_complete: bool
    events: tuple[CorporateActionEventV1, ...]
    source_manifest_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(cls, **values: object) -> "CorporateActionManifestV1":
        provisional = cls.model_validate({**values, "content_hash": "0" * 64})
        payload = provisional.model_dump(mode="json")
        payload["content_hash"] = ""
        result = provisional.model_copy(update={"content_hash": sha256_json(payload)})
        result.verify(result.trade_date)
        return result

    def verify(self, trade_date: date) -> None:
        if trade_date != self.trade_date:
            raise ValueError("CORPORATE_ACTION_DATE_MISMATCH")
        if not self.coverage_complete or len(set(self.requested_symbols)) != len(self.requested_symbols):
            raise ValueError("CORPORATE_ACTION_COVERAGE_INCOMPLETE")
        if (len({event.event_id for event in self.events}) != len(self.events)
                or any(event.symbol not in self.requested_symbols for event in self.events)):
            raise ValueError("CORPORATE_ACTION_SYMBOL_MISMATCH")
        payload = self.model_dump(mode="json")
        payload["content_hash"] = ""
        if sha256_json(payload) != self.content_hash:
            raise ValueError("CORPORATE_ACTION_HASH_MISMATCH")
