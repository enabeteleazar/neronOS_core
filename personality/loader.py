"""
loader.py — Chargement de la persona Neron
Fusionne persona.yaml (config de base) avec l'état SQLite (état dynamique).
Protège les champs définis dans core_identity.protected_fields du YAML.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import yaml

from .constants import DB_FILENAME, DEFAULTS

logger = logging.getLogger(__name__)

BASE_PATH          = Path(__file__).parent
DB_PATH            = BASE_PATH / DB_FILENAME
YAML_PATH          = BASE_PATH / "persona.yaml"
JSON_FALLBACK_PATH = BASE_PATH / "persona_state.json"


# ── Utilitaire JSON partagé ───────────────────────────────────────────────────

def _safe_json_load(value: str | None):
    """
    Parse JSON silencieusement.
    Retourne la valeur brute en cas d'échec, None si valeur absente.
    Centralisé ici — source unique pour __init__.py et loader.
    """
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


# ── SQLite ────────────────────────────────────────────────────────────────────

def _init_db() -> sqlite3.Connection:
    """Initialise la base SQLite et crée les tables si elles n'existent pas."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS persona_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS persona_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            field     TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            reason    TEXT
        )
    """)
    conn.commit()

    cursor = conn.execute("SELECT COUNT(*) FROM persona_state")
    if cursor.fetchone()[0] == 0:
        try:
            with open(JSON_FALLBACK_PATH, encoding="utf-8") as f:
                seed = json.load(f)
            for key, value in seed.items():
                if key != "history":
                    conn.execute(
                        "INSERT OR IGNORE INTO persona_state(key, value) VALUES (?, ?)",
                        (key, json.dumps(value)),
                    )
            conn.commit()
            logger.info("persona_state.db initialisée depuis persona_state.json")
        except Exception as e:
            # FIX: %s au lieu de f-string
            logger.warning("Impossible de seeder la DB depuis JSON : %s", e)

    return conn


def _read_field(conn: sqlite3.Connection, key: str):
    """Lit un champ depuis SQLite. Retourne None si absent."""
    cursor = conn.execute("SELECT value FROM persona_state WHERE key = ?", (key,))
    row    = cursor.fetchone()
    return _safe_json_load(row[0]) if row else None


def _load_db_state(conn: sqlite3.Connection) -> dict:
    """Charge l'état dynamique complet depuis SQLite."""
    state = {}
    try:
        cursor = conn.execute("SELECT key, value FROM persona_state")
        for key, value in cursor.fetchall():
            state[key] = _safe_json_load(value)
    except Exception as e:
        logger.warning("[PERSONA] Lecture SQLite échouée : %s", e)
    return state


# ── YAML ──────────────────────────────────────────────────────────────────────

def _load_yaml_base() -> dict:
    """Charge persona.yaml avec messages d'erreur explicites."""
    try:
        with open(YAML_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError("persona.yaml ne contient pas un mapping valide.")
        return data
    except FileNotFoundError:
        logger.error("[PERSONA] Fichier introuvable : %s", YAML_PATH)
        raise RuntimeError(
            f"[PERSONA ERREUR] persona.yaml est manquant ({YAML_PATH}). "
            "Vérifiez l'installation du module personality."
        )
    except yaml.YAMLError as e:
        logger.error("[PERSONA] Erreur de parsing YAML : %s", e)
        raise RuntimeError(
            f"[PERSONA ERREUR] persona.yaml est corrompu ou mal formaté.\nDétail : {e}"
        )
    except Exception as e:
        logger.error("[PERSONA] Erreur inattendue lors du chargement YAML : %s", e)
        raise RuntimeError(f"[PERSONA ERREUR] Chargement impossible : {e}")


def _get_protected_fields(yaml_base: dict) -> set:
    """
    Lit les champs protégés depuis core_identity.protected_fields dans le YAML.
    Fallback sur un set minimal si le champ est absent ou mal formé.
    """
    try:
        fields = yaml_base.get("core_identity", {}).get("protected_fields", [])
        if isinstance(fields, list) and fields:
            return set(fields)
    except Exception:
        pass
    logger.warning(
        "[PERSONA] core_identity.protected_fields absent ou invalide — fallback minimal."
    )
    return {"name", "role", "core_identity"}


# ── Point d'entrée principal ──────────────────────────────────────────────────

def load_persona() -> dict:
    """
    Charge et fusionne la persona complète.
    1. Lit persona.yaml (base immuable)
    2. Sauvegarde les champs protégés
    3. Charge l'état SQLite
    4. Fusionne sections dynamiques + DEFAULTS
    5. Réimpose les champs protégés
    """
    base      = _load_yaml_base()
    protected = _get_protected_fields(base)
    protected_values = {f: base[f] for f in protected if f in base}

    conn = None
    try:
        conn  = _init_db()
        state = _load_db_state(conn)
    except Exception as e:
        logger.warning("[PERSONA] SQLite inaccessible, utilisation YAML seule : %s", e)
        state = {}
    finally:
        if conn is not None:
            conn.close()

    # Fusion sections dynamiques
    for section in ("communication", "behavior", "learning"):
        db_section = state.get(section)
        if isinstance(db_section, dict):
            base.setdefault(section, {}).update(db_section)
        for k, v in DEFAULTS.get(section, {}).items():
            base.setdefault(section, {}).setdefault(k, v)

    # Champs de premier niveau
    for field in ("mood", "energy_level"):
        base[field] = state.get(field, DEFAULTS.get(field))

    # Réimpose les champs protégés
    base.update(protected_values)

    return base