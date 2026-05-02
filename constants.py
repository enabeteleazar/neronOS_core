# core/constants.py
# Source de vérité unique pour tous les mots-clés de détection d'intent.
# Importé par intent_router.py et telegram_agent.py.
#
# v2.0 — Ajout des intents : NEWS_QUERY, WEATHER_QUERY, TODO_ACTION, WIKI_QUERY
#         Inspiré de J.A.R.V.I.S (GauravSingh9356) — fonctionnalités portées
#         dans l'architecture agent Néron (sans dépendances Windows/cloud).

from __future__ import annotations

# ── Mots-clés code / développement ───────────────────────────────────────────

CODE_KEYWORDS: list[str] = [
    # Génération
    "génère", "genere",
    "crée un fichier", "cree un fichier",
    "écris un script", "ecris un script",
    "écris un module", "ecris un module",
    "écris une classe", "ecris une classe",
    "écris une fonction", "ecris une fonction",
    # Amélioration / correction
    "améliore le fichier", "ameliore le fichier",
    "améliore ce code", "ameliore ce code",
    "optimise le fichier", "optimise ce code",
    "corrige le fichier", "corrige ce code",
    "refactorise",
    # Analyse
    "analyse le fichier", "analyse ce code",
    "inspecte le fichier",
    "qualité du code", "qualite du code",
    # Lecture
    "lis le fichier", "montre le code", "affiche le fichier",
    # Revue / rollback
    "self review", "auto review", "revue de code",
    "passe en revue", "rollback", "restaure le fichier",
]

# ── Mots-clés auto-audit Néron ────────────────────────────────────────────────

CODE_AUDIT_KEYWORDS: list[str] = [
    "analyse ton code", "analyse toi", "analyse-toi",
    "inspecte ton code", "inspecte toi", "audite toi",
    "audite ton code", "auto audit", "auto-audit",
    "analyse ton propre code", "inspecte ton propre code",
    "qualite de ton code", "analyse le code de neron",
    "inspecte le code de neron", "review de ton code",
]

# ── Mots-clés Home Assistant ──────────────────────────────────────────────────

HA_KEYWORDS: list[str] = [
    "allume", "eteins", "thermostat", "lumiere", "volet", "home assistant",
]

# ── Mots-clés recherche web ───────────────────────────────────────────────────

WEB_KEYWORDS: list[str] = [
    "cherche sur internet", "recherche sur le web", "google",
    "trouve sur le web", "recherche en ligne",
]

# ── Mots-clés heure / date ────────────────────────────────────────────────────

TIME_KEYWORDS: list[str] = [
    "quelle heure", "il est quelle heure",
    "quelle heure est il", "donne moi l heure",
    "quel jour sommes", "on est quel jour",
    "quel mois sommes", "quelle date sommes",
    "donne moi la date", "c est quoi la date",
    "on est le combien",
]

# ── Mots-clés feedback personnalité ──────────────────────────────────────────

PERSONALITY_KEYWORDS: list[str] = [
    "trop long", "trop verbeux", "trop bavard", "raccourcis", "sois bref",
    "plus de détail", "développe", "explique mieux", "trop court",
    "niveau ok", "longueur ok",
    "sois direct", "va droit au but", "sans détour", "sans blabla",
    "plus doux", "sois plus sympa", "moins froid", "plus chaleureux",
    "redeviens technique", "mode technique", "sois technique",
    "arrête de proposer", "moins de suggestions", "pas de suggestions",
    "sois proactif", "propose plus", "anticipe",
    "arrête d'apprendre", "désactive l'apprentissage", "mode statique",
    "réactive l'apprentissage", "apprends de moi", "mode adaptatif",
    "tu sembles fatigué", "sois plus énergique", "réveille-toi",
    "calme-toi", "moins d'énergie", "sois plus calme",
    "énergie normale", "niveau normal",
    "mode normal", "humeur normale",
    "sois positif", "bonne humeur", "optimiste",
    "mode focus", "concentration", "sois sérieux",
]

# ─────────────────────────────────────────────────────────────────────────────
# NOUVEAUX INTENTS — v2.0  (inspirés de J.A.R.V.I.S)
# ─────────────────────────────────────────────────────────────────────────────

# ── Mots-clés actualités ──────────────────────────────────────────────────────

NEWS_KEYWORDS: list[str] = [
    "actualite", "actualités", "actualites",
    "les news", "les nouvelles", "nouvelles du jour",
    "quoi de neuf", "headline", "journal du jour",
    "info du jour", "infos du jour",
    "dernières nouvelles", "dernieres nouvelles",
    "actualité tech", "actualite tech",
    "actualité france", "actualite france",
    "actualité monde", "actualite monde",
    "actualité science", "actualite science",
]

# ── Mots-clés météo ───────────────────────────────────────────────────────────

WEATHER_KEYWORDS: list[str] = [
    "meteo", "météo",
    "temperature", "température",
    "temps qu il fait", "quel temps",
    "il fait combien", "combien de degrés",
    "va-t-il pleuvoir", "va t il pleuvoir",
    "est-ce qu il va pleuvoir", "pluie aujourd",
    "prevision meteo", "prévision météo",
    "vent aujourd", "humidite aujourd",
]

# ── Mots-clés todo list ───────────────────────────────────────────────────────

TODO_KEYWORDS: list[str] = [
    "ma liste", "mes taches", "mes tâches",
    "todo", "a faire", "à faire",
    "ajoute a ma liste", "ajoute à ma liste",
    "rappelle-moi de", "rappelle moi de",
    "n oublie pas de", "n'oublie pas de",
    "note que", "j ai fait", "j'ai fait",
    "c est fait", "c'est fait", "marque comme termine",
    "efface tout", "vide la liste",
]

# ── Mots-clés Wikipédia ───────────────────────────────────────────────────────

WIKI_KEYWORDS: list[str] = [
    "qu est-ce que", "qu'est-ce que",
    "c est quoi", "c'est quoi",
    "definition de", "définition de",
    "explique-moi", "explique moi",
    "parle-moi de", "parle moi de",
    "qui est", "wikipedia", "wiki",
    "dis-moi ce qu est", "dis moi ce qu est",
]
