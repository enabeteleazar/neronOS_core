# agents/tts_agent.py
# Neron Core - Agent TTS (espeak + ffmpeg MP3)

import asyncio
import os
from core.config import settings
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from core.agents.base_agent import AgentResult, get_logger

# Pool dédié pour les opérations CPU/IO bloquantes du TTS
_tts_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tts_io")

logger = get_logger("tts_agent")

TTS_MAX_CHARS = int(settings.TTS_MAX_CHARS)

_TTS_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "neron_tts"
)
if _TTS_MODULE_PATH not in sys.path:
    sys.path.insert(0, os.path.abspath(_TTS_MODULE_PATH))

_tts_engine = None


def load_engine():
    global _tts_engine
    from engine import get_engine
    engine_name = settings.TTS_ENGINE
    logger.info(f"Chargement moteur TTS '{engine_name}'...")
    _tts_engine = get_engine()
    logger.info(f"TTS engine '{_tts_engine.name()}' prêt")
    return _tts_engine


class TTSAgent:
    def __init__(self):
        logger.info("TTSAgent init — espeak+ffmpeg")

    async def synthesize(self, text: str) -> AgentResult:
        if not text or not text.strip():
            return AgentResult(
                success=False, content="", source="tts_agent",
                error="Texte vide", latency_ms=0.0, metadata={}
            )

        if len(text) > TTS_MAX_CHARS:
            return AgentResult(
                success=False, content="", source="tts_agent",
                error=f"Texte trop long : {len(text)} > {TTS_MAX_CHARS}",
                latency_ms=0.0, metadata={}
            )

        if _tts_engine is None:
            return AgentResult(
                success=False, content="", source="tts_agent",
                error="Moteur TTS non chargé",
                latency_ms=0.0, metadata={}
            )

        start = time.monotonic()
        try:
            # Déleguer l'opération bloquante (subprocess.run) au ThreadPool
            result = await asyncio.get_event_loop().run_in_executor(
                _tts_executor, _tts_engine.synthesize, text, "mp3"
            )
            if isinstance(result, tuple):
                audio_bytes, mimetype = result
            else:
                audio_bytes = result
                mimetype = "audio/wav"

            latency_ms = round((time.monotonic() - start) * 1000, 2)
            logger.info(f"TTS OK : {latency_ms}ms -> {len(audio_bytes)} bytes ({mimetype})")

            return AgentResult(
                success=True, content="", source="tts_agent",
                error=None, latency_ms=latency_ms,
                metadata={
                    "audio_bytes": audio_bytes,
                    "mimetype":    mimetype,
                    "engine":      _tts_engine.name(),
                    "chars":       len(text)
                }
            )

        except Exception as e:
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            logger.error(f"Erreur TTS : {e}")
            return AgentResult(
                success=False, content="", source="tts_agent",
                error=f"Erreur synthèse : {str(e)}",
                latency_ms=latency_ms, metadata={}
            )

    async def reload(self) -> bool:
        try:
            load_engine()
            return True
        except Exception as e:
            logger.error(f"TTS reload error: {e}")
            return False

    async def check_connection(self) -> bool:
        return _tts_engine is not None
