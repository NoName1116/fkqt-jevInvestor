import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.ingestion.canonical import sha256_json


class CandidateQueueV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["candidate-queue-v1"] = "candidate-queue-v1"
    trade_date: date
    source_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_version: str = Field(min_length=1)
    source_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_limit: int = Field(gt=0, strict=True)
    ordered_symbols: tuple[str, ...]
    created_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(
        cls, *, trade_date: date, source_id: str, source_version: str,
        source_content_hash: str, candidate_limit: int, ordered_symbols: tuple[str, ...],
        created_at: datetime,
    ) -> "CandidateQueueV1":
        provisional = cls(
            trade_date=trade_date, source_id=source_id, source_version=source_version,
            source_content_hash=source_content_hash, candidate_limit=candidate_limit,
            ordered_symbols=ordered_symbols, created_at=created_at, content_hash="0" * 64,
        )
        payload = provisional.model_dump(mode="json")
        payload["content_hash"] = ""
        queue = provisional.model_copy(update={"content_hash": sha256_json(payload)})
        queue.verify()
        return queue

    def verify(self) -> None:
        if (not self.ordered_symbols or len(self.ordered_symbols) > self.candidate_limit
                or len(set(self.ordered_symbols)) != len(self.ordered_symbols)
                or any(re.fullmatch(r"\d{6}\.(SH|SZ)", s) is None for s in self.ordered_symbols)
                or self.created_at.tzinfo is None):
            raise ValueError("CANDIDATE_QUEUE_INVALID")
        payload = self.model_dump(mode="json")
        payload["content_hash"] = ""
        if sha256_json(payload) != self.content_hash:
            raise ValueError("CANDIDATE_QUEUE_HASH_MISMATCH")
