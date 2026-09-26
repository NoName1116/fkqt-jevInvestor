from datetime import date, time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from fkqt_jevinvestor.ingestion.canonical import sha256_json


class ForwardOperationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    portfolio_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    start_date: date
    fkqt_cwd: Path
    fkqt_python: Path
    jev_cwd: Path
    jev_python: Path
    data_root: Path
    candidate_source: Path
    candidate_limit: int = Field(gt=0, strict=True)
    readiness_deadline_local: time
    max_attempts: int = Field(default=4, ge=1, le=4)
    process_timeout_seconds: int = Field(default=300, gt=0)
    max_run_seconds: int = Field(default=600, gt=0)
    typesafe_model: str = Field(default="jev-latest", min_length=1)
    deepseek_model: str = Field(default="deepseek-flash", min_length=1)
    deepseek_reasoning_effort: Literal["low", "high"] = "high"
    webhook_url: SecretStr | None = None

    @model_validator(mode="after")
    def validate_paths(self) -> "ForwardOperationConfig":
        if self.deepseek_model in {"deepseek-chat", "deepseek-reasoner"}:
            raise ValueError("DEEPSEEK_MODEL_POLICY_MISMATCH")
        for name in ("fkqt_cwd", "fkqt_python", "jev_cwd", "jev_python", "data_root", "candidate_source"):
            path = getattr(self, name)
            if not path.is_absolute():
                raise ValueError("FORWARD_ABSOLUTE_PATH_REQUIRED")
        for name in ("fkqt_cwd", "jev_cwd"):
            if not getattr(self, name).is_dir():
                raise ValueError("FORWARD_WORKSPACE_MISSING")
        for name in ("fkqt_python", "jev_python"):
            if not getattr(self, name).is_file():
                raise ValueError("FORWARD_INTERPRETER_MISSING")
        if not self.candidate_source.exists():
            raise ValueError("FORWARD_CANDIDATE_SOURCE_MISSING")
        if self.readiness_deadline_local.tzinfo is not None:
            raise ValueError("FORWARD_DEADLINE_LOCAL_TIME_REQUIRED")
        if self.webhook_url is not None:
            url = urlsplit(self.webhook_url.get_secret_value())
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("FORWARD_WEBHOOK_URL_INVALID")
        return self

    def content_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"webhook_url"})
        payload["webhook_destination_hash"] = sha256_json(self.webhook_url.get_secret_value()) if self.webhook_url else None
        return sha256_json(payload)

    @classmethod
    def load(cls, path: Path) -> "ForwardOperationConfig":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
