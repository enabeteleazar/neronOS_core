# core/pipeline/nlp/context_manager.py
# v2 — Mémoire hybride : court terme (in-memory) + long terme (SQLite PersistentStore).
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional

# ── Config ────────────────────────────────────────────────────────────────────

_MAX_HOT_TURNS  = 6      # tours en mémoire vive par session
_SESSION_TTL    = 1800   # expiration mémoire chaude (30 min)
_SHORT_QUERY    = 6      # seuil mot court → suivi contextuel

# ── Patterns anaphoriques ─────────────────────────────────────────────────────

_FOLLOWUP_REFS = re.compile(
    r"\b(ça|ca|cela|ceci|lui|elle|eux|elles|"
    r"pareil|idem|encore|aussi|là|la|"
    r"et\s+demain|et\s+hier|et\s+ce\s+soir|"
    r"plutôt|pluto|sinon|et\s+si|mais\s+si)\b",
    re.IGNORECASE,
)
_EXPLICIT_FOLLOWUP = re.compile(
    r"^(et\s+|aussi\s+|mais\s+|sinon\s+|plutôt\s+)?"
    r"(demain|hier|après-?demain|ce\s+soir|ce\s+matin|vendredi|lundi|mardi"
    r"|mercredi|jeudi|samedi|dimanche|dans\s+\d+\s+(?:jours?|heures?))$",
    re.IGNORECASE,
)
_NEGATION_PATTERN = re.compile(
    r"\b(non|pas|stop|annule|oublie|laisse\s+tomber|c[e']?est\s+bon)\b",
    re.IGNORECASE,
)
_PREF_PATTERN = re.compile(
    r"\b(je\s+(?:vis|habite|suis)\s+(?:à|a|en)\s+([A-ZÀ-Ÿa-zà-ÿ][a-zà-ÿA-ZÀ-Ÿ\s\-]{1,30})|"
    r"j[e']?aime\s+([a-zà-ÿA-ZÀ-Ÿ\s]{2,30})|"
    r"appelle[- ]moi\s+([A-ZÀ-Ÿa-zà-ÿ]{2,20}))\b",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    n = unicodedata.normalize("NFD", text.lower().strip())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.replace("'", " ").replace("'", " ")


# ── Modèles ───────────────────────────────────────────────────────────────────

@dataclass
class ContextTurn:
    query:     str
    intent:    str
    entities:  Dict[str, Any]
    timestamp: float = field(default_factory=time.monotonic)

    def age(self) -> float:
        return time.monotonic() - self.timestamp


@dataclass
class ResolvedQuery:
    text:             str
    enriched:         bool
    inherited:        Dict[str, Any]
    is_negation:      bool
    is_pure_followup: bool


# ── Context Manager v2 ────────────────────────────────────────────────────────

class ContextManager:
    """
    Mémoire hybride :
      - hot cache  : dict[session_id → List[ContextTurn]] (in-process, TTL 30 min)
      - cold store : PersistentStore SQLite (cross-restart, GC automatique)
    """

    def __init__(self, store=None) -> None:
        self._hot: Dict[str, List[ContextTurn]] = {}
        self._lock = Lock()
        self._store = store  # PersistentStore | None

    def _get_store(self):
        if self._store is None:
            try:
                from core.memory.persistent_store import get_store
                self._store = get_store()
            except Exception:
                pass
        return self._store

    # ── API publique ──────────────────────────────────────────────────────────

    def push(self, session_id: str, turn: ContextTurn) -> None:
        with self._lock:
            if session_id not in self._hot:
                self._hot[session_id] = []
            self._hot[session_id].append(turn)
            # Garder uniquement les N derniers
            if len(self._hot[session_id]) > _MAX_HOT_TURNS:
                self._hot[session_id] = self._hot[session_id][-_MAX_HOT_TURNS:]
            self._gc_hot()

        # Persistance asynchrone (best-effort)
        store = self._get_store()
        if store:
            try:
                store.push_turn(
                    session_id=session_id,
                    query=turn.query,
                    intent=turn.intent,
                    entities=turn.entities,
                    confidence=0.0,
                )
                self._extract_and_store_facts(session_id, turn, store)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "[ContextManager] persist error: %s", exc
                )

    def resolve(self, session_id: str, query: str) -> ResolvedQuery:
        with self._lock:
            hot = list(self._hot.get(session_id, []))

        # Fallback sur store SQLite si hot cache vide
        if not hot:
            store = self._get_store()
            if store:
                last = store.get_last_turn(session_id)
                if last:
                    hot = [ContextTurn(
                        query=last["query"],
                        intent=last["intent"],
                        entities=last["entities"],
                        timestamp=time.monotonic() - (time.time() - last["ts"]),
                    )]

        if not hot:
            return ResolvedQuery(
                text=query, enriched=False, inherited={},
                is_negation=False, is_pure_followup=False,
            )

        last = hot[-1]
        q_norm = _norm(query)

        # ── Annulation ────────────────────────────────────────────────────────
        if _NEGATION_PATTERN.search(q_norm):
            return ResolvedQuery(
                text=query, enriched=False, inherited={},
                is_negation=True, is_pure_followup=False,
            )

        # ── Suivi temporel pur ────────────────────────────────────────────────
        if _EXPLICIT_FOLLOWUP.match(q_norm.strip()):
            time_ref = q_norm.strip().lstrip("et").strip()
            inherited = dict(last.entities)
            inherited["time_ref"] = time_ref
            enriched = f"{last.query} {time_ref}"
            return ResolvedQuery(
                text=enriched, enriched=True, inherited=inherited,
                is_negation=False, is_pure_followup=True,
            )

        # ── Référence anaphorique + requête courte ────────────────────────────
        words = q_norm.split()
        short = len(words) <= _SHORT_QUERY
        has_ref = bool(_FOLLOWUP_REFS.search(q_norm))

        if short and has_ref and last.intent not in ("conversation",):
            inherited = self._inherit_entities(query, last)
            if inherited:
                enriched_text = self._enrich_text(query, inherited, last)
                return ResolvedQuery(
                    text=enriched_text, enriched=True, inherited=inherited,
                    is_negation=False, is_pure_followup=False,
                )

        return ResolvedQuery(
            text=query, enriched=False, inherited={},
            is_negation=False, is_pure_followup=False,
        )

    def get_last(self, session_id: str) -> Optional[ContextTurn]:
        with self._lock:
            turns = self._hot.get(session_id, [])
            return turns[-1] if turns else None

    def get_facts(self, session_id: str) -> Dict[str, Any]:
        store = self._get_store()
        return store.get_all_facts(session_id) if store else {}

    def get_context_summary(self, session_id: str) -> str:
        store = self._get_store()
        return store.build_context_summary(session_id) if store else ""

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._hot.pop(session_id, None)

    # ── Interne ───────────────────────────────────────────────────────────────

    def _inherit_entities(self, query: str, last: ContextTurn) -> Dict[str, Any]:
        q_norm = _norm(query)
        return {
            k: v for k, v in last.entities.items()
            if not (isinstance(v, str) and _norm(v) in q_norm)
        }

    def _enrich_text(self, query: str, inherited: Dict[str, Any], last: ContextTurn) -> str:
        additions = []
        if "location" in inherited:
            additions.append(f"à {inherited['location']}")
        if "device" in inherited:
            additions.append(f"le {inherited['device']}")
        if "room" in inherited:
            additions.append(f"dans le {inherited['room']}")
        if additions:
            return f"{query} {' '.join(additions)}"
        return f"{query} (contexte: {last.intent})"

    def _extract_and_store_facts(
        self, session_id: str, turn: ContextTurn, store
    ) -> None:
        """Extrait et persiste les faits implicites d'un tour."""
        # Localisation
        if "location" in turn.entities:
            store.set_fact(session_id, "last_location", turn.entities["location"])

        # Device HA
        if "device" in turn.entities and turn.intent == "ha_action":
            store.set_fact(session_id, f"device_{turn.entities['device']}", True)

        # Préférences explicites
        m = _PREF_PATTERN.search(turn.query)
        if m:
            if m.group(2):
                store.set_fact(session_id, "user_city", m.group(2).strip().title())
            if m.group(4):
                store.set_fact(session_id, "user_name", m.group(4).strip())

    def _gc_hot(self) -> None:
        """Supprime les sessions chaudes expirées (appelé sous lock)."""
        expired = [
            sid for sid, turns in self._hot.items()
            if turns and turns[-1].age() > _SESSION_TTL
        ]
        for sid in expired:
            del self._hot[sid]


# ── Singleton ─────────────────────────────────────────────────────────────────

_ctx: Optional[ContextManager] = None


def get_context_manager() -> ContextManager:
    global _ctx
    if _ctx is None:
        _ctx = ContextManager()
    return _ctx
