# core/pipeline/nlp/nlp_processor.py
# v2 — Orchestration NLP context-aware + plan multi-action.
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.pipeline.nlp.intent_classifier import classify, scores_all
from core.pipeline.nlp.entity_extractor import extract_entities
from core.pipeline.nlp.context_manager import (
    ContextManager, ContextTurn, ResolvedQuery, get_context_manager,
)
from core.pipeline.nlp.orchestrator_plan import OrchestratorPlan, build_plan


@dataclass
class NLPResult:
    intent:       str
    entities:     Dict[str, Any]
    confidence:   float
    scores:       Dict[str, float] = field(default_factory=dict)
    resolved:     Optional[ResolvedQuery] = None
    plan:         Optional[OrchestratorPlan] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent":     self.intent,
            "entities":   self.entities,
            "confidence": self.confidence,
        }


class NLPProcessor:
    """
    NLP pipeline v2 : context-aware, multi-action, hybride exact+fuzzy.
    CPU only — aucune dépendance externe.
    """

    def __init__(self, ctx: Optional[ContextManager] = None) -> None:
        self._ctx = ctx or get_context_manager()

    def process(self, text: str, session_id: str = "default") -> NLPResult:
        if not text or not text.strip():
            return NLPResult(intent="conversation", entities={}, confidence=0.0)

        raw = text.strip()

        # ── 1. Résolution contextuelle ────────────────────────────────────────
        resolved = self._ctx.resolve(session_id, raw)
        effective_text = resolved.text

        # ── 2. Détection plan multi-action ────────────────────────────────────
        plan = build_plan(effective_text)

        # ── 3. Classification + extraction (sur la 1ère action / texte complet)
        target = plan.first().query if plan.is_multi else effective_text
        intent, confidence = classify(target)
        entities = extract_entities(target, intent)

        # Fusionner entités héritées du contexte (sans écraser les nouvelles)
        if resolved.inherited:
            merged = dict(resolved.inherited)
            merged.update(entities)
            entities = merged

        scores = scores_all(target)

        result = NLPResult(
            intent=intent,
            entities=entities,
            confidence=confidence,
            scores=scores,
            resolved=resolved,
            plan=plan,
        )

        # ── 4. Push dans le contexte de session ───────────────────────────────
        self._ctx.push(session_id, ContextTurn(
            query=raw,
            intent=intent,
            entities=entities,
        ))

        return result


# ── Singleton ─────────────────────────────────────────────────────────────────

_processor: Optional[NLPProcessor] = None


def get_processor() -> NLPProcessor:
    global _processor
    if _processor is None:
        _processor = NLPProcessor()
    return _processor


def process(text: str, session_id: str = "default") -> NLPResult:
    return get_processor().process(text, session_id)
