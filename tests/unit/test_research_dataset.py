from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.unit.research_fixtures import inputs


def build_day():
    from fkqt_jevinvestor.research.dataset import ResearchDayV1

    snapshot, features, queue, execution = inputs()
    return ResearchDayV1(snapshot=snapshot, features=features, queue=queue, execution=execution)


async def test_publish_replay_is_immutable_and_decision_has_no_future(tmp_path: Path) -> None:
    from fkqt_jevinvestor.research.dataset import DatasetStore, FrozenReplayDataProvider

    day = build_day()
    store = DatasetStore(tmp_path)
    path = store.publish("research-fixture", (day,))
    assert store.publish("research-fixture", (day,)) == path
    provider = FrozenReplayDataProvider(store, path)
    assert await provider.decision_dates(date(2026, 9, 18), date(2026, 9, 21)) == (
        date(2026, 9, 18),
    )
    replay = await provider.load_day(date(2026, 9, 18))
    assert replay.planned_execution_date == date(2026, 9, 21)
    assert not hasattr(replay.decision_view(), "execution_market")
    with pytest.raises(ValueError, match="WARMUP_INCOMPLETE"):
        await provider.warmup_dates(date(2026, 9, 18), 1)
    chunk = path.parent / "days" / f"{provider.manifest.days[0].content_hash}.json"
    chunk.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        await provider.load_day(date(2026, 9, 18))


def test_future_feature_is_rejected_before_publication(tmp_path: Path) -> None:
    from fkqt_jevinvestor.research.dataset import DatasetStore

    day = build_day()
    symbol = "600000.SH"
    feature = day.features[symbol]
    value = feature.values["return_20d"]
    invalid = feature.model_copy(
        update={
            "values": dict(feature.values)
            | {"return_20d": value.model_copy(update={"as_of": value.as_of + timedelta(days=1)})}
        }
    )
    bad = day.model_copy(update={"features": dict(day.features) | {symbol: invalid}})
    with pytest.raises(ValueError, match="POINT_IN_TIME"):
        DatasetStore(tmp_path).publish("research-fixture", (bad,))
    assert not list(tmp_path.rglob("manifest*.json"))


def test_missing_execution_coverage_and_path_escape_are_rejected(tmp_path: Path) -> None:
    from fkqt_jevinvestor.research.dataset import DatasetStore

    day = build_day()
    bad = day.model_copy(update={"execution": day.execution.model_copy(update={"snapshots": {}})})
    with pytest.raises(ValueError):
        DatasetStore(tmp_path).publish("research-fixture", (bad,))
    with pytest.raises(ValueError, match="DATASET_ID_INVALID"):
        DatasetStore(tmp_path).publish("../escape", (day,))


def test_missing_security_state_or_bars_rejects_validly_rehashed_day(tmp_path: Path) -> None:
    from fkqt_jevinvestor.ingestion.canonical import sha256_json
    from fkqt_jevinvestor.research.dataset import DatasetStore

    for field in ("security_states", "daily_bars"):
        day = build_day()
        snapshot = day.snapshot.model_copy(update={field: {}})
        snapshot = snapshot.model_copy(
            update={
                "content_hash": sha256_json(snapshot.model_dump(mode="json") | {"content_hash": ""})
            }
        )
        features = {}
        for symbol, feature in day.features.items():
            updated = feature.model_copy(
                update={
                    "values": {
                        code: value.model_copy(
                            update={"source_snapshot_hash": snapshot.content_hash}
                        )
                        for code, value in feature.values.items()
                    }
                }
            )
            features[symbol] = updated.model_copy(
                update={
                    "content_hash": sha256_json(
                        updated.model_dump(mode="json") | {"content_hash": ""}
                    )
                }
            )
        bad = day.model_copy(update={"snapshot": snapshot, "features": features})
        with pytest.raises(ValueError, match="RESEARCH_SECURITY_COVERAGE_INCOMPLETE"):
            DatasetStore(tmp_path).publish("research-fixture", (bad,))
    assert not list(tmp_path.rglob("manifest.json"))


def test_calendar_gap_and_queue_rotation_missing_held_feature_are_rejected(tmp_path: Path) -> None:
    from fkqt_jevinvestor.domain.candidate_queue import CandidateQueueV1
    from fkqt_jevinvestor.research.dataset import DatasetStore
    from tests.unit.research_fixtures import two_days

    first, second = two_days()
    with pytest.raises(ValueError, match="CALENDAR_CHAIN_MISMATCH"):
        DatasetStore(tmp_path).publish("research-fixture", (first, first))
    queue = CandidateQueueV1.create(
        trade_date=second.queue.trade_date,
        source_id=second.queue.source_id,
        source_version=second.queue.source_version,
        source_content_hash=second.queue.source_content_hash,
        candidate_limit=second.queue.candidate_limit,
        ordered_symbols=("600000.SH",),
        created_at=second.queue.created_at,
    )
    bad = second.model_copy(
        update={
            "queue": queue,
            "features": {s: f for s, f in second.features.items() if s != "000001.SZ"},
        }
    )
    with pytest.raises(ValueError, match="HELD_SYMBOL_COVERAGE_INCOMPLETE"):
        DatasetStore(tmp_path).publish("research-fixture", (first, bad))
    assert not list(tmp_path.rglob("manifest.json"))
