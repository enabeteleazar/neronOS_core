"""core/agents/io/wiki_agent.py
Neron Core — Agent Wikipédia  v1.0.0

Inspiré de helpers.py (JARVIS) — résumé Wikipédia via l'API officielle
+ correction orthographique automatique via difflib (comme JARVIS).

Intent déclenché : WIKI_QUERY
Commandes Telegram : /wiki <sujet>

Détecte les requêtes : "qu'est-ce que", "c'est quoi", "définition de",
"explique-moi", "parle-moi de", "qui est", "wikipedia"
"""
from __future__ import annotations

import difflib
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("agent.wiki")

# ── Config ────────────────────────────────────────────────────────────────────

_WIKI_API    = "https://fr.wikipedia.org/w/api.php"
_WIKI_SEARCH = "https://fr.wikipedia.org/w/api.php"
_MAX_CHARS   = 600  # longueur max du résumé retourné

# ── Patterns de détection ─────────────────────────────────────────────────────

_SUBJECT_PATTERNS = [
    r"(?:qu.est.ce que|c.est quoi|definition de|définition de)\s+(.+?)(?:\?|$)",
    r"(?:explique.moi|parle.moi de|dis.moi ce qu.est)\s+(.+?)(?:\?|$)",
    r"(?:qui est|qu.est.ce qu.est|what is)\s+(.+?)(?:\?|$)",
    r"(?:wikipedia|wiki)\s+(.+?)(?:\?|$)",
    r"(?:cherche|recherche)\s+(?:sur\s+)?(.+?)\s+(?:sur|dans)\s+wikipedia",
]


def _extract_subject(query: str) -> str:
    """Extrait le sujet de la requête."""
    for pat in _SUBJECT_PATTERNS:
        m = re.search(pat, query, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    # Fallback : supprime les mots déclencheurs et retourne le reste
    q = re.sub(
        r"^(dis.moi|explique|cherche|wikipedia|wiki|qu.est.ce que|définition de)",
        "", query, flags=re.IGNORECASE
    ).strip()
    return q or query


# ── API Wikipédia ─────────────────────────────────────────────────────────────

async def _search_page(query: str) -> Optional[str]:
    """Cherche le titre exact via l'API de recherche Wikipédia."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(_WIKI_SEARCH, params={
            "action": "query",
            "list":   "search",
            "srsearch": query,
            "srlimit": 5,
            "format": "json",
            "utf8":   1,
        })
        r.raise_for_status()
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return None
        return results[0]["title"]


async def _get_summary(title: str) -> Optional[str]:
    """Récupère le résumé de la page Wikipédia."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(_WIKI_API, params={
            "action":   "query",
            "prop":     "extracts",
            "exintro":  True,
            "explaintext": True,
            "titles":   title,
            "format":   "json",
            "utf8":     1,
        })
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for pid, page in pages.items():
            if pid == "-1":
                return None
            extract = page.get("extract", "").strip()
            if extract:
                # Tronque proprement à la fin d'une phrase
                if len(extract) > _MAX_CHARS:
                    extract = extract[:_MAX_CHARS]
                    last_dot = extract.rfind(".")
                    if last_dot > 100:
                        extract = extract[:last_dot + 1]
                return extract
    return None


def _spell_correct(query: str, candidates: list[str]) -> Optional[str]:
    """
    Correction orthographique via difflib — identique à l'approche JARVIS.
    Retourne le candidat le plus proche si similarité > 0.6.
    """
    matches = difflib.get_close_matches(query, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None


# ── Agent ─────────────────────────────────────────────────────────────────────

class WikiAgent:
    """
    Retourne un résumé Wikipédia pour le sujet demandé.
    Intègre une correction orthographique automatique via difflib.
    """

    async def run(self, query: str = "") -> str:
        subject = _extract_subject(query)
        if not subject:
            return "⚠️ Je n'ai pas compris de quoi tu voulais parler. Précise le sujet."

        try:
            # Recherche du titre
            title = await _search_page(subject)
            if not title:
                # Tentative de correction orthographique
                corrected = _spell_correct(
                    subject.lower(),
                    ["intelligence artificielle", "python", "linux",
                     "physique quantique", "informatique", "réseau"],
                )
                if corrected:
                    return (
                        f"⚠️ « {subject} » introuvable. "
                        f"Vouliez-vous dire **{corrected}** ?"
                    )
                return f"⚠️ Aucun article Wikipédia trouvé pour « {subject} »."

            summary = await _get_summary(title)
            if not summary:
                return f"⚠️ L'article « {title} » n'a pas de résumé disponible."

            wiki_url = f"https://fr.wikipedia.org/wiki/{title.replace(' ', '_')}"
            return f"📖 **{title}**\n\n{summary}\n\n🔗 {wiki_url}"

        except httpx.TimeoutException:
            return "⚠️ Délai dépassé lors de la recherche Wikipédia."
        except Exception as e:
            logger.error("WikiAgent error for '%s': %s", subject, e)
            return f"Impossible de récupérer l'article Wikipédia pour « {subject} »."
