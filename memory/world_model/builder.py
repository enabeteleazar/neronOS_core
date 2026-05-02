# core/world_model/builder.py
# World Model Builder — Source de vérité centrale de Néron.
# Collecte toutes les sources et construit un snapshot cohérent.

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import psutil

logger = logging.getLogger("world_model.builder")

# Seuils par défaut — peuvent être surchargés depuis settings
_THRESHOLDS = {
    "cpu_degraded":  80.0,
    "ram_degraded":  85.0,
    "disk_critical": 90.0,
    "llm_latency_degraded_ms": 1200,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status(value: float, warn: float, crit: float | None = None) -> str:
    if crit and value >= crit:
        return "critical"
    if value >= warn:
        return "degraded"
    return "normal"


# ── Collecteurs ───────────────────────────────────────────────────────────────

def _collect_system() -> dict:
    """Métriques système via psutil."""
    try:
        cpu_pct = psutil.cpu_percent(interval=0.5)
        ram     = psutil.virtual_memory()
        disk    = psutil.disk_usage("/")
        load    = os.getloadavg()
        net     = psutil.net_io_counters()

        return {
            "cpu": {
                "current": round(cpu_pct, 1),
                "status":  _status(cpu_pct, _THRESHOLDS["cpu_degraded"]),
            },
            "ram": {
                "current":      round(ram.percent, 1),
                "available_mb": round(ram.available / 1024 / 1024),
                "status":       _status(ram.percent, _THRESHOLDS["ram_degraded"]),
            },
            "disk": {
                "usage":   round(disk.percent, 1),
                "free_gb": round(disk.free / 1024 / 1024 / 1024, 1),
                "status":  _status(disk.percent, _THRESHOLDS["disk_critical"], 95.0),
            },
            "load": {
                "1m":  round(load[0], 2),
                "5m":  round(load[1], 2),
                "15m": round(load[2], 2),
            },
            "network": {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
            },
        }
    except Exception as e:
        logger.warning("_collect_system error : %s", e)
        return {"error": str(e)}


def _collect_process() -> dict:
    """Métriques du process Néron."""
    try:
        proc    = psutil.Process(os.getpid())
        mem     = proc.memory_info()
        return {
            "ram_mb":    round(mem.rss / 1024 / 1024),
            "cpu_pct":   round(proc.cpu_percent(interval=None), 1),
            "threads":   proc.num_threads(),
            "open_fds":  proc.num_fds(),
            "pid":       proc.pid,
        }
    except Exception as e:
        logger.warning("_collect_process error : %s", e)
        return {"error": str(e)}


def _collect_agents() -> dict:
    """État des agents depuis watchdog_agent."""
    try:
        from core.agents.automation.watchdog_agent import get_status, get_health_score
        sys_status = get_status()
        score      = get_health_score()

        return {
            "core": {
                "status":    "healthy",
                "uptime_s":  sys_status.get("uptime_s", 0),
                "ram_mb":    sys_status.get("process_ram_mb", 0),
            },
            "score": {
                "global":               score.get("score", 0),
                "level":                score.get("level", "unknown"),
                "crashes_7d":           score.get("crashes", 0),
                "manual_interventions": score.get("manual_interventions", 0),
            },
        }
    except Exception as e:
        logger.warning("_collect_agents error : %s", e)
        return {"error": str(e)}


def _collect_anomalies() -> list[dict]:
    """Anomalies détectées par le watchdog."""
    try:
        from core.agents.automation.watchdog_agent import get_anomalies
        raw = get_anomalies(days=1)
        return [
            {
                "type":     a.get("type", "unknown"),
                "service":  a.get("service", ""),
                "message":  a.get("message", ""),
                "severity": _anomaly_severity(a.get("type", "")),
            }
            for a in raw[:10]  # max 10 anomalies dans le snapshot
        ]
    except Exception as e:
        logger.warning("_collect_anomalies error : %s", e)
        return []


def _collect_prometheus() -> dict:
    """Métriques Prometheus depuis le registry local."""
    try:
        from prometheus_client import REGISTRY
        metrics: dict = {}
        for metric in REGISTRY.collect():
            for sample in metric.samples:
                if sample.name.startswith("neron_"):
                    metrics[sample.name] = sample.value
        return metrics
    except Exception as e:
        logger.warning("_collect_prometheus error : %s", e)
        return {}


def _anomaly_severity(anomaly_type: str) -> str:
    high = {"cascade", "crash_after_restart", "memory_leak_pattern"}
    if anomaly_type in high:
        return "high"
    return "medium"


def _compute_global_score(system: dict, agents: dict, anomalies: list) -> dict:
    """Calcule un score global de santé du World Model."""
    score = 100.0

    # Pénalités système
    cpu_val  = system.get("cpu",  {}).get("current", 0)
    ram_val  = system.get("ram",  {}).get("current", 0)
    disk_val = system.get("disk", {}).get("usage",   0)

    if cpu_val  > 80: score -= 10
    if cpu_val  > 90: score -= 10
    if ram_val  > 85: score -= 10
    if disk_val > 90: score -= 15

    # Pénalités agents
    agent_score = agents.get("score", {}).get("global", 100)
    score = min(score, agent_score)

    # Pénalités anomalies
    for a in anomalies:
        if a.get("severity") == "high":
            score -= 15
        else:
            score -= 5

    score = max(0.0, min(100.0, score))

    if   score >= 90: level = "healthy"
    elif score >= 70: level = "degraded"
    elif score >= 50: level = "warning"
    else:             level = "critical"

    return {
        "score": round(score, 1),
        "level": level,
        "trend": "stable",  # sera enrichi par store.py via historique
    }


# ── Point d'entrée principal ──────────────────────────────────────────────────

def build_world_model(source: str = "builder") -> dict:
    """
    Construit un snapshot complet du World Model.
    Appelé par l'API, le scheduler, ou le watchdog.
    """
    t_start = time.monotonic()

    system    = _collect_system()
    process   = _collect_process()
    agents    = _collect_agents()
    anomalies = _collect_anomalies()
    prom      = _collect_prometheus()
    score     = _compute_global_score(system, agents, anomalies)

    build_ms = round((time.monotonic() - t_start) * 1000, 2)

    return {
        "meta": {
            "version":    "1.0",
            "timestamp":  _utc_now(),
            "source":     source,
            "build_ms":   build_ms,
            "confidence": _compute_confidence(system, agents),
        },
        "system":     system,
        "process":    process,
        "agents":     agents,
        "anomalies":  anomalies,
        "prometheus": prom,
        "score":      score,
        "recommendations": _build_recommendations(system, agents, anomalies),
    }


def _compute_confidence(system: dict, agents: dict) -> float:
    """Confiance du snapshot — baisse si des collecteurs ont échoué."""
    confidence = 1.0
    if "error" in system:
        confidence -= 0.3
    if "error" in agents:
        confidence -= 0.3
    return round(max(0.0, confidence), 2)


def _build_recommendations(
    system: dict, agents: dict, anomalies: list
) -> list[dict]:
    """Génère des recommandations d'action basées sur l'état courant."""
    recs: list[dict] = []

    cpu_val  = system.get("cpu",  {}).get("current", 0)
    ram_val  = system.get("ram",  {}).get("current", 0)
    disk_val = system.get("disk", {}).get("usage",   0)

    if cpu_val > 85:
        recs.append({
            "action":   "monitor_cpu",
            "reason":   f"CPU eleve : {cpu_val}%",
            "priority": "high" if cpu_val > 90 else "medium",
        })
    if ram_val > 85:
        recs.append({
            "action":   "check_memory",
            "reason":   f"RAM elevee : {ram_val}%",
            "priority": "high" if ram_val > 92 else "medium",
        })
    if disk_val > 88:
        recs.append({
            "action":   "free_disk_space",
            "reason":   f"Disque presque plein : {disk_val}%",
            "priority": "high" if disk_val > 93 else "medium",
        })
    for a in anomalies:
        if a.get("severity") == "high":
            recs.append({
                "action":   "investigate_anomaly",
                "reason":   a.get("message", ""),
                "priority": "high",
            })

    return recs
