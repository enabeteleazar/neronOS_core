# core/pipeline/intent/intent_router.py
# v2.2 — route() accepte session_id pour le context manager
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict

from core.agents.base_agent import get_logger

logger = get_logger(__name__)


def _nlp():
    from core.pipeline.nlp.nlp_processor import get_processor
    return get_processor()


class Intent(str, Enum):
    CONVERSATION         = "conversation"
    WEB_SEARCH           = "web_search"
    HA_ACTION            = "ha_action"
    TIME_QUERY           = "time_query"
    PERSONALITY_FEEDBACK = "personality_feedback"
    CODE                 = "code"
    CODE_AUDIT           = "code_audit"
    NEWS_QUERY           = "news_query"
    WEATHER_QUERY        = "weather_query"
    TODO_ACTION          = "todo_action"
    WIKI_QUERY           = "wiki_query"


_INTENT_MAP: Dict[str, Intent] = {i.value: i for i in Intent}


@dataclass
class IntentResult:
    intent:           Intent
    confidence:       str                    # "high" | "medium" | "low"
    confidence_score: float = 0.0
    entities:         Dict[str, Any] = field(default_factory=dict)

    def to_nlp_dict(self) -> Dict[str, Any]:
        return {
            "intent":     self.intent.value,
            "entities":   self.entities,
            "confidence": self.confidence_score,
        }


class IntentRouter:
    def __init__(self, llm_agent=None) -> None:
        self.llm_agent = llm_agent

    async def route(self, query: str, session_id: str = "default") -> IntentResult:
        nlp_result = _nlp().process(query, session_id)
        intent     = _INTENT_MAP.get(nlp_result.intent, Intent.CONVERSATION)
        score      = nlp_result.confidence
        confidence = "high" if score >= 0.7 else ("medium" if score >= 0.4 else "low")

        logger.info(
            "[NLP] intent=%s conf=%.3f entities=%s plan_mode=%s",
            nlp_result.intent, score, nlp_result.entities,
            nlp_result.plan.mode if nlp_result.plan else "single",
        )

        return IntentResult(
            intent=intent,
            confidence=confidence,
            confidence_score=score,
            entities=nlp_result.entities,
        )
