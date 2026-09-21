from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from fkqt_jevinvestor.domain.evidence import MinimalEvidenceSnapshot

SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


def make_evidence(
    available_from: datetime,
    evidence_excerpt: str = "事件摘要",
) -> MinimalEvidenceSnapshot:
    return MinimalEvidenceSnapshot(
        evidence_id="evidence-1",
        provider="semantic-api",
        external_id="external-1",
        source_type="NEWS",
        symbol="600000.SH",
        title="测试事件",
        published_at=datetime(2026, 9, 20, 14, tzinfo=SHANGHAI),
        available_from=available_from,
        retrieved_at=datetime(2026, 9, 20, 18, tzinfo=SHANGHAI),
        source_url="https://example.com/news/1",
        evidence_excerpt=evidence_excerpt,
        content_hash="a" * 64,
    )


def test_evidence_rejects_future_availability() -> None:
    evidence = make_evidence(datetime(2026, 9, 21, tzinfo=SHANGHAI))

    with pytest.raises(ValueError, match="available_from"):
        evidence.assert_available_at(datetime(2026, 9, 20, 18, tzinfo=SHANGHAI))


def test_evidence_contract_has_no_full_content_field() -> None:
    assert "full_content" not in MinimalEvidenceSnapshot.model_fields
    assert "raw_response" not in MinimalEvidenceSnapshot.model_fields


def test_evidence_excerpt_is_limited_to_two_thousand_characters() -> None:
    with pytest.raises(ValidationError):
        make_evidence(
            datetime(2026, 9, 20, 15, tzinfo=SHANGHAI),
            evidence_excerpt="x" * 2001,
        )


def test_available_evidence_passes_cutoff() -> None:
    evidence = make_evidence(datetime(2026, 9, 20, 15, tzinfo=SHANGHAI))

    evidence.assert_available_at(datetime(2026, 9, 20, 18, tzinfo=SHANGHAI))
