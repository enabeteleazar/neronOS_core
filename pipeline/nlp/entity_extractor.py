# core/pipeline/nlp/entity_extractor.py
# Extraction d'entités par regex — CPU only, zéro dépendance externe.
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Any

# ── Patterns ──────────────────────────────────────────────────────────────────

_STOP_TIME = (
    r"demain|aujourd|hier|ce\s+soir|ce\s+matin|maintenant"
    r"|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche"
)

_CITY_PATTERN = re.compile(
    r"(?:meteo|m[eé]t[eé]o|temps|temp[eé]rature|il fait|pleuvoir)\s+"
    r"(?:[aà]|sur|pour|en)?\s*"
    r"([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ\-]+"
    r"(?:\s+[A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ\-]+)?)"
    r"(?=\s*[?,.]|\s+(?:" + _STOP_TIME + r")|\s*$)",
    re.IGNORECASE,
)

_DEVICE_PATTERN = re.compile(
    r"(?:allume|[eé]teins|active|d[eé]sactive|r[eè]gle)\s+"
    r"(?:le|la|les|l[e']?)?\s*"
    r"([a-zà-ÿA-ZÀ-Ÿ][a-zà-ÿA-ZÀ-Ÿ\s\-]{1,40}?)"
    r"(?:\s+(?:dans|de|du|au|[aà])\s+.+)?(?:[?,.]|$)",
    re.IGNORECASE,
)

_ROOM_PATTERN = re.compile(
    r"(?:dans|du|de la|au|en)\s+"
    r"(salon|chambre|cuisine|salle de bain|bureau|garage|couloir|entr[eé]e|jardin|cave|grenier)",
    re.IGNORECASE,
)

_TOPIC_PATTERN = re.compile(
    r"(?:qu[e']?est[\-\s]ce que|c[e']?est quoi|d[eé]finition de"
    r"|explique[\-\s]moi|parle[\-\s]moi de|qui est|dis[\-\s]moi ce qu[e']?est"
    r"|cherche|recherche|trouve|wikipedia|wiki)\s+"
    r"(?:le|la|les|l[e']?|un|une|des)?\s*"
    r"(.{3,80}?)(?:[?,.]|$)",
    re.IGNORECASE,
)

_TODO_ACTION_PATTERN = re.compile(
    r"(?:ajoute|rappelle[\-\s]moi de|n[e']?oublie pas de|note que|marque comme)\s+"
    r"(.{3,120}?)(?:[?,.]|$)",
    re.IGNORECASE,
)

_NEWS_TOPIC_PATTERN = re.compile(
    r"(?:actualit[eé]s?|news|nouvelles?)\s+(?:sur|de|du|en|pour)?\s+"
    r"([a-zà-ÿA-ZÀ-Ÿ][a-zà-ÿA-ZÀ-Ÿ\s\-]{2,30}?)(?:[?,.]|$)",
    re.IGNORECASE,
)

_FILEPATH_PATTERN = re.compile(
    r"(?:fichier|file|module|script)\s+['\"]?([^\s'\"]{3,80})['\"]?",
    re.IGNORECASE,
)

_TIME_REF_PATTERN = re.compile(
    r"\b(aujourd[e']?hui|demain|hier|ce soir|ce matin|maintenant|"
    r"lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|"
    r"\d{1,2}[h:]\d{0,2})\b",
    re.IGNORECASE,
)

_NUMBER_PATTERN = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(degr[eé]s?|%|°C|°F|km|m|kg|l)?\b")

_HA_STATE_PATTERN = re.compile(
    r"\b(allum[eé]|[eé]teint|ouvert|ferm[eé]|"
    r"verrouill[eé]|d[eé]verrouill[eé])\b",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    n = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in n if unicodedata.category(c) != "Mn")

_LOC_STRIP = {
    "demain","aujourd","hier","matin","soir","maintenant",
    "lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche",
    "le","la","les","du","de","en","dans","pour","sur","et","ou",
}

def _clean_location(raw: str) -> str:
    words = raw.strip().split()
    while words and words[-1].lower() in _LOC_STRIP:
        words.pop()
    return " ".join(words).title()


def extract_entities(text: str, intent: str) -> Dict[str, Any]:
    """Extrait les entités pertinentes selon l'intent détecté."""
    entities: Dict[str, Any] = {}

    # ── Ville (météo) ─────────────────────────────────────────────────────────
    m = _CITY_PATTERN.search(text)
    if m:
        entities["location"] = _clean_location(m.group(1))

    # ── Pièce + device + état (HA) ────────────────────────────────────────────
    if intent == "ha_action":
        m = _ROOM_PATTERN.search(text)
        if m:
            entities["room"] = m.group(1).strip().lower()

        m = _DEVICE_PATTERN.search(text)
        if m:
            entities["device"] = m.group(1).strip().lower()

        m = _HA_STATE_PATTERN.search(text)
        if m:
            entities["target_state"] = _norm(m.group(1))

    # ── Sujet (wiki / web) ────────────────────────────────────────────────────
    if intent in ("wiki_query", "web_search", "conversation"):
        m = _TOPIC_PATTERN.search(text)
        if m:
            entities["topic"] = m.group(1).strip()

    # ── Sujet news ────────────────────────────────────────────────────────────
    if intent == "news_query":
        m = _NEWS_TOPIC_PATTERN.search(text)
        if m:
            entities["news_topic"] = m.group(1).strip().lower()

    # ── Tâche todo ────────────────────────────────────────────────────────────
    if intent == "todo_action":
        m = _TODO_ACTION_PATTERN.search(text)
        if m:
            entities["task"] = m.group(1).strip()

    # ── Fichier (code) ────────────────────────────────────────────────────────
    if intent in ("code", "code_audit"):
        m = _FILEPATH_PATTERN.search(text)
        if m:
            entities["filepath"] = m.group(1).strip()

    # ── Référence temporelle ──────────────────────────────────────────────────
    m = _TIME_REF_PATTERN.search(text)
    if m:
        entities["time_ref"] = m.group(1).strip().lower()

    # ── Valeur numérique ──────────────────────────────────────────────────────
    m = _NUMBER_PATTERN.search(text)
    if m and m.group(1):
        entities["value"] = m.group(1)
        if m.group(2):
            entities["unit"] = m.group(2).strip()

    return entities
