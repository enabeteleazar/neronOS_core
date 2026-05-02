# agents/base_agent.py
# Socle commun de tous les agents Neron.

from __future__ import annotations

import logging
import os
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Logging coloré
# ──────────────────────────────────────────────────────────────────────────────


class ColorFormatter(logging.Formatter):
    # FIX: préfixe \033 ajouté — sans lui les codes s'affichaient en texte brut
    COLORS = {
        "DEBUG":    "\033[36m",
        "INFO":     "\033[32m",
        "WARNING":  "\033[33m",
        "ERROR":    "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"
    DIM   = "\033[2m"

    def __init__(self, *args: Any, use_color: bool = True, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        ts  = self.formatTime(record, self.datefmt)
        if self.use_color:
            color = self.COLORS.get(record.levelname, self.RESET)
            return (
                f"{self.DIM}{ts}{self.RESET} "
                f"{self.DIM}{record.name}{self.RESET} "
                f"{color}{record.levelname:<8}{self.RESET} "
                f"{color}{msg}{self.RESET}"
            )
        return f"{ts} {record.name} {record.levelname:<8} {msg}"


def get_logger(name: str, level: int | None = None) -> logging.Logger:
    """
    Retourne un logger nommé avec formatter coloré.

    FIX: niveau de log configurable via le paramètre 'level'
    ou la variable d'env NERON_LOG_LEVEL (défaut : INFO).
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        use_color = (
            os.environ.get("FORCE_COLOR", "") == "1"
            or (hasattr(sys.stdout, "isatty") and sys.stdout.isatty())
        )
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(ColorFormatter(datefmt="%H:%M:%S", use_color=use_color))
        logger.addHandler(handler)
        logger.propagate = False

        # FIX: niveau défini explicitement — sans ça DEBUG/INFO sont silencieux
        if level is not None:
            logger.setLevel(level)
        else:
            env_level = os.environ.get("NERON_LOG_LEVEL", "INFO").upper()
            logger.setLevel(getattr(logging, env_level, logging.INFO))

    return logger


# ──────────────────────────────────────────────────────────────────────────────
# AgentResult
# ──────────────────────────────────────────────────────────────────────────────

# FIX: confidence typé Literal au lieu de str libre
ConfidenceLevel = Literal["low", "medium", "high"]


@dataclass
class AgentResult:
    success:    bool
    content:    str
    source:     str
    intent:     str                = "unknown"
    confidence: ConfidenceLevel    = "low"
    metadata:   Dict[str, Any]     = field(default_factory=dict)
    error:      Optional[str]      = None
    latency_ms: Optional[float]    = None


# ──────────────────────────────────────────────────────────────────────────────
# BaseAgent
# ──────────────────────────────────────────────────────────────────────────────


class BaseAgent(ABC):
    def __init__(self, name: str) -> None:
        self.name   = name
        self.logger = get_logger(f"agent.{name}")

    async def on_start(self) -> None:
        """Hook de démarrage — override dans les agents si besoin."""
        pass

    @abstractmethod
    async def execute(self, query: str, **kwargs: Any) -> AgentResult:
        """Point d'entrée principal de l'agent."""
        ...

    def _success(
        self,
        content:    str,
        metadata:   Dict[str, Any] | None = None,
        latency_ms: float | None          = None,
        confidence: ConfidenceLevel        = "low",
    ) -> AgentResult:
        return AgentResult(
            success    = True,
            content    = content,
            source     = self.name,
            metadata   = metadata or {},
            latency_ms = latency_ms,
            confidence = confidence,
        )

    def _failure(
        self,
        error:      str,
        latency_ms: float | None = None,
    ) -> AgentResult:
        # FIX: log simplifié — le nom de l'agent est déjà dans le nom du logger
        self.logger.error("Echec : %s", error)
        return AgentResult(
            success    = False,
            content    = "",
            source     = self.name,
            error      = error,
            latency_ms = latency_ms,
        )

    def _timer(self) -> float:
        """Retourne le timestamp de départ pour mesure de latence."""
        return time.monotonic()

    def _elapsed_ms(self, start: float) -> float:
        """Retourne le temps écoulé en millisecondes depuis start."""
        return round((time.monotonic() - start) * 1000, 2)

# ─────────────────────────────────────────────────────────────
# Ajouter en bas du fichier, après la classe BaseAgent
# ─────────────────────────────────────────────────────────────

from typing import Dict

# dictionnaire global de tous les agents instanciés
_agents: Dict[str, BaseAgent] = {}

def register_agent(agent: BaseAgent) -> None:
    """Enregistre un agent globalement, accessible depuis Telegram et Watchdog"""
    global _agents
    _agents[agent.name] = agent
    agent.logger.info("Agent enregistré globalement")

def get_agents() -> Dict[str, BaseAgent]:
    """Retourne tous les agents enregistrés"""
    return _agents
