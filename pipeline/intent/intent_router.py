# core/pipeline/intent/intent_router.py
# v2.0 — Ajout intents NEWS_QUERY, WEATHER_QUERY, TODO_ACTION, WIKI_QUERY
#         Inspiré de la logique de dispatch de J.A.R.V.I.S (GauravSingh9356)
#         portée dans le modèle agent Néron.

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum

from core.agents.base_agent import get_logger
from core.constants import (
    CODE_KEYWORDS,
    CODE_AUDIT_KEYWORDS,
    HA_KEYWORDS,
    NEWS_KEYWORDS,
    PERSONALITY_KEYWORDS,
    TIME_KEYWORDS,
    TODO_KEYWORDS,
    WEATHER_KEYWORDS,
    WEB_KEYWORDS,
    WIKI_KEYWORDS,
)

logger = get_logger(__name__)


class Intent(str, Enum):
    CONVERSATION         = "conversation"
    WEB_SEARCH           = "web_search"
    HA_ACTION            = "ha_action"
    TIME_QUERY           = "time_query"
    PERSONALITY_FEEDBACK = "personality_feedback"
    CODE                 = "code"
    CODE_AUDIT           = "code_audit"
    # ── Nouveaux intents v2.0 ─────────────────────────────────────────────────
    NEWS_QUERY           = "news_query"
    WEATHER_QUERY        = "weather_query"
    TODO_ACTION          = "todo_action"
    WIKI_QUERY           = "wiki_query"


@dataclass
class IntentResult:
    intent:     Intent
    confidence: str


def _normalize(text: str) -> str:
    """Normalise : minuscules + suppression des accents + apostrophes → espace."""
    n = unicodedata.normalize("NFD", text.lower().strip())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = n.replace("'", " ").replace("'", " ").replace("`", " ")
    return n


class IntentRouter:
    def __init__(self, llm_agent=None) -> None:
        self.llm_agent = llm_agent

    async def route(self, query: str) -> IntentResult:
        q_norm = _normalize(query)

        # ── 1. Feedback comportemental (priorité max) ─────────────────────────
        for kw in PERSONALITY_KEYWORDS:
            if _normalize(kw) in q_norm:
                logger.info("[ROUTER] intent=personality_feedback — déclencheur: %r", kw)
                return IntentResult(intent=Intent.PERSONALITY_FEEDBACK, confidence="high")

        # ── 2. Auto-audit Néron ───────────────────────────────────────────────
        for kw in CODE_AUDIT_KEYWORDS:
            if _normalize(kw) in q_norm:
                logger.info("[ROUTER] intent=code_audit — déclencheur: %r", kw)
                return IntentResult(intent=Intent.CODE_AUDIT, confidence="high")

        # ── 3. Code / développement ───────────────────────────────────────────
        for kw in CODE_KEYWORDS:
            if _normalize(kw) in q_norm:
                logger.info("[ROUTER] intent=code — déclencheur: %r", kw)
                return IntentResult(intent=Intent.CODE, confidence="high")

        # ── 4. Todo list ──────────────────────────────────────────────────────
        if any(_normalize(w) in q_norm for w in TODO_KEYWORDS):
            logger.info("[ROUTER] intent=todo_action")
            return IntentResult(intent=Intent.TODO_ACTION, confidence="high")

        # ── 5. Actualités ─────────────────────────────────────────────────────
        if any(_normalize(w) in q_norm for w in NEWS_KEYWORDS):
            logger.info("[ROUTER] intent=news_query")
            return IntentResult(intent=Intent.NEWS_QUERY, confidence="high")

        # ── 6. Météo ──────────────────────────────────────────────────────────
        if any(_normalize(w) in q_norm for w in WEATHER_KEYWORDS):
            logger.info("[ROUTER] intent=weather_query")
            return IntentResult(intent=Intent.WEATHER_QUERY, confidence="high")

        # ── 7. Wikipédia ──────────────────────────────────────────────────────
        if any(_normalize(w) in q_norm for w in WIKI_KEYWORDS):
            logger.info("[ROUTER] intent=wiki_query")
            return IntentResult(intent=Intent.WIKI_QUERY, confidence="high")

        # ── 8. Heure / date ───────────────────────────────────────────────────
        if any(_normalize(w) in q_norm for w in TIME_KEYWORDS):
            return IntentResult(intent=Intent.TIME_QUERY, confidence="high")

        # ── 9. Recherche web ──────────────────────────────────────────────────
        if any(_normalize(w) in q_norm for w in WEB_KEYWORDS):
            return IntentResult(intent=Intent.WEB_SEARCH, confidence="high")

        # ── 10. Home Assistant ────────────────────────────────────────────────
        if any(_normalize(w) in q_norm for w in HA_KEYWORDS):
            return IntentResult(intent=Intent.HA_ACTION, confidence="high")

        # ── 11. Conversation générale (défaut) ────────────────────────────────
        return IntentResult(intent=Intent.CONVERSATION, confidence="medium")
