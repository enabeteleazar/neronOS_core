# core/pipeline/intent/intent_router.py
# v2.1 — Intégration couche NLP (intent_classifier + entity_extractor)
#         Backward-compatible : confidence str conservée, entities ajouté.

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict

from core.agents.base_agent import get_logger

logger = get_logger(__name__)

# ── NLP processor (lazy import pour éviter les circulaires) ───────────────────

def _nlp():
    from core.pipeline.nlp.nlp_processor import get_processor
    return get_processor()


# ── Intent enum ───────────────────────────────────────────────────────────────

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
    confidence:       str                          # "high" | "medium" | "low" (compat)
    confidence_score: float = 0.0                 # float [0.0-1.0] via NLP
    entities:         Dict[str, Any] = field(default_factory=dict)

    def to_nlp_dict(self) -> Dict[str, Any]:
        """Sortie standard NLP exploitable par les agents."""
        return {
            "intent":     self.intent.value,
            "entities":   self.entities,
            "confidence": self.confidence_score,
        }


def _normalize(text: str) -> str:
    n = unicodedata.normalize("NFD", text.lower().strip())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.replace("'", " ").replace("'", " ").replace("`", " ")


class IntentRouter:
    def __init__(self, llm_agent=None) -> None:
        self.llm_agent = llm_agent

    async def route(self, query: str) -> IntentResult:
        # ── NLP processing ────────────────────────────────────────────────────
        nlp_result = _nlp().process(query)
        intent_str = nlp_result.intent
        intent     = _INTENT_MAP.get(intent_str, Intent.CONVERSATION)
        entities   = nlp_result.entities
        score      = nlp_result.confidence
        confidence = "high" if score >= 0.7 else ("medium" if score >= 0.4 else "low")

        logger.info(
            "[NLP] intent=%s confidence=%.3f entities=%s",
            intent_str, score, entities,
        )

        return IntentResult(
            intent=intent,
            confidence=confidence,
            confidence_score=score,
            entities=entities,
        )
