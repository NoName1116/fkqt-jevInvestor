from typing import Any

from typesafe_sdk import Choice, Score

EXPECTED_QUESTION_IDS = frozenset(
    {
        "symbol_relevance",
        "event_direction",
        "materiality",
        "novelty",
        "persistence",
        "thesis_break_risk",
        "uncertainty",
        "industry_spillover",
    }
)


def build_jev_questions() -> dict[str, Any]:
    return {
        "symbol_relevance": Score(
            instructions="How directly does `event` affect `company`?",
            criteria=["unrelated", "weakly related", "directly related", "core impact"],
        ),
        "event_direction": Choice(
            instructions="What is the direction of `event` for `company` value?",
            criteria={"POSITIVE": None, "NEUTRAL": None, "NEGATIVE": None},
        ),
        "materiality": Score(
            instructions="How material is `event` to `company` fundamentals?",
            criteria=["immaterial", "minor", "material", "transformational"],
        ),
        "novelty": Score(
            instructions="How new is `event` relative to `prior_context`?",
            criteria=["already known", "mostly known", "meaningfully new", "entirely new"],
        ),
        "persistence": Choice(
            instructions="How long is the likely effect of `event`?",
            criteria={"INTRADAY": None, "SHORT": None, "MEDIUM": None, "LONG": None},
        ),
        "thesis_break_risk": Score(
            instructions="How strongly does `event` invalidate `current_thesis`?",
            criteria=["none", "weak", "serious", "thesis broken"],
        ),
        "uncertainty": Score(
            instructions="How uncertain or unverified is `event`?",
            criteria=["confirmed", "low uncertainty", "high uncertainty", "speculative"],
        ),
        "industry_spillover": Score(
            instructions="How strongly does `event` affect `industry` peers?",
            criteria=["none", "limited", "broad", "industry wide"],
        ),
    }
