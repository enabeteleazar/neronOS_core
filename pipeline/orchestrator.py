# core/pipeline/orchestrator.py
# v3 — Orchestrateur intelligent : boucle de décision, retry, LLM fallback,
#       mémoire persistante, plan multi-étapes avec état.
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.pipeline.nlp.orchestrator_plan import OrchestratorPlan, PlannedAction
from core.pipeline.nlp.nlp_processor import NLPProcessor, NLPResult, get_processor
from core.pipeline.intent.intent_router import Intent, IntentResult

logger = logging.getLogger("pipeline.orchestrator")

# ── Config ────────────────────────────────────────────────────────────────────

_CONFIDENCE_LLM_FALLBACK = 0.40   # en dessous → LLM fallback
_MAX_RETRIES             = 2      # tentatives max par action
_RETRY_DELAY             = 0.05   # s entre retries (CPU-friendly)
_MULTI_SEP               = "\n\n---\n\n"
_TIMEOUT_ACTION          = 30.0   # timeout par action (s)


# ── État d'une action dans le plan ────────────────────────────────────────────

@dataclass
class ActionState:
    action:    PlannedAction
    attempts:  int   = 0
    success:   bool  = False
    response:  str   = ""
    error:     str   = ""
    elapsed_ms: float = 0.0


# ── Résultat final ────────────────────────────────────────────────────────────

@dataclass
class OrchestratorResult:
    response:        str
    intent:          str
    confidence:      float
    nlp:             Dict[str, Any]
    multi_responses: List[str]          = field(default_factory=list)
    fallback_used:   bool               = False
    retries:         int                = 0
    elapsed_ms:      float              = 0.0

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "intent":        self.intent,
            "confidence":    (
                "high"   if self.confidence >= 0.7 else
                "medium" if self.confidence >= 0.4 else "low"
            ),
            "nlp":           self.nlp,
            "multi":         len(self.multi_responses) > 1,
            "fallback_used": self.fallback_used,
            "retries":       self.retries,
            "elapsed_ms":    self.elapsed_ms,
        }


# ── Orchestrateur v3 ──────────────────────────────────────────────────────────

class NLPOrchestrator:
    """
    Pipeline complet :
      1. NLP (intent + entities + context)
      2. Détection multi-action → plan séquentiel / parallèle
      3. Boucle de décision :
           a. confidence ≥ seuil → agent spécialisé
           b. confidence < seuil → LLM fallback avec contexte
      4. Retry automatique (max 2) sur erreur transitoire
      5. Persistance du tour dans SQLite après réponse
    """

    def __init__(
        self,
        agent_router,
        nlp:   Optional[NLPProcessor] = None,
        store=None,
    ) -> None:
        self._router = agent_router
        self._nlp    = nlp or get_processor()
        self._store  = store  # PersistentStore | None (lazy)

    def _get_store(self):
        if self._store is None:
            try:
                from core.memory.persistent_store import get_store
                self._store = get_store()
            except Exception:
                pass
        return self._store

    # ── Point d'entrée ────────────────────────────────────────────────────────

    async def handle(
        self,
        query:      str,
        session_id: str = "default",
    ) -> OrchestratorResult:
        t0 = time.monotonic()

        # ── 1. NLP ────────────────────────────────────────────────────────────
        nlp_result = self._nlp.process(query, session_id)
        plan       = nlp_result.plan

        logger.info(
            "[ORCH] q=%r intent=%s conf=%.2f mode=%s n=%d",
            query[:60], nlp_result.intent, nlp_result.confidence,
            plan.mode if plan else "single",
            len(plan.actions) if plan else 1,
        )

        # ── 2. Annulation ─────────────────────────────────────────────────────
        if nlp_result.resolved and nlp_result.resolved.is_negation:
            return self._make_result(
                "D'accord, j'annule.", "conversation", 1.0, nlp_result,
                t0=t0,
            )

        # ── 3. Multi-action ───────────────────────────────────────────────────
        if plan and plan.is_multi:
            return await self._handle_multi(plan, nlp_result, session_id, t0)

        # ── 4. Action unique ──────────────────────────────────────────────────
        state = ActionState(action=PlannedAction(query=query, order=0))
        await self._execute_with_retry(state, nlp_result, session_id)

        result = self._make_result(
            state.response or state.error,
            nlp_result.intent,
            nlp_result.confidence,
            nlp_result,
            fallback_used=False,
            retries=state.attempts - 1,
            t0=t0,
        )

        self._persist_turn(session_id, query, nlp_result, result.response)
        return result

    # ── Multi-action ──────────────────────────────────────────────────────────

    async def _handle_multi(
        self,
        plan:       OrchestratorPlan,
        nlp_result: NLPResult,
        session_id: str,
        t0:         float,
    ) -> OrchestratorResult:
        actions = sorted(plan.actions, key=lambda a: a.order)

        if plan.mode == "parallel":
            states = await self._run_parallel(actions, session_id)
        else:
            states = await self._run_sequential(actions, session_id)

        responses = [s.response or s.error for s in states]
        merged    = _MULTI_SEP.join(r for r in responses if r)
        retries   = sum(max(0, s.attempts - 1) for s in states)

        result = self._make_result(
            merged, nlp_result.intent, nlp_result.confidence, nlp_result,
            multi_responses=responses, retries=retries, t0=t0,
        )
        self._persist_turn(session_id, plan.raw_query, nlp_result, merged)
        return result

    async def _run_sequential(
        self, actions: List[PlannedAction], session_id: str
    ) -> List[ActionState]:
        states = []
        for action in actions:
            sub_nlp = self._nlp.process(action.query, session_id)
            state   = ActionState(action=action)
            await self._execute_with_retry(state, sub_nlp, session_id)
            states.append(state)
        return states

    async def _run_parallel(
        self, actions: List[PlannedAction], session_id: str
    ) -> List[ActionState]:
        async def _one(action: PlannedAction) -> ActionState:
            sub_nlp = self._nlp.process(action.query, session_id)
            state   = ActionState(action=action)
            await self._execute_with_retry(state, sub_nlp, session_id)
            return state

        return list(await asyncio.gather(*[_one(a) for a in actions]))

    # ── Boucle de décision + retry ────────────────────────────────────────────

    async def _execute_with_retry(
        self,
        state:      ActionState,
        nlp_result: NLPResult,
        session_id: str,
    ) -> None:
        """
        Tente d'exécuter l'action jusqu'à _MAX_RETRIES fois.
        Si confidence < seuil → LLM fallback immédiat (pas de retry agent).
        """
        for attempt in range(1, _MAX_RETRIES + 1):
            state.attempts = attempt
            t = time.monotonic()

            try:
                # ── Décision : agent spécialisé ou LLM fallback ───────────────
                if nlp_result.confidence >= _CONFIDENCE_LLM_FALLBACK:
                    response = await asyncio.wait_for(
                        self._dispatch_agent(nlp_result, state.action.query),
                        timeout=_TIMEOUT_ACTION,
                    )
                else:
                    response = await asyncio.wait_for(
                        self._llm_fallback(state.action.query, session_id, nlp_result),
                        timeout=_TIMEOUT_ACTION,
                    )

                state.response  = response
                state.success   = True
                state.elapsed_ms = (time.monotonic() - t) * 1000
                return

            except asyncio.TimeoutError:
                state.error = "⏱️ Timeout — réessaie dans un instant."
                logger.warning("[ORCH] timeout attempt=%d q=%r", attempt, state.action.query[:40])

            except Exception as exc:
                state.error = f"⚠️ Erreur : {exc}"
                logger.error("[ORCH] error attempt=%d: %s", attempt, exc)

            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_DELAY)

        # Tous les essais épuisés
        state.response = state.error or "⚠️ Impossible de traiter cette demande."

    # ── Dispatch agent ────────────────────────────────────────────────────────

    async def _dispatch_agent(self, nlp_result: NLPResult, query: str) -> str:
        """Délègue à AgentRouter via IntentResult enrichi."""
        intent_result = _nlp_to_intent_result(nlp_result)
        return await self._router.route(intent_result, query)

    # ── LLM Fallback ──────────────────────────────────────────────────────────

    async def _llm_fallback(
        self, query: str, session_id: str, nlp_result: NLPResult
    ) -> str:
        """
        Appelle le LLM directement avec contexte enrichi quand
        la confidence NLP est trop faible pour un agent spécialisé.
        """
        logger.info(
            "[ORCH] LLM fallback — conf=%.2f query=%r",
            nlp_result.confidence, query[:50],
        )

        # Construire contexte
        context_summary = ""
        try:
            ctx_mgr = self._nlp._ctx
            context_summary = ctx_mgr.get_context_summary(session_id)
        except Exception:
            pass

        augmented = query
        if context_summary:
            augmented = f"{context_summary}\n\nQuestion actuelle : {query}"

        # Utiliser LLM via AgentRouter en mode conversation
        from core.pipeline.intent.intent_router import IntentResult
        fallback_intent = IntentResult(
            intent=Intent.CONVERSATION,
            confidence="low",
            confidence_score=nlp_result.confidence,
            entities=nlp_result.entities,
        )
        return await self._router.route(fallback_intent, augmented)

    # ── Persistance ───────────────────────────────────────────────────────────

    def _persist_turn(
        self,
        session_id: str,
        query:      str,
        nlp_result: NLPResult,
        response:   str,
    ) -> None:
        store = self._get_store()
        if not store:
            return
        try:
            store.push_turn(
                session_id=session_id,
                query=query,
                intent=nlp_result.intent,
                entities=nlp_result.entities,
                response=response[:500],
                confidence=nlp_result.confidence,
            )
        except Exception as exc:
            logger.warning("[ORCH] persist error: %s", exc)

    # ── Builder résultat ──────────────────────────────────────────────────────

    def _make_result(
        self,
        response:       str,
        intent:         str,
        confidence:     float,
        nlp_result:     NLPResult,
        multi_responses: Optional[List[str]] = None,
        fallback_used:  bool = False,
        retries:        int  = 0,
        t0:             float = 0.0,
    ) -> OrchestratorResult:
        return OrchestratorResult(
            response=response,
            intent=intent,
            confidence=confidence,
            nlp=nlp_result.to_dict(),
            multi_responses=multi_responses or [],
            fallback_used=fallback_used,
            retries=retries,
            elapsed_ms=round((time.monotonic() - t0) * 1000, 1),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _nlp_to_intent_result(nlp: NLPResult) -> IntentResult:
    _MAP = {i.value: i for i in Intent}
    intent = _MAP.get(nlp.intent, Intent.CONVERSATION)
    score  = nlp.confidence
    return IntentResult(
        intent=intent,
        confidence="high" if score >= 0.7 else ("medium" if score >= 0.4 else "low"),
        confidence_score=score,
        entities=nlp.entities,
    )
