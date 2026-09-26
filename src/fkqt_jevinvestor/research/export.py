import json
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.backtest import (
    DecisionAction,
    ExperimentArm,
    TargetPosition,
    TargetPositionBatch,
)
from fkqt_jevinvestor.domain.candidate_queue import CandidateQueueV1
from fkqt_jevinvestor.domain.market_features import FeatureValue, MarketFeatureSnapshot
from fkqt_jevinvestor.domain.portfolio import PortfolioState
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.ingestion.execution_bundle import load_execution_bundle
from fkqt_jevinvestor.ingestion.snapshot_store import MarketSnapshotStore
from fkqt_jevinvestor.research.artifacts import write_immutable
from fkqt_jevinvestor.research.dataset import (
    DatasetManifestV1,
    DatasetStore,
    DayReference,
    ResearchDayV1,
)
from fkqt_jevinvestor.research.recorded import RecordedTargetV1, reject_legacy_rights
from fkqt_jevinvestor.services.position_sizing import PositionSizingConfigV1, SizedTargetV1


class ExportDay(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    decision_date: date
    market_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_queue_path: Path
    execution_bundle_path: Path
    run_id: str | None = None


class ExportRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    database_path: Path
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    days: tuple[ExportDay, ...]


class RecordedIndex(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dataset_hash: str
    records: dict[str, str]


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.00000001"))


def _features(
    conn: sqlite3.Connection, snapshot_id: str, decision_date: date
) -> dict[str, MarketFeatureSnapshot]:
    grouped: defaultdict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in conn.execute(
        "SELECT * FROM ai_signal_market_feature WHERE market_snapshot_id=? ORDER BY symbol,feature_code",
        (snapshot_id,),
    ):
        grouped[row["symbol"]].append(row)
    result: dict[str, MarketFeatureSnapshot] = {}
    for symbol, rows in grouped.items():
        hashes = {r["feature_snapshot_hash"] for r in rows}
        if len(hashes) != 1 or len({r["feature_code"] for r in rows}) != len(rows):
            raise ValueError("RESEARCH_STORED_FEATURE_CONFLICT")
        result[symbol] = MarketFeatureSnapshot(
            symbol=symbol,
            decision_date=decision_date,
            values={
                r["feature_code"]: FeatureValue(
                    feature_code=r["feature_code"],
                    feature_version=r["feature_version"],
                    as_of=_time(r["as_of"]),
                    lookback_window=r["lookback_window"],
                    value=_decimal(r["value"]) if r["value"] is not None else None,
                    missing_reason=r["missing_reason"],
                    source_snapshot_hash=r["source_snapshot_hash"],
                )
                for r in rows
            },
            content_hash=hashes.pop(),
        )
    return result


def _record(
    conn: sqlite3.Connection,
    request: ExportRequest,
    item: ExportDay,
    day: ResearchDayV1,
    dataset_hash: str,
) -> RecordedTargetV1:
    run = conn.execute(
        "SELECT * FROM ai_signal_position_sizing_run WHERE run_id=?", (item.run_id,)
    ).fetchone()
    if (
        run is None
        or run["decision_date"] != item.decision_date.isoformat()
        or run["planned_execution_date"] != day.snapshot.next_trade_date.isoformat()
    ):
        raise ValueError("RESEARCH_SIZING_RUN_NOT_FOUND")
    snapshot = conn.execute(
        "SELECT details_json FROM ai_signal_portfolio_snapshot WHERE portfolio_id=? AND snapshot_type='DECISION_INPUT' AND content_hash=?",
        (run["portfolio_id"], run["decision_portfolio_hash"]),
    ).fetchone()
    if snapshot is None or snapshot[0] is None:
        raise ValueError("RESEARCH_DECISION_PORTFOLIO_NOT_FOUND")
    portfolio = PortfolioState.model_validate(json.loads(snapshot[0]))
    reject_legacy_rights(portfolio)
    if sha256_json(portfolio) != run["decision_portfolio_hash"]:
        raise ValueError("RESEARCH_DECISION_PORTFOLIO_HASH_MISMATCH")
    rows = list(
        conn.execute(
            "SELECT t.*,d.input_json AS decision_input,d.thesis,d.invalidation,d.input_hash AS decision_input_hash "
            "FROM ai_signal_position_target t JOIN ai_signal_llm_decision_evaluation d ON d.id=t.decision_evaluation_id "
            "WHERE t.sizing_run_id=? ORDER BY t.symbol",
            (run["id"],),
        )
    )
    required = set(day.queue.ordered_symbols) | {p.symbol for p in portfolio.positions}
    if {r["symbol"] for r in rows} != required:
        raise ValueError("RESEARCH_RECORDED_TARGET_COVERAGE_INCOMPLETE")
    sized: list[SizedTargetV1] = []
    for r in rows:
        position = portfolio.position(r["symbol"])
        current_weight = position.current_position_pct if position is not None else Decimal(0)
        if _decimal(r["current_position_pct"]) != current_weight:
            raise ValueError("RESEARCH_STORED_POSITION_WEIGHT_MISMATCH")
        decision_input = json.loads(r["decision_input"])
        if (
            sha256_json(decision_input) != r["decision_input_hash"]
            or decision_input.get("market_snapshot_hash") != day.snapshot.content_hash
            or decision_input.get("portfolio") != portfolio.model_dump(mode="json")
            or decision_input.get("symbol") != r["symbol"]
        ):
            raise ValueError("RESEARCH_RECORDED_INPUT_MISMATCH")
        sized.append(
            SizedTargetV1(
                symbol=r["symbol"],
                decision_evaluation_id=r["decision_evaluation_id"],
                requested_action=r["requested_action"],
                status=r["sizing_status"],
                current_position_pct=current_weight,
                raw_target_position_pct=_decimal(r["raw_target_position_pct"]),
                target_position_pct=_decimal(r["target_position_pct"]),
                signal_action=r["signal_action"],
                block_code=r["block_code"],
                thesis=r["thesis"],
                invalidation=r["invalidation"],
            )
        )
    cfg = PositionSizingConfigV1.model_validate(json.loads(run["config_json"]))
    input_hash = sha256_json(
        {
            "portfolio": portfolio.model_dump(mode="json"),
            "decision_date": item.decision_date.isoformat(),
            "planned_execution_date": day.snapshot.next_trade_date.isoformat(),
            "evaluations": [
                {
                    "evaluation_id": r["decision_evaluation_id"],
                    "input_hash": r["decision_input_hash"],
                    "symbol": r["symbol"],
                    "action": r["requested_action"],
                }
                for r in rows
            ],
            "feature_snapshot_hashes": {s: f.content_hash for s, f in sorted(day.features.items())},
            "config_hash": cfg.config_hash,
        }
    )
    if cfg.config_hash != run["config_hash"] or input_hash != run["input_hash"]:
        raise ValueError("RESEARCH_SIZING_INPUT_HASH_MISMATCH")
    if (
        sha256_json(
            {
                "sizing_version": run["sizing_version"],
                "input_hash": input_hash,
                "targets": [s.model_dump(mode="json") for s in sized],
                "cash_target_pct": str(_decimal(run["cash_target_pct"])),
            }
        )
        != run["target_batch_hash"]
    ):
        raise ValueError("RESEARCH_SIZING_TARGET_HASH_MISMATCH")
    batch = TargetPositionBatch(
        decision_date=item.decision_date,
        planned_execution_date=day.snapshot.next_trade_date,
        experiment_arm=ExperimentArm.C_JEV_LLM,
        input_hash=input_hash,
        targets=tuple(
            TargetPosition(
                symbol=s.symbol,
                action=DecisionAction(s.requested_action.value),
                target_position_pct=s.target_position_pct,
                sizing_version=run["sizing_version"],
                sizing_input_hash=input_hash,
            )
            for s in sized
        ),
    )
    return RecordedTargetV1(
        dataset_id=request.dataset_id,
        dataset_hash=dataset_hash,
        day=day.replay_day().decision_view(),
        portfolio=portfolio,
        targets=batch,
    )


def export_dataset(request: ExportRequest, output: Path) -> Path:
    if not request.database_path.is_file():
        raise ValueError("RESEARCH_SOURCE_DATABASE_NOT_FOUND")
    days: list[ResearchDayV1] = []
    records: list[RecordedTargetV1] = []
    with sqlite3.connect(request.database_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        for item in request.days:
            row = conn.execute(
                "SELECT * FROM ai_signal_market_snapshot WHERE decision_date=? AND content_hash=?",
                (item.decision_date.isoformat(), item.market_snapshot_hash),
            ).fetchone()
            if row is None:
                raise ValueError("RESEARCH_MARKET_SNAPSHOT_NOT_FOUND")
            path = Path(row["storage_path"]).resolve()
            snapshot = MarketSnapshotStore(path.parent.parent).load(
                item.decision_date, item.market_snapshot_hash
            )
            if snapshot.snapshot_id != row["id"]:
                raise ValueError("RESEARCH_SNAPSHOT_RECORD_MISMATCH")
            queue = CandidateQueueV1.model_validate_json(
                item.candidate_queue_path.read_text(encoding="utf-8")
            )
            execution = load_execution_bundle(item.execution_bundle_path)
            day = ResearchDayV1(
                snapshot=snapshot,
                queue=queue,
                execution=execution,
                features=_features(conn, row["id"], item.decision_date),
            )
            day.verify()
            days.append(day)
        DatasetStore.verify_days(tuple(days))
        manifest = DatasetManifestV1(
            dataset_id=request.dataset_id,
            days=tuple(
                DayReference(decision_date=d.snapshot.decision_date, content_hash=d.content_hash)
                for d in days
            ),
        )
        for item, day in zip(request.days, days, strict=True):
            if item.run_id is not None:
                records.append(_record(conn, request, item, day, manifest.dataset_hash))
    path = DatasetStore(output).publish(request.dataset_id, tuple(days))
    index: dict[str, str] = {}
    for record in records:
        record.save(path.parent / "recorded")
        index[record.day.decision_date.isoformat()] = record.content_hash
    if index:
        value = RecordedIndex(dataset_hash=manifest.dataset_hash, records=index)
        write_immutable(path.parent / f"recorded-index-{sha256_json(value)}.json", value)
    return path
