import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from fkqt_jevinvestor.domain.jev_market import JevEvaluationCommand
from fkqt_jevinvestor.providers.jev_market_questions import QUESTION_DEFINITIONS

REASONS = frozenset({
    "JEV_CHOICES_MAPPING_REQUIRED", "JEV_QUESTION_SET_INVALID", "JEV_PROBABILITIES_MAPPING_REQUIRED",
    "JEV_PROBABILITY_LABELS_INVALID", "JEV_PROBABILITY_VALUE_INVALID", "JEV_PROBABILITY_SUM_INVALID",
    "JEV_SELECTED_LABEL_INVALID", "JEV_RESPONSE_CONTRACT_INVALID",
})


def safe_usage(response: object) -> dict[str, int] | None:
    usage = getattr(response, "usage", None)
    values: dict[str, int] = {}
    for name, alternate in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
        value = usage.get(name, usage.get(alternate)) if isinstance(usage, Mapping) else getattr(usage, name, getattr(usage, alternate, None))
        if type(value) is not int or not 0 <= value <= 10**12:
            return None
        values[name] = value
    return values


def build_jev_diagnostic(response: object, command: JevEvaluationCommand, reason_codes: tuple[str, ...]) -> dict[str, Any]:
    choices = getattr(response, "choices", None)
    questions: list[dict[str, Any]] = []
    expected = {k: d for k, d in QUESTION_DEFINITIONS.items() if d.scope is command.scope}
    for key, definition in expected.items():
        answer = choices.get(key) if isinstance(choices, Mapping) else None
        probabilities = getattr(answer, "probabilities", None)
        values: dict[str, str | None] = {}
        for label in definition.label_order:
            value = probabilities.get(label) if isinstance(probabilities, Mapping) else None
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
                          "unknown_label_count": len(set(probabilities) - set(definition.label_order)) if isinstance(probabilities, Mapping) else 0})
    payload: dict[str, Any] = {
        "version": "jev-diagnostic-v1", "input_hash": command.input_hash,
        "model_id_hash": hashlib.sha256(command.model_id.encode()).hexdigest(),
        "question_set_version": command.question_set_version,
        "reason_codes": sorted(set(reason_codes) & REASONS), "questions": questions,
        "unknown_question_count": len(set(choices) - set(expected)) if isinstance(choices, Mapping) else 0,
        "truncated": False,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > 16384:
        payload["questions"] = []
        payload["truncated"] = True
        payload["diagnostic_content_hash"] = hashlib.sha256(encoded).hexdigest()
    return payload
