# core/world_model/api.py
# World Model API — Routes FastAPI /world-model
# A intégrer dans app.py via : app.include_router(world_model_router)

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security.api_key import APIKeyHeader

from config import settings
from .builder import build_world_model
from .store   import WorldModelStore

logger = logging.getLogger("world_model.api")

world_model_router = APIRouter(prefix="/world-model", tags=["World Model"])

# Instance store partagée
_store = WorldModelStore()

# ThreadPool dédié pour les opérations bloquantes (build_world_model, SQLite I/O)
_wm_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wm_io")

# Auth — réutilise le même header que app.py
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _verify_key(api_key: str = Depends(_API_KEY_HEADER)) -> None:
    if not settings.API_KEY or settings.API_KEY == "changez_moi":
        return
    if not api_key or api_key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="API Key invalide")


# ── Routes ────────────────────────────────────────────────────────────────────

@world_model_router.get("/")
async def get_world_model(_: None = Depends(_verify_key)) -> dict:
    """
    Retourne le snapshot courant du World Model.
    Si un snapshot récent existe en cache (< 30s), le retourne directement.
    Sinon, en construit un nouveau.
    """
    loop = asyncio.get_event_loop()

    # Lecture du cache via executor (SQLite I/O bloquant)
    cached = await loop.run_in_executor(_wm_executor, _store.load)
    if cached:
        ts        = cached.get("meta", {}).get("timestamp", "")
        # Cache valide si < 30 secondes
        try:
            from datetime import datetime, timezone
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
            if age < 30:
                cached["meta"]["cache"] = True
                cached["meta"]["cache_age_s"] = round(age, 1)
                return cached
        except Exception:
            pass

    # Construction d'un nouveau snapshot via executor (CPU + I/O bloquant)
    try:
        snapshot = await loop.run_in_executor(
            _wm_executor, lambda: build_world_model(source="api")
        )
        await loop.run_in_executor(_wm_executor, _store.save, snapshot)
        snapshot["meta"]["cache"] = False
        return snapshot
    except Exception as e:
        logger.error("get_world_model error : %s", e)
        raise HTTPException(500, f"Erreur construction World Model : {e}")


@world_model_router.post("/refresh")
async def refresh_world_model(_: None = Depends(_verify_key)) -> dict:
    """
    Force la reconstruction du World Model (ignore le cache).
    """
    loop = asyncio.get_event_loop()
    try:
        snapshot = await loop.run_in_executor(
            _wm_executor, lambda: build_world_model(source="api_refresh")
        )
        await loop.run_in_executor(_wm_executor, _store.save, snapshot)
        return {
            "status":    "ok",
            "score":     snapshot.get("score", {}),
            "build_ms":  snapshot.get("meta", {}).get("build_ms"),
            "timestamp": snapshot.get("meta", {}).get("timestamp"),
        }
    except Exception as e:
        logger.error("refresh_world_model error : %s", e)
        raise HTTPException(500, f"Erreur refresh World Model : {e}")


@world_model_router.get("/score")
async def get_score(_: None = Depends(_verify_key)) -> dict:
    """Retourne uniquement le score de santé global."""
    loop = asyncio.get_event_loop()
    snapshot = await loop.run_in_executor(_wm_executor, _store.load)
    if not snapshot:
        snapshot = await loop.run_in_executor(
            _wm_executor, lambda: build_world_model(source="api_score")
        )
        await loop.run_in_executor(_wm_executor, _store.save, snapshot)
    return {
        "score":           snapshot.get("score", {}),
        "recommendations": snapshot.get("recommendations", []),
        "timestamp":       snapshot.get("meta", {}).get("timestamp"),
    }


@world_model_router.get("/anomalies")
async def get_anomalies(_: None = Depends(_verify_key)) -> dict:
    """Retourne les anomalies détectées dans le snapshot courant."""
    loop = asyncio.get_event_loop()
    snapshot = await loop.run_in_executor(_wm_executor, _store.load)
    if not snapshot:
        snapshot = await loop.run_in_executor(
            _wm_executor, lambda: build_world_model(source="api_anomalies")
        )
        await loop.run_in_executor(_wm_executor, _store.save, snapshot)
    return {
        "anomalies": snapshot.get("anomalies", []),
        "count":     len(snapshot.get("anomalies", [])),
        "timestamp": snapshot.get("meta", {}).get("timestamp"),
    }


@world_model_router.get("/history")
async def get_history(
    limit: int = Query(default=100, ge=1, le=1000),
    days:  int = Query(default=1,   ge=1, le=30),
    _: None = Depends(_verify_key),
) -> dict:
    """
    Retourne l'historique des snapshots.
    Paramètres : limit (max 1000), days (max 30).
    """
    loop = asyncio.get_event_loop()
    history = await loop.run_in_executor(
        _wm_executor, lambda: _store.get_history(limit=limit, days=days)
    )
    return {
        "history": history,
        "count":   len(history),
        "days":    days,
    }


@world_model_router.get("/trend/{metric}")
async def get_trend(
    metric: str,
    periods: int = Query(default=24, ge=2, le=200),
    _: None = Depends(_verify_key),
) -> dict:
    """
    Retourne la tendance d'une métrique.
    metric : score | cpu | ram | disk | process_ram | anomaly_count
    """
    allowed = {"score", "cpu", "ram", "disk", "process_ram", "anomaly_count"}
    if metric not in allowed:
        raise HTTPException(
            400,
            f"Metrique invalide : {metric!r}. Disponibles : {sorted(allowed)}"
        )
    loop = asyncio.get_event_loop()
    trend = await loop.run_in_executor(
        _wm_executor, lambda: _store.get_trend(metric=metric, periods=periods)
    )
    return {
        "metric":  metric,
        "periods": periods,
        "trend":   trend,
    }


@world_model_router.get("/stats")
async def get_stats(_: None = Depends(_verify_key)) -> dict:
    """Statistiques globales de l'historique sur 7 jours."""
    return _store.stats()


@world_model_router.get("/system")
async def get_system(_: None = Depends(_verify_key)) -> dict:
    """Retourne uniquement les métriques système du snapshot courant."""
    snapshot = _store.load()
    if not snapshot:
        snapshot = build_world_model(source="api_system")
        _store.save(snapshot)
    return {
        "system":    snapshot.get("system",  {}),
        "process":   snapshot.get("process", {}),
        "timestamp": snapshot.get("meta", {}).get("timestamp"),
    }
