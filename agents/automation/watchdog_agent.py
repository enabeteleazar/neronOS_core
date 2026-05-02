# agents/watchdog_agent.py
# Neron Core - Watchdog natif v2 avec détecteur d'anomalies


from __future__ import annotations

import asyncio
import json
import logging
import os
import re as _re
import sqlite3
import time

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List

import psutil
from telegram import Update as TGUpdate
from telegram.ext import (
    Application as TGApplication,
    CommandHandler as TGCommandHandler,
    ContextTypes as TGContextTypes,
)

from core.agents.base_agent import get_logger
from core.config import settings
from core.memory.world_model.world_model import WorldModel

# ── Logger ────────────────────────────────────────────────────────────────────

logger = get_logger("watchdog_agent")

# ── WorldModel — instance unique ──────────────────────────────────────────────

world_model = WorldModel()

# ── Executor pour I/O bloquantes (psutil interval, sqlite) ───────────────────

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="watchdog_io")

# ── Constantes (depuis settings, avec fallbacks) ──────────────────────────────

CHECK_INTERVAL        = settings.WATCHDOG_INTERVAL
CPU_ALERT_PCT         = settings.WATCHDOG_CPU_ALERT
RAM_ALERT_PCT         = settings.WATCHDOG_RAM_ALERT
DISK_ALERT_PCT        = settings.WATCHDOG_DISK_ALERT
ALERT_COOLDOWN        = 300

DB_PATH               = str(settings.MEMORY_DB_PATH)
WATCHDOG_BOT_TOKEN    = settings.WATCHDOG_BOT_TOKEN
WATCHDOG_CHAT_ID      = settings.WATCHDOG_CHAT_ID

RAM_PROCESS_ALERT_MB  = 500
CPU_TEMP_ALERT_C      = settings.WATCHDOG_CPU_TEMP_ALERT
OLLAMA_SILENT_MINUTES = 10
NERON_SILENT_HOURS    = 24
CPU_HIGH_CONSECUTIVE  = 3
AUTO_RESTART_WINDOW   = 300
AUTO_RESTART_THRESHOLD = 3

# ── État global ───────────────────────────────────────────────────────────────

_agents:            dict                 = {}
_notify_fn                               = None
_watchdog_bot_app:  TGApplication | None = None
_task:              asyncio.Task | None  = None
_last_alert:        dict                 = {}
_alerted_anomalies: set                  = set()
_agent_failures:    dict                 = {}
_pending_confirm:   dict                 = {}
_last_ollama_ok:    float                = 0.0
_last_conversation: float                = 0.0
_cpu_high_count:    int                  = 0
_mute_until:        float                = 0.0
_start_time:        float                = time.monotonic()

# ── Setup ─────────────────────────────────────────────────────────────────────

def setup(agents: dict, notify_fn) -> None:
    global _agents, _notify_fn
    _agents    = agents
    _notify_fn = notify_fn


# ── DB Events ─────────────────────────────────────────────────────────────────

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def log_event(
    type_:   str,
    service: str | None  = None,
    message: str | None  = None,
    data:    dict | None = None,
) -> None:
    """Persiste un event en DB (appelable depuis sync et async)."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO events (type, service, message, data) VALUES (?, ?, ?, ?)",
                (type_, service, message, json.dumps(data or {})),
            )
            conn.commit()
    except Exception as e:
        logger.error("log_event error : %s", e)


def read_events(days: int = 7) -> List[Dict]:
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM events "
                "WHERE timestamp > datetime('now', ? || ' days') "
                "ORDER BY timestamp ASC",
                (f"-{days}",),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                try:
                    d["data"] = json.loads(d["data"]) if d["data"] else {}
                except Exception:
                    d["data"] = {}
                result.append(d)
            return result
    except Exception as e:
        logger.error("read_events error : %s", e)
        return []


# ── Helpers système ───────────────────────────────────────────────────────────

def _get_cpu_temp() -> float:
    """Lecture température CPU — instantanée, pas de sleep."""
    try:
        temps = psutil.sensors_temperatures()
        if "coretemp" in temps:
            for e in temps["coretemp"]:
                if "Package" in e.label:
                    return e.current
            return temps["coretemp"][0].current
        if "acpitz" in temps:
            return max(e.current for e in temps["acpitz"])
    except Exception:
        pass
    return 0.0


async def _cpu_percent_async() -> float:
    """
    Mesure CPU non-bloquante via run_in_executor.
    psutil.cpu_percent(interval=1) fait un sleep(1) — inacceptable dans
    l'event loop. On le délègue au ThreadPoolExecutor dédié.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor,
        lambda: psutil.cpu_percent(interval=1),
    )


def _sqlite_ping() -> bool:
    """Vérifie la DB SQLite — appelé en run_in_executor depuis les handlers async."""
    try:
        with get_db() as conn:
            conn.execute("SELECT COUNT(*) FROM memory")
        return True
    except Exception:
        return False


def _sqlite_clear_events() -> None:
    """Efface tous les events — appelé en run_in_executor."""
    with get_db() as conn:
        conn.execute("DELETE FROM events")
        conn.commit()


# ── Notifications ─────────────────────────────────────────────────────────────

async def _notify(msg: str, level: str = "warning", key: str | None = None) -> None:
    """Point central de notification : bot watchdog > notify_fn > log."""
    if _mute_until > time.monotonic():
        logger.debug("Notification muette (mute actif) : %s", msg[:60])
        return

    if key:
        last = _last_alert.get(key, 0)
        if time.monotonic() - last < ALERT_COOLDOWN:
            return
        _last_alert[key] = time.monotonic()

    logger.warning("[watchdog] %s", msg)

    if _watchdog_bot_app and WATCHDOG_CHAT_ID:
        try:
            icons = {"info": "ℹ️", "warning": "⚠️", "alert": "🔴"}
            icon  = icons.get(level, "📢")
            await _watchdog_bot_app.bot.send_message(
                chat_id=WATCHDOG_CHAT_ID,
                text=f"{icon} {msg}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Erreur notification watchdog bot : %s", e)
    elif _notify_fn:
        try:
            await _notify_fn(msg, level)
        except Exception as e:
            logger.error("Erreur notification fallback : %s", e)


# ── Checks système ────────────────────────────────────────────────────────────

def check_agents() -> None:
    """Met à jour l'état de chaque agent dans le WorldModel (sync, rapide)."""
    for name, agent in _agents.items():
        status = "online" if agent else "offline"
        world_model.update("agents", name, {
            "status":    status,
            "timestamp": time.time(),
        })


async def check_system() -> None:
    """
    Collecte les métriques système et met à jour le WorldModel.
    cpu_percent est mesuré via run_in_executor pour ne pas bloquer la loop.
    """
    try:
        cpu  = await _cpu_percent_async()          # non-bloquant
        ram  = psutil.virtual_memory().percent      # instantané
        disk = psutil.disk_usage("/").percent       # instantané
        world_model.update("system", "resources", {
            "cpu":       cpu,
            "ram":       ram,
            "disk":      disk,
            "timestamp": time.time(),
        })
    except Exception as e:
        world_model.update("system", "error", str(e))


async def check_alert() -> None:
    """
    Envoie des alertes si les seuils sont dépassés.
    Async pour pouvoir await _notify() directement (plus de create_task dangereux).
    """
    system    = world_model.get_category("system")
    resources = system.get("resources", {})
    if not resources:
        return

    ram = resources.get("ram", 0)
    if ram > RAM_ALERT_PCT:
        alert = f"RAM usage high: {ram}%"
        world_model.update("alerts", "ram", {"message": alert, "timestamp": time.time()})
        if _notify_fn:
            await _notify(alert, "warning", key="ram_alert")

    cpu = resources.get("cpu", 0)
    if cpu > CPU_ALERT_PCT:
        alert = f"CPU usage high: {cpu}%"
        world_model.update("alerts", "cpu", {"message": alert, "timestamp": time.time()})
        if _notify_fn:
            await _notify(alert, "warning", key="cpu_alert")

    disk = resources.get("disk", 0)
    if disk > DISK_ALERT_PCT:
        alert = f"Disk usage high: {disk}%"
        world_model.update("alerts", "disk", {"message": alert, "timestamp": time.time()})
        if _notify_fn:
            await _notify(alert, "alert", key="disk_alert")


async def _check_system() -> dict:
    """
    Version enrichie pour la boucle principale watchdog :
    collecte métriques + alerte immédiate + log DB.
    """
    cpu      = await _cpu_percent_async()
    ram      = psutil.virtual_memory().percent
    disk     = psutil.disk_usage("/").percent
    proc     = psutil.Process(os.getpid())
    proc_ram = round(proc.memory_info().rss / 1024 / 1024)

    stats = {"cpu": cpu, "ram": ram, "disk": disk, "proc_ram_mb": proc_ram}
    log_event("check", message="system_stats", data=stats)

    if cpu > CPU_ALERT_PCT:
        await _notify(f"⚠️ CPU élevé : {cpu}%", "warning", key="cpu")
        log_event("instability", service="system", message=f"CPU élevé {cpu}%")
    if ram > RAM_ALERT_PCT:
        await _notify(f"⚠️ RAM élevée : {ram}%", "warning", key="ram")
        log_event("instability", service="system", message=f"RAM élevée {ram}%")
    if disk > DISK_ALERT_PCT:
        await _notify(f"🔴 Disque plein : {disk}%", "alert", key="disk")
        log_event("instability", service="system", message=f"Disque plein {disk}%")

    return stats


async def _check_agents() -> List[str]:
    """Vérifie la connectivité des agents et déclenche les auto-restarts si besoin."""
    issues = []
    checks = {
        "llm": ("LLM (Ollama)", "check_connection"),
    }
    for key, (label, method) in checks.items():
        if key not in _agents:
            continue
        try:
            ok = await getattr(_agents[key], method)()
            if not ok:
                issues.append(label)
                log_event("crash", service=label, message=f"{label} inaccessible")
        except Exception as e:
            issues.append(label)
            log_event("crash", service=label, message=str(e))

    if issues:
        msg = "🔴 Agents en erreur : " + ", ".join(issues)
        await _notify(msg, "alert", key="agents_" + "_".join(issues))

    for issue in issues:
        key = issue.split()[0].lower()
        now = time.monotonic()
        _agent_failures.setdefault(key, [])
        _agent_failures[key].append(now)
        _agent_failures[key] = [
            t for t in _agent_failures[key] if now - t < AUTO_RESTART_WINDOW
        ]
        if len(_agent_failures[key]) >= AUTO_RESTART_THRESHOLD:
            agent = _agents.get(key)
            if agent:
                logger.warning(
                    "Auto-restart %s (%d échecs en %ds)",
                    key, AUTO_RESTART_THRESHOLD, AUTO_RESTART_WINDOW,
                )
                try:
                    ok = (
                        await agent.reload()
                        if asyncio.iscoroutinefunction(agent.reload)
                        else agent.reload()
                    )
                    status = "✅ réussi" if ok else "⚠️ incertain"
                    log_event("recovery", service=key, message=f"auto-restart {status}")
                    await _notify(
                        f"🔄 Auto-restart {key} — {status}", "info",
                        key=f"auto_restart_{key}",
                    )
                except Exception as e:
                    logger.error("Auto-restart %s échoué : %s", key, e)
                _agent_failures[key] = []

    return issues


# ── Détecteur d'anomalies ─────────────────────────────────────────────────────

class AnomalyDetector:

    def detect_recurring_crash(self, entries: list) -> List[Dict]:
        anomalies  = []
        crashes    = [e for e in entries if e.get("type") == "crash"]
        by_service = defaultdict(list)
        for c in crashes:
            try:
                ts = datetime.strptime(c["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
                by_service[c["service"]].append(ts.hour)
            except Exception:
                continue
        for service, hours in by_service.items():
            if len(hours) < 3:
                continue
            for hour, count in Counter(hours).items():
                if count >= 3:
                    anomalies.append({
                        "type":    "recurring_crash",
                        "service": service,
                        "message": f"{service} crashe régulièrement vers {hour:02d}h ({count}x)",
                    })
        return anomalies

    def detect_cascade(self, entries: list) -> List[Dict]:
        anomalies = []
        crashes   = sorted(
            [e for e in entries if e.get("type") == "crash"],
            key=lambda x: x["timestamp"],
        )
        i = 0
        while i < len(crashes):
            window = [crashes[i]]
            try:
                t0 = datetime.strptime(crashes[i]["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                i += 1
                continue
            j = i + 1
            while j < len(crashes):
                try:
                    tj = datetime.strptime(crashes[j]["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
                    if (tj - t0).total_seconds() <= 60:
                        window.append(crashes[j])
                        j += 1
                    else:
                        break
                except Exception:
                    j += 1
            if len(window) >= 3:
                services = list({e["service"] for e in window if e.get("service")})
                anomalies.append({
                    "type":     "cascade",
                    "services": services,
                    "message":  f"Cascade : {len(services)} agents tombés en 60s ({', '.join(services)})",
                })
                i = j
            else:
                i += 1
        return anomalies

    def detect_crash_after_restart(self, entries: list) -> List[Dict]:
        anomalies  = []
        recoveries = [e for e in entries if e.get("type") == "recovery"]
        crashes    = [e for e in entries if e.get("type") == "crash"]
        for rec in recoveries:
            service = rec.get("service")
            try:
                t_rec = datetime.strptime(rec["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            for crash in crashes:
                if crash.get("service") != service:
                    continue
                try:
                    t_crash = datetime.strptime(crash["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
                    delta   = (t_crash - t_rec).total_seconds()
                    if 0 < delta < 300:
                        anomalies.append({
                            "type":    "crash_after_restart",
                            "service": service,
                            "message": f"{service} retombé {round(delta)}s après recovery (instabilité)",
                        })
                except Exception:
                    continue
        return anomalies

    def detect_increasing_frequency(self, entries: list) -> List[Dict]:
        anomalies  = []
        crashes    = [e for e in entries if e.get("type") == "crash"]
        by_service = defaultdict(list)
        for c in crashes:
            try:
                ts = datetime.strptime(c["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
                by_service[c.get("service", "unknown")].append(ts)
            except Exception:
                continue
        for service, timestamps in by_service.items():
            if len(timestamps) < 4:
                continue
            timestamps.sort()
            intervals = [
                (timestamps[i + 1] - timestamps[i]).total_seconds()
                for i in range(len(timestamps) - 1)
            ]
            if len(intervals) < 3:
                continue
            mid        = len(intervals) // 2
            avg_first  = sum(intervals[:mid]) / mid
            avg_second = sum(intervals[mid:]) / (len(intervals) - mid)
            if avg_second < avg_first * 0.5 and avg_first > 60:
                anomalies.append({
                    "type":    "increasing_frequency",
                    "service": service,
                    "message": (
                        f"{service} crashe de + en + vite "
                        f"({avg_first / 60:.1f}min → {avg_second / 60:.1f}min)"
                    ),
                })
        return anomalies

    def detect_memory_leak(self, entries: list) -> List[Dict]:
        anomalies  = []
        checks     = [e for e in entries if e.get("type") == "check"]
        ram_values = [
            c.get("data", {}).get("proc_ram_mb", 0)
            for c in checks
            if c.get("data", {}).get("proc_ram_mb", 0) > 0
        ]
        if len(ram_values) < 10:
            return anomalies
        mid        = len(ram_values) // 2
        avg_first  = sum(ram_values[:mid]) / mid
        avg_second = sum(ram_values[mid:]) / (len(ram_values) - mid)
        if avg_second > avg_first * 1.2 and (avg_second - avg_first) > 50:
            anomalies.append({
                "type":    "memory_leak_pattern",
                "service": "neron_core",
                "message": (
                    f"RAM process en hausse "
                    f"{avg_first:.0f}MB → {avg_second:.0f}MB (possible memory leak)"
                ),
            })
        return anomalies

    def compute_health_score(self, entries: list) -> Dict:
        crashes       = [e for e in entries if e.get("type") == "crash"]
        manual        = [e for e in entries if e.get("type") == "manual_required"]
        instabilities = [e for e in entries if e.get("type") == "instability"]
        recoveries    = [e for e in entries if e.get("type") == "recovery"]

        score  = 100.0
        score -= len(crashes)       * 2
        score -= len(manual)        * 15
        score -= len(instabilities) * 5
        score += len(recoveries)    * 1
        score  = max(0.0, min(100.0, score))

        if   score >= 90: level = "🟢 Excellent"
        elif score >= 75: level = "🟡 Bon"
        elif score >= 50: level = "🟠 Dégradé"
        else:             level = "🔴 Critique"

        return {
            "score":                round(score, 1),
            "level":                level,
            "crashes":              len(crashes),
            "manual_interventions": len(manual),
        }

    async def run_analysis(self, notify_fn, days: int = 7) -> List[Dict]:
        entries       = read_events(days=days)
        all_anomalies = []
        for detector in [
            self.detect_recurring_crash,
            self.detect_cascade,
            self.detect_crash_after_restart,
            self.detect_increasing_frequency,
            self.detect_memory_leak,
        ]:
            try:
                all_anomalies.extend(detector(entries))
            except Exception as e:
                logger.error("Détecteur %s : %s", detector.__name__, e)

        for anomaly in all_anomalies:
            key = (
                f"{anomaly['type']}_{anomaly.get('service','')}"
                f"_{anomaly.get('message','')[:30]}"
            )
            if key not in _alerted_anomalies:
                _alerted_anomalies.add(key)
                logger.warning("Anomalie : %s", anomaly["message"])
                if notify_fn:
                    await notify_fn(
                        f"🔍 <b>Anomalie</b>\n{anomaly['message']}", "warning"
                    )
        return all_anomalies


_detector = AnomalyDetector()


# ── Rapports ──────────────────────────────────────────────────────────────────

async def _send_daily_report() -> None:
    if not _watchdog_bot_app or not WATCHDOG_CHAT_ID:
        return
    try:
        sys_    = get_status()
        score   = get_health_score()
        entries = read_events(days=1)
        crashes = len([e for e in entries if e.get("type") == "crash"])
        elapsed = time.monotonic() - _start_time
        h = int(elapsed // 3600)
        m = int((elapsed % 3600) // 60)

        await _watchdog_bot_app.bot.send_message(
            chat_id=WATCHDOG_CHAT_ID,
            text=(
                f"📊 <b>Rapport quotidien Néron</b>\n\n"
                f"{score['level']} — Score: {score['score']}/100\n"
                f"Uptime: {h}h {m}m\n"
                f"Crashs 24h: {crashes}\n"
                f"CPU: {sys_.get('cpu_pct')}% | RAM: {sys_.get('ram_pct')}%\n"
                f"Process: {sys_.get('process_ram_mb')}MB"
            ),
            parse_mode="HTML",
        )
        log_event("check", message="rapport_quotidien")
    except Exception as e:
        logger.error("Rapport quotidien erreur : %s", e)


async def _send_weekly_report() -> None:
    if not _watchdog_bot_app or not WATCHDOG_CHAT_ID:
        return
    try:
        entries    = read_events(days=7)
        crashes    = [e for e in entries if e.get("type") == "crash"]
        recoveries = [e for e in entries if e.get("type") == "recovery"]
        manuals    = [e for e in entries if e.get("type") == "manual_required"]
        instab     = [e for e in entries if e.get("type") == "instability"]
        auto_rst   = [e for e in recoveries if "auto-restart" in e.get("message", "")]
        score      = get_health_score()
        elapsed    = time.monotonic() - _start_time
        days_up    = int(elapsed // 86400)
        hours_up   = int((elapsed % 86400) // 3600)

        crash_by_agent: dict = {}
        for e in crashes:
            svc = e.get("service", "inconnu")
            crash_by_agent[svc] = crash_by_agent.get(svc, 0) + 1
        top_crashes = sorted(crash_by_agent.items(), key=lambda x: x[1], reverse=True)

        lines = [
            "📊 <b>Rapport hebdomadaire Néron</b>",
            f"Semaine du {(datetime.now() - timedelta(days=7)).strftime('%d/%m')} "
            f"au {datetime.now().strftime('%d/%m/%Y')}",
            "",
            f"{score['level']} <b>Score santé : {score['score']}/100</b>",
            "",
            f"⏱ Uptime : {days_up}j {hours_up}h",
            f"🔴 Crashs : {len(crashes)}",
            f"✅ Recoveries : {len(recoveries)}",
            f"🔄 Auto-restarts : {len(auto_rst)}",
            f"🔧 Interventions manuelles : {len(manuals)}",
            f"⚠️ Instabilités : {len(instab)}",
        ]

        if top_crashes:
            lines += ["", "📋 <b>Agents touchés :</b>"]
            for agent, count in top_crashes[:3]:
                lines.append(f"  • {agent} : {count} crash(s)")

        week_cutoff       = datetime.now() - timedelta(days=7)
        entries_14        = read_events(days=14)
        last_week_crashes = len([
            e for e in entries_14
            if e.get("type") == "crash"
            and datetime.strptime(e["timestamp"][:19], "%Y-%m-%d %H:%M:%S") < week_cutoff
        ])
        if last_week_crashes > 0:
            diff  = len(crashes) - last_week_crashes
            trend = f"📈 +{diff}" if diff > 0 else f"📉 {diff}" if diff < 0 else "➡️ stable"
            lines += ["", f"📈 Tendance crashs : {trend} vs semaine précédente"]

        lines += ["", "━━━━━━━━━━━━━━━━━━", "Bonne semaine ! 🚀"]

        await _watchdog_bot_app.bot.send_message(
            chat_id=WATCHDOG_CHAT_ID,
            text="\n".join(lines),
            parse_mode="HTML",
        )
        log_event("check", message="rapport_hebdomadaire")
        logger.info("Rapport hebdomadaire envoyé")
    except Exception as e:
        logger.error("Rapport hebdomadaire erreur : %s", e)


# ── Boucle principale — une seule définition ──────────────────────────────────

async def watchdog_loop() -> None:
    """
    Boucle principale du watchdog.
    - check_agents() : sync, mise à jour WorldModel uniquement
    - check_system() : async, mesure CPU via executor (non-bloquant)
    - check_alert()  : async, await _notify() direct (pas de create_task)
    """
    logger.info("Watchdog démarré — intervalle %ds", CHECK_INTERVAL)
    await asyncio.sleep(10)  # laisser le temps au démarrage de se stabiliser

    while True:
        try:
            check_agents()
            await check_system()
            await check_alert()
        except Exception as e:
            logger.error("Watchdog loop error : %s", e)
            world_model.update("system", "watchdog_error", str(e))
        await asyncio.sleep(CHECK_INTERVAL)


# ── API publique ──────────────────────────────────────────────────────────────

async def send_watchdog_notification(message: str, level: str = "info") -> None:
    """
    Point d'entrée pour les notifications externes (scheduler, app.py).
    Fallback transparent sur _notify() qui gère le bot et le mute.
    """
    await _notify(message, level)


async def start_watchdog() -> None:
    global _task, _start_time
    _start_time = time.monotonic()
    _task       = asyncio.create_task(watchdog_loop())
    logger.info("Watchdog task créée")


async def stop_watchdog() -> None:
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
    logger.info("Watchdog arrêté")


def get_status() -> dict:
    """Snapshot système instantané (cpu_percent sans interval → non-bloquant)."""
    try:
        proc = psutil.Process(os.getpid())
        return {
            "cpu_pct":        psutil.cpu_percent(interval=None),   # pas de sleep
            "ram_pct":        psutil.virtual_memory().percent,
            "ram_used_mb":    round(psutil.virtual_memory().used / 1024 / 1024),
            "disk_pct":       psutil.disk_usage("/").percent,
            "process_ram_mb": round(proc.memory_info().rss / 1024 / 1024),
            "uptime_s":       round(time.monotonic() - _start_time),
        }
    except Exception as e:
        return {"error": str(e)}


def get_health_score() -> dict:
    entries = read_events(days=7)
    return _detector.compute_health_score(entries)


def get_anomalies(days: int = 7) -> list:
    entries = read_events(days=days)
    results = []
    for detector in [
        _detector.detect_recurring_crash,
        _detector.detect_cascade,
        _detector.detect_crash_after_restart,
        _detector.detect_increasing_frequency,
        _detector.detect_memory_leak,
    ]:
        try:
            results.extend(detector(entries))
        except Exception:
            pass
    return results


# ── Bot Watchdog — autorisation ───────────────────────────────────────────────

WATCHDOG_ALLOWED = set(settings.WATCHDOG_CHAT_ID.split(","))


def _wdog_authorized(update) -> bool:
    if not WATCHDOG_ALLOWED or WATCHDOG_ALLOWED == {""}:
        return True
    return str(update.message.chat_id) in WATCHDOG_ALLOWED


# ── Bot Watchdog — commandes ──────────────────────────────────────────────────

async def _wdog_cmd_start(update, context) -> None:
    if not _wdog_authorized(update):
        return
    await update.message.reply_text(
        "🔍 <b>Néron Watchdog v2</b>\n\n"
        "Commandes disponibles:\n"
        "\n📊 <b>Monitoring</b>\n"
        "/status — état complet\n"
        "/score — score de santé 7 jours\n"
        "/anomalies — anomalies détectées\n"
        "/stats — CPU/RAM 24h\n"
        "/trend — tendance 7j vs 7j précédents\n"
        "/uptime — temps de fonctionnement\n"
        "\n📋 <b>Historique</b>\n"
        "/history [agent] — historique events 7j\n"
        "/logs [n] — dernières lignes de log\n"
        "\n📊 <b>Rapports</b>\n"
        "/rapport — rapport quotidien immédiat\n"
        "/hebdo — rapport hebdomadaire immédiat\n"
        "\n🔧 <b>Actions</b>\n"
        "/reload — recharger tous les agents sans redémarrer\n"
        "/restart &lt;agent&gt; — recharger un agent (core, llm, memory)\n"
        "/mute &lt;min&gt; — couper les alertes X minutes\n"
        "/config [clé] [valeur] — voir/modifier les seuils\n"
        "/clear — effacer l'historique events\n",
        parse_mode="HTML",
    )


async def _wdog_cmd_status(update, context) -> None:
    if not _wdog_authorized(update):
        return
    try:
        import httpx as _httpx

        sys_   = get_status()
        score  = get_health_score()

        ok_llm  = await _agents["llm"].check_connection() if "llm" in _agents else False
        ok_core = True

        try:
            async with _httpx.AsyncClient(timeout=3) as c:
                r         = await c.get("http://localhost:11434/api/tags")
                ok_ollama = r.status_code == 200
        except Exception:
            ok_ollama = False

        # Vérification SQLite via executor (non-bloquant)
        loop      = asyncio.get_event_loop()
        ok_memory = await loop.run_in_executor(_executor, _sqlite_ping)

        cpu_temp = _get_cpu_temp()
        temp_str = f" | 🌡️ {cpu_temp:.1f}°C" if cpu_temp > 0 else ""

        lines = [
            "📊 <b>Néron v2 — Watchdog</b>\n",
            f"{'✅' if ok_core   else '🔴'} Core",
            f"{'✅' if ok_ollama else '🔴'} Ollama",
            f"{'✅' if ok_llm    else '🔴'} LLM",
            f"{'✅' if ok_memory else '🔴'} Mémoire",
            f"\n🖥 CPU: {sys_.get('cpu_pct')}%{temp_str} | RAM: {sys_.get('ram_pct')}%",
            f"💾 Disque: {sys_.get('disk_pct')}% | Process: {sys_.get('process_ram_mb')}MB",
            f"\n{score['level']} — Score: {score['score']}/100",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def _wdog_cmd_score(update, context) -> None:
    if not _wdog_authorized(update):
        return
    score = get_health_score()
    await update.message.reply_text(
        f"🏥 <b>Score santé</b>\n\n{score['level']} — {score['score']}/100\n"
        f"Crashs 7j: {score['crashes']}\nInterventions: {score['manual_interventions']}",
        parse_mode="HTML",
    )


async def _wdog_cmd_anomalies(update, context) -> None:
    if not _wdog_authorized(update):
        return
    anomalies = get_anomalies(days=7)
    if not anomalies:
        await update.message.reply_text("✅ Aucune anomalie")
        return
    lines = [f"🔍 <b>Anomalies ({len(anomalies)})</b>\n"]
    for a in anomalies[:10]:
        lines.append(f"• {a.get('message')}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _wdog_cmd_uptime(update, context) -> None:
    if not _wdog_authorized(update):
        return
    elapsed = time.monotonic() - _start_time
    h    = int(elapsed // 3600)
    m    = int((elapsed % 3600) // 60)
    s    = int(elapsed % 60)
    sys_ = get_status()
    await update.message.reply_text(
        f"⏱ <b>Uptime</b>\n\nDémarré il y a {h}h {m}m {s}s\n"
        f"Process RAM: {sys_.get('process_ram_mb')}MB\n"
        f"CPU: {sys_.get('cpu_pct')}% | RAM sys: {sys_.get('ram_pct')}%",
        parse_mode="HTML",
    )


async def _wdog_cmd_history(update, context) -> None:
    if not _wdog_authorized(update):
        return
    service = context.args[0].lower() if context.args else None
    entries = read_events(days=7)
    if service:
        entries = [e for e in entries if e.get("service", "").lower() == service]
    if not entries:
        await update.message.reply_text(
            f"📭 Aucun event{' pour ' + service if service else ''}"
        )
        return
    icons = {"crash": "🔴", "recovery": "✅", "instability": "⚠️", "check": "📊"}
    lines = [f"📋 <b>Historique{' — ' + service if service else ''}</b> ({len(entries)} events 7j)\n"]
    for e in entries[-10:]:
        icon = icons.get(e.get("type", ""), "•")
        ts   = e.get("timestamp", "")[:16]
        svc  = e.get("service", "") or ""
        msg  = e.get("message", "")[:40]
        lines.append(f"{icon} {ts} {svc} {msg}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def _wdog_cmd_logs(update, context) -> None:
    if not _wdog_authorized(update):
        return
    log_path = str(settings.LOGS_DIR / settings.LOG_NERON)
    try:
        n = min(int(context.args[0]) if context.args else 20, 50)
        with open(log_path) as f:
            lines = f.readlines()
        last = [
            line.strip() for line in lines[-n:]
            if any(x in line for x in ["ERROR", "WARNING", "INFO"])
        ]
        text = "\n".join(last[-20:])
        if len(text) > 3500:
            text = "..." + text[-3500:]
        await update.message.reply_text(
            f"📄 <b>Logs ({n} lignes)</b>\n\n<pre>{text}</pre>",
            parse_mode="HTML",
        )
    except FileNotFoundError:
        await update.message.reply_text("❌ Fichier log introuvable")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def _wdog_cmd_stats(update, context) -> None:
    if not _wdog_authorized(update):
        return
    try:
        entries              = read_events(days=1)
        checks               = [e for e in entries if e.get("type") == "check"]
        cpu_vals, ram_vals, labels = [], [], []
        for e in checks[-12:]:
            msg   = e.get("message", "")
            cpu_m = _re.search(r"CPU=([\d.]+)%", msg)
            ram_m = _re.search(r"RAM=([\d.]+)%", msg)
            ts    = e.get("timestamp", "")[11:16]
            if cpu_m and ram_m:
                cpu_vals.append(float(cpu_m.group(1)))
                ram_vals.append(float(ram_m.group(1)))
                labels.append(ts)
        if len(cpu_vals) < 2:
            await update.message.reply_text("📊 Pas encore assez de données")
            return

        def bar(val, width=15):
            filled = int(val / 100 * width)
            return "█" * filled + "░" * (width - filled)

        temp_now = _get_cpu_temp()
        temp_str = f" | 🌡️ {temp_now:.1f}°C" if temp_now > 0 else ""
        lines    = [f"📊 <b>Stats CPU / RAM (24h){temp_str}</b>\n<pre>"]
        for ts, cpu, ram in zip(labels, cpu_vals, ram_vals):
            lines.append(f"{ts} CPU {bar(cpu)} {cpu:.1f}%")
            lines.append(f"     RAM {bar(ram)} {ram:.1f}%")
            lines.append("")
        lines.append("</pre>")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def _wdog_cmd_trend(update, context) -> None:
    if not _wdog_authorized(update):
        return
    try:
        entries_14 = read_events(days=14)
        now        = datetime.now()
        week_ago   = now - timedelta(days=7)
        this_week  = [
            e for e in entries_14
            if datetime.strptime(e["timestamp"][:19], "%Y-%m-%d %H:%M:%S") >= week_ago
        ]
        last_week  = [
            e for e in entries_14
            if datetime.strptime(e["timestamp"][:19], "%Y-%m-%d %H:%M:%S") < week_ago
        ]

        def ct(lst, t):
            return len([e for e in lst if e.get("type") == t])

        def ti(a, b):
            d = a - b
            return f"📈 +{d}" if d > 0 else f"📉 {d}" if d < 0 else "➡️ ="

        score = get_health_score()
        lines = [
            "📈 <b>Tendance 7j vs 7j précédents</b>\n",
            f"🔴 Crashs       : {ct(this_week,'crash')} {ti(ct(this_week,'crash'),ct(last_week,'crash'))}",
            f"✅ Recoveries   : {ct(this_week,'recovery')} {ti(ct(this_week,'recovery'),ct(last_week,'recovery'))}",
            f"🔧 Interventions: {ct(this_week,'manual_required')} {ti(ct(this_week,'manual_required'),ct(last_week,'manual_required'))}",
            f"\n{score['level']} Score: {score['score']}/100",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def _wdog_cmd_rapport(update, context) -> None:
    if not _wdog_authorized(update):
        return
    await update.message.reply_text("📊 Génération du rapport...")
    await _send_daily_report()


async def _wdog_cmd_rapport_hebdo(update, context) -> None:
    if not _wdog_authorized(update):
        return
    await update.message.reply_text("📊 Génération du rapport hebdomadaire...")
    await _send_weekly_report()


async def _wdog_cmd_config(update, context) -> None:
    if not _wdog_authorized(update):
        return
    import sys as _sys
    mod        = _sys.modules[__name__]
    config_map = {
        "cpu":      "CPU_ALERT_PCT",
        "ram":      "RAM_ALERT_PCT",
        "disk":     "DISK_ALERT_PCT",
        "procram":  "RAM_PROCESS_ALERT_MB",
        "ollama":   "OLLAMA_SILENT_MINUTES",
        "silence":  "NERON_SILENT_HOURS",
        "interval": "CHECK_INTERVAL",
        "cooldown": "ALERT_COOLDOWN",
        "temp":     "CPU_TEMP_ALERT_C",
    }
    if not context.args:
        descriptions = {
            "cpu":      "% CPU système avant alerte",
            "ram":      "% RAM système avant alerte",
            "disk":     "% disque avant alerte",
            "procram":  "MB RAM process Néron avant alerte",
            "ollama":   "min sans réponse Ollama avant alerte",
            "silence":  "h sans conversation avant alerte",
            "interval": "s entre chaque check watchdog",
            "cooldown": "s minimum entre 2 alertes identiques",
            "temp":     "°C température CPU avant alerte",
        }
        lines = ["⚙️ <b>Configuration Watchdog</b>\n"]
        for k, desc in descriptions.items():
            val = getattr(mod, config_map[k])
            lines.append(f"<b>{k}</b> = {val}\n  └ {desc}")
        lines.append("\n📝 Usage: /config &lt;clé&gt; &lt;valeur&gt;")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /config <clé> <valeur>")
        return
    key = context.args[0].lower()
    if key not in config_map:
        await update.message.reply_text(
            f"❌ Clé inconnue: {key}\nDisponibles: {', '.join(config_map)}"
        )
        return
    try:
        value   = float(context.args[1])
        var     = config_map[key]
        old_val = getattr(mod, var)
        setattr(mod, var, type(old_val)(value))
        await update.message.reply_text(
            f"✅ <b>{var}</b>: {old_val} → {getattr(mod, var)}", parse_mode="HTML"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def _wdog_cmd_mute(update, context) -> None:
    if not _wdog_authorized(update):
        return
    global _mute_until
    if not context.args:
        if _mute_until > time.monotonic():
            rem = int((_mute_until - time.monotonic()) / 60)
            await update.message.reply_text(
                f"🔕 Alertes coupées encore {rem} min\n/mute 0 pour réactiver"
            )
        else:
            await update.message.reply_text("🔔 Alertes actives\nUsage: /mute <minutes>")
        return
    try:
        minutes = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Valeur invalide")
        return
    if minutes == 0:
        _mute_until = 0.0
        await update.message.reply_text("🔔 Alertes réactivées")
    else:
        _mute_until = time.monotonic() + minutes * 60
        await update.message.reply_text(f"🔕 Alertes coupées pendant {minutes} min")


async def _wdog_cmd_clear(update, context) -> None:
    if not _wdog_authorized(update):
        return
    chat_id = str(update.message.chat_id)
    _pending_confirm[chat_id] = {
        "action":  "clear_events",
        "expires": time.monotonic() + 30,
    }
    await update.message.reply_text(
        "⚠️ <b>Effacer tous les events ?</b>\n\nIrréversible.\n"
        "/confirm | /cancel\n<i>(expire 30s)</i>",
        parse_mode="HTML",
    )


async def _wdog_cmd_cancel(update, context) -> None:
    if not _wdog_authorized(update):
        return
    chat_id = str(update.message.chat_id)
    if chat_id in _pending_confirm:
        del _pending_confirm[chat_id]
        await update.message.reply_text("✅ Action annulée.")
    else:
        await update.message.reply_text("ℹ️ Aucune action en attente.")


async def _wdog_cmd_confirm(update, context) -> None:
    if not _wdog_authorized(update):
        return
    chat_id = str(update.message.chat_id)
    pending = _pending_confirm.get(chat_id)
    if not pending:
        await update.message.reply_text("❌ Aucune action en attente.")
        return
    if time.monotonic() > pending["expires"]:
        del _pending_confirm[chat_id]
        await update.message.reply_text("⏰ Confirmation expirée. Recommencez.")
        return
    action = pending.pop("action")
    del _pending_confirm[chat_id]

    if action == "restart_core":
        await update.message.reply_text("🔄 Redémarrage de Néron Core via systemd...")
        log_event("restart", service="core", message="restart systemd via Telegram watchdog")
        await _notify("🔄 Redémarrage Core déclenché via Telegram", "warning")
        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", "restart", "neron",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            await update.message.reply_text(
                f"❌ Échec systemctl restart : {stderr.decode()[:200]}"
            )

    elif action == "clear_events":
        try:
            # SQLite via executor — non-bloquant dans le handler async
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(_executor, _sqlite_clear_events)
            await update.message.reply_text("✅ Historique events effacé")
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")


async def _wdog_cmd_reload(update, context) -> None:
    """Recharge tous les agents sans redémarrer le process."""
    if not _wdog_authorized(update):
        return
    await update.message.reply_text("🔄 Rechargement des agents en cours...")

    results = []
    for name, agent in _agents.items():
        try:
            ok = (
                await agent.reload()
                if asyncio.iscoroutinefunction(agent.reload)
                else agent.reload()
            )
            status = "✅" if ok else "⚠️"
            results.append(f"{status} {name}")
            log_event("recovery", service=name, message="reload via /reload watchdog")
        except Exception as e:
            results.append(f"❌ {name} : {e}")
            log_event("crash", service=name, message=f"reload échoué: {e}")

    lines = ["🔄 <b>Reload terminé</b>\n"] + results
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    logger.info("Reload agents terminé : %s", results)


async def _wdog_cmd_restart(update, context) -> None:
    if not _wdog_authorized(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /restart <agent>\nAgents: core, llm, memory"
        )
        return
    agent_name = context.args[0].lower()
    if agent_name == "core":
        chat_id = str(update.message.chat_id)
        _pending_confirm[chat_id] = {
            "action":  "restart_core",
            "expires": time.monotonic() + 30,
        }
        await update.message.reply_text(
            "⚠️ <b>Redémarrage complet de Néron Core</b>\n\n"
            "/confirm | /cancel\n<i>(expire dans 30s)</i>",
            parse_mode="HTML",
        )
        return
    agent = _agents.get(agent_name)
    if not agent:
        await update.message.reply_text(
            f"❌ Agent inconnu: {agent_name}\nDisponibles: core, llm, memory"
        )
        return
    await update.message.reply_text(f"🔄 Rechargement de {agent_name}...")
    try:
        ok = (
            await agent.reload()
            if asyncio.iscoroutinefunction(agent.reload)
            else agent.reload()
        )
        if ok:
            log_event("recovery", service=agent_name, message="reload manuel via Telegram")
            await update.message.reply_text(f"✅ {agent_name} rechargé avec succès")
        else:
            await update.message.reply_text(f"⚠️ {agent_name} rechargé mais non confirmé")
    except Exception as e:
        log_event("crash", service=agent_name, message=f"reload échoué: {e}")
        await update.message.reply_text(f"❌ {e}")


# ── Bot lifecycle ─────────────────────────────────────────────────────────────

async def start_watchdog_bot() -> None:
    global _watchdog_bot_app
    token = settings.WATCHDOG_BOT_TOKEN
    if not token:
        logger.warning("WATCHDOG_BOT_TOKEN non défini — bot watchdog désactivé")
        return

    _watchdog_bot_app = TGApplication.builder().token(token).build()

    for cmd, handler in [
        ("start",     _wdog_cmd_start),
        ("help",      _wdog_cmd_start),
        ("status",    _wdog_cmd_status),
        ("score",     _wdog_cmd_score),
        ("anomalies", _wdog_cmd_anomalies),
        ("reload",    _wdog_cmd_reload),
        ("restart",   _wdog_cmd_restart),
        ("confirm",   _wdog_cmd_confirm),
        ("cancel",    _wdog_cmd_cancel),
        ("config",    _wdog_cmd_config),
        ("mute",      _wdog_cmd_mute),
        ("clear",     _wdog_cmd_clear),
        ("uptime",    _wdog_cmd_uptime),
        ("logs",      _wdog_cmd_logs),
        ("stats",     _wdog_cmd_stats),
        ("trend",     _wdog_cmd_trend),
        ("rapport",   _wdog_cmd_rapport),
        ("hebdo",     _wdog_cmd_rapport_hebdo),
        ("history",   _wdog_cmd_history),
    ]:
        _watchdog_bot_app.add_handler(TGCommandHandler(cmd, handler))

    await _watchdog_bot_app.initialize()
    await _watchdog_bot_app.start()
    await _watchdog_bot_app.updater.start_polling()

    if WATCHDOG_CHAT_ID:
        try:
            await _watchdog_bot_app.bot.send_message(
                chat_id=WATCHDOG_CHAT_ID,
                text="🟢 <b>Néron v2 démarré</b>\nTous les agents sont en ligne.",
                parse_mode="HTML",
            )
        except Exception:
            pass

    logger.info("Bot Watchdog démarré")


async def stop_watchdog_bot() -> None:
    global _watchdog_bot_app
    if not _watchdog_bot_app:
        return
    try:
        if WATCHDOG_CHAT_ID:
            await _watchdog_bot_app.bot.send_message(
                chat_id=WATCHDOG_CHAT_ID,
                text="🔴 <b>Néron v2 arrêté</b>",
                parse_mode="HTML",
            )
    except Exception:
        pass
    try:
        await _watchdog_bot_app.updater.stop()
        await _watchdog_bot_app.stop()
        await _watchdog_bot_app.shutdown()
        logger.info("Bot Watchdog arrêté")
    except Exception as e:
        logger.error("Erreur stop_watchdog_bot : %s", e)
    finally:
        _watchdog_bot_app = None
