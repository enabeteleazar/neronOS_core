# core/pipeline/nlp/intent_classifier.py
# Classification par score pondéré — remplace le match booléen de l'intent_router.
# Retourne un score float [0.0, 1.0] + intent gagnant.
from __future__ import annotations

import unicodedata
from typing import Dict, List, Tuple

from core.constants import (
    CODE_KEYWORDS, CODE_AUDIT_KEYWORDS, HA_KEYWORDS, NEWS_KEYWORDS,
    PERSONALITY_KEYWORDS, TIME_KEYWORDS, TODO_KEYWORDS,
    WEATHER_KEYWORDS, WEB_KEYWORDS, WIKI_KEYWORDS,
)

# ── Intent map: (keywords, base_score) ───────────────────────────────────────
# Les mots-clés longs (multi-mots) valent plus que les mono-mots.

_INTENT_SPECS: List[Tuple[str, List[str], float]] = [
    ("personality_feedback", PERSONALITY_KEYWORDS, 1.0),
    ("code_audit",           CODE_AUDIT_KEYWORDS,  1.0),
    ("code",                 CODE_KEYWORDS,         0.95),
    ("todo_action",          TODO_KEYWORDS,         0.9),
    ("news_query",           NEWS_KEYWORDS,         0.9),
    ("weather_query",        WEATHER_KEYWORDS,      0.9),
    ("wiki_query",           WIKI_KEYWORDS,         0.85),
    ("time_query",           TIME_KEYWORDS,         0.9),
    ("web_search",           WEB_KEYWORDS,          0.85),
    ("ha_action",            HA_KEYWORDS,           0.9),
]


def _normalize(text: str) -> str:
    n = unicodedata.normalize("NFD", text.lower().strip())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.replace("'", " ").replace("'", " ").replace("`", " ")


def _kw_score(kw_norm: str) -> float:
    """Score d'un mot-clé = 1.0 + 0.15 par mot supplémentaire (multi-mots > mono)."""
    words = kw_norm.split()
    return 1.0 + 0.15 * max(0, len(words) - 1)


def classify(text: str) -> Tuple[str, float]:
    """
    Retourne (intent_name, confidence) avec confidence ∈ [0.0, 1.0].
    Falls back à 'conversation' avec confidence=0.4.
    """
    q = _normalize(text)
    best_intent = "conversation"
    best_score  = 0.0

    for intent_name, keywords, base in _INTENT_SPECS:
        raw_score = 0.0
        for kw in keywords:
            kw_n = _normalize(kw)
            if kw_n in q:
                raw_score += _kw_score(kw_n)

        if raw_score == 0.0:
            continue

        # Normaliser : score brut max théorique ≈ 4.0 pour un kw de 3 mots très long
        # On clampe à 1.0 après pondération par la priorité base
        confidence = min(1.0, (raw_score / 3.0) * base)

        if confidence > best_score:
            best_score  = confidence
            best_intent = intent_name

    if best_intent == "conversation":
        return "conversation", 0.4

    return best_intent, round(best_score, 3)


def scores_all(text: str) -> Dict[str, float]:
    """Debug : retourne tous les scores (non nuls)."""
    q = _normalize(text)
    result: Dict[str, float] = {}

    for intent_name, keywords, base in _INTENT_SPECS:
        raw = sum(_kw_score(_normalize(kw)) for kw in keywords if _normalize(kw) in q)
        if raw > 0:
            result[intent_name] = round(min(1.0, (raw / 3.0) * base), 3)

    return result
