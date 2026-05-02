"""
personality/__init__.py — API publique du module Neron Personality

Expose :
- build_system_prompt(user_context)   : prompt prêt à envoyer au LLM
- update_from_feedback(feedback)      : mise à jour via analyse d'intention
- force_update(section, field, value) : mise à jour directe programmatique
- get_current_state()                 : état complet de la persona active
- get_history(limit)                  : historique des changements d'état
"""

from __future__ import annotations

import logging

from .engine  import build_system_prompt
from .loader  import _init_db, _safe_json_load, load_persona
from .updater import force_update, update_from_feedback

logger = logging.getLogger(__name__)

__all__ = [
    "build_system_prompt",
    "update_from_feedback",
    "force_update",
    "get_current_state",
    "get_history",
]


def get_current_state() -> dict:
    """Retourne la configuration active complète de la persona."""
    try:
        return load_persona()
    except Exception as e:
        # FIX: %s au lieu de f-string
        logger.error("[PERSONALITY] get_current_state échoué : %s", e)
        return {
            "error":        str(e),
            "status":       "unavailable",
            "name":         "Neron",
            "mood":         "neutre",
            "energy_level": "normal",
        }


def get_history(limit: int = 20) -> list[dict]:
    """
    Retourne les dernières entrées de l'historique des changements d'état.

    Args:
        limit: nombre maximum d'entrées à retourner (défaut : 20)
    """
    conn = None
    try:
        conn = _init_db()
        cursor = conn.execute(
            """SELECT id, timestamp, field, old_value, new_value, reason
               FROM persona_history
               ORDER BY id DESC
               LIMIT ?""",
            (limit,),
        )
        return [
            {
                "id":        id_,
                "timestamp": ts,
                "field":     field,
                "old_value": _safe_json_load(old_val),
                "new_value": _safe_json_load(new_val),
                "reason":    reason or "",
            }
            for id_, ts, field, old_val, new_val, reason in cursor.fetchall()
        ]
    except Exception as e:
        logger.error("[PERSONALITY] get_history échoué : %s", e)
        return [{"error": str(e)}]
    finally:
        if conn is not None:
            conn.close()