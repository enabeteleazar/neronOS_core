"""core/agents/io/news_agent.py
Neron Core — Agent Actualités  v1.0.0

Inspiré de news.py (JARVIS) — récupère les titres d'actualité via NewsAPI
ou via le flux RSS de Le Monde (fallback sans clé API).

Intent déclenché : NEWS_QUERY
Commandes Telegram : /news [catégorie]

Config attendue dans neron.yaml :
  NEWS_API_KEY: "votre_clé_newsapi"   # optionnel, fallback RSS si absent
  NEWS_COUNTRY: "fr"                  # défaut : fr
  NEWS_MAX_HEADLINES: 5               # défaut : 5
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger("agent.news")

# ── Config ────────────────────────────────────────────────────────────────────

_API_KEY     = getattr(settings, "NEWS_API_KEY", "")
_COUNTRY     = getattr(settings, "NEWS_COUNTRY", "fr")
_MAX         = int(getattr(settings, "NEWS_MAX_HEADLINES", 5))

_NEWSAPI_URL = "https://newsapi.org/v2/top-headlines"
_RSS_FEEDS   = {
    "general":      "https://www.lemonde.fr/rss/une.xml",
    "tech":         "https://www.lemonde.fr/pixels/rss_full.xml",
    "science":      "https://www.lemonde.fr/sciences/rss_full.xml",
    "france":       "https://www.lemonde.fr/france/rss_full.xml",
    "international":"https://www.lemonde.fr/international/rss_full.xml",
}

CATEGORY_ALIASES: dict[str, str] = {
    "tech":          "tech",
    "technologie":   "tech",
    "science":       "science",
    "sciences":      "science",
    "france":        "france",
    "politique":     "france",
    "monde":         "international",
    "international": "international",
    "general":       "general",
    "une":           "general",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_category(query: str) -> str:
    """Extrait la catégorie depuis la query utilisateur."""
    q = query.lower()
    for alias, cat in CATEGORY_ALIASES.items():
        if alias in q:
            return cat
    return "general"


async def _fetch_newsapi(category: str) -> list[str]:
    """Récupère les titres via NewsAPI (requiert une clé API)."""
    params = {
        "apiKey":   _API_KEY,
        "country":  _COUNTRY,
        "category": category if category != "general" else "",
        "pageSize": _MAX,
    }
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(_NEWSAPI_URL, params=params)
        r.raise_for_status()
        data = r.json()
        return [
            f"• {a['title']} ({a.get('source', {}).get('name', '?')})"
            for a in data.get("articles", [])[:_MAX]
        ]


async def _fetch_rss(category: str) -> list[str]:
    """Fallback : récupère les titres depuis un flux RSS Le Monde."""
    url = _RSS_FEEDS.get(category, _RSS_FEEDS["general"])
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(url)
        r.raise_for_status()
    root = ET.fromstring(r.text)
    items = root.findall(".//item")[:_MAX]
    headlines = []
    for item in items:
        title = item.findtext("title", "").strip()
        if title:
            headlines.append(f"• {title}")
    return headlines


# ── Agent ─────────────────────────────────────────────────────────────────────

class NewsAgent:
    """
    Retourne les titres d'actualité du moment.
    Utilise NewsAPI si une clé est configurée, sinon RSS Le Monde.
    """

    async def run(self, query: str = "") -> str:
        category = _parse_category(query)
        label = {
            "general":      "à la une",
            "tech":         "tech",
            "science":      "sciences",
            "france":       "France",
            "international":"monde",
        }.get(category, "à la une")

        try:
            if _API_KEY:
                headlines = await _fetch_newsapi(category)
                source = "NewsAPI"
            else:
                headlines = await _fetch_rss(category)
                source = "Le Monde (RSS)"

            if not headlines:
                return f"Aucune actualité trouvée pour la catégorie « {label} »."

            header = f"📰 Actualités {label} ({source}) :\n\n"
            return header + "\n".join(headlines)

        except httpx.TimeoutException:
            return "⚠️ Délai dépassé lors de la récupération des actualités."
        except Exception as e:
            logger.error("NewsAgent error: %s", e)
            return "Impossible de récupérer les actualités pour le moment."
