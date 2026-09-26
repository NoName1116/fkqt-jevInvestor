from argparse import Namespace
from datetime import date
from pathlib import Path

import pytest

from fkqt_jevinvestor.cli.main import (
    _resolve_daily_candidates,  # pyright: ignore[reportPrivateUsage]
    stable_daily_run_id,
)
from fkqt_jevinvestor.config import Settings
from fkqt_jevinvestor.ingestion.candidate_queue import CandidateQueueStore, prepare_candidate_queue
from tests.contract import test_fkqt_manifest_provider as manifest_fixtures
from tests.contract.test_fkqt_manifest_provider import (
    _write_dataset,  # pyright: ignore[reportPrivateUsage]
)

valid_bundle = manifest_fixtures.valid_bundle


def test_daily_candidates_consumes_frozen_queue_without_env_symbols(valid_bundle: Path) -> None:
    _write_dataset(valid_bundle, "candidate_universe", [{"symbol": "600000.SH", "rank": 1}])
    queue = prepare_candidate_queue(valid_bundle, date(2026, 9, 18), 2)
    path = CandidateQueueStore(valid_bundle / "queues").save(queue)
    args = Namespace(candidate_queue=str(path), decision_date=date(2026, 9, 18), candidate_limit=2)
    settings = Settings(fkqt_manifest_bundle_root=valid_bundle, c_group_candidate_symbols=None)
    symbols, loaded = _resolve_daily_candidates(args, settings)
    assert symbols == ("600000.SH",)
    assert loaded is not None and loaded.content_hash == queue.content_hash
    with pytest.raises(ValueError, match="CANDIDATE_QUEUE_CONFIG_CONFLICT"):
        _resolve_daily_candidates(args, settings.model_copy(update={"c_group_candidate_symbols": "000001.SZ"}))


def test_queue_and_manifest_mismatch_rejected(valid_bundle: Path) -> None:
    _write_dataset(valid_bundle, "candidate_universe", [{"symbol": "600000.SH", "rank": 1}])
    queue = prepare_candidate_queue(valid_bundle, date(2026, 9, 18), 2)
    path = CandidateQueueStore(valid_bundle / "queues").save(queue)
    _write_dataset(valid_bundle, "candidate_universe", [{"symbol": "000001.SZ", "rank": 1}])
    with pytest.raises(ValueError, match="CANDIDATE_QUEUE_SOURCE_MISMATCH"):
        _resolve_daily_candidates(
            Namespace(candidate_queue=str(path), decision_date=date(2026, 9, 18), candidate_limit=2),
            Settings(fkqt_manifest_bundle_root=valid_bundle, c_group_candidate_symbols=None),
        )


def test_queue_identity_changes_run_id() -> None:
    args = ("p", date(2026, 9, 18), "a" * 64, ("600000.SH",), 2, 1)
    first = stable_daily_run_id(*args, candidate_queue_hash="b" * 64)
    assert first == stable_daily_run_id(*args, candidate_queue_hash="b" * 64)
    assert first != stable_daily_run_id(*args, candidate_queue_hash="c" * 64)
