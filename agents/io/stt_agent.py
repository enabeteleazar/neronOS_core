# agents/stt_agent.py
# Neron Core - Agent STT (faster-whisper direct, sans neron_stt intermédiaire)

import asyncio
import logging
import os
from core.config import settings
import tempfile
import time
import wave
import struct
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from core.agents.base_agent import AgentResult, get_logger

# Pool dédié pour les opérations CPU-bound (Whisper, file I/O)
_stt_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="stt_io")

logger = get_logger("stt_agent")

WHISPER_MODEL_NAME = settings.WHISPER_MODEL
WHISPER_LANGUAGE   = settings.WHISPER_LANG
WHISPER_DOWNLOAD_ROOT = settings.WHISPER_DOWNLOAD_ROOT
AUDIO_MAX_SIZE_MB  = float(str(settings.AUDIO_MAX_MB))
AUDIO_MAX_SIZE_BYTES = int(AUDIO_MAX_SIZE_MB * 1024 * 1024)
SUPPORTED_FORMATS  = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"}

_whisper_model = None


def load_model():
    """Charge le modèle faster-whisper (appelé au démarrage de core)"""
    global _whisper_model
    from faster_whisper import WhisperModel

    logger.info(f"Chargement faster-whisper '{WHISPER_MODEL_NAME}' (int8, cpu)...")
    _whisper_model = WhisperModel(
        WHISPER_MODEL_NAME,
        device="cpu",
        compute_type="int8",
        download_root=WHISPER_DOWNLOAD_ROOT
    )

    # Warmup
    logger.info("Warmup faster-whisper...")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name
        with wave.open(tmp_path, 'w') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack('<' + 'h' * 1600, *([0] * 1600)))
    try:
        list(_whisper_model.transcribe(tmp_path, language="fr"))
    except Exception:
        pass
    finally:
        os.remove(tmp_path)

    logger.info(f"faster-whisper prêt | langue: {WHISPER_LANGUAGE or 'auto'}")
    return _whisper_model


class STTAgent:
    def __init__(self):
        logger.info("STTAgent init — faster-whisper direct")

    async def transcribe(self, audio_bytes: bytes, filename: str) -> AgentResult:
        ext = Path(filename).suffix.lower()

        if ext not in SUPPORTED_FORMATS:
            return AgentResult(
                success=False, content="", source="stt_agent",
                error=f"Format non supporté : '{ext}'",
                latency_ms=0.0, metadata={}
            )

        if len(audio_bytes) > AUDIO_MAX_SIZE_BYTES:
            return AgentResult(
                success=False, content="", source="stt_agent",
                error=f"Fichier trop volumineux : {len(audio_bytes)//1024//1024}MB > {AUDIO_MAX_SIZE_MB}MB",
                latency_ms=0.0, metadata={}
            )

        if _whisper_model is None:
            return AgentResult(
                success=False, content="", source="stt_agent",
                error="Modèle STT non chargé",
                latency_ms=0.0, metadata={}
            )

        start = time.monotonic()

        try:
            # Décharger TOUT le travail CPU/IO bloquant dans le ThreadPool
            result = await asyncio.get_event_loop().run_in_executor(
                _stt_executor, self._transcribe_sync, audio_bytes, ext
            )
            return result

        except Exception as e:
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            logger.error(f"Erreur transcription : {e}")
            return AgentResult(
                success=False, content="", source="stt_agent",
                error=f"Erreur transcription : {str(e)}",
                latency_ms=latency_ms, metadata={}
            )

    def _transcribe_sync(self, audio_bytes: bytes, ext: str) -> AgentResult:
        """Version synchrone de la transcription — exécutée dans un thread."""
        start = time.monotonic()
        tmp_path = None

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            logger.info(f"Transcription : ({len(audio_bytes)} bytes)")

            kwargs = {"beam_size": 5}
            if WHISPER_LANGUAGE:
                kwargs["language"] = WHISPER_LANGUAGE

            segments, info = _whisper_model.transcribe(tmp_path, **kwargs)
            text = " ".join(seg.text.strip() for seg in segments).strip()
            language = info.language
            latency_ms = round((time.monotonic() - start) * 1000, 2)

            logger.info(f"Transcription OK : {latency_ms}ms | langue: {language} | texte: {text[:80]}")

            return AgentResult(
                success=True, content=text, source="stt_agent",
                error=None, latency_ms=latency_ms,
                metadata={
                    "language": language,
                    "stt_model": WHISPER_MODEL_NAME,
                    "duration_ms": latency_ms
                }
            )

        except Exception as e:
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            logger.error(f"Erreur transcription : {e}")
            return AgentResult(
                success=False, content="", source="stt_agent",
                error=f"Erreur transcription : {str(e)}",
                latency_ms=latency_ms, metadata={}
            )

        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    async def reload(self) -> bool:
        """Recharge le modèle faster-whisper"""
        try:
            load_model()
            return True
        except Exception as e:
            logger.error(f"STT reload error: {e}")
            return False

    async def check_connection(self) -> bool:
        return _whisper_model is not None
