from typesafe_sdk import Choice

from fkqt_jevinvestor.domain.jev_market import JevScope
from fkqt_jevinvestor.providers.jev_market_questions import (
    QUESTION_DEFINITIONS,
    build_jev_market_questions,
)


def test_universe_question_is_choice_only_and_complete() -> None:
    questions = build_jev_market_questions(JevScope.UNIVERSE)

    assert tuple(questions) == ("universe_risk_regime",)
    assert all(isinstance(question, Choice) for question in questions.values())
    assert tuple(questions["universe_risk_regime"].criteria) == (
        "RISK_ON",
        "NEUTRAL",
        "RISK_OFF",
    )


def test_symbol_questions_are_choice_only_and_complete() -> None:
    questions = build_jev_market_questions(JevScope.SYMBOL)

    assert tuple(questions) == (
        "next_session_pnl",
        "profitability_5d",
        "drawdown_risk_5d",
        "payoff_asymmetry_5d",
        "data_sufficiency",
    )
    assert all(isinstance(question, Choice) for question in questions.values())


def test_question_versions_and_labels_are_frozen() -> None:
    assert QUESTION_DEFINITIONS["profitability_5d"].question_version == "profitability-5d-v1"
    assert QUESTION_DEFINITIONS["profitability_5d"].criteria_version == "pnl-label-criteria-v1"
    assert QUESTION_DEFINITIONS["profitability_5d"].label_order == (
        "PROFITABLE",
        "FLAT",
        "LOSS",
    )
    assert QUESTION_DEFINITIONS["payoff_asymmetry_5d"].label_order == (
        "UPSIDE_DOMINANT",
        "BALANCED",
        "DOWNSIDE_DOMINANT",
    )


def test_question_catalog_has_no_weight_or_trade_action_output() -> None:
    serialized = " ".join(
        f"{item.question_id} {item.instructions} {' '.join(item.label_order)}"
        for item in QUESTION_DEFINITIONS.values()
    ).upper()

    for forbidden in ("WEIGHT", "ENTER", "KEEP", "EXIT", "AVOID"):
        assert forbidden not in serialized
