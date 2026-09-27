from collections.abc import Mapping

from fkqt_jevinvestor.domain.decision import DecisionInputV1
from fkqt_jevinvestor.ingestion.canonical import canonical_json

DECISION_PROMPT_VERSION = "decision-prompt-v1"
DECISION_OUTPUT_SCHEMA_VERSION = "decision-output-v1"
DECISION_MAX_OUTPUT_CHARS = 4096

_SYSTEM_PROMPT = """你是 A 股日频离散动作分类器，不是行情数据源或仓位计算器。
只能使用 user payload 中的冻结事实和 Jev 概率；Jev 概率是未校准预测，不是真实胜率。
只从 allowed_actions 选择一个动作。不得计算仓位，不得补充输入中不存在的数字、新闻、价格、事实或因果关系。
只输出符合给定 JSON Schema 的对象，不要 Markdown，不要解释 JSON 之外的内容。"""


def decision_output_json_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["action", "thesis", "invalidation"],
        "properties": {
            "action": {"type": "string", "enum": ["ENTER", "KEEP", "EXIT", "AVOID"]},
            "thesis": {"type": "string", "minLength": 1, "maxLength": 240},
            "invalidation": {"type": "string", "minLength": 1, "maxLength": 240},
        },
    }


def build_decision_messages(
    decision_input: DecisionInputV1,
) -> tuple[Mapping[str, str], Mapping[str, str]]:
    return (
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": canonical_json(decision_input)},
    )
