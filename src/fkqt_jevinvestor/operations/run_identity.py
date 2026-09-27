import json
import re
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from fkqt_jevinvestor.ingestion.canonical import canonical_json, sha256_json


def validate_run_identity(payload: dict[str, Any]) -> None:
    from datetime import date

    required = {"schema_version", "portfolio_id", "date", "run_id", "market_snapshot_hash",
                "required_symbols", "operation_config_hash", "content_hash"}
    if set(payload) != required or payload["schema_version"] != "forward-run-identity-v1":
        raise ValueError("REPORT_RUN_IDENTITY_INVALID")
    if (not isinstance(payload["portfolio_id"], str) or re.fullmatch(r"[A-Za-z0-9_-]{1,40}", payload["portfolio_id"]) is None
            or str(UUID(payload["run_id"])) != payload["run_id"]):
        raise ValueError("REPORT_RUN_IDENTITY_INVALID")
    date.fromisoformat(payload["date"])
    for key in ("content_hash", "market_snapshot_hash"):
        if not isinstance(payload[key], str) or re.fullmatch(r"[a-f0-9]{64}", payload[key]) is None:
            raise ValueError("REPORT_RUN_IDENTITY_INVALID")
    config_hash = payload["operation_config_hash"]
    if config_hash is not None and (not isinstance(config_hash, str) or re.fullmatch(r"[a-f0-9]{64}", config_hash) is None):
        raise ValueError("REPORT_RUN_IDENTITY_INVALID")
    raw_symbols = payload["required_symbols"]
    if not isinstance(raw_symbols, list):
        raise ValueError("REPORT_RUN_IDENTITY_INVALID")  # noqa: TRY004 — 契约统一稳定失败码。
    symbols = cast(list[object], raw_symbols)
    if (not symbols or any(not isinstance(s, str) or re.fullmatch(r"\d{6}\.(SH|SZ)", s) is None for s in symbols)
            or len(symbols) != len(set(symbols))):
        raise ValueError("REPORT_RUN_IDENTITY_INVALID")
    if sha256_json({key: value for key, value in payload.items() if key != "content_hash"}) != payload["content_hash"]:
        raise ValueError("REPORT_RUN_IDENTITY_HASH_MISMATCH")


def write_run_identity(root: Path, identity: dict[str, Any]) -> Path:
    payload = {**identity, "schema_version": "forward-run-identity-v1"}
    payload["content_hash"] = sha256_json(payload)
    validate_run_identity(payload)
    path = root / "daily_audits" / payload["run_id"] / "run-identity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_json(payload) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != text:
            raise ValueError("REPORT_RUN_IDENTITY_CONFLICT") from None
    return path


def read_run_identities(root: Path, portfolio_id: str) -> tuple[list[dict[str, Any]], int]:
    identities: list[dict[str, Any]] = []
    invalid = 0
    for path in sorted((root / "daily_audits").glob("*/run-identity.json")):
        try:
            payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            validate_run_identity(payload)
            if path.parent.name != payload["run_id"]:
                raise ValueError("REPORT_RUN_IDENTITY_PATH_MISMATCH")
            if payload["portfolio_id"] == portfolio_id:
                identities.append(payload)
        except (OSError, ValueError, TypeError, KeyError):
            invalid += 1
    return identities, invalid
