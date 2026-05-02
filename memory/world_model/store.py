# core/world_model/store.py
# World Model Store — Persistance JSON + historique SQLite.

from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import settings

logger = logging.getLogger("world_model.store")

# Chemins
_WM_DIR      = settings.LOGS_DIR.parent / "data" / "world_model"
_SNAPSHOT_PATH = _WM_DIR / "current.json"
_DB_PATH       = _WM_DIR / "history.db"

# Conservation de l'historique
_HISTORY_MAX_ROWS = 10_000
_HISTORY_DAYS     = 30


@contextmanager
def _get_db():
    _WM_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _init_db() -> None:
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wm_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT NOT NULL,
                score        REAL,
                level        TEXT,
                cpu          REAL,
                ram          REAL,
                disk         REAL,
                process_ram  REAL,
                anomaly_count INTEGER,
                snapshot     TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wm_timestamp ON wm_history(timestamp)"
        )
        conn.commit()
    logger.debug("World Model DB initialisée")


class WorldModelStore:
    """
    Gère la persistance du World Model :
    - Snapshot courant → fichier JSON (lecture rapide)
    - Historique       → SQLite (tendances, graphiques)
    """

    def __init__(self) -> None:
        _WM_DIR.mkdir(parents=True, exist_ok=True)
        _init_db()

    # ── Snapshot courant ──────────────────────────────────────────────────────

    def save(self, snapshot: dict) -> None:
        """Persiste le snapshot courant en JSON + SQLite."""
        # JSON — lecture rapide par l'API
        tmp = _SNAPSHOT_PATH.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
            tmp.replace(_SNAPSHOT_PATH)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            logger.error("WorldModelStore.save JSON error : %s", e)
            raise

        # SQLite — historique
        self._append_history(snapshot)

    def load(self) -> dict | None:
        """Charge le snapshot courant depuis le JSON."""
        if not _SNAPSHOT_PATH.exists():
            return None
        try:
            return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("WorldModelStore.load error : %s", e)
            return None

    # ── Historique ────────────────────────────────────────────────────────────

    def _append_history(self, snapshot: dict) -> None:
        """Ajoute une entrée dans l'historique SQLite."""
        try:
            score_data = snapshot.get("score", {})
            system     = snapshot.get("system", {})
            process    = snapshot.get("process", {})
            anomalies  = snapshot.get("anomalies", [])

            with _get_db() as conn:
                conn.execute(
                    """INSERT INTO wm_history
                       (timestamp, score, level, cpu, ram, disk, process_ram, anomaly_count, snapshot)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        snapshot.get("meta", {}).get("timestamp", datetime.now(timezone.utc).isoformat()),
                        score_data.get("score"),
                        score_data.get("level"),
                        system.get("cpu",  {}).get("current"),
                        system.get("ram",  {}).get("current"),
                        system.get("disk", {}).get("usage"),
                        process.get("ram_mb"),
                        len(anomalies),
                        json.dumps(snapshot, ensure_ascii=False),
                    ),
                )
                conn.commit()
                self._rotate(conn)
        except Exception as e:
            logger.error("WorldModelStore._append_history error : %s", e)

    def _rotate(self, conn: sqlite3.Connection) -> None:
        """Supprime les entrées trop anciennes ou au-delà du plafond."""
        conn.execute(
            "DELETE FROM wm_history "
            "WHERE timestamp < datetime('now', ? || ' days')",
            (f"-{_HISTORY_DAYS}",),
        )
        count = conn.execute("SELECT COUNT(*) FROM wm_history").fetchone()[0]
        if count > _HISTORY_MAX_ROWS:
            overflow = count - _HISTORY_MAX_ROWS
            conn.execute(
                "DELETE FROM wm_history WHERE id IN "
                "(SELECT id FROM wm_history ORDER BY id ASC LIMIT ?)",
                (overflow,),
            )
        conn.commit()

    def get_history(
        self,
        limit: int = 100,
        days:  int = 1,
    ) -> list[dict]:
        """
        Retourne l'historique des snapshots.
        Par défaut : 100 dernières entrées sur 24h.
        """
        try:
            with _get_db() as conn:
                rows = conn.execute(
                    """SELECT timestamp, score, level, cpu, ram, disk,
                              process_ram, anomaly_count
                       FROM wm_history
                       WHERE timestamp > datetime('now', ? || ' days')
                       ORDER BY id DESC
                       LIMIT ?""",
                    (f"-{days}", limit),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.error("WorldModelStore.get_history error : %s", e)
            return []

    def get_trend(self, metric: str = "score", periods: int = 12) -> list[dict]:
        """
        Retourne la tendance d'une métrique sur les dernières périodes.
        metric : score | cpu | ram | disk | process_ram | anomaly_count
        """
        allowed = {"score", "cpu", "ram", "disk", "process_ram", "anomaly_count"}
        if metric not in allowed:
            metric = "score"

        try:
            with _get_db() as conn:
                rows = conn.execute(
                    f"""SELECT timestamp, {metric} as value
                        FROM wm_history
                        ORDER BY id DESC
                        LIMIT ?""",
                    (periods,),
                ).fetchall()
                return [{"timestamp": r["timestamp"], "value": r["value"]} for r in reversed(rows)]
        except Exception as e:
            logger.error("WorldModelStore.get_trend error : %s", e)
            return []

    def stats(self) -> dict:
        """Statistiques globales de l'historique."""
        try:
            with _get_db() as conn:
                row = conn.execute(
                    """SELECT COUNT(*) as total,
                              AVG(score)        as avg_score,
                              MIN(score)        as min_score,
                              MAX(score)        as max_score,
                              AVG(cpu)          as avg_cpu,
                              AVG(ram)          as avg_ram,
                              SUM(anomaly_count) as total_anomalies
                       FROM wm_history
                       WHERE timestamp > datetime('now', '-7 days')"""
                ).fetchone()
                return dict(row) if row else {}
        except Exception as e:
            logger.error("WorldModelStore.stats error : %s", e)
            return {}
