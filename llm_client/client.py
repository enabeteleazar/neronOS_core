"""server/core/llm_client/client.py
NéronLLMClient — seul point de contact autorisé entre server/ et neron/llm/.

Contrat strict :
  • server/ ne connaît JAMAIS le modèle utilisé
  • server/ ne contacte JAMAIS Ollama directement
  • toute communication passe par POST /llm/generate

Fonctionnalités :
  • generate()     → POST /llm/generate avec retry + fallback dégradé
  • health()       → GET  /llm/health
  • Correlation-ID (x-neron-request-id) sur chaque requête
  • Logs JSON structurés
  • Timeout configurable (défaut 30s)
  • Retry configurable (défaut 2)
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import httpx

from core.llm_client.types import (
    DEGRADED_RESPONSE,
    LLMGenerateRequest,
    LLMGenerateResponse,
    TaskType,
)

logger = logging.getLogger("neron.llm_client")

# ── Re-exported for convenience ───────────────────────────────────────────────
__all__ = ["NéronLLMClient"]


class NéronLLMClient:
    """Async REST client towards neron/llm/ service.

    Instantiate once at agent startup; reuse across requests.

    Configuration is read from settings at construction time so the client
    is always consistent with the current YAML without requiring a restart
    (use NéronLLMClient() after POST /llm/reload to pick up changes).
    """

    def __init__(self) -> None:
        # Import here to avoid circular imports at module level
        from core.config import settings

        cfg: dict[str, Any] = getattr(settings, "NERON_LLM", {})
        self._base_url: str = cfg.get("url",     "http://localhost:8765").rstrip("/")
        self._timeout:  float = float(cfg.get("timeout", 30))
        self._retries:  int   = int(cfg.get("retry",   2))

        logger.info(
            json.dumps({
                "event":   "llm_client_init",
                "base_url": self._base_url,
                "timeout":  self._timeout,
                "retries":  self._retries,
            })
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def generate(
        self,
        task_type:  TaskType        = "chat",
        prompt:     str             = "",
        context:    dict | None     = None,
        request_id: str | None      = None,
    ) -> LLMGenerateResponse:
        """Call POST /llm/generate with retry + graceful degraded fallback.

        Returns a LLMGenerateResponse.  NEVER raises — callers can always
        read .result regardless of LLM availability.
        """
        rid = request_id or str(uuid.uuid4())
        payload = LLMGenerateRequest(
            task_type        = task_type,
            prompt           = prompt,
            context          = context or {},
            model_preference = "auto",
            request_id       = rid,
        )
        headers = {
            "Content-Type":        "application/json",
            "x-neron-request-id":  rid,
        }

        last_error: str | None = None

        for attempt in range(1, self._retries + 2):   # +2 → initial + N retries
            t0 = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        f"{self._base_url}/llm/generate",
                        content  = payload.model_dump_json(),
                        headers  = headers,
                    )
                    resp.raise_for_status()
                    data       = resp.json()
                    latency_ms = int((time.monotonic() - t0) * 1000)

                    result = LLMGenerateResponse(
                        result     = data.get("result",     ""),
                        model_used = data.get("model_used", "unknown"),
                        latency_ms = data.get("latency_ms", latency_ms),
                        warning    = data.get("warning"),
                    )
                    logger.info(
                        json.dumps({
                            "event":       "llm_generate_ok",
                            "request_id":  rid,
                            "task_type":   task_type,
                            "model_used":  result.model_used,
                            "latency_ms":  result.latency_ms,
                            "attempt":     attempt,
                        })
                    )
                    return result

            except httpx.TimeoutException:
                last_error = f"timeout after {self._timeout}s"
                # Timeout is retryable

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                last_error = f"HTTP {status}"
                if status in (400, 401, 403, 404, 422):
                    # 4xx client errors — no point retrying
                    logger.error(
                        json.dumps({
                            "event":      "llm_client_error",
                            "request_id": rid,
                            "error":      last_error,
                            "retryable":  False,
                        })
                    )
                    break

            except httpx.ConnectError:
                last_error = f"connection refused to {self._base_url}"
                # Service is down — keep retrying (systemd may be restarting it)

            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)

            logger.warning(
                json.dumps({
                    "event":      "llm_client_retry",
                    "request_id": rid,
                    "task_type":  task_type,
                    "attempt":    attempt,
                    "max":        self._retries + 1,
                    "error":      last_error,
                })
            )

        # All attempts exhausted — return controlled degraded response
        logger.error(
            json.dumps({
                "event":      "llm_client_degraded",
                "request_id": rid,
                "task_type":  task_type,
                "error":      last_error,
            })
        )
        return DEGRADED_RESPONSE

    async def health(self) -> bool:
        """Check whether neron/llm/ is reachable.  Returns True/False, never raises."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self._base_url}/llm/health")
                return resp.status_code == 200
        except Exception:  # noqa: BLE001
            return False

