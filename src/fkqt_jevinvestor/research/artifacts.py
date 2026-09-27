import os
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from fkqt_jevinvestor.ingestion.canonical import canonical_json


def write_immutable(path: Path, value: BaseModel) -> Path:
    serialized = canonical_json(value) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_text(encoding="utf-8") != serialized:
                raise ValueError("RESEARCH_ARTIFACT_CONFLICT") from None
    finally:
        temporary.unlink(missing_ok=True)
    return path
