# core/app.py
# Neron Core v3.0.0

from __future__ import annotations

# =========================
# INIT LOGGING (PRIORITAIRE)
# =========================

from core.logging.setup import logger

logger.info("Booting Néron Core...")

# =========================
# IMPORTS STANDARD
# =========================

import asyncio
import json
import os
import re
import time
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import psutil
from fastapi import Depends, FastAPI, File, HTTPException, Security, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.security.api_key import APIKeyHeader
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel

# =========================
# IMPORTS NÉRON (APRÈS LOGGING)
# =========================

from core.agents.base_agent import get_logger

# DEV
from core.agents.dev.code_agent.agent import CodeAgent
from core.agents.dev.code_audit_agent import CodeAuditAgent

# AUTOMATION
from core.agents.automation.ha_agent import HAAgent
from core.agents.automation.watchdog_agent import (
    send_watchdog_notification,
    setup as watchdog_setup,
    start_watchdog,
    start_watchdog_bot,
    stop_watchdog,
    stop_watchdog_bot,
    world_model,
)

# CORE
from core.agents.core.llm_agent import LLMAgent
from core.agents.core.memory_agent import MemoryAgent, init_db as memory_init_db
from core.agents.core.system_agent import SystemAgent

# COMMUNICATION
from core.agents.communication.telegram_agent import (
    send_notification,
    set_agents,
    start_bot,
    stop_bot
)
from core.agents.communication.web_agent import WebAgent

# IO
from core.agents.io.stt_agent import STTAgent
from core.agents.io.tts_agent import TTSAgent


from core.config import settings
from core.pipeline.routing.agent_router import AgentRouter, LLMConfig, ToolRegistry
from core.gateway.gateway import GatewayConfig, NeronGateway
from core.modules.scheduler import setup as scheduler_setup
from core.modules.scheduler import start as scheduler_start
from core.modules.scheduler import stop as scheduler_stop
from core.modules.sessions import SessionStore
from core.modules.skills import SkillRegistry
from core.neron_time.time_provider import TimeProvider
from core.pipeline.intent.intent_router import Intent, IntentRouter

# =========================
# LOGGER LOCAL (OPTIONNEL PAR MODULE)
# =========================

logger = get_logger("neron.core")

VERSION = "3.0.0"

# ── Etat global ───────────────────────────────────────────────────────────────

_startup_time: float               = 0.0
_gateway_task: asyncio.Task | None = None

llm_agent:        LLMAgent        | None = None
memory_agent:     MemoryAgent     | None = None
web_agent:        WebAgent        | None = None
stt_agent:        STTAgent        | None = None
tts_agent:        TTSAgent        | None = None
ha_agent:         HAAgent         | None = None
code_agent:       CodeAgent       | None = None
code_audit_agent: CodeAuditAgent  | None = None
router:           IntentRouter    | None = None
time_provider:    TimeProvider    | None = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# ── Module personality ────────────────────────────────────────────────────────

def _personality_available() -> bool:
    try:
        import personality  # noqa: F401
        return True
    except ImportError:
        return False


# ── Metriques Prometheus ──────────────────────────────────────────────────────

def _counter(name: str, doc: str, labels=None):
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    return Counter(name, doc, labels or [])


def _gauge(name: str, doc: str):
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    return Gauge(name, doc)


def _histogram(name: str, doc: str, labels=None, buckets=None):
    if name in REGISTRY._names_to_collectors:
        return REGISTRY._names_to_collectors[name]
    kwargs = {}
    if labels:
        kwargs["labelnames"] = labels
    if buckets:
        kwargs["buckets"] = buckets
    return Histogram(name, doc, **kwargs)


_prom_requests_total  = _counter("neron_requests_total",     "Nombre total de requetes")
_prom_intent_total    = _counter("neron_intent_total",       "Requetes par intent",      ["intent"])
_prom_agent_errors    = _counter("neron_agent_errors_total", "Erreurs par agent",        ["agent"])
_prom_llm_calls       = _counter("neron_llm_calls_by_model", "Appels LLM par modele",   ["model"])
_prom_requests_flight = _gauge("neron_requests_in_flight",   "Requetes en cours")
_prom_uptime          = _gauge("neron_uptime_seconds",        "Duree depuis le demarrage")
_prom_cpu             = _gauge("neron_system_cpu_percent",    "CPU systeme %")
_prom_ram             = _gauge("neron_system_ram_percent",    "RAM systeme %")
_prom_disk            = _gauge("neron_system_disk_percent",   "Disque systeme %")
_prom_process_ram     = _gauge("neron_process_ram_mb",        "RAM process Neron MB")
_prom_exec_time       = _histogram(
    "neron_execution_time_ms", "Temps orchestration ms",
    buckets=[10, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
)
_prom_agent_latency   = _histogram(
    "neron_agent_latency_ms", "Latence par agent ms",
    labels=["agent"],
    buckets=[10, 50, 100, 250, 500, 1000, 2500, 5000],
)


class Metrics:
    """Facade prometheus_client."""

    def record_request_start(self) -> None:
        _prom_requests_total.inc()
        _prom_requests_flight.inc()

    def record_request_end(self, execution_time_ms: float) -> None:
        _prom_requests_flight.dec()
        _prom_exec_time.observe(execution_time_ms)

    def record_intent(self, intent: str) -> None:
        _prom_intent_total.labels(intent=intent).inc()

    def record_error(self, agent: str) -> None:
        _prom_agent_errors.labels(agent=agent).inc()

    def record_latency(self, agent: str, latency_ms: float) -> None:
        _prom_agent_latency.labels(agent=agent).observe(latency_ms)

    def record_model_call(self, model: str) -> None:
        if model:
            _prom_llm_calls.labels(model=model).inc()

    def update_system_metrics(self) -> None:
        try:
            _prom_uptime.set(round(time.monotonic() - _startup_time, 2))
            _prom_cpu.set(psutil.cpu_percent(interval=0.5))
            _prom_ram.set(psutil.virtual_memory().percent)
            _prom_disk.set(psutil.disk_usage("/").percent)
            proc = psutil.Process(os.getpid())
            _prom_process_ram.set(round(proc.memory_info().rss / 1024 / 1024))
        except Exception as e:
            logger.warning("update_system_metrics error : %s", e)

    def export(self) -> str:
        self.update_system_metrics()
        return generate_latest(REGISTRY).decode("utf-8")


metrics = Metrics()


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global llm_agent, web_agent, stt_agent, tts_agent, ha_agent
    global router, time_provider, _startup_time, memory_agent
    global code_agent, code_audit_agent, _gateway_task

    _startup_time = time.monotonic()
    logger.info(json.dumps({"event": "startup", "version": VERSION}))
    metrics.update_system_metrics()

    llm_agent    = LLMAgent()
    web_agent    = WebAgent()
    memory_init_db()
    memory_agent = MemoryAgent()
    ha_agent     = HAAgent()
    code_agent       = CodeAgent()
    code_audit_agent = CodeAuditAgent()

    await ha_agent.on_start()
    router        = IntentRouter(llm_agent=llm_agent)
    time_provider = TimeProvider()

    if _personality_available():
        try:
            from personality import get_current_state
            state = get_current_state()
            logger.info(json.dumps({
                "event":  "personality_loaded",
                "mood":   state.get("mood"),
                "energy": state.get("energy_level"),
                "tone":   state.get("communication", {}).get("tone"),
            }))
        except Exception as e:
            logger.warning("Personality charge mais etat illisible : %s", e)
    else:
        logger.warning("Module personality non disponible — system prompt statique actif")

    logger.info(json.dumps({"event": "agents_ready"}))

    scheduler_setup(
        agents={"code": code_agent, "memory": memory_agent},
        notify_fn=send_watchdog_notification,
    )
    scheduler_start()

    try:
        llm_cfg = LLMConfig(
            provider="ollama",
            model=settings.OLLAMA_MODEL,
            base_url=settings.OLLAMA_HOST,
            max_tokens=settings.LLM_MAX_TOKENS,
            temperature=settings.LLM_TEMPERATURE,
        )
        _sessions     = SessionStore()
        _skills       = SkillRegistry()
        _tools        = ToolRegistry().setup_defaults()
        _agent_router = AgentRouter(
            sessions=_sessions,
            skills=_skills,
            llm_config=llm_cfg,
            tools=_tools,
        )
        gw_config = GatewayConfig(
            host=settings.SERVER_HOST,
            port=18789,
            token=settings.API_KEY or None,
            ping_interval=60.0,
            ping_timeout=120.0,
        )
        _gw = NeronGateway(
            config=gw_config,
            agent_router=_agent_router,
            session_store=_sessions,
            skill_registry=_skills,
        )
        _gateway_task = asyncio.create_task(_gw.start())
        logger.info("Gateway WebSocket demarre sur ws://0.0.0.0:18789")
    except Exception as e:
        logger.warning("Gateway WebSocket non demarre : %s", e)

    set_agents({
        "llm":        llm_agent,
        "stt":        stt_agent,
        "tts":        tts_agent,
        "memory":     memory_agent,
        "ha":         ha_agent,
        "code":       code_agent,
        "code_audit": code_audit_agent,
    })

    telegram_enabled = getattr(settings, "TELEGRAM_ENABLED", False)
    telegram_token   = getattr(settings, "TELEGRAM_BOT_TOKEN", "")

    if telegram_enabled and telegram_token not in ("", "votre_token_ici", None):
        try:
            await start_bot()
        except Exception as e:
            logger.warning("Impossible de demarrer Telegram : %s", e)
    else:
        logger.info("Telegram desactive ou token non configure")

    if getattr(settings, "WATCHDOG_ENABLED", False):
        watchdog_setup(
            agents={"llm": llm_agent, "stt": stt_agent, "tts": tts_agent},
            notify_fn=send_watchdog_notification,
        )
        await start_watchdog()
        await start_watchdog_bot()

    yield

    scheduler_stop()
    await ha_agent.on_stop()

    if _gateway_task and not _gateway_task.done():
        _gateway_task.cancel()
        try:
            await _gateway_task
        except asyncio.CancelledError:
            pass

    if getattr(settings, "WATCHDOG_ENABLED", False):
        await stop_watchdog_bot()
        await stop_watchdog()

    if telegram_enabled and telegram_token not in ("", "votre_token_ici", None):
        try:
            await stop_bot()
        except Exception as e:
            logger.warning("Impossible d'arreter Telegram : %s", e)

    logger.info(json.dumps({"event": "shutdown"}))


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Neron Core",
    description="Orchestrateur central - v" + VERSION,
    version=VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Models ────────────────────────────────────────────────────────────────────

class TextInput(BaseModel):
    text: str


class CoreResponse(BaseModel):
    response:          str
    intent:            str
    agent:             str
    confidence:        str
    timestamp:         str
    execution_time_ms: float
    model:             Optional[str] = None
    error:             Optional[str] = None
    transcription:     Optional[str] = None
    metadata:          dict          = {}


# ── Auth ──────────────────────────────────────────────────────────────────────

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(API_KEY_HEADER)) -> None:
    if not settings.API_KEY or settings.API_KEY == "changez_moi":
        return
    if api_key is None:
        raise HTTPException(status_code=401, detail="API Key manquante")
    if api_key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="API Key invalide")


# ── Routes systeme ────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"service": "Neron Core", "version": VERSION, "status": "active"}


@app.get("/health")
def health():
    return {"status": "healthy", "version": VERSION}


@app.get("/status")
def status():
    try:
        return world_model.get()
    except Exception as e:
        raise HTTPException(500, f"Impossible de recuperer le status : {e}")


@app.get("/metrics")
def prometheus_metrics():
    from fastapi.responses import Response
    return Response(content=metrics.export(), media_type=CONTENT_TYPE_LATEST)


@app.post("/ha/reload")
async def ha_reload(_: None = Depends(verify_api_key)):
    if not ha_agent:
        raise HTTPException(503, "Agent HA non disponible")
    count = await ha_agent.reload()
    return {"status": "ok", "entities": count, "timestamp": utc_now_iso()}


# ── Route /memory ─────────────────────────────────────────────────────────────

@app.get("/memory")
async def get_memory(limit: int = 5, _: None = Depends(verify_api_key)):
    """Retourne les dernières entrées de la mémoire conversationnelle."""
    if not memory_agent:
        raise HTTPException(status_code=503, detail="Agent mémoire non disponible")
    limit = min(max(1, limit), 100)
    try:
        entries = memory_agent.retrieve(limit=limit)
        return {"entries": entries, "count": len(entries), "timestamp": utc_now_iso()}
    except Exception as e:
        logger.error("Erreur récupération mémoire : %s", e)
        raise HTTPException(status_code=500, detail=f"Erreur mémoire : {e}")


# ── Routes /personality ───────────────────────────────────────────────────────

@app.get("/personality/state")
async def personality_state(_: None = Depends(verify_api_key)):
    if not _personality_available():
        raise HTTPException(503, "Module personality non disponible")
    try:
        from personality import get_current_state
        return {"status": "ok", "state": get_current_state(), "timestamp": utc_now_iso()}
    except Exception as e:
        raise HTTPException(500, f"Erreur lecture etat personality : {e}")


@app.get("/personality/history")
async def personality_history(limit: int = 20, _: None = Depends(verify_api_key)):
    if not _personality_available():
        raise HTTPException(503, "Module personality non disponible")
    limit = min(max(1, limit), 100)
    try:
        from personality import get_history
        history = get_history(limit=limit)
        return {"status": "ok", "history": history, "count": len(history), "timestamp": utc_now_iso()}
    except Exception as e:
        raise HTTPException(500, f"Erreur lecture historique personality : {e}")


@app.post("/personality/reset")
async def personality_reset(_: None = Depends(verify_api_key)):
    if not _personality_available():
        raise HTTPException(503, "Module personality non disponible")
    try:
        from personality import force_update
        from personality.updater import _resolve_protected
        results = [
            force_update(None, "mood",         "neutre", "reset via API"),
            force_update(None, "energy_level", "normal", "reset via API"),
        ]
        _resolve_protected.cache_clear()
        return {"status": "ok", "reset": ["mood", "energy_level"], "results": results, "timestamp": utc_now_iso()}
    except Exception as e:
        raise HTTPException(500, f"Erreur reset personality : {e}")


# ── Routes /input ─────────────────────────────────────────────────────────────

@app.post("/input/text", response_model=CoreResponse)
async def text_input(input_data: TextInput, _: None = Depends(verify_api_key)):
    query = input_data.text.strip()
    start = time.monotonic()
    metrics.record_request_start()
    logger.info(json.dumps({"event": "request_received", "query": query[:80]}))

    intent_result = await router.route(query)
    metrics.record_intent(intent_result.intent.value)

    metadata = {
        "intent":     intent_result.intent.value,
        "confidence": intent_result.confidence,
    }

    try:
        if intent_result.intent == Intent.PERSONALITY_FEEDBACK:
            return await _handle_personality_feedback(query, intent_result, metadata, start)
        elif intent_result.intent == Intent.TIME_QUERY:
            return _handle_time_query(intent_result, metadata, start, query)
        elif intent_result.intent == Intent.WEB_SEARCH:
            return await _handle_web_search(query, intent_result, metadata, start)
        elif intent_result.intent == Intent.HA_ACTION:
            return await _handle_ha_action(query, intent_result, metadata, start)
        elif intent_result.intent == Intent.CODE_AUDIT:
            return await _handle_code_audit(intent_result, metadata, start)
        elif intent_result.intent == Intent.CODE:
            return await _handle_code(query, intent_result, metadata, start)
        else:
            return await _handle_conversation(query, intent_result, metadata, start)
    finally:
        elapsed = round((time.monotonic() - start) * 1000, 2)
        metrics.record_request_end(elapsed)


@app.post("/input/stream")
async def text_input_stream(input_data: TextInput, _: None = Depends(verify_api_key)):
    query = input_data.text.strip()

    async def generate():
        try:
            intent_result = await router.route(query)
            logger.debug("stream: intent=%s", intent_result.intent.value)

            if intent_result.intent == Intent.PERSONALITY_FEEDBACK:
                result = await _handle_personality_feedback(query, intent_result, {}, 0)
                yield f"data: {json.dumps({'token': result.response, 'done': True})}\n\n"
                return

            if intent_result.intent == Intent.TIME_QUERY:
                response = _handle_time_query(intent_result, {}, 0, query).response
                yield f"data: {json.dumps({'token': response, 'done': True})}\n\n"
                return

            memory_context = await _get_memory_context(query)
            full_response  = ""
            token_count    = 0

            async for token in llm_agent.stream(query, context_data=memory_context or None):
                full_response += token
                token_count   += 1
                yield f"data: {json.dumps({'token': token, 'done': False})}\n\n"

            logger.debug("stream: %d tokens recus", token_count)

            if token_count == 0:
                # Aucun token reçu du LLM — Ollama probablement indisponible
                logger.warning("stream: aucun token recu — Ollama indisponible ou timeout")
                error_msg = "⚠️ Le service LLM n'a retourné aucune réponse. Vérifie qu'Ollama est bien démarré (`systemctl status ollama`)."
                yield f"data: {json.dumps({'token': error_msg, 'done': True, 'error': 'no_tokens'})}\n\n"
                return

            await _store_memory(query, full_response, {"intent": intent_result.intent.value})
            yield f"data: {json.dumps({'token': '', 'done': True})}\n\n"
            logger.debug("stream: termine")

        except Exception as e:
            logger.exception("stream: exception : %s", e)
            yield f"data: {json.dumps({'token': '', 'done': True, 'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/input/audio", response_model=CoreResponse)
async def audio_input(file: UploadFile = File(...)):
    start = time.monotonic()
    metrics.record_request_start()
    try:
        if stt_agent is None:
            raise HTTPException(503, "STT non disponible (désactivé dans cette configuration)")
        audio_bytes = await file.read()
        result      = await stt_agent.transcribe(audio_bytes, file.filename)
        if not result.success:
            metrics.record_error("stt_agent")
            raise HTTPException(503, f"STT indisponible : {result.error}")
        if result.latency_ms:
            metrics.record_latency("stt_agent", result.latency_ms)
        transcription               = result.content
        core_response               = await text_input(TextInput(text=transcription))
        core_response.transcription = transcription
        core_response.metadata["stt"] = {
            "language":       result.metadata.get("language"),
            "stt_latency_ms": result.latency_ms,
        }
        return core_response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Erreur pipeline audio : {e}")
    finally:
        metrics.record_request_end(round((time.monotonic() - start) * 1000, 2))


@app.post("/input/voice")
async def voice_input(file: UploadFile = File(...)):
    from fastapi.responses import Response as FastAPIResponse
    start = time.monotonic()
    metrics.record_request_start()
    try:
        if stt_agent is None or tts_agent is None:
            raise HTTPException(503, "Pipeline vocal non disponible (STT/TTS désactivés dans cette configuration)")
        audio_bytes = await file.read()
        stt_result  = await stt_agent.transcribe(audio_bytes, file.filename)
        if not stt_result.success:
            metrics.record_error("stt_agent")
            raise HTTPException(503, f"STT indisponible : {stt_result.error}")
        if stt_result.latency_ms:
            metrics.record_latency("stt_agent", stt_result.latency_ms)
        transcription = stt_result.content
        if not transcription:
            raise HTTPException(400, "Transcription vide")
        core_response = await text_input(TextInput(text=transcription))
        tts_result    = await tts_agent.synthesize(core_response.response)
        if not tts_result.success:
            metrics.record_error("tts_agent")
            return core_response
        if tts_result.latency_ms:
            metrics.record_latency("tts_agent", tts_result.latency_ms)
        execution_time_ms = round((time.monotonic() - start) * 1000, 2)
        return FastAPIResponse(
            content=tts_result.metadata["audio_bytes"],
            media_type=tts_result.metadata.get("mimetype", "audio/wav"),
            headers={
                "X-Transcription":     transcription[:200].encode("ascii", "replace").decode(),
                "X-Response-Text":     core_response.response[:200].encode("ascii", "replace").decode(),
                "X-Intent":            core_response.intent,
                "X-Execution-Time-Ms": str(execution_time_ms),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Erreur pipeline vocal : {e}")
    finally:
        metrics.record_request_end(round((time.monotonic() - start) * 1000, 2))


# ── Handlers internes ─────────────────────────────────────────────────────────

async def _handle_personality_feedback(query, intent_result, metadata, start) -> CoreResponse:
    execution_time_ms = round((time.monotonic() - start) * 1000, 2)
    if not _personality_available():
        return CoreResponse(
            response="Je n'ai pas pu mettre a jour ma personnalite (module non disponible).",
            intent="personality_feedback", agent="personality",
            confidence=intent_result.confidence, timestamp=utc_now_iso(),
            execution_time_ms=execution_time_ms, metadata=metadata,
        )
    try:
        from personality import update_from_feedback
        result = update_from_feedback(query)
        if result["status"] == "updated" and result["changes"]:
            parts    = [f"{c['field']} -> {c['new_value']}" for c in result["changes"]]
            response = "Compris. J'ai adapte mon comportement : " + ", ".join(parts) + "."
            logger.info(json.dumps({"event": "personality_updated", "changes": result["changes"]}))
        else:
            response = "Message recu, mais aucun changement de comportement n'a ete necessaire."
        return CoreResponse(
            response=response, intent="personality_feedback", agent="personality",
            confidence=intent_result.confidence, timestamp=utc_now_iso(),
            execution_time_ms=execution_time_ms,
            metadata={**metadata, "personality_changes": result.get("changes", [])},
        )
    except Exception as e:
        logger.error("personality update_from_feedback echoue : %s", e)
        return CoreResponse(
            response="Je n'ai pas pu appliquer ce changement de comportement.",
            intent="personality_feedback", agent="personality",
            confidence=intent_result.confidence, timestamp=utc_now_iso(),
            execution_time_ms=execution_time_ms, error=str(e), metadata=metadata,
        )


def _handle_time_query(intent_result, metadata, start, query="") -> CoreResponse:
    q          = query.lower()
    heure_keys = ["heure", "time", "il est", "quelle heure"]
    date_keys  = ["quelle date sommes", "on est quel jour", "quel jour sommes",
                  "quel mois sommes", "donne moi la date", "c est quoi la date", "on est le combien"]
    want_heure = any(k in q for k in heure_keys)
    want_date  = any(k in q for k in date_keys)
    n = time_provider.now()
    from core.neron_time.time_provider import JOURS, MOIS
    jour = JOURS[n.weekday()]
    mois = MOIS[n.month - 1]
    if want_heure and not want_date:
        response = f"Il est {n.hour:02d}h{n.minute:02d}."
    elif want_date and not want_heure:
        response = f"Nous sommes {jour} {n.day} {mois} {n.year}."
    else:
        response = f"Il est {n.hour:02d}h{n.minute:02d}, {jour} {n.day} {mois} {n.year}."
    return CoreResponse(
        response=response, intent="time_query", agent="time_provider",
        confidence=intent_result.confidence, timestamp=utc_now_iso(),
        execution_time_ms=round((time.monotonic() - start) * 1000, 2),
        metadata={**metadata, "iso": time_provider.iso(), "timestamp": time_provider.timestamp()},
    )


async def _get_memory_context(query: str) -> str:
    try:
        recent = memory_agent.retrieve(limit=1)
        if recent:
            entry = recent[0]
            return (
                f"Echange precedent:\n"
                f"Utilisateur: {entry['input']}\n"
                f"Neron: {entry['response'][:120]}"
            )
    except Exception as e:
        logger.warning(json.dumps({"event": "memory_context_failed", "error": str(e)}))
    return ""


async def _store_memory(query: str, response: str, metadata: dict) -> None:
    try:
        memory_agent.store(query, response, metadata)
    except Exception as e:
        logger.warning(json.dumps({"event": "memory_store_failed", "error": str(e)}))


async def _handle_conversation(query, intent_result, metadata, start) -> CoreResponse:
    memory_context = await _get_memory_context(query)
    result = await llm_agent.execute(query, context_data=memory_context if memory_context else None)
    if not result.success:
        metrics.record_error("llm_agent")
        raise HTTPException(503, f"LLM indisponible : {result.error}")
    if result.latency_ms:
        metrics.record_latency("llm_agent", result.latency_ms)
    model = result.metadata.get("model")
    metrics.record_model_call(model)
    await _store_memory(query, result.content, metadata)
    return CoreResponse(
        response=result.content, intent=metadata.get("intent", "conversation"),
        agent="llm_agent", confidence=metadata.get("confidence", "low"),
        timestamp=utc_now_iso(),
        execution_time_ms=round((time.monotonic() - start) * 1000, 2),
        model=model, metadata={**metadata, **result.metadata},
    )


async def _handle_web_search(query, intent_result, metadata, start) -> CoreResponse:
    web_result = await web_agent.execute(query)
    if not web_result.success:
        metrics.record_error("web_agent")
        return await _handle_conversation(query, intent_result, metadata, start)
    if web_result.latency_ms:
        metrics.record_latency("web_agent", web_result.latency_ms)
    llm_result = await llm_agent.execute(query=query, context_data=web_result.content)
    if not llm_result.success:
        metrics.record_error("llm_agent")
        response_text = web_result.content
        model         = None
    else:
        response_text = llm_result.content
        model         = llm_result.metadata.get("model")
        metrics.record_model_call(model)
        if llm_result.latency_ms:
            metrics.record_latency("llm_agent", llm_result.latency_ms)
    metadata["web_sources"] = web_result.metadata.get("sources", [])
    await _store_memory(query, response_text, metadata)
    return CoreResponse(
        response=response_text, intent="web_search", agent="web_agent+llm_agent",
        confidence=intent_result.confidence, timestamp=utc_now_iso(),
        execution_time_ms=round((time.monotonic() - start) * 1000, 2),
        model=model, metadata={**metadata, **(llm_result.metadata if llm_result.success else {})},
    )


async def _handle_ha_action(query, intent_result, metadata, start) -> CoreResponse:
    result  = await ha_agent.execute(query)
    elapsed = round((time.monotonic() - start) * 1000, 2)
    if result.success:
        return CoreResponse(
            response=result.content, intent=intent_result.intent.value, agent="ha_agent",
            confidence=intent_result.confidence, timestamp=utc_now_iso(),
            execution_time_ms=elapsed, metadata=result.metadata,
        )
    return CoreResponse(
        response=f"Je n'ai pas pu executer cette action : {result.error}",
        intent=intent_result.intent.value, agent="ha_agent",
        confidence=intent_result.confidence, timestamp=utc_now_iso(),
        execution_time_ms=elapsed, error=result.error, metadata={},
    )


async def _handle_code_audit(intent_result, metadata, start) -> CoreResponse:
    elapsed = round((time.monotonic() - start) * 1000, 2)
    if not code_audit_agent:
        return CoreResponse(
            response="Agent d'audit non disponible.",
            intent="code_audit", agent="code_audit_agent",
            confidence=intent_result.confidence, timestamp=utc_now_iso(),
            execution_time_ms=elapsed, metadata=metadata,
        )
    result = await code_audit_agent.execute("", action="audit_all")
    if result.success:
        meta    = result.metadata
        score   = meta.get("avg_score", "?")
        files   = meta.get("files_count", "?")
        issues  = meta.get("total_issues", "?")
        reports = meta.get("reports", [])
        weak    = [
            r for r in reports
            if isinstance(r.get("quality_score"), (int, float)) and r["quality_score"] < 70
        ]
        detail = ""
        if weak:
            detail = "\n\nFichiers à améliorer :\n" + "\n".join(
                f"- {r['file']} ({r.get('quality_score','?')}/100) : "
                + ", ".join(r.get("issues", [])[:2])
                for r in weak[:5]
            )
        response = (
            f"Voici mon auto-audit :\n"
            f"- {files} fichiers analysés\n"
            f"- Score moyen : {score}/100\n"
            f"- Issues détectées : {issues}"
            f"{detail}"
        )
    else:
        response = f"Erreur lors de l'auto-audit : {result.error}"
    return CoreResponse(
        response=response, intent="code_audit", agent="code_audit_agent",
        confidence=intent_result.confidence, timestamp=utc_now_iso(),
        execution_time_ms=round((time.monotonic() - start) * 1000, 2),
        metadata={**metadata, **(result.metadata if result.success else {})},
    )


async def _handle_code(query, intent_result, metadata, start) -> CoreResponse:
    path_match = re.search(r"(\S+\.py)", query)
    path       = path_match.group(1) if path_match else ""
    if not path:
        def _norm(t):
            n = unicodedata.normalize("NFD", t.lower())
            return "".join(c for c in n if unicodedata.category(c) != "Mn")
        stop = {
            "un", "une", "le", "la", "les", "de", "du", "des", "qui", "pour",
            "que", "moi", "me", "genere", "cree", "ecris", "script", "fichier",
            "module", "python", "code", "affiche", "bonjour", "donne",
        }
        words      = re.findall(r"[a-z0-9]+", _norm(query))
        name_words = [w for w in words if w not in stop][:3]
        path       = "_".join(name_words) + ".py" if name_words else "script.py"
    result            = await code_agent.execute(query, path=path)
    execution_time_ms = round((time.monotonic() - start) * 1000, 2)
    if not result.success:
        metrics.record_error("code_agent")
        return CoreResponse(
            response=f"Je n'ai pas pu executer cette action : {result.error}",
            intent="code", agent="code_agent", confidence=intent_result.confidence,
            timestamp=utc_now_iso(), execution_time_ms=execution_time_ms,
            error=result.error, metadata={},
        )
    metrics.record_latency("code_agent", result.latency_ms or 0)
    return CoreResponse(
        response=result.content, intent="code", agent="code_agent",
        confidence=intent_result.confidence, timestamp=utc_now_iso(),
        execution_time_ms=execution_time_ms, metadata=result.metadata,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT)
