"""server/core/llm_client/types.py
Pydantic contracts for the server ↔ llm REST bus.

server/ uses ONLY these types — never imports from neron_llm directly.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


# ── Task types supported by the LLM service ──────────────────────────────────

TaskType = Literal["code", "reasoning", "chat", "agent"]

# ── Outgoing request ──────────────────────────────────────────────────────────

class LLMGenerateRequest(BaseModel):
    """Payload sent by server/ to POST /llm/generate."""

    task_type:        TaskType = Field(default="chat")
    prompt:           str
    context:          dict     = Field(default_factory=dict)
    model_preference: str      = Field(default="auto")
    request_id:       str      = Field(default="")   # correlation ID, filled by client


# ── Incoming response ─────────────────────────────────────────────────────────

class LLMGenerateResponse(BaseModel):
    """Response returned by POST /llm/generate."""

    result:     str
    model_used: str
    latency_ms: int
    warning:    Optional[str] = None


# ── Degraded sentinel (used when LLM service is completely down) ──────────────

DEGRADED_RESPONSE = LLMGenerateResponse(
    result     = "Je suis temporairement indisponible. Veuillez réessayer dans quelques instants.",
    model_used = "degraded",
    latency_ms = 0,
    warning    = "LLM service unreachable",
)

