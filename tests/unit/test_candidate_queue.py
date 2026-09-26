from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from fkqt_jevinvestor.domain.candidate_queue import CandidateQueueV1
from fkqt_jevinvestor.ingestion.candidate_queue import CandidateQueueStore


def queue(symbols: tuple[str, ...] = ("600000.SH", "000001.SZ")) -> CandidateQueueV1:
    return CandidateQueueV1.create(
        trade_date=date(2026, 9, 24), source_id="a" * 64,
        source_version="TUSHARE_PRO_V1", source_content_hash="b" * 64,
        candidate_limit=3, ordered_symbols=symbols,
        created_at=datetime(2026, 9, 24, 7, tzinfo=UTC),
    )


def test_rank_order_changes_queue_hash() -> None:
    assert queue().content_hash != queue(("000001.SZ", "600000.SH")).content_hash


def test_same_queue_preserves_frozen_file(tmp_path: Path) -> None:
    store = CandidateQueueStore(tmp_path)
    first = queue()
    path = store.save(first)
    assert store.save(first) == path
    assert store.load(path).ordered_symbols == ("600000.SH", "000001.SZ")
    path.write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="CANDIDATE_QUEUE_STORE_CONFLICT"):
        store.save(first)


@pytest.mark.parametrize("symbols", [("600000.SH", "600000.SH"), ("ABC",), ()])
def test_invalid_candidate_symbols_fail(symbols: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="CANDIDATE_QUEUE_INVALID"):
        queue(symbols)


def test_queue_tampered_hash_and_capacity_fail() -> None:
    with pytest.raises(ValueError, match="CANDIDATE_QUEUE_HASH_MISMATCH"):
        queue().model_copy(update={"content_hash": "0" * 64}).verify()
    with pytest.raises(ValueError, match="CANDIDATE_QUEUE_INVALID"):
        queue().model_copy(update={"candidate_limit": 1}).verify()
