from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.backtest import TargetPositionBatch
from fkqt_jevinvestor.domain.decision import DecisionEvaluationV1
from fkqt_jevinvestor.domain.jev_market import JevEvaluationCommand, JevEvaluationV1
from fkqt_jevinvestor.ingestion.canonical import sha256_json
from fkqt_jevinvestor.research.artifacts import write_immutable


class FrozenJevCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    command: JevEvaluationCommand
    evaluation: JevEvaluationV1
    usage: dict[str, int] | None
    diagnostic: dict[str, Any] | None
    content_hash: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")


class LlmCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    request: dict[str, Any]
    response_hash: str | None
    usage: dict[str, int] | None
    status: str


class ExperimentArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    identity: dict[str, Any]
    evaluations: tuple[DecisionEvaluationV1, ...]
    llm_calls: tuple[LlmCall, ...]
    jev_call_keys: tuple[str, ...]
    targets: TargetPositionBatch
    content_hash: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")


def seal(value: FrozenJevCall | ExperimentArtifact) -> FrozenJevCall | ExperimentArtifact:
    return value.model_copy(
        update={
            "content_hash": sha256_json(value.model_dump(mode="json", exclude={"content_hash"}))
        }
    )


def verify(value: FrozenJevCall | ExperimentArtifact) -> None:
    if value.content_hash != sha256_json(value.model_dump(mode="json", exclude={"content_hash"})):
        raise ValueError("RESEARCH_AUDIT_HASH_MISMATCH")


class AuditStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path(self, lane: str, key: str) -> Path:
        if (
            lane not in {"experiments", "jev"}
            or len(key) != 64
            or any(c not in "0123456789abcdef" for c in key)
        ):
            raise ValueError("RESEARCH_AUDIT_KEY_INVALID")
        path = (self.root / lane / f"{key}.json").resolve()
        if self.root not in path.parents:
            raise ValueError("RESEARCH_PATH_ESCAPE")
        return path

    @contextmanager
    def claim(self, lane: str, key: str) -> Generator[None, None, None]:
        path = self.path(lane, key).with_suffix(".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = path.open("x", encoding="utf-8")
        except FileExistsError:
            raise ValueError("RESEARCH_EVALUATION_IN_PROGRESS") from None
        handle.close()
        try:
            yield
        finally:
            path.unlink(missing_ok=True)

    def save(self, lane: str, key: str, value: FrozenJevCall | ExperimentArtifact) -> None:
        write_immutable(self.path(lane, key), seal(value))
