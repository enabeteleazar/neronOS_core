# core/memory/persistent_store.py
# Mémoire persistante SQLite — zéro ORM, thread-safe, WAL mode.
# Tables : sessions | turns | facts
from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterator, List, Optional

from core.config import settings

logger = logging.getLogger("memory.persistent_store")

# ── Chemins ───────────────────────────────────────────────────────────────────

_DB_PATH = settings.MEMORY_DB_PATH
_MAX_TURNS_PER_SESSION = 100
_MAX_FACTS_PER_SESSION = 50
_SESSION_TTL_DAYS = 30

# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT    PRIMARY KEY,
    created_at   REAL    NOT NULL,
    last_active  REAL    NOT NULL,
    turn_count   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    query       TEXT    NOT NULL,
    intent      TEXT    NOT NULL,
    entities    TEXT    NOT NULL DEFAULT '{}',
    response    TEXT,
    confidence  REAL    NOT NULL DEFAULT 0.0,
    ts          REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, ts DESC);

CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    key         TEXT    NOT NULL,
    value       TEXT    NOT NULL,
    confidence  REAL    NOT NULL DEFAULT 1.0,
    ts          REAL    NOT NULL,
    UNIQUE(session_id, key)
);
CREATE INDEX IF NOT EXISTS idx_facts_session ON facts(session_id);
"""


# ── Connexion ─────────────────────────────────────────────────────────────────

@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH), check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


# ── Store principal ───────────────────────────────────────────────────────────

class PersistentStore:
    """
    Accès SQLite à la mémoire long terme de Néron.
    Thread-safe via mutex global léger (lecture haute fréquence → WAL).
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._init()

    # ── Init ──────────────────────────────────────────────────────────────────

    def _init(self) -> None:
        try:
            with _conn() as c:
                c.executescript(_DDL)
            logger.info("[PersistentStore] DB initialisée : %s", _DB_PATH)
        except Exception as exc:
            logger.error("[PersistentStore] Échec init DB : %s", exc)

    # ── Sessions ──────────────────────────────────────────────────────────────

    def ensure_session(self, session_id: str) -> None:
        now = time.time()
        with self._lock, _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO sessions(session_id, created_at, last_active) VALUES(?,?,?)",
                (session_id, now, now),
            )
            c.execute(
                "UPDATE sessions SET last_active=? WHERE session_id=?",
                (now, session_id),
            )

    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        with _conn() as c:
            row = c.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    # ── Turns ─────────────────────────────────────────────────────────────────

    def push_turn(
        self,
        session_id: str,
        query: str,
        intent: str,
        entities: Dict[str, Any],
        response: Optional[str] = None,
        confidence: float = 0.0,
    ) -> int:
        """Enregistre un tour, retourne l'id inséré. Prune si > MAX_TURNS."""
        self.ensure_session(session_id)
        now = time.time()
        with self._lock, _conn() as c:
            cur = c.execute(
                """INSERT INTO turns(session_id, query, intent, entities, response, confidence, ts)
                   VALUES(?,?,?,?,?,?,?)""",
                (session_id, query, intent,
                 json.dumps(entities, ensure_ascii=False),
                 response, confidence, now),
            )
            row_id = cur.lastrowid
            c.execute(
                "UPDATE sessions SET turn_count=turn_count+1, last_active=? WHERE session_id=?",
                (now, session_id),
            )
            # Pruning : ne conserver que les N derniers tours
            c.execute(
                """DELETE FROM turns WHERE session_id=? AND id NOT IN (
                       SELECT id FROM turns WHERE session_id=? ORDER BY ts DESC LIMIT ?
                   )""",
                (session_id, session_id, _MAX_TURNS_PER_SESSION),
            )
            return row_id

    def update_turn_response(self, turn_id: int, response: str) -> None:
        with self._lock, _conn() as c:
            c.execute("UPDATE turns SET response=? WHERE id=?", (response, turn_id))

    def get_recent_turns(
        self, session_id: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        with _conn() as c:
            rows = c.execute(
                """SELECT query, intent, entities, response, confidence, ts
                   FROM turns WHERE session_id=? ORDER BY ts DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
        result = []
        for r in reversed(rows):
            d = dict(r)
            d["entities"] = json.loads(d["entities"])
            result.append(d)
        return result

    def get_last_turn(self, session_id: str) -> Optional[Dict[str, Any]]:
        turns = self.get_recent_turns(session_id, limit=1)
        return turns[0] if turns else None

    # ── Facts (mémoire sémantique) ────────────────────────────────────────────

    def set_fact(
        self,
        session_id: str,
        key: str,
        value: Any,
        confidence: float = 1.0,
    ) -> None:
        """Upsert un fait mémorisé (ex: préférences, localisation, devices)."""
        self.ensure_session(session_id)
        now = time.time()
        val = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        with self._lock, _conn() as c:
            c.execute(
                """INSERT INTO facts(session_id, key, value, confidence, ts)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(session_id, key) DO UPDATE SET
                       value=excluded.value,
                       confidence=excluded.confidence,
                       ts=excluded.ts""",
                (session_id, key, val, confidence, now),
            )
            # Pruning facts
            c.execute(
                """DELETE FROM facts WHERE session_id=? AND id NOT IN (
                       SELECT id FROM facts WHERE session_id=? ORDER BY ts DESC LIMIT ?
                   )""",
                (session_id, session_id, _MAX_FACTS_PER_SESSION),
            )

    def get_fact(self, session_id: str, key: str) -> Optional[Any]:
        with _conn() as c:
            row = c.execute(
                "SELECT value FROM facts WHERE session_id=? AND key=?",
                (session_id, key),
            ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    def get_all_facts(self, session_id: str) -> Dict[str, Any]:
        with _conn() as c:
            rows = c.execute(
                "SELECT key, value FROM facts WHERE session_id=? ORDER BY confidence DESC",
                (session_id,),
            ).fetchall()
        result = {}
        for r in rows:
            try:
                result[r["key"]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                result[r["key"]] = r["value"]
        return result

    # ── GC ───────────────────────────────────────────────────────────────────

    def gc(self) -> int:
        """Supprime les sessions inactives depuis > SESSION_TTL_DAYS jours."""
        cutoff = time.time() - _SESSION_TTL_DAYS * 86400
        with self._lock, _conn() as c:
            cur = c.execute(
                "DELETE FROM sessions WHERE last_active < ?", (cutoff,)
            )
            deleted = cur.rowcount
        if deleted:
            logger.info("[PersistentStore] GC : %d sessions supprimées", deleted)
        return deleted

    # ── Résumé contexte (pour injection LLM) ─────────────────────────────────

    def build_context_summary(self, session_id: str, max_turns: int = 5) -> str:
        """
        Construit un résumé textuel du contexte récent
        destiné à enrichir un prompt LLM.
        """
        turns = self.get_recent_turns(session_id, limit=max_turns)
        facts = self.get_all_facts(session_id)

        lines: List[str] = []

        if facts:
            facts_str = ", ".join(f"{k}={v}" for k, v in list(facts.items())[:10])
            lines.append(f"[Faits connus] {facts_str}")

        if turns:
            lines.append("[Historique récent]")
            for t in turns[-3:]:
                resp_preview = (t.get("response") or "")[:80]
                lines.append(f"  U: {t['query'][:80]}")
                if resp_preview:
                    lines.append(f"  N: {resp_preview}")

        return "\n".join(lines) if lines else ""


# ── Singleton ─────────────────────────────────────────────────────────────────

_store: Optional[PersistentStore] = None


def get_store() -> PersistentStore:
    global _store
    if _store is None:
        _store = PersistentStore()
    return _store
