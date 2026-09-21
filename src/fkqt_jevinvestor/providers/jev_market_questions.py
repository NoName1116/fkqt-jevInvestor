from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from typesafe_sdk import Choice

from fkqt_jevinvestor.domain.jev_market import JevScope


@dataclass(frozen=True)
class QuestionDefinition:
    question_id: str
    scope: JevScope
    question_version: str
    criteria_version: str
    label_order: tuple[str, ...]
    instructions: str
    criteria: dict[str, str]


_DEFINITIONS = (
    QuestionDefinition(
        question_id="universe_risk_regime",
        scope=JevScope.UNIVERSE,
        question_version="universe-risk-regime-v1",
        criteria_version="universe-risk-criteria-v1",
        label_order=("RISK_ON", "NEUTRAL", "RISK_OFF"),
        instructions=(
            "Given only the frozen candidate-universe metrics in `state`, which risk "
            "regime best describes the universe at the decision cutoff?"
        ),
        criteria={
            "RISK_ON": "Breadth, trend, liquidity, and downside conditions support equity risk.",
            "NEUTRAL": "The evidence is mixed or does not support either risk extreme.",
            "RISK_OFF": "Breadth, trend, liquidity, or downside conditions oppose equity risk.",
        },
    ),
    QuestionDefinition(
        question_id="next_session_pnl",
        scope=JevScope.SYMBOL,
        question_version="next-session-pnl-v1",
        criteria_version="pnl-label-criteria-v1",
        label_order=("PROFIT", "FLAT", "LOSS"),
        instructions=(
            "Using only `state` available at the decision cutoff, classify the predicted net "
            "return category from the next eligible session open to that session close, "
            "using the frozen round-trip cost 0.00100000 (round-trip-cost-v1)."
        ),
        criteria={
            "PROFIT": "Predicted net return is above 0.002 after the frozen cost assumption.",
            "FLAT": "Predicted net return is between -0.002 and 0.002 inclusive.",
            "LOSS": "Predicted net return is below -0.002 after the frozen cost assumption.",
        },
    ),
    QuestionDefinition(
        question_id="profitability_5d",
        scope=JevScope.SYMBOL,
        question_version="profitability-5d-v1",
        criteria_version="pnl-label-criteria-v1",
        label_order=("PROFITABLE", "FLAT", "LOSS"),
        instructions=(
            "Using only `state` available at the decision cutoff, classify the predicted net "
            "return category from the next eligible session open through the fifth session close, "
            "using the frozen round-trip cost 0.00100000 (round-trip-cost-v1)."
        ),
        criteria={
            "PROFITABLE": "Predicted net return is above 0.005 after the frozen cost assumption.",
            "FLAT": "Predicted net return is between -0.005 and 0.005 inclusive.",
            "LOSS": "Predicted net return is below -0.005 after the frozen cost assumption.",
        },
    ),
    QuestionDefinition(
        question_id="drawdown_risk_5d",
        scope=JevScope.SYMBOL,
        question_version="drawdown-risk-5d-v1",
        criteria_version="pnl-label-criteria-v1",
        label_order=("LOW", "MEDIUM", "HIGH"),
        instructions=(
            "Using only `state` available at the decision cutoff, classify the predicted maximum "
            "adverse excursion during the next five eligible sessions."
        ),
        criteria={
            "LOW": "Predicted absolute maximum adverse excursion is at most 0.02.",
            "MEDIUM": "Predicted absolute maximum adverse excursion is above 0.02 and at most 0.05.",
            "HIGH": "Predicted absolute maximum adverse excursion is above 0.05.",
        },
    ),
    QuestionDefinition(
        question_id="payoff_asymmetry_5d",
        scope=JevScope.SYMBOL,
        question_version="payoff-asymmetry-5d-v1",
        criteria_version="pnl-label-criteria-v1",
        label_order=("UPSIDE_DOMINANT", "BALANCED", "DOWNSIDE_DOMINANT"),
        instructions=(
            "Using only `state` available at the decision cutoff, classify the predicted relation "
            "between favorable and adverse excursion during the next five eligible sessions."
        ),
        criteria={
            "UPSIDE_DOMINANT": "Favorable excursion is at least 1.5 times adverse excursion.",
            "BALANCED": "Neither excursion is at least 1.5 times the other.",
            "DOWNSIDE_DOMINANT": "Adverse excursion is at least 1.5 times favorable excursion.",
        },
    ),
    QuestionDefinition(
        question_id="data_sufficiency",
        scope=JevScope.SYMBOL,
        question_version="data-sufficiency-v1",
        criteria_version="data-sufficiency-criteria-v1",
        label_order=("SUFFICIENT", "LIMITED", "INSUFFICIENT"),
        instructions=(
            "Given the available and missing fields in `state`, classify whether the evidence is "
            "sufficient for the four forecast questions."
        ),
        criteria={
            "SUFFICIENT": "The available fields support all forecast questions.",
            "LIMITED": "The available fields support a cautious forecast with material uncertainty.",
            "INSUFFICIENT": "The missing fields prevent a defensible forecast.",
        },
    ),
)

QUESTION_DEFINITIONS = MappingProxyType(
    {definition.question_id: definition for definition in _DEFINITIONS}
)


def build_jev_market_questions(scope: JevScope) -> dict[str, Any]:
    return {
        definition.question_id: Choice(
            instructions=definition.instructions,
            criteria={label: definition.criteria[label] for label in definition.label_order},
        )
        for definition in _DEFINITIONS
        if definition.scope is scope
    }
