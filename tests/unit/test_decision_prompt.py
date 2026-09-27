import json
from typing import cast

from fkqt_jevinvestor.providers.decision_prompt import (
    DECISION_OUTPUT_SCHEMA_VERSION,
    DECISION_PROMPT_VERSION,
    build_decision_messages,
    decision_output_json_schema,
)
from tests.unit.test_decision_contracts import _input  # pyright: ignore[reportPrivateUsage]


def test_prompt_versions_and_schema_freeze_discrete_output() -> None:
    schema = decision_output_json_schema()
    properties = cast(dict[str, dict[str, object]], schema["properties"])

    assert DECISION_PROMPT_VERSION == "decision-prompt-v1"
    assert DECISION_OUTPUT_SCHEMA_VERSION == "decision-output-v1"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["action", "thesis", "invalidation"]
    assert set(properties) == {"action", "thesis", "invalidation"}
    assert properties["action"]["enum"] == [
        "ENTER",
        "KEEP",
        "EXIT",
        "AVOID",
    ]


def test_prompt_uses_canonical_input_and_forbids_numeric_decisions() -> None:
    decision_input = _input()
    messages = build_decision_messages(decision_input)

    assert tuple(message["role"] for message in messages) == ("system", "user")
    system = messages[0]["content"]
    assert "不得计算仓位" in system
    assert "只从 allowed_actions 选择" in system
    assert "Jev 概率是未校准预测" in system
    assert json.loads(messages[1]["content"]) == decision_input.model_dump(mode="json")
    forbidden = ("API Key", "target_position_pct", "quantity", "D+1 行情")
    assert all(term not in messages[1]["content"] for term in forbidden)
