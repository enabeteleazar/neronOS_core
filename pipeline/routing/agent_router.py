# core/pipeline/routing/agent_router.py
# v2.0 — Câblage des 4 nouveaux intents (news, météo, todo, wiki)
#
# DIFF vs v1 :
#   + import NewsAgent, WeatherAgent, TodoAgent, WikiAgent
#   + singletons _news, _weather, _todo, _wiki
#   + cases Intent.NEWS_QUERY / WEATHER_QUERY / TODO_ACTION / WIKI_QUERY dans route()

from __future__ import annotations

import logging
from typing import Optional

from core.pipeline.intent.intent_router import Intent, IntentResult

logger = logging.getLogger("pipeline.agent_router")

# ── Lazy imports (évite les imports circulaires au démarrage) ─────────────────

_llm:     Optional[object] = None
_memory:  Optional[object] = None
_system:  Optional[object] = None
_ha:      Optional[object] = None
_web:     Optional[object] = None
# Nouveaux agents
_news:    Optional[object] = None
_weather: Optional[object] = None
_todo:    Optional[object] = None
_wiki:    Optional[object] = None


def _get_llm():
    global _llm
    if _llm is None:
        from core.agents.core.llm_agent import LLMAgent
        _llm = LLMAgent()
    return _llm


def _get_memory():
    global _memory
    if _memory is None:
        from core.agents.core.memory_agent import MemoryAgent
        _memory = MemoryAgent()
    return _memory


def _get_system():
    global _system
    if _system is None:
        from core.agents.core.system_agent import SystemAgent
        _system = SystemAgent()
    return _system


def _get_ha():
    global _ha
    if _ha is None:
        from core.agents.automation.ha_agent import HAAgent
        _ha = HAAgent()
    return _ha


def _get_web():
    global _web
    if _web is None:
        from core.agents.communication.web_agent import WebAgent
        _web = WebAgent()
    return _web


def _get_news():
    global _news
    if _news is None:
        from core.agents.io.news_agent import NewsAgent
        _news = NewsAgent()
    return _news


def _get_weather():
    global _weather
    if _weather is None:
        from core.agents.io.weather_agent import WeatherAgent
        _weather = WeatherAgent()
    return _weather


def _get_todo():
    global _todo
    if _todo is None:
        from core.agents.core.todo_agent import TodoAgent
        _todo = TodoAgent()
    return _todo


def _get_wiki():
    global _wiki
    if _wiki is None:
        from core.agents.io.wiki_agent import WikiAgent
        _wiki = WikiAgent()
    return _wiki


# ── Router ────────────────────────────────────────────────────────────────────

class AgentRouter:
    """
    Dispatch une IntentResult vers l'agent approprié et retourne la réponse.
    """

    async def route(self, intent_result: IntentResult, query: str) -> str:
        intent = intent_result.intent
        logger.info("[AGENT_ROUTER] dispatching intent=%s", intent)

        # ── Nouveaux intents v2.0 ─────────────────────────────────────────────
        if intent == Intent.NEWS_QUERY:
            return await _get_news().run(query)

        if intent == Intent.WEATHER_QUERY:
            return await _get_weather().run(query)

        if intent == Intent.TODO_ACTION:
            return await _get_todo().run(query)

        if intent == Intent.WIKI_QUERY:
            return await _get_wiki().run(query)

        # ── Intents existants ─────────────────────────────────────────────────
        if intent == Intent.TIME_QUERY:
            from core.neron_time.time_provider import get_formatted_time
            return get_formatted_time()

        if intent == Intent.HA_ACTION:
            result = await _get_ha().execute(query)
            return result.content if result.success else f"⚠️ {result.error}"

        if intent == Intent.WEB_SEARCH:
            result = await _get_web().execute(query)
            return result.content if result.success else f"⚠️ {result.error}"

        if intent in (Intent.CODE, Intent.CODE_AUDIT):
            # Délégation au code_agent / code_audit_agent existant
            from core.agents.dev.code_audit_agent import CodeAuditAgent
            agent = CodeAuditAgent()
            result = await agent.execute(query)
            return result.content if result.success else f"⚠️ {result.error}"

        if intent == Intent.PERSONALITY_FEEDBACK:
            from core.personality.updater import apply_feedback
            apply_feedback(query)
            return "⚙️ Ajustement de comportement pris en compte."

        # ── Conversation générale (LLM) ───────────────────────────────────────
        memory  = _get_memory()
        context = await memory.get_context(query) if hasattr(memory, "get_context") else None
        result  = await _get_llm().execute(query, context_data=context)

        if result.success:
            if hasattr(memory, "save"):
                await memory.save(query, result.content)
            return result.content

        return f"⚠️ Erreur LLM : {result.error}"
