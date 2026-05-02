# agents/memory_agent.py
# Neron Core - Memory direct SQLite (sans neron_memory intermédiaire)

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from typing import Dict, List, Optional

from core.config import settings

logger = logging.getLogger("memory_agent")

DB_PATH = str(settings.MEMORY_DB_PATH)

# Limite haute de la table memory (rotation automatique au-delà)
_MAX_MEMORY_ROWS = int(getattr(settings, "MEMORY_MAX_ROWS", 10_000))


# ── Connexion ─────────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ── Init ──────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Initialise la base de données (appelé au démarrage de core)."""
    logger.info("Memory DB init : %s", DB_PATH)
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                input     TEXT NOT NULL,
                response  TEXT NOT NULL,
                metadata  TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_timestamp ON memory(timestamp)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                type      TEXT,
                service   TEXT,
                message   TEXT,
                data      TEXT
            )
        """)
        conn.commit()
    logger.info("Memory DB prête")


# ── Agent ─────────────────────────────────────────────────────────────────────

class MemoryAgent:
    """Accès direct SQLite — remplace les appels HTTP à neron_memory:8002."""

    def reload(self) -> bool:
        """Réinitialise la connexion SQLite."""
        try:
            init_db()
            return True
        except Exception as e:
            # FIX: exception loggée au lieu d'être silencieuse
            logger.error("Memory reload error : %s", e)
            return False

    def store(self, input_text: str, response: str, metadata: dict | None = None) -> int:
        """Persiste un échange en mémoire. Retourne l'id inséré ou -1."""
        try:
            with get_db() as conn:
                cursor = conn.execute(
                    "INSERT INTO memory (input, response, metadata) VALUES (?, ?, ?)",
                    (input_text, response, json.dumps(metadata or {})),
                )
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error("Memory store error : %s", e)
            return -1

    def retrieve(self, limit: int = 3) -> List[Dict]:
        """Retourne les N derniers échanges."""
        try:
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT id, input, response, metadata, timestamp "
                    "FROM memory ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
        except Exception as e:
            logger.error("Memory retrieve error : %s", e)
            return []

    def search(self, query: str, limit: int = 3) -> List[Dict]:
        """Recherche plein texte dans les échanges."""
        try:
            with get_db() as conn:
                rows = conn.execute(
                    """SELECT id, input, response, metadata, timestamp FROM memory
                       WHERE input LIKE ? OR response LIKE ?
                       ORDER BY id DESC LIMIT ?""",
                    (f"%{query}%", f"%{query}%", limit),
                ).fetchall()
                return [self._row_to_dict(r) for r in rows]
        except Exception as e:
            logger.error("Memory search error : %s", e)
            return []

    def cleanup(self, days: int = 30) -> int:
        """
        FIX: méthode manquante — appelée par le scheduler chaque lundi.
        Supprime les entrées plus anciennes que `days` jours.
        Applique aussi une rotation si la table dépasse _MAX_MEMORY_ROWS.
        Retourne le nombre d'entrées supprimées.
        """
        deleted = 0
        try:
            with get_db() as conn:
                # Suppression par ancienneté
                cursor = conn.execute(
                    "DELETE FROM memory "
                    "WHERE timestamp < datetime('now', ? )",
                    (f"-{days} days",),
                )
                deleted += cursor.rowcount

                # Rotation si dépassement du plafond
                count = conn.execute(
                    "SELECT COUNT(*) FROM memory"
                ).fetchone()[0]

                if count > _MAX_MEMORY_ROWS:
                    overflow = count - _MAX_MEMORY_ROWS
                    cursor = conn.execute(
                        "DELETE FROM memory WHERE id IN "
                        "(SELECT id FROM memory ORDER BY id ASC LIMIT ?)",
                        (overflow,),
                    )
                    deleted += cursor.rowcount

                conn.commit()
            logger.info("Memory cleanup : %d entrées supprimées", deleted)
        except Exception as e:
            logger.error("Memory cleanup error : %s", e)
        return deleted

    def count(self) -> int:
        """Retourne le nombre total d'entrées en mémoire."""
        try:
            with get_db() as conn:
                return conn.execute(
                    "SELECT COUNT(*) FROM memory"
                ).fetchone()[0]
        except Exception as e:
            logger.error("Memory count error : %s", e)
            return 0

    def _row_to_dict(self, row) -> Dict:
        try:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        except Exception:
            metadata = {}
        return {
            "id":        row["id"],
            "input":     row["input"],
            "response":  row["response"],
            "metadata":  metadata,
            "timestamp": row["timestamp"],
        }
