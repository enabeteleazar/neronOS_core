"""core/agents/llm_agent.py
Neron Core — Agent LLM  v3.0.0

Changement v3 : l'agent ne contacte plus Ollama directement.
Tout appel IA passe par NéronLLMClient → POST /llm/generate.
server/ ne connaît jamais le modèle utilisé.

Ce qui est conservé à l'identique :
  • interface BaseAgent (execute / stream)
  • _get_system_prompt() + module personality
  • _build_messages()
  • AgentResult wrapping
  • logs structurés JSON
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from core.agents.base_agent import BaseAgent, AgentResult
from core.config import settings
from core.llm_client.client import NéronLLMClient
from core.llm_client.types import TaskType

# ── Logging ───────────────────────────────────────────────────────────────────

logger = logging.getLogger("agent.llm_agent")

# ── Personality ───────────────────────────────────────────────────────────────

try:
    from personality import build_system_prompt as _build_personality_prompt
    _PERSONALITY_AVAILABLE = True
except Exception:
    _build_personality_prompt  = None   # type: ignore[assignment]
    _PERSONALITY_AVAILABLE     = False

_STATIC_SYSTEM_PROMPT: str = settings.SYSTEM_PROMPT


def _get_system_prompt(user_context: str = "") -> tuple[str, bool]:
    """Return (system_prompt, personality_active).

    Tries the dynamic personality module first; falls back to the static
    SYSTEM_PROMPT from neron.yaml on any error.
    """
    if _PERSONALITY_AVAILABLE and _build_personality_prompt is not None:
        try:
            return _build_personality_prompt(user_context=user_context), True
        except Exception:
            pass
    return _STATIC_SYSTEM_PROMPT, False


def _build_prompt(query: str, context_data: str | None = None) -> str:
    """Combine system prompt + optional context + user query into a single prompt.

    neron/llm/ owns the model and its message format.  We pass a fully
    rendered text prompt; the LLM service decides how to format it for
    the chosen model (chat vs. completion API, system field injection, etc.).
    """
    system_prompt, _ = _get_system_prompt()

    parts: list[str] = [system_prompt]

    if context_data:
        if context_data.startswith("Historique"):
            parts.append(
                "Voici le contexte de notre conversation :\n\n"
                + context_data
                + "\n\nRéponds maintenant à cette nouvelle question "
                  "en tenant compte du contexte : "
                + query
            )
        else:
            parts.append(
                "Voici des informations pertinentes :\n\n"
                + context_data
                + "\n\nEn te basant sur ces informations, "
                  "réponds à la question suivante : "
                + query
            )
    else:
        parts.append(query)

    return "\n\n".join(parts)


# ── Singleton client — shared across all execute() calls ─────────────────────
# Instantiated once at import-time so the httpx connection pool is reused.

_llm_client = NéronLLMClient()


# ── Agent ─────────────────────────────────────────────────────────────────────

class LLMAgent(BaseAgent):
    """Orchestrator-side LLM agent.

    Delegates all AI work to the neron/llm/ microservice via NéronLLMClient.
    Never imports httpx, never knows which model is used.
    """

    def __init__(self) -> None:
        super().__init__(name="llm_agent")
        _, personality_active = _get_system_prompt()
        self.logger.info(
            json.dumps({
                "event":              "llm_agent_init",
                "llm_service":        getattr(settings, "NERON_LLM", {}).get("url", "http://localhost:8765"),
                "personality_module": "v7" if personality_active else "static_fallback",
            })
        )

    # ── execute() ─────────────────────────────────────────────────────────────

    async def execute(
        self,
        query:        str,
        context_data: str | None = None,
        task_type:    TaskType   = "chat",
        request_id:   str | None = None,
        **kwargs,
    ) -> AgentResult:
        """Execute a query through the LLM service.

        Args:
            query:        User-facing question or instruction.
            context_data: Optional memory/web context injected into the prompt.
            task_type:    Routing hint for the LLM service (chat/code/reasoning/agent).
            request_id:   Optional correlation ID propagated to all logs.
        """
        self.logger.info(
            json.dumps({
                "event":      "llm_execute",
                "query":      query[:80],
                "task_type":  task_type,
                "request_id": request_id,
            })
        )
        start = self._timer()

        prompt = _build_prompt(query, context_data)
        _, personality_active = _get_system_prompt()

        # context dict passed to llm service (stateless — llm never stores it)
        context: dict = {}
        if context_data:
            context["memory"] = context_data[:2000]   # hard cap to avoid huge payloads

        result = await _llm_client.generate(
            task_type  = task_type,
            prompt     = prompt,
            context    = context,
            request_id = request_id,
        )
        latency = self._elapsed_ms(start)

        # model_used = "degraded" when the LLM service is completely down
        if result.model_used == "degraded":
            return self._failure(
                error      = result.warning or "LLM service unreachable",
                latency_ms = latency,
            )

        return self._success(
            content    = result.result,
            metadata   = {
                "model":              result.model_used,
                "latency_ms":         result.latency_ms,
                "personality_active": personality_active,
                "request_id":         request_id or "",
                "warning":            result.warning,
            },
            latency_ms = latency,
        )

    # ── stream() ──────────────────────────────────────────────────────────────

    async def stream(
        self,
        query:        str,
        context_data: str | None = None,
        task_type:    TaskType   = "chat",
        request_id:   str | None = None,
    ) -> AsyncIterator[str]:
        """Streaming via POST /llm/stream (SSE).

        Falls back to a single non-streamed call if the stream endpoint is
        unavailable (e.g. first deploy before the endpoint is wired up).
        """
        import uuid
        rid = request_id or str(uuid.uuid4())
        prompt = _build_prompt(query, context_data)
        context: dict = {}
        if context_data:
            context["memory"] = context_data[:2000]

        base_url: str = getattr(settings, "NERON_LLM", {}).get("url", "http://localhost:8765")
        timeout:  float = float(getattr(settings, "NERON_LLM", {}).get("timeout", 30))

        payload = {
            "task_type":        task_type,
            "prompt":           prompt,
            "context":          context,
            "model_preference": "auto",
            "request_id":       rid,
        }
        headers = {
            "Content-Type":       "application/json",
            "x-neron-request-id": rid,
        }

        stream_url = f"{base_url}/llm/stream"

        try:
            # read=None : pas de timeout entre chunks — le LLM peut être lent à démarrer.
            # Le timeout global (asyncio.wait_for) protège contre un blocage infini.
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=None, write=5.0, pool=5.0)) as client:
                async with client.stream("POST", stream_url, json=payload, headers=headers) as response:
                    if response.status_code == 404:
                        # /llm/stream not yet implemented — fall back to /llm/generate
                        raise NotImplementedError("stream endpoint not available")
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            import json as _json
                            data  = _json.loads(line[6:])
                            token = data.get("token", "")
                            done  = data.get("done", False)
                            if token:
                                yield token
                            if done:
                                break
                        except Exception:
                            continue

        except (NotImplementedError, httpx.ConnectError, httpx.HTTPStatusError):
            # Graceful fallback: non-streamed generate, yielded as a single chunk
            self.logger.warning(
                json.dumps({
                    "event":      "llm_stream_fallback",
                    "request_id": rid,
                    "reason":     "stream endpoint unavailable, using generate",
                })
            )
            result = await _llm_client.generate(
                task_type  = task_type,
                prompt     = prompt,
                context    = context,
                request_id = rid,
            )
            yield result.result

        except httpx.TimeoutException:
            self.logger.warning(
                json.dumps({"event": "llm_stream_timeout", "request_id": rid})
            )

        except Exception as exc:
            self.logger.exception(
                json.dumps({"event": "llm_stream_error", "request_id": rid, "error": str(exc)})
            )

    # ── utility ───────────────────────────────────────────────────────────────

    async def reload(self) -> bool:
        """Ask neron/llm/ to reload its config, then check health."""
        base_url = getattr(settings, "NERON_LLM", {}).get("url", "http://localhost:8765")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(f"{base_url}/llm/reload")
            return await _llm_client.health()
        except Exception as exc:
            self.logger.error(
                json.dumps({"event": "llm_reload_error", "error": str(exc)})
            )
            return False

    async def check_connection(self) -> bool:
        """Return True if neron/llm/ health endpoint is reachable."""
        return await _llm_client.health()

