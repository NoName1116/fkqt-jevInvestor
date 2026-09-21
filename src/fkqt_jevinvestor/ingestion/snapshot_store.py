import re
from datetime import date
from pathlib import Path

from fkqt_jevinvestor.domain.market_features import MarketSnapshot
from fkqt_jevinvestor.ingestion.canonical import canonical_json, sha256_json


class SnapshotValidationError(RuntimeError):
    pass


class MarketSnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, snapshot: MarketSnapshot) -> Path:
        self._validate(snapshot)
        destination = self._path(snapshot.decision_date, snapshot.content_hash)
        serialized = f"{canonical_json(snapshot)}\n"
        destination.parent.mkdir(parents=True, exist_ok=True)

        try:
            with destination.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
        except FileExistsError:
            if destination.read_text(encoding="utf-8") != serialized:
                raise SnapshotValidationError("SNAPSHOT_HASH_CONFLICT") from None

        return destination

    def load(self, decision_date: date, content_hash: str) -> MarketSnapshot:
        source = self._path(decision_date, content_hash)
        try:
            payload = source.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise SnapshotValidationError("SNAPSHOT_NOT_FOUND") from None
        snapshot = MarketSnapshot.model_validate_json(payload)
        self._validate(snapshot)
        if snapshot.decision_date != decision_date or snapshot.content_hash != content_hash:
            raise SnapshotValidationError("SNAPSHOT_PATH_MISMATCH")
        return snapshot

    def _validate(self, snapshot: MarketSnapshot) -> None:
        if snapshot.decision_cutoff.date() != snapshot.decision_date:
            raise SnapshotValidationError("POINT_IN_TIME_VIOLATION: cutoff date mismatch")

        if any(
            bar.trade_date > snapshot.decision_date
            for bars in snapshot.daily_bars.values()
            for bar in bars
        ):
            raise SnapshotValidationError("POINT_IN_TIME_VIOLATION: future daily bar")

        if any(
            state.trade_date != snapshot.decision_date
            for state in snapshot.security_states.values()
        ):
            raise SnapshotValidationError("SECURITY_STATE_DATE_MISMATCH")

        if snapshot.next_trade_date <= snapshot.decision_date:
            raise SnapshotValidationError("INVALID_NEXT_TRADE_DATE")

        payload = snapshot.model_dump(mode="json")
        payload["content_hash"] = ""
        if sha256_json(payload) != snapshot.content_hash:
            raise SnapshotValidationError("SNAPSHOT_HASH_MISMATCH")

    def _path(self, decision_date: date, content_hash: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
            raise SnapshotValidationError("INVALID_SNAPSHOT_HASH")
        return self.root / decision_date.isoformat() / f"{content_hash}.json"
