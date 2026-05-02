import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from core.config import settings

logger = logging.getLogger("scheduler")

_scheduler: AsyncIOScheduler = None
_agents: dict = {}
_notify_fn = None


def setup(agents: dict, notify_fn):
    """Initialise le scheduler avec les agents et la fonction de notification."""
    global _agents, _notify_fn

    if not isinstance(agents, dict):
        raise ValueError("setup() : 'agents' doit être un dict")
    if notify_fn is None:
        logger.warning("setup() : notify_fn est None — les notifications seront silencieuses")

    _agents    = agents
    _notify_fn = notify_fn


# ==========================
# Tâches existantes
# ==========================

async def _task_self_review():
    """Auto-review nocturne — Néron analyse son code et liste les corrections."""
    logger.info("Scheduler: démarrage auto-review nocturne")
    code_agent = _agents.get("code")
    if not code_agent:
        logger.warning("Scheduler: code_agent non disponible")
        return

    try:
        result   = await code_agent.execute("passe en revue tout le code de Néron", action="self_review")
        metadata = result.metadata or {}
        reports  = metadata.get("reports", [])
        avg      = metadata.get("avg_score", "?")
        now      = datetime.now().strftime("%d/%m/%Y")

        lines = [f"🔍 <b>Auto-review nocturne — {now}</b>\n"]
        lines.append(f"📊 Score moyen : {avg}/100 ({len(reports)} fichiers)\n")

        files_with_issues = [
            r for r in reports
            if r.get("issues") and not r.get("error")
        ]

        if files_with_issues:
            lines.append("⚠️ <b>Corrections à effectuer :</b>\n")
            for r in sorted(files_with_issues, key=lambda x: x.get("quality_score") or 100):
                score  = r.get("quality_score", "?")
                fname  = r.get("file", "?").split("/")[-1]
                issues = r.get("issues", [])
                lines.append(f"📄 <b>{fname}</b> ({score}/100)")
                for issue in issues[:3]:
                    lines.append(f"  • {issue}")
                lines.append("")
        else:
            lines.append("✅ Aucune correction nécessaire")

        msg = "\n".join(lines)
        if len(msg) > 4000:
            msg = msg[:4000] + "\n... (tronqué)"

        if _notify_fn:
            await _notify_fn(msg, "info")
        logger.info(f"Auto-review terminée — {len(files_with_issues)} fichiers avec issues")

    except Exception as e:
        logger.error(f"Erreur auto-review : {e}")


async def _task_memory_cleanup():
    """Nettoyage de la mémoire ancienne."""
    logger.info("Scheduler: nettoyage mémoire")
    memory_agent = _agents.get("memory")
    if not memory_agent:
        return
    try:
        retention = getattr(settings, "MEMORY_RETENTION", 30)
        deleted   = memory_agent.cleanup(days=retention)
        logger.info(f"Mémoire nettoyée : {deleted} entrées supprimées")
    except Exception as e:
        logger.error(f"Erreur nettoyage mémoire : {e}")


async def _task_daily_report():
    """Rapport quotidien envoyé sur Telegram."""
    logger.info("Scheduler: rapport quotidien")
    # FIX: utilisation de _notify_fn (variable globale) au lieu de notify_fn (indéfinie)
    if not _notify_fn:
        return
    try:
        from core.agents.automation.watchdog_agent import get_status, get_health_score
        # FIX: renommé sys_ en sys_status pour éviter la collision avec le module 'sys'
        #      et corriger la NameError sur sys_ vs sys
        sys_status = get_status()
        score      = get_health_score()
        now        = datetime.now().strftime("%d/%m/%Y %H:%M")

        msg = (
            f"📊 <b>Rapport quotidien Néron</b>\n"
            f"🕐 {now}\n\n"
            f"{score['level']} Score santé : {score['score']}/100\n"
            f"🖥 CPU : {sys_status.get('cpu_pct')}% | RAM : {sys_status.get('ram_pct')}%\n"
            f"💾 Disque : {sys_status.get('disk_pct')}%\n"
            f"⚙️ Process : {sys_status.get('process_ram_mb')}MB"
        )
        await _notify_fn(msg, "info")
    except Exception as e:
        logger.error(f"Erreur rapport quotidien : {e}")


async def _task_workspace_cleanup():
    """Nettoie les vieux fichiers du workspace généré."""
    # FIX: ajout d'un try/except — cette tâche n'en avait pas
    try:
        from pathlib import Path
        workspace = Path(getattr(settings, "WORKSPACE_PATH", "/mnt/usb-storage/neron/workspace"))
        if not workspace.exists():
            return
        cutoff  = datetime.now().timestamp() - (7 * 86400)
        cleaned = 0
        for f in workspace.glob("*.py"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                cleaned += 1
        if cleaned:
            logger.info(f"Workspace : {cleaned} fichiers anciens supprimés")
    except Exception as e:
        logger.error(f"Erreur nettoyage workspace : {e}")


# ==========================
# Nouvelle tâche : générateur README
# ==========================

async def _task_generate_readme():
    """Lance le script generateur.py pour mettre à jour le README."""
    # FIX: chemin externalisé depuis settings (avec fallback sur l'ancienne valeur)
    script_path = getattr(
        settings,
        "GENERATOR_SCRIPT_PATH",
        "/mnt/usb-storage/neron/workspace/generateur.py"
    )
    logger.info("Scheduler: lancement du générateur README")
    try:
        # FIX: subprocess.run() bloquant remplacé par asyncio.create_subprocess_exec()
        #      pour ne pas bloquer la boucle d'événements
        proc = await asyncio.create_subprocess_exec(
            "python3", script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info("Générateur README terminé avec succès")
            if stdout:
                logger.debug(stdout.decode())
        else:
            logger.error(f"Erreur générateur README : {stderr.decode()}")
    except Exception as e:
        logger.error(f"Exception lors de l'exécution du générateur : {e}")


# ==========================
# Démarrage du scheduler
# ==========================

def start():
    """Démarre le scheduler avec toutes les tâches configurées."""
    global _scheduler

    # FIX: vérification que setup() a bien été appelé avant start()
    if not _agents:
        logger.warning("Scheduler: start() appelé sans agents — avez-vous appelé setup() ?")

    cfg     = getattr(settings, '_cfg', {}).get("scheduler", {})
    enabled = str(cfg.get("enabled", True)).lower() == "true"

    if not enabled:
        logger.info("Scheduler désactivé dans neron.yaml")
        return

    self_review_hour  = int(cfg.get("self_review_hour", 3))
    daily_report_hour = int(cfg.get("daily_report_hour", 8))
    generate_hour     = int(cfg.get("generate_readme_hour", 2))

    _scheduler = AsyncIOScheduler(timezone="Europe/Paris")

    # Auto-review nocturne
    _scheduler.add_job(
        _task_self_review,
        CronTrigger(hour=self_review_hour, minute=0),
        id="self_review",
        name="Auto-review nocturne",
        replace_existing=True,
    )

    # Rapport quotidien
    _scheduler.add_job(
        _task_daily_report,
        CronTrigger(hour=daily_report_hour, minute=0),
        id="daily_report",
        name="Rapport quotidien",
        replace_existing=True,
    )

    # Nettoyage mémoire
    _scheduler.add_job(
        _task_memory_cleanup,
        CronTrigger(day_of_week="mon", hour=4, minute=0),
        id="memory_cleanup",
        name="Nettoyage mémoire",
        replace_existing=True,
    )

    # Nettoyage workspace
    _scheduler.add_job(
        _task_workspace_cleanup,
        CronTrigger(day_of_week="sun", hour=4, minute=0),
        id="workspace_cleanup",
        name="Nettoyage workspace",
        replace_existing=True,
    )

    # Génération automatique README
    _scheduler.add_job(
        _task_generate_readme,
        CronTrigger(hour=generate_hour, minute=0),
        id="generate_readme",
        name="Génération README automatique",
        replace_existing=True,
    )
    logger.info(f"Tâche générateur README planifiée à {generate_hour}h")

    _scheduler.start()
    logger.info(
        f"Scheduler démarré — "
        f"auto-review {self_review_hour}h | "
        f"rapport {daily_report_hour}h | "
        f"générateur README {generate_hour}h"
    )


def stop():
    """Arrête le scheduler proprement."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler arrêté")


def get_jobs() -> list[dict]:
    """Retourne la liste des tâches planifiées."""
    if not _scheduler:
        return []
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id":       job.id,
            "name":     job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else "N/A",
        })
    return jobs
