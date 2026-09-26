from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from fkqt_jevinvestor.domain.backtest import ExperimentArm
from fkqt_jevinvestor.domain.decision import DecisionAction, DecisionProviderResult
from fkqt_jevinvestor.services.position_sizing import PositionSizingConfigV1


class ResearchStrategyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    rule_version: Literal["trend-baseline-v1"] = "trend-baseline-v1"
    direct_version: Literal["jev-action-map-v1"] = "jev-action-map-v1"
    prompt_version: Literal["research-decision-prompt-v1"] = "research-decision-prompt-v1"
    probability_threshold: Decimal = Field(default=Decimal("0.6"), ge=0, le=1, allow_inf_nan=False)
    next_profit_threshold: Decimal = Field(default=Decimal("0.5"), ge=0, le=1, allow_inf_nan=False)
    sizing: PositionSizingConfigV1 = PositionSizingConfigV1()
    jev_model: str = Field(default="jev-latest", min_length=1)
    jev_version: str = Field(default="0.7.0", min_length=1)
    llm_model: str = Field(default="deepseek-flash", min_length=1)
    llm_version: str = Field(default="deepseek-responses-v1", min_length=1)
    llm_base_url: str = Field(default="https://api.deepseek.com", min_length=1)
    reasoning_effort: Literal["low", "high"] = "high"
    timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)


class ResearchLlmRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    experiment_arm: ExperimentArm
    symbol: str
    decision_date: date
    decision_cutoff: datetime
    allowed_actions: tuple[DecisionAction, ...]
    payload: dict[str, Any]
    model_config_snapshot: ResearchStrategyConfig


class ResearchLlmProvider(Protocol):
    async def evaluate(self, request: ResearchLlmRequest) -> DecisionProviderResult: ...
