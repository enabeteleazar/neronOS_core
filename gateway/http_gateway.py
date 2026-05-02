# core/gateway/http_gateway.py
# Gateway HTTP REST — endpoints FastAPI pour l'accès texte/stream.

from __future__ import annotations

import logging
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

logger = logging.getLogger("neron.gateway.http")

router = APIRouter()

# InternalGateway injectée via init_gateway()
_internal = None


def init_gateway(gw) -> None:
    """Injecte l'InternalGateway dans le router HTTP."""
    global _internal
    _internal = gw


# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────


@router.post("/input/text")
async def input_text(payload: dict):
    """Traite un message texte, retourne la réponse complète."""
    if not _internal:
        raise HTTPException(status_code=503, detail="Gateway non initialisée")
    text       = payload.get("text", "")
    session_id = payload.get("session_id", "default")
    if not text:
        raise HTTPException(status_code=400, detail="Champ 'text' requis")
    response = await _internal.handle_text(text, session_id)
    return {"response": response}


@router.post("/input/stream")
async def input_stream(payload: dict):
    """Streame la réponse token par token (SSE)."""
    if not _internal:
        raise HTTPException(status_code=503, detail="Gateway non initialisée")
    text       = payload.get("text", "")
    session_id = payload.get("session_id", "default")
    if not text:
        raise HTTPException(status_code=400, detail="Champ 'text' requis")

    import json

    async def event_stream() -> AsyncIterator[str]:
        async for token in _internal.stream(text, session_id):
            yield f"data: {json.dumps({'token': token, 'done': False})}\n\n"
        yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
