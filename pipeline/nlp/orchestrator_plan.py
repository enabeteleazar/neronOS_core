# core/pipeline/nlp/orchestrator_plan.py
# Détecte et décompose les commandes multi-action.
# Retourne un OrchestratorPlan exploitable par le pipeline orchestrateur.
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── Connecteurs de séquencement ───────────────────────────────────────────────
# Ordre du plus fort au plus faible pour éviter les faux positifs.

_SEQ_CONNECTORS = [
    r"et\s+ensuite",
    r"puis\s+ensuite",
    r"après\s+(?:ça|cela|quoi)",
    r"ensuite",
    r"puis",
]

_PAR_CONNECTORS = [
    r"en\s+même\s+temps\s+que",
    r"en\s+même\s+temps",
    r"simultanément",
    r"aussi",            # "allume la lumière et aussi dis-moi la météo"
]

_ADDITIVE_CONNECTORS = [
    r"et\s+(?:dis[- ]moi|donne[- ]moi|montre[- ]moi|cherche|allume|éteins|eteins|ajoute|rappelle)",
    r",\s*(?:et\s+)?(?:dis[- ]moi|donne[- ]moi|montre[- ]moi|cherche|allume|éteins|eteins|ajoute|rappelle)",
]

# Construit le pattern de split (capturant pour détecter le type)
_SEQ_PATTERN = re.compile(
    r"\s+(?:" + "|".join(_SEQ_CONNECTORS) + r")\s+",
    re.IGNORECASE,
)
_PAR_PATTERN = re.compile(
    r"\s+(?:" + "|".join(_PAR_CONNECTORS) + r")\s+",
    re.IGNORECASE,
)
_ADD_PATTERN = re.compile(
    r"(?:" + "|".join(_ADDITIVE_CONNECTORS) + r")",
    re.IGNORECASE,
)

# Longueur minimale d'un sous-fragment pour être considéré comme action valide
_MIN_ACTION_LEN = 4


def _norm(text: str) -> str:
    n = unicodedata.normalize("NFD", text.lower().strip())
    return "".join(c for c in n if unicodedata.category(c) != "Mn")


# ── Modèles ───────────────────────────────────────────────────────────────────

@dataclass
class PlannedAction:
    query:    str
    order:    int
    nlp_hint: Optional[Dict[str, Any]] = None  # pré-rempli après NLP


@dataclass
class OrchestratorPlan:
    actions:     List[PlannedAction]
    mode:        str   # "single" | "sequential" | "parallel"
    raw_query:   str   # requête originale

    @property
    def is_multi(self) -> bool:
        return len(self.actions) > 1

    def first(self) -> PlannedAction:
        return self.actions[0]


# ── Splitter ──────────────────────────────────────────────────────────────────

def _split(text: str, pattern: re.Pattern) -> List[str]:
    parts = pattern.split(text)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) >= _MIN_ACTION_LEN]


def build_plan(query: str) -> OrchestratorPlan:
    """
    Analyse une requête et retourne un OrchestratorPlan.

    Priorité de détection :
      1. Séquentiel (puis, ensuite)
      2. Parallèle (en même temps, simultanément)
      3. Additif (et + verbe d'action)
      4. Single action
    """
    q = query.strip()

    # ── 1. Split séquentiel ───────────────────────────────────────────────────
    parts = _split(q, _SEQ_PATTERN)
    if len(parts) > 1:
        return OrchestratorPlan(
            actions=[PlannedAction(query=p, order=i) for i, p in enumerate(parts)],
            mode="sequential",
            raw_query=q,
        )

    # ── 2. Split parallèle ────────────────────────────────────────────────────
    parts = _split(q, _PAR_PATTERN)
    if len(parts) > 1:
        return OrchestratorPlan(
            actions=[PlannedAction(query=p, order=i) for i, p in enumerate(parts)],
            mode="parallel",
            raw_query=q,
        )

    # ── 3. Split additif (heuristique sur verbe d'action) ────────────────────
    m = _ADD_PATTERN.search(q)
    if m:
        split_pos = m.start()
        part_a = q[:split_pos].strip()
        part_b = q[split_pos:].lstrip("et ,").strip()
        if len(part_a) >= _MIN_ACTION_LEN and len(part_b) >= _MIN_ACTION_LEN:
            return OrchestratorPlan(
                actions=[
                    PlannedAction(query=part_a, order=0),
                    PlannedAction(query=part_b, order=1),
                ],
                mode="sequential",
                raw_query=q,
            )

    # ── 4. Action unique ──────────────────────────────────────────────────────
    return OrchestratorPlan(
        actions=[PlannedAction(query=q, order=0)],
        mode="single",
        raw_query=q,
    )
