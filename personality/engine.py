"""
engine.py — Construction du prompt système pour Neron
Intègre la persona complète (base YAML + état dynamique SQLite).
"""

from __future__ import annotations

import logging

from .constants import ENERGY_INSTRUCTIONS, MOOD_INSTRUCTIONS
from .loader    import load_persona

logger = logging.getLogger(__name__)

_UNKNOWN_MOOD_HINT   = "Adapte ton comportement selon le contexte de la conversation."
_UNKNOWN_ENERGY_HINT = "Maintiens un équilibre entre clarté et concision."


def build_system_prompt(user_context: str = "") -> str:
    """
    Construit le prompt système complet à partir de la persona active.
    Format texte brut — sans headers Markdown, sans ##.
    """
    try:
        persona = load_persona()
    except RuntimeError as e:
        return (
            f"[ERREUR MODULE PERSONALITY]\n{e}\n\n"
            "Le module de personnalité n'a pas pu être chargé. "
            "Répondez de façon neutre et informez l'utilisateur du problème."
        )

    name = persona.get("name", "Neron")
    role = persona.get("role", "assistant")

    traits_raw = persona.get("traits", [])
    traits_inline = (
        ", ".join(traits_raw) if isinstance(traits_raw, list) else str(traits_raw)
    )

    rules_raw   = persona.get("rules", [])
    rules_block = (
        "\n".join(f"- {r}" for r in rules_raw)
        if isinstance(rules_raw, list)
        else f"- {rules_raw}"
    )

    behavior     = persona.get("behavior", {})
    learning     = persona.get("learning", {})
    mood         = persona.get("mood", "neutre")
    energy_level = persona.get("energy_level", "normal")

    if mood not in MOOD_INSTRUCTIONS:
        logger.warning("[ENGINE] Valeur mood inconnue : %r. Instruction générique appliquée.", mood)
    mood_hint = MOOD_INSTRUCTIONS.get(mood, _UNKNOWN_MOOD_HINT)

    if energy_level not in ENERGY_INSTRUCTIONS:
        logger.warning("[ENGINE] Valeur energy_level inconnue : %r. Instruction générique appliquée.", energy_level)
    energy_hint = ENERGY_INSTRUCTIONS.get(energy_level, _UNKNOWN_ENERGY_HINT)

    learning_hint = (
        "Tu adaptes ton comportement selon les retours de l'utilisateur."
        if learning.get("enabled", True)
        else "Mode statique — ne modifie pas ton comportement selon les retours."
    )

    proactive = behavior.get("proactive", True)
    suggest   = behavior.get("suggest_improvements", True)

    context_block = (
        f"\nContexte de la conversation : {user_context}" if user_context else ""
    )

    lines = [
        f"Tu es {name}, {role}. Tu réponds uniquement en français, de façon naturelle et conversationnelle.",
        "",
        f"Tes traits : {traits_inline}.",
        "",
        f"Humeur actuelle : {mood} — {mood_hint}",
        f"Énergie : {energy_level} — {energy_hint}",
    ]
    if proactive:
        lines.append("Tu es proactif et proposes des pistes quand c'est utile.")
    if suggest:
        lines.append("Tu suggères des améliorations quand c'est pertinent.")
    lines.append(learning_hint)
    lines += ["", "Règles absolues à respecter :", rules_block]
    if context_block:
        lines.append(context_block)

    return "\n".join(lines).strip()
