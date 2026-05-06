# core/pipeline/nlp/fuzzy.py
# Similarité textuelle pure Python — zéro dépendance externe.
# Levenshtein normalisé + overlap de trigrammes de caractères.
from __future__ import annotations

import unicodedata
from functools import lru_cache
from typing import Sequence


# ── Normalisation ─────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    n = unicodedata.normalize("NFD", text.lower().strip())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.replace("'", " ").replace("'", " ").replace("`", " ")


# ── Levenshtein normalisé ─────────────────────────────────────────────────────

@lru_cache(maxsize=2048)
def levenshtein(a: str, b: str) -> float:
    """
    Retourne une similarité ∈ [0.0, 1.0].
    1.0 = identiques, 0.0 = totalement différents.
    Cache LRU pour les paires répétées (mots-clés fixes vs tokens).
    """
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 0.0
    if abs(la - lb) > max(la, lb) * 0.5:
        return 0.0  # short-circuit : trop différents

    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
        prev = curr

    dist = prev[lb]
    return 1.0 - dist / max(la, lb)


# ── Trigrammes de caractères ──────────────────────────────────────────────────

def char_trigrams(text: str) -> set:
    """Ensemble de trigrammes de caractères d'un token normalisé."""
    t = normalize(text)
    if len(t) < 3:
        return {t}
    return {t[i:i+3] for i in range(len(t) - 2)}


def trigram_similarity(a: str, b: str) -> float:
    """Similarité par Jaccard sur trigrammes de caractères ∈ [0.0, 1.0]."""
    ta, tb = char_trigrams(a), char_trigrams(b)
    if not ta or not tb:
        return 1.0 if a == b else 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


# ── Similarité de token ───────────────────────────────────────────────────────

def token_sim(a: str, b: str) -> float:
    """
    Combine Levenshtein + trigrammes.
    Rapide pour tokens courts (< 12 chars) grâce au LRU cache.
    """
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return 1.0
    lev = levenshtein(na, nb)
    tri = trigram_similarity(na, nb)
    return 0.6 * lev + 0.4 * tri


# ── Matching requête ↔ keyword (multi-tokens) ─────────────────────────────────

_FUZZY_THRESHOLD = 0.82   # seuil de correspondance flou
_EXACT_BONUS     = 0.25   # bonus si match exact d'un token


def keyword_match_score(query_norm: str, keyword_norm: str) -> float:
    """
    Retourne un score ∈ [0.0, 1.0] indiquant si `keyword_norm` est
    présent (exactement ou approximativement) dans `query_norm`.

    Stratégie :
      1. Match exact substring → score 1.0
      2. Token-level fuzzy match → score proportionnel
    """
    # ── 1. Match exact ────────────────────────────────────────────────────────
    if keyword_norm in query_norm:
        return 1.0

    # ── 2. Match par tokens ───────────────────────────────────────────────────
    kw_tokens = keyword_norm.split()
    if not kw_tokens:
        return 0.0

    q_tokens = query_norm.split()
    if not q_tokens:
        return 0.0

    # Pour chaque token du keyword, chercher la meilleure correspondance
    matched = 0.0
    for kt in kw_tokens:
        best = max((token_sim(kt, qt) for qt in q_tokens), default=0.0)
        if best >= _FUZZY_THRESHOLD:
            matched += best + (
                _EXACT_BONUS if any(kt == qt for qt in q_tokens) else 0.0
            )

    ratio = matched / len(kw_tokens)

    # Pénaliser si le keyword multi-mots n'est que partiellement couvert
    coverage = sum(
        1 for kt in kw_tokens
        if max((token_sim(kt, qt) for qt in q_tokens), default=0.0) >= _FUZZY_THRESHOLD
    ) / len(kw_tokens)

    if coverage < 0.6:
        return 0.0   # match trop partiel → on ignore

    return min(1.0, ratio * coverage)
