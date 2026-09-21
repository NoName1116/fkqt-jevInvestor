from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MinimalEvidenceSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    provider: str
    external_id: str
    source_type: str
    symbol: str
    title: str
    published_at: datetime
    available_from: datetime
    retrieved_at: datetime
    source_url: str
    evidence_excerpt: str = Field(max_length=2000)
    content_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def require_timezone_aware_timestamps(self) -> Self:
        timestamps = (self.published_at, self.available_from, self.retrieved_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in timestamps):
            raise ValueError("evidence timestamps must be timezone-aware")
        return self

    def assert_available_at(self, cutoff: datetime) -> None:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        if self.available_from > cutoff:
            raise ValueError("available_from is later than decision cutoff")
