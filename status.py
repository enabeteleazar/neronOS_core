# core/status.py
# Néron — État système et santé

from __future__ import annotations

import psutil
from agents.watchdog_agent import get_health_score, get_status as _get_status
from modules.scheduler import get_jobs


def check_all_improved() -> dict:
    """
    Retourne un résumé de l'état du système et des services.
    """
    sys_stats = _get_status()
    health    = get_health_score()
    jobs      = get_jobs()

    next_jobs = []
    for j in jobs:
        next_jobs.append({
            "name": j["name"],
            "next_run": j["next_run"][:16] if j.get("next_run") else "—"
        })

    return {
        "system": {
            "cpu_pct": sys_stats.get("cpu_pct", psutil.cpu_percent()),
            "ram_pct": sys_stats.get("ram_pct", psutil.virtual_memory().percent),
            "disk_pct": sys_stats.get("disk_pct", psutil.disk_usage("/").percent),
            "process_ram_mb": sys_stats.get("process_ram_mb", 0),
        },
        "health": {
            "score": health.get("score", 0),
            "level": health.get("level", "unknown")
        },
        "jobs": next_jobs
    }


def get_summary_text() -> str:
    """
    Génère un texte prêt pour Telegram ou interface.
    """
    data = check_all_improved()
    sys_ = data["system"]
    score = data["health"]
    jobs = data["jobs"]

    jobs_text = "\n".join(f"  • {j['name']} → {j['next_run']}" for j in jobs)

    return (
        f"📊 <b>Néron — Status</b>\n\n"
        f"{score['level']} Score : {score['score']}/100\n"
        f"🖥 CPU : {sys_['cpu_pct']}% | RAM : {sys_['ram_pct']}%\n"
        f"💾 Disque : {sys_['disk_pct']}%\n"
        f"⚙️ Process : {sys_['process_ram_mb']}MB\n\n"
        f"⏰ <b>Prochaines tâches</b>\n{jobs_text}"
    )
