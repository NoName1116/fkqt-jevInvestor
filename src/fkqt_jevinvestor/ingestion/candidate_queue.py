from datetime import date, datetime
from pathlib import Path

from fkqt_jevinvestor.domain.candidate_queue import CandidateQueueV1
from fkqt_jevinvestor.ingestion.canonical import canonical_json
from fkqt_jevinvestor.ingestion.fkqt_manifest import FkqtManifestProvider


def prepare_candidate_queue(root: Path, trade_date: date, candidate_limit: int) -> CandidateQueueV1:
    provider = FkqtManifestProvider(root)
    manifest = provider._load_manifests(trade_date)["candidate_universe"]  # pyright: ignore[reportPrivateUsage]
    rows = provider._read_rows(manifest)  # pyright: ignore[reportPrivateUsage]
    ranks = [item.get("rank") for item in rows]
    if (any(type(rank) is not int for rank in ranks)
            or sorted(int(str(rank)) for rank in ranks) != list(range(1, len(rows) + 1))):
        raise ValueError("CANDIDATE_QUEUE_RANK_INVALID")
    symbols = tuple(str(item["symbol"]) for item in sorted(rows, key=lambda item: int(str(item["rank"]))))
    provider._validate_universe(rows, symbols, manifest)  # pyright: ignore[reportPrivateUsage]
    return CandidateQueueV1.create(
        trade_date=trade_date, source_id=manifest.dataset_id,
        source_version=manifest.dataset_version, source_content_hash=manifest.content_hash,
        candidate_limit=candidate_limit, ordered_symbols=symbols,
        created_at=datetime.fromisoformat(manifest.fetched_at),
    )


class CandidateQueueStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, queue: CandidateQueueV1) -> Path:
        queue.verify()
        path = self.root / queue.trade_date.isoformat() / f"{queue.content_hash}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = canonical_json(queue) + "\n"
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != content:
                raise ValueError("CANDIDATE_QUEUE_STORE_CONFLICT") from None
        return path

    @staticmethod
    def load(path: Path) -> CandidateQueueV1:
        try:
            queue = CandidateQueueV1.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("CANDIDATE_QUEUE_INVALID") from exc
        queue.verify()
        return queue
