import hashlib
import json

from fkqt_jevinvestor.domain.decision import (
    DecisionAction,
    DecisionModelOutputV1,
    DecisionProviderResult,
)
from fkqt_jevinvestor.ingestion.canonical import canonical_json
from fkqt_jevinvestor.providers.base import ProviderContractError, ProviderUnavailableError
from fkqt_jevinvestor.providers.decision_prompt import decision_output_json_schema
from fkqt_jevinvestor.providers.deepseek_decision import ResponsesClient
from fkqt_jevinvestor.providers.diagnostics import safe_usage
from fkqt_jevinvestor.research.models import ResearchLlmRequest, ResearchStrategyConfig


class ResearchDeepSeekProvider:
    def __init__(self, client: ResponsesClient, config: ResearchStrategyConfig) -> None:
        self.client = client
        self.config = config

    async def evaluate(self, request: ResearchLlmRequest) -> DecisionProviderResult:
        if request.model_config_snapshot != self.config:
            raise ProviderContractError("RESEARCH_LLM_CONFIG_MISMATCH")
        system = "你是A股日频离散动作分类器。只使用冻结事实；若输入含预测概率，它们尚未校准。只从allowed_actions选动作。禁止生成仓位、数量、价格或补造事实。只输出给定JSON Schema。"
        try:
            response = await self.client.responses.create(
                model=self.config.llm_model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": canonical_json(request.payload)},
                ],
                reasoning={"effort": self.config.reasoning_effort},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "research_decision_v1",
                        "strict": True,
                        "schema": decision_output_json_schema(),
                    }
                },
                timeout=self.config.timeout_seconds,
            )
        except Exception as exc:
            raise ProviderUnavailableError(type(exc).__name__) from exc
        usage = safe_usage(response)
        raw = getattr(response, "output_text", None)
        response_hash = hashlib.sha256(raw.encode()).hexdigest() if isinstance(raw, str) else None
        nested_refusal = any(
            getattr(c, "type", None) == "refusal" or bool(getattr(c, "refusal", None))
            for o in (getattr(response, "output", None) or ())
            for c in (getattr(o, "content", None) or ())
        )
        try:
            if (
                getattr(response, "status", None) != "completed"
                or nested_refusal
                or getattr(response, "refusal", None)
                or not isinstance(raw, str)
                or not 0 < len(raw) <= 4096
            ):
                raise ValueError("RESEARCH_LLM_RESPONSE_INVALID")
            output = DecisionModelOutputV1.model_validate(json.loads(raw))
            if DecisionAction(output.action) not in request.allowed_actions:
                raise ValueError("RESEARCH_LLM_ACTION_NOT_ALLOWED")
        except ValueError as exc:
            raise ProviderContractError(
                "RESEARCH_LLM_RESPONSE_INVALID", response_hash=response_hash, usage=usage
            ) from exc
        assert response_hash is not None
        return DecisionProviderResult(
            output=output, raw_response_text=raw, raw_response_hash=response_hash, usage=usage
        )
