# core/control_plane/core.py
# Kernel central de Néron v3 — orchestration complète.

from __future__ import annotations

import logging

from core.control_plane.registry import Registry
from core.control_plane.lifecycle import LifecycleManager
from core.control_plane.health import HealthManager
from core.control_plane.events import EventBus

from core.gateway.internal_gateway import InternalGateway
from core.gateway.websocket_gateway import NeronGateway, GatewayConfig
from core.gateway.telegram_gateway import TelegramGateway
from core.gateway.http_gateway import init_gateway as init_http_gateway

from core.pipeline.intent.intent_router import IntentRouter
from core.pipeline.routing.agent_router import AgentRouter
from core.modules.sessions import SessionStore
from core.modules.skills import SkillRegistry
from core.config import settings

logger = logging.getLogger("neron.control_plane")


class NeronCore:
    """
    Kernel central de Néron.
    Orchestration globale : pipeline, gateways, services.
    """

    def __init__(self) -> None:
        self.registry  = Registry()
        self.events    = EventBus()
        self.health    = HealthManager()
        self.lifecycle = LifecycleManager()

        self.internal:  InternalGateway | None = None
        self.gateway:   NeronGateway    | None = None
        self.telegram:  TelegramGateway | None = None
        self.pipeline:  AgentRouter     | None = None

    # ─────────────────────────────────────────────
    # BOOTSTRAP
    # ─────────────────────────────────────────────

    def boot(self) -> None:
        logger.info("Boot control plane...")

        self._init_pipeline()
        self._init_internal_gateway()
        self._init_ws_gateway()
        self._init_telegram_gateway()
        self._init_http_gateway()
        self._register_core_services()

        logger.info("Control plane prêt")

    # ─────────────────────────────────────────────
    # INIT PIPELINE
    # ─────────────────────────────────────────────

    def _init_pipeline(self) -> None:
        session_store  = SessionStore()
        skill_registry = SkillRegistry()

        self.pipeline = AgentRouter(
            sessions=session_store,
            skills=skill_registry,
        )
        self.registry.register_service("pipeline", self.pipeline)
        logger.info("Pipeline initialisé")

    # ─────────────────────────────────────────────
    # INIT GATEWAYS
    # ─────────────────────────────────────────────

    def _init_internal_gateway(self) -> None:
        intent_router = IntentRouter()
        self.internal = InternalGateway(
            agent_router=self.pipeline,
            intent_router=intent_router,
        )
        self.registry.register_service("internal_gateway", self.internal)
        logger.info("InternalGateway initialisée")

    def _init_ws_gateway(self) -> None:
        config = GatewayConfig()
        self.gateway = NeronGateway(
            config=config,
            internal=self.internal,
        )
        self.registry.register_service("ws_gateway", self.gateway)
        logger.info("NeronGateway WebSocket initialisée")

    def _init_telegram_gateway(self) -> None:
        if not settings.TELEGRAM_ENABLED:
            logger.info("TelegramGateway désactivée (TELEGRAM_ENABLED=false)")
            return
        self.telegram = TelegramGateway(internal=self.internal)
        self.registry.register_service("telegram_gateway", self.telegram)
        logger.info("TelegramGateway initialisée")

    def _init_http_gateway(self) -> None:
        """Injecte l'InternalGateway dans le router HTTP."""
        init_http_gateway(self.internal)
        logger.info("HTTP Gateway initialisée")

    # ─────────────────────────────────────────────
    # SERVICES CORE
    # ─────────────────────────────────────────────

    def _register_core_services(self) -> None:
        self.registry.register_service("health",    self.health)
        self.registry.register_service("events",    self.events)
        self.registry.register_service("lifecycle", self.lifecycle)

    # ─────────────────────────────────────────────
    # INTERFACES PUBLIQUES
    # ─────────────────────────────────────────────

    def get_gateway(self) -> NeronGateway | None:
        return self.gateway

    def get_pipeline(self) -> AgentRouter | None:
        return self.pipeline

    def get_internal(self) -> InternalGateway | None:
        return self.internal
