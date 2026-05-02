# core/gateway/websocket_gateway.py
# Gateway WebSocket Néron — intégration FastAPI native.

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logger = logging.getLogger("neron.gateway.websocket")


@dataclass
class GatewayConfig:
    ws_path: str = "/ws"
    host:    str = "0.0.0.0"
    port:    int = 18789


class NeronGateway:
    """
    Gateway WebSocket principal.
    Exposé comme sous-application FastAPI via http_app().
    Protocole JSON-RPC simplifié :
      → { "id": ..., "method": "chat"|"stream", "params": { "text": ..., "session_id": ... } }
      ← { "id": ..., "result": { "response": ... } }          (chat)
      ← { "id": ..., "event": "token", "data": { "token": ... } }  (stream)
      ← { "id": ..., "event": "done", "data": {} }                 (stream fin)
      ← { "id": ..., "error": "..." }                             (erreur)
    """

    def __init__(
        self,
        config:       GatewayConfig | None = None,
        internal=None,  # InternalGateway — injecté après init possible
        # compat params (ignorés, conservés pour signature)
        agent_router=None,
        session_store=None,
        skill_registry=None,
    ) -> None:
        self.config   = config or GatewayConfig()
        self.internal = internal
        self._clients: Set[WebSocket] = set()
        self._app: FastAPI | None = None

    # ─────────────────────────────────────────────
    # INTERFACE PUBLIQUE
    # ─────────────────────────────────────────────

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def set_internal(self, internal) -> None:
        """Injecte l'InternalGateway après construction."""
        self.internal = internal

    def http_app(self) -> FastAPI:
        """Retourne la sous-app FastAPI à monter dans l'app principale."""
        if self._app is None:
            self._app = self._build_app()
        return self._app

    async def start(self) -> None:
        logger.info(
            "NeronGateway WebSocket prêt — chemin : %s", self.config.ws_path
        )

    async def stop(self) -> None:
        for ws in list(self._clients):
            try:
                await ws.close()
            except Exception:
                pass
        self._clients.clear()
        logger.info("NeronGateway arrêté")

    # ─────────────────────────────────────────────
    # CONSTRUCTION FastAPI
    # ─────────────────────────────────────────────

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Néron WebSocket Gateway")

        gateway_ref = self  # capture pour les closures

        @app.websocket(self.config.ws_path)
        async def ws_endpoint(ws: WebSocket):
            await ws.accept()
            gateway_ref._clients.add(ws)
            logger.info("Client WS connecté — total : %d", gateway_ref.client_count)
            try:
                await gateway_ref._handle_client(ws)
            except WebSocketDisconnect:
                pass
            except Exception as e:
                logger.exception("Erreur WS client : %s", e)
            finally:
                gateway_ref._clients.discard(ws)
                logger.info(
                    "Client WS déconnecté — total : %d", gateway_ref.client_count
                )

        return app

    # ─────────────────────────────────────────────
    # HANDLER DE CONNEXION
    # ─────────────────────────────────────────────

    async def _handle_client(self, ws: WebSocket) -> None:
        async for raw in ws.iter_text():
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"error": "json invalide"})
                continue

            method     = data.get("method", "chat")
            params     = data.get("params", {})
            req_id     = data.get("id")
            text       = params.get("text") or params.get("message", "")
            session_id = params.get("session_id", "default")

            if not text:
                await ws.send_json({"id": req_id, "error": "champ 'text' manquant"})
                continue

            if not self.internal:
                await ws.send_json({"id": req_id, "error": "gateway non initialisée"})
                continue

            if method == "stream":
                await self._do_stream(ws, req_id, text, session_id)
            else:
                await self._do_chat(ws, req_id, text, session_id)

    async def _do_chat(
        self, ws: WebSocket, req_id, text: str, session_id: str
    ) -> None:
        try:
            response = await self.internal.handle_text(text, session_id)
            await ws.send_json({"id": req_id, "result": {"response": response}})
        except Exception as e:
            logger.exception("Erreur chat WS : %s", e)
            await ws.send_json({"id": req_id, "error": str(e)})

    async def _do_stream(
        self, ws: WebSocket, req_id, text: str, session_id: str
    ) -> None:
        try:
            async for token in self.internal.stream(text, session_id):
                await ws.send_json({
                    "id":    req_id,
                    "event": "token",
                    "data":  {"token": token, "done": False},
                })
            await ws.send_json({
                "id":    req_id,
                "event": "done",
                "data":  {"done": True},
            })
        except Exception as e:
            logger.exception("Erreur stream WS : %s", e)
            await ws.send_json({"id": req_id, "error": str(e)})
