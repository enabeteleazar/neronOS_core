"""core/agents/core/todo_agent.py
Neron Core — Agent Todo List  v1.0.0

Inspiré du todo list generator (JARVIS) — gestion de tâches persistées
dans la base SQLite existante de Néron (memory.db).

Intent déclenché : TODO_ACTION
Commandes Telegram : /todo, /todo add <tâche>, /todo done <id>, /todo clear

Actions reconnues depuis le langage naturel :
  - Ajout   : "ajoute", "rappelle-moi", "note que", "n'oublie pas"
  - Liste   : "ma liste", "mes tâches", "todo", "qu'est-ce que j'ai à faire"
  - Terminer: "j'ai fait", "c'est fait", "marque comme terminé", "done"
  - Supprimer toutes : "efface tout", "vide la liste"
"""
from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from core.config import settings

logger = logging.getLogger("agent.todo")

# ── DB ────────────────────────────────────────────────────────────────────────

_DB_PATH = str(settings.MEMORY_DB_PATH)


@contextmanager
def _db():
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _init_table() -> None:
    with _db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS todo (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                task      TEXT NOT NULL,
                done      INTEGER DEFAULT 0,
                created   DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed DATETIME
            )
        """)
        conn.commit()


# ── CRUD ──────────────────────────────────────────────────────────────────────

def _add_task(task: str) -> int:
    with _db() as conn:
        cur = conn.execute("INSERT INTO todo (task) VALUES (?)", (task,))
        conn.commit()
        return cur.lastrowid  # type: ignore[return-value]


def _list_tasks(include_done: bool = False) -> list[sqlite3.Row]:
    with _db() as conn:
        if include_done:
            return conn.execute(
                "SELECT * FROM todo ORDER BY done ASC, created DESC LIMIT 20"
            ).fetchall()
        return conn.execute(
            "SELECT * FROM todo WHERE done = 0 ORDER BY created DESC"
        ).fetchall()


def _mark_done(task_id: int) -> bool:
    with _db() as conn:
        cur = conn.execute(
            "UPDATE todo SET done = 1, completed = ? WHERE id = ? AND done = 0",
            (datetime.now().isoformat(), task_id),
        )
        conn.commit()
        return cur.rowcount > 0


def _clear_done() -> int:
    with _db() as conn:
        cur = conn.execute("DELETE FROM todo WHERE done = 1")
        conn.commit()
        return cur.rowcount


def _clear_all() -> int:
    with _db() as conn:
        cur = conn.execute("DELETE FROM todo")
        conn.commit()
        return cur.rowcount


# ── Parsing ───────────────────────────────────────────────────────────────────

_ADD_PATTERNS = [
    r"(?:ajoute|note|rappelle.moi de?|n.oublie pas de?)\s+(.+)",
    r"(?:todo|tache|tâche)\s*:\s*(.+)",
    r"(?:j.?ai besoin de|pense à)\s+(.+)",
]

_DONE_PATTERNS = [
    r"(?:j.?ai fait|c.?est fait|done|terminé|marque comme terminé)\s+(?:le\s+)?#?(\d+)",
    r"#?(\d+)\s+(?:est fait|done|terminé)",
]

_LIST_KEYWORDS = ["ma liste", "mes taches", "mes tâches", "todo", "qu est-ce que j ai",
                  "qu'est-ce que j'ai", "liste", "a faire", "à faire"]
_CLEAR_KEYWORDS = ["efface tout", "vide la liste", "supprime tout", "clear"]


def _detect_action(query: str) -> tuple[str, Optional[str]]:
    """Retourne (action, argument) — action : add | list | done | clear.
    La détection se fait sur la version lowercasée, mais le texte de la tâche
    est extrait depuis la query originale pour préserver la casse.
    """
    q = query.lower()

    # Effacement
    if any(kw in q for kw in _CLEAR_KEYWORDS):
        return "clear", None

    # Marquage terminé — on cherche l'ID numérique
    for pat in _DONE_PATTERNS:
        m = re.search(pat, q)
        if m:
            return "done", m.group(1)

    # Ajout — matcher sur q mais capturer sur la query originale
    for pat in _ADD_PATTERNS:
        m_lower = re.search(pat, q, re.IGNORECASE)
        if m_lower:
            # Reproduire le match sur la query originale pour préserver la casse
            m_orig = re.search(pat, query, re.IGNORECASE)
            task = (m_orig.group(1) if m_orig else m_lower.group(1)).strip().rstrip(".")
            return "add", task

    # Liste (défaut)
    return "list", None


# ── Agent ─────────────────────────────────────────────────────────────────────

class TodoAgent:
    """
    Gestion de la todo list persistée dans SQLite.
    Comprend le langage naturel pour ajouter / lister / terminer des tâches.
    """

    def __init__(self) -> None:
        _init_table()

    async def run(self, query: str = "") -> str:
        action, arg = _detect_action(query)

        if action == "add" and arg:
            task_id = _add_task(arg)
            return f"✅ Tâche #{task_id} ajoutée : « {arg} »"

        if action == "done" and arg:
            tid = int(arg)
            ok  = _mark_done(tid)
            if ok:
                return f"✅ Tâche #{tid} marquée comme terminée."
            return f"⚠️ Tâche #{tid} introuvable ou déjà terminée."

        if action == "clear":
            n = _clear_done()
            if n:
                return f"🗑️ {n} tâche(s) terminée(s) supprimée(s)."
            return "Aucune tâche terminée à supprimer."

        # Affichage liste
        tasks = _list_tasks()
        if not tasks:
            return "📋 Ta liste de tâches est vide. Ajoute quelque chose !"

        lines = ["📋 **Todo list** :"]
        for t in tasks:
            status = "✅" if t["done"] else "🔲"
            lines.append(f"  {status} #{t['id']} — {t['task']}")
        lines.append(f"\n_{len(tasks)} tâche(s) active(s)_")
        return "\n".join(lines)

    # ── Méthode directe pour les commandes Telegram /todo ────────────────────

    async def handle_command(self, args: str) -> str:
        """Gère /todo [add <tâche>|done <id>|clear|list]."""
        args = args.strip()
        if args.startswith("add "):
            return await self.run("ajoute " + args[4:])
        if args.startswith("done "):
            return await self.run("j'ai fait " + args[5:])
        if args == "clear":
            return await self.run("efface tout")
        return await self.run("")  # liste
