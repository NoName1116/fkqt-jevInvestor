import hashlib
import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from fkqt_jevinvestor.domain.jev_market import JevEvaluationCommand
from fkqt_jevinvestor.providers.jev_market_questions import QUESTION_DEFINITIONS

REASONS = frozenset({
    "JEV_CHOICES_MAPPING_REQUIRED", "JEV_QUESTION_SET_INVALID", "JEV_PROBABILITIES_MAPPING_REQUIRED",
    "JEV_PROBABILITY_LABELS_INVALID", "JEV_PROBABILITY_VALUE_INVALID", "JEV_PROBABILITY_SUM_INVALID",
    "JEV_SELECTED_LABEL_INVALID", "JEV_RESPONSE_CONTRACT_INVALID",
})


def safe_usage(response: object) -> dict[str, int] | None:
    return normalize_usage(getattr(response, "usage", None))


def normalize_usage(usage: object) -> dict[str, int] | None:
    mapping = cast(Mapping[str, Any], usage) if isinstance(usage, Mapping) else None
    values: dict[str, int] = {}
    for name, alternate in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
        value = mapping.get(name, mapping.get(alternate)) if mapping is not None else getattr(cast(Any, usage), name, getattr(cast(Any, usage), alternate, None))
        if type(value) is not int or not 0 <= value <= 10**12:
            return None
        values[name] = value
    return values


def build_jev_diagnostic(response: object, command: JevEvaluationCommand, reason_codes: tuple[str, ...]) -> dict[str, Any]:
    raw_choices = getattr(response, "choices", None)
    choices = cast(Mapping[str, Any], raw_choices) if isinstance(raw_choices, Mapping) else None
    questions: list[dict[str, Any]] = []
    expected = {k: d for k, d in QUESTION_DEFINITIONS.items() if d.scope is command.scope}
    for key, definition in expected.items():
        answer = choices.get(key) if choices is not None else None
        raw_probabilities = getattr(answer, "probabilities", None)
        probabilities = cast(Mapping[str, Any], raw_probabilities) if isinstance(raw_probabilities, Mapping) else None
        values: dict[str, str | None] = {}
        for label in definition.label_order:
            value = probabilities.get(label) if probabilities is not None else None
            try:
                if len(str(value)) > 64:
                    raise ValueError("NUMERIC_LENGTH")
                number = Decimal(str(value))
                values[label] = str(number) if number.is_finite() else None
            except (ValueError, InvalidOperation):
                values[label] = None
        selected = getattr(answer, "choice", None)
        questions.append({"question_id": key, "question_version": definition.question_version,
                          "criteria_version": definition.criteria_version, "probabilities": values,
                          "selected_label": selected if selected in definition.label_order else None,
                          "unknown_label_count": len(set(probabilities) - set(definition.label_order)) if probabilities is not None else 0})
    payload: dict[str, Any] = {
        "version": "jev-diagnostic-v1", "input_hash": command.input_hash,
        "model_id_hash": hashlib.sha256(command.model_id.encode()).hexdigest(),
        "question_set_version": command.question_set_version,
        "reason_codes": sorted(set(reason_codes) & REASONS), "questions": questions,
        "unknown_question_count": len(set(choices) - set(expected)) if choices is not None else 0,
        "truncated": False,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > 16384:
        payload["questions"] = []
        payload["truncated"] = True
        payload["diagnostic_content_hash"] = hashlib.sha256(encoded).hexdigest()
    return payload


def sanitize_diagnostic(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None or payload.get("version") != "jev-diagnostic-v1":
        return None
    reasons = payload.get("reason_codes", [])
    safe: dict[str, Any] = {"version": "jev-diagnostic-v1", "question_set_version": "jev-pnl-questions-v1",
        "reason_codes": sorted({code for code in cast(list[Any], reasons) if isinstance(code, str) and code in REASONS}) if isinstance(reasons, list) else [],
        "questions": [], "truncated": bool(payload.get("truncated", False))}
    for key in ("input_hash", "model_id_hash", "diagnostic_content_hash"):
        value = payload.get(key)
        if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value):
            safe[key] = value
    for key in ("unknown_question_count",):
        value = payload.get(key, 0)
        safe[key] = value if type(value) is int and 0 <= value <= 10**12 else 0
    questions = payload.get("questions", [])
    if not isinstance(questions, list):
        questions = []
    for question in cast(list[Any], questions)[:6]:
        if not isinstance(question, dict):
            continue
        question = cast(dict[str, Any], question)
        key = question.get("question_id")
        if not isinstance(key, str) or key not in QUESTION_DEFINITIONS:
            continue
        definition = QUESTION_DEFINITIONS[key]
        values: dict[str, str | None] = {}
        probabilities = question.get("probabilities", {})
        for label in definition.label_order:
            value = cast(dict[str, Any], probabilities).get(label) if isinstance(probabilities, dict) else None
            try:
                numeric = Decimal(value) if isinstance(value, str) and len(value) <= 64 else Decimal("NaN")
                values[label] = str(numeric) if numeric.is_finite() else None
            except InvalidOperation:
                values[label] = None
        selected = question.get("selected_label")
        count = question.get("unknown_label_count", 0)
        safe["questions"].append({"question_id": key, "question_version": definition.question_version,
            "criteria_version": definition.criteria_version, "probabilities": values,
            "selected_label": selected if isinstance(selected, str) and selected in definition.label_order else None,
            "unknown_label_count": count if type(count) is int and 0 <= count <= 10**12 else 0})
    if isinstance(payload.get("transport_type"), str) and payload.get("transport_type") in {"TimeoutError", "ConnectionError", "APIConnectionError", "APITimeoutError", "RateLimitError", "AuthenticationError", "PermissionDeniedError", "OTHER"}:
        safe["transport_type"] = payload["transport_type"]
    return safe
