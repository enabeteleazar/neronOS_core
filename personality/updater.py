"""
updater.py — Mise à jour de l'état dynamique de Neron
"""

from __future__ import annotations

import functools
import json
import logging
from datetime import datetime, timezone
from typing import Any

from .loader import _get_protected_fields, _init_db, _load_yaml_base, _read_field

logger = logging.getLogger(__name__)

# ── Matrice d'intentions ──────────────────────────────────────────────────────

INTENT_MATRIX = [
    # Verbosité
    (["trop long", "trop verbeux", "trop bavard", "raccourcis", "sois bref"],
     "communication", "verbosity", "low"),
    (["plus de détail", "développe", "explique mieux", "j'ai pas compris", "trop court"],
     "communication", "verbosity", "high"),
    (["c'est bien", "parfait", "niveau ok", "longueur ok"],
     "communication", "verbosity", "medium"),
    # Ton
    (["sois direct", "va droit au but", "sans détour", "sans blabla"],
     "communication", "tone", "direct"),
    (["plus doux", "sois plus sympa", "moins froid", "plus chaleureux"],
     "communication", "tone", "bienveillant"),
    (["redeviens technique", "mode technique", "sois technique"],
     "communication", "tone", "technique"),
    # Proactivité
    (["arrête de proposer", "moins de suggestions", "pas de suggestions"],
     "behavior", "proactive", False),
    (["sois proactif", "propose plus", "plus de suggestions", "anticipe"],
     "behavior", "proactive", True),
    # Apprentissage
    (["arrête d'apprendre", "désactive l'apprentissage", "mode statique"],
     "learning", "enabled", False),
    (["réactive l'apprentissage", "apprends de moi", "mode adaptatif"],
     "learning", "enabled", True),
    # Énergie
    (["tu sembles fatigué", "sois plus énergique", "réveille-toi"],
     None, "energy_level", "high"),
    (["calme-toi", "moins d'énergie", "sois plus calme"],
     None, "energy_level", "low"),
    (["énergie normale", "niveau normal"],
     None, "energy_level", "normal"),
    # Humeur
    (["tu vas bien", "mode normal", "humeur normale"],
     None, "mood", "neutre"),
    (["sois positif", "bonne humeur", "optimiste"],
     None, "mood", "positif"),
    (["mode focus", "concentration", "sois sérieux"],
     None, "mood", "focus"),
]

ALLOWED_VALUES: dict[str, set] = {
    "verbosity":             {"low", "medium", "high"},
    "tone":                  {"technique", "direct", "bienveillant"},
    "energy_level":          {"low", "normal", "high"},
    "mood":                  {"neutre", "positif", "focus"},
    "proactive":             {True, False},
    "suggest_improvements":  {True, False},
    "enabled":               {True, False},
}


# ── Cache champs protégés ─────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def _resolve_protected() -> frozenset:
    """
    Résout les champs protégés depuis le YAML une seule fois (lru_cache).
    Pour invalider : _resolve_protected.cache_clear()
    """
    try:
        base = _load_yaml_base()
        return frozenset(_get_protected_fields(base))
    except Exception:
        logger.warning("[UPDATER] YAML inaccessible pour _resolve_protected — fallback minimal.")
        return frozenset({"name", "role", "core_identity"})


# ── Écriture SQLite ───────────────────────────────────────────────────────────

def _write_field(
    conn, key: str, value: Any, reason: str, protected: frozenset
) -> bool:
    """
    Écrit un champ dans SQLite et journalise le changement.
    Gardes : champ protégé → refus | valeur hors ALLOWED_VALUES → refus | no-op → False
    """
    if key in protected:
        logger.warning("[UPDATER] Écriture refusée — champ protégé : %r", key)
        return False

    if key in ALLOWED_VALUES:
        allowed       = ALLOWED_VALUES[key]
        is_bool_field = allowed == {True, False}
        if is_bool_field and not isinstance(value, bool):
            logger.warning(
                "[UPDATER] Type invalide pour %r : %r (%s). Booléen strict requis.",
                key, value, type(value).__name__,
            )
            return False
        if not is_bool_field and value not in allowed:
            logger.warning(
                "[UPDATER] Valeur invalide pour %r : %r. Autorisées : %s.",
                key, value, allowed,
            )
            return False

    old_value = _read_field(conn, key)
    if old_value == value:
        logger.debug("[UPDATER] No-op %r — valeur déjà à %r.", key, value)
        return False

    serialized = json.dumps(value)
    conn.execute(
        "INSERT OR REPLACE INTO persona_state(key, value) VALUES (?, ?)",
        (key, serialized),
    )
    conn.execute(
        """INSERT INTO persona_history(timestamp, field, old_value, new_value, reason)
           VALUES (?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            key,
            json.dumps(old_value),
            serialized,
            reason,
        ),
    )
    conn.commit()
    logger.info("[UPDATER] %s : %r → %r (raison : %r)", key, old_value, value, reason)
    return True


def _write_nested(
    conn, section: str, field: str, value: Any, reason: str, protected: frozenset
) -> bool:
    """
    Met à jour un champ imbriqué (ex: communication.verbosity).
    Valide la valeur sur le champ feuille avant de reconstruire la section.
    Note : la garde protégée est appliquée sur `section` dans _write_field —
    la validation du champ feuille est faite ici en amont.
    """
    if field in ALLOWED_VALUES:
        allowed       = ALLOWED_VALUES[field]
        is_bool_field = allowed == {True, False}
        if is_bool_field and not isinstance(value, bool):
            logger.warning(
                "[UPDATER] Type invalide pour %r.%r : %r (%s). Booléen strict requis.",
                section, field, value, type(value).__name__,
            )
            return False
        if not is_bool_field and value not in allowed:
            logger.warning(
                "[UPDATER] Valeur invalide pour %r.%r : %r. Autorisées : %s.",
                section, field, value, allowed,
            )
            return False

    current = _read_field(conn, section) or {}
    if not isinstance(current, dict):
        current = {}

    if current.get(field) == value:
        logger.debug("[UPDATER] No-op %r.%r — valeur déjà à %r.", section, field, value)
        return False

    current[field] = value
    return _write_field(conn, section, current, reason, protected)


# ── Analyse d'intention ───────────────────────────────────────────────────────

def _analyse_intent(feedback: str) -> list[dict]:
    """Analyse le feedback et retourne la liste des changements candidats."""
    text    = feedback.lower().strip()
    changes = []
    for keywords, section, field, value in INTENT_MATRIX:
        for kw in keywords:
            if kw in text:
                changes.append({
                    "section":        section,
                    "field":          field,
                    "value":          value,
                    "matched_phrase": kw,
                })
                break
    return changes


# ── Points d'entrée publics ───────────────────────────────────────────────────

def update_from_feedback(feedback: str) -> dict:
    """
    Analyse l'intention du feedback et met à jour l'état.
    Retourne status='no_change' si aucune écriture effective n'a eu lieu.
    """
    changes = _analyse_intent(feedback)
    if not changes:
        logger.info("[UPDATER] Aucune intention reconnue dans : %r", feedback)
        return {"status": "no_change", "feedback": feedback, "changes": []}

    protected = _resolve_protected()
    conn      = None
    try:
        conn    = _init_db()
        applied = []

        for change in changes:
            section = change["section"]
            field   = change["field"]
            value   = change["value"]
            reason  = f"feedback utilisateur — déclencheur: '{change['matched_phrase']}'"

            written = (
                _write_field(conn, field, value, reason, protected)
                if section is None
                else _write_nested(conn, section, field, value, reason, protected)
            )
            if written:
                applied.append({
                    "field":     f"{section}.{field}" if section else field,
                    "new_value": value,
                    "trigger":   change["matched_phrase"],
                })

        status = "updated" if applied else "no_change"
        return {"status": status, "feedback": feedback, "changes": applied}

    except Exception as e:
        logger.error("[UPDATER] Erreur lors de la mise à jour : %s", e)
        return {"status": "error", "error": str(e), "feedback": feedback, "changes": []}
    finally:
        if conn is not None:
            conn.close()


def force_update(
    section: str | None,
    field:   str,
    value:   Any,
    reason:  str = "mise à jour forcée",
) -> dict:
    """Mise à jour directe sans analyse d'intention."""
    protected = _resolve_protected()

    if field in protected or (section and section in protected):
        return {"status": "refused", "reason": "champ protégé par core_identity"}

    conn = None
    try:
        conn    = _init_db()
        written = (
            _write_field(conn, field, value, reason, protected)
            if section is None
            else _write_nested(conn, section, field, value, reason, protected)
        )
        return {
            "status": "updated" if written else "no_change",
            "field":  f"{section}.{field}" if section else field,
            "value":  value,
        }
    except Exception as e:
        logger.error("[UPDATER] force_update échoué : %s", e)
        return {"status": "error", "error": str(e)}
    finally:
        if conn is not None:
            conn.close()