# core/pipeline/nlp/intent_classifier.py
# v2 — scoring hybride : exact keyword + fuzzy (Levenshtein + trigrams)
# CPU only, zéro dépendance externe.
from __future__ import annotations

import unicodedata
from typing import Dict, List, Tuple

from core.constants import (
    CODE_KEYWORDS, CODE_AUDIT_KEYWORDS, HA_KEYWORDS, NEWS_KEYWORDS,
    PERSONALITY_KEYWORDS, TIME_KEYWORDS, TODO_KEYWORDS,
    WEATHER_KEYWORDS, WEB_KEYWORDS, WIKI_KEYWORDS,
)
from core.pipeline.nlp.fuzzy import normalize, keyword_match_score

# ── Specs intent : (nom, keywords, priorité_base) ────────────────────────────

_INTENT_SPECS: List[Tuple[str, List[str], float]] = [
    ("personality_feedback", PERSONALITY_KEYWORDS, 1.00),
    ("code_audit",           CODE_AUDIT_KEYWORDS,  1.00),
    ("code",                 CODE_KEYWORDS,         0.95),
    ("todo_action",          TODO_KEYWORDS,         0.90),
    ("news_query",           NEWS_KEYWORDS,         0.90),
    ("weather_query",        WEATHER_KEYWORDS,      0.90),
    ("wiki_query",           WIKI_KEYWORDS,         0.85),
    ("time_query",           TIME_KEYWORDS,         0.90),
    ("web_search",           WEB_KEYWORDS,          0.85),
    ("ha_action",            HA_KEYWORDS,           0.90),
]

# Poids du match exact vs fuzzy dans le score final
_EXACT_WEIGHT = 0.7
_FUZZY_WEIGHT = 0.3

# Seuil minimal de match fuzzy pour être pris en compte
_FUZZY_MIN = 0.55


def _kw_length_bonus(kw_norm: str) -> float:
    """Les mots-clés multi-mots valent plus que les mono-mots."""
    words = kw_norm.split()
    return 1.0 + 0.15 * max(0, len(words) - 1)


def classify(text: str) -> Tuple[str, float]:
    """
    Retourne (intent_name, confidence ∈ [0.0, 1.0]).
    Stratégie hybride :
      - Match exact substring → score fort (× _EXACT_WEIGHT)
      - Match fuzzy token-level → score faible (× _FUZZY_WEIGHT)
    Falls back à 'conversation' avec confidence=0.35.
    """
    q = normalize(text)
    best_intent = "conversation"
    best_score  = 0.0

    for intent_name, keywords, base in _INTENT_SPECS:
        raw_exact = 0.0
        raw_fuzzy = 0.0

        for kw in keywords:
            kw_n  = normalize(kw)
            bonus = _kw_length_bonus(kw_n)

            # ── Match exact substring ─────────────────────────────────────────
            if kw_n in q:
                raw_exact += bonus
                continue  # exact > fuzzy, pas besoin de calculer les deux

            # ── Match fuzzy ───────────────────────────────────────────────────
            fscore = keyword_match_score(q, kw_n)
            if fscore >= _FUZZY_MIN:
                raw_fuzzy += fscore * bonus

        if raw_exact == 0.0 and raw_fuzzy == 0.0:
            continue

        # Normalisation — max théorique ≈ 4.0 pour un long keyword multi-mots
        exact_contrib = min(1.0, raw_exact / 2.5) * _EXACT_WEIGHT
        fuzzy_contrib = min(1.0, raw_fuzzy / 2.5) * _FUZZY_WEIGHT
        confidence    = min(1.0, (exact_contrib + fuzzy_contrib) * base)

        if confidence > best_score:
            best_score  = confidence
            best_intent = intent_name

    if best_intent == "conversation":
        return "conversation", 0.35

    return best_intent, round(best_score, 3)


def scores_all(text: str) -> Dict[str, float]:
    """Debug : retourne tous les scores non nuls."""
    q = normalize(text)
    result: Dict[str, float] = {}

    for intent_name, keywords, base in _INTENT_SPECS:
        raw = 0.0
        for kw in keywords:
            kw_n = normalize(kw)
            if kw_n in q:
                raw += _kw_length_bonus(kw_n)
            else:
                fs = keyword_match_score(q, kw_n)
                if fs >= _FUZZY_MIN:
                    raw += fs * _kw_length_bonus(kw_n) * _FUZZY_WEIGHT

        if raw > 0:
            result[intent_name] = round(min(1.0, (raw / 2.5) * base), 3)

    return result
