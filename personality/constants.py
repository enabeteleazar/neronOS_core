"""
constants.py — Constantes partagées du module Neron Personality

Centralise les valeurs utilisées dans plusieurs fichiers du module
pour éviter toute duplication et garantir la cohérence.

Note : PROTECTED_FIELDS n'est plus défini ici.
       Il est lu dynamiquement depuis core_identity.protected_fields
       dans persona.yaml via loader._get_protected_fields().
       C'est le YAML qui fait autorité.
"""

from __future__ import annotations

# Nom du fichier de base de données SQLite
DB_FILENAME = "persona_state.db"

# Valeurs par défaut pour les champs dynamiques
DEFAULTS: dict = {
    "mood":         "neutre",
    "energy_level": "normal",
    "communication": {
        "tone":      "technique",
        "verbosity": "medium",
        "format":    "structuré",
    },
    "behavior": {
        "proactive":             True,
        "suggest_improvements":  True,
    },
    "learning": {
        "enabled": True,
    },
}

# Traductions energy_level → instruction de ton pour le LLM
ENERGY_INSTRUCTIONS: dict[str, str] = {
    "high":   "Sois particulièrement énergique, précis et enthousiaste dans tes réponses.",
    "low":    "Adopte un ton posé, calme et mesuré.",
    "normal": "Maintiens un équilibre entre clarté et concision.",
}

# Traductions mood → nuance comportementale pour le LLM
MOOD_INSTRUCTIONS: dict[str, str] = {
    "neutre":  "Reste objectif et factuel.",
    "positif": "Adopte un ton encourageant et constructif.",
    "focus":   "Concentre-toi sur l'essentiel, évite tout hors-sujet.",
}