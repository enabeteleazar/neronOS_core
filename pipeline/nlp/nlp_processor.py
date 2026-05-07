# core/pipeline/nlp/nlp_processor.py
# Point d'entrée unique du module NLP.
# Produit un NLPResult exploitable par IntentRouter et les agents.
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict

from core.pipeline.nlp.intent_classifier import classify, scores_all
from core.pipeline.nlp.entity_extractor import extract_entities


@dataclass
class NLPResult:
    intent:     str
    entities:   Dict[str, Any]
    confidence: float
    scores:     Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent":     self.intent,
            "entities":   self.entities,
            "confidence": self.confidence,
        }


def _normalize(text: str) -> str:
    n = unicodedata.normalize("NFD", text.lower().strip())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.replace("'", " ").replace("'", " ").replace("`", " ")


class NLPProcessor:
    """
    Traitement NLP léger — CPU only, aucune dépendance externe.

    Usage:
        processor = NLPProcessor()
        result = processor.process("Quelle météo à Paris demain ?")
        # NLPResult(intent='weather_query', entities={'location': 'Paris',
        #           'time_ref': 'demain'}, confidence=0.9)
    """

    def process(self, text: str) -> NLPResult:
        if not text or not text.strip():
            return NLPResult(intent="conversation", entities={}, confidence=0.0)

        text = text.strip()
        intent, confidence = classify(text)
        entities = extract_entities(text, intent)
        scores   = scores_all(text)

        return NLPResult(
            intent=intent,
            entities=entities,
            confidence=confidence,
            scores=scores,
        )


# ── Singleton ─────────────────────────────────────────────────────────────────

_processor: NLPProcessor | None = None


def get_processor() -> NLPProcessor:
    global _processor
    if _processor is None:
        _processor = NLPProcessor()
    return _processor


def process(text: str) -> NLPResult:
    """Shortcut module-level."""
    return get_processor().process(text)
