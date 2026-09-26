from datetime import date
from pathlib import Path

import pytest

from fkqt_jevinvestor.ingestion.candidate_queue import prepare_candidate_queue
from fkqt_jevinvestor.ingestion.fkqt_manifest import FkqtBundleError
from tests.contract import test_fkqt_manifest_provider as manifest_fixtures
from tests.contract.test_fkqt_manifest_provider import (
    _write_dataset,  # pyright: ignore[reportPrivateUsage]
)

valid_bundle = manifest_fixtures.valid_bundle


def test_queue_manifest_preserves_rank_and_source(valid_bundle: Path) -> None:
    tmp_path = valid_bundle
    _write_dataset(tmp_path, "candidate_universe", [
        {"symbol": "000001.SZ", "rank": 2}, {"symbol": "600000.SH", "rank": 1},
    ])
    queue = prepare_candidate_queue(tmp_path, date(2026, 9, 18), 3)
    assert queue.ordered_symbols == ("600000.SH", "000001.SZ")
    assert queue.candidate_limit == 3
    assert len(queue.source_content_hash) == 64
    assert prepare_candidate_queue(tmp_path, date(2026, 9, 18), 3).content_hash == queue.content_hash


def test_duplicate_rank_and_mismatched_date_fail(valid_bundle: Path) -> None:
    tmp_path = valid_bundle
    _write_dataset(tmp_path, "candidate_universe", [
        {"symbol": "000001.SZ", "rank": 1}, {"symbol": "600000.SH", "rank": 1},
    ])
    with pytest.raises(ValueError, match="CANDIDATE_QUEUE_RANK_INVALID"):
        prepare_candidate_queue(tmp_path, date(2026, 9, 18), 3)
    with pytest.raises(FkqtBundleError, match="DATASET_AS_OF_DATE_MISMATCH"):
        prepare_candidate_queue(tmp_path, date(2026, 9, 19), 3)
