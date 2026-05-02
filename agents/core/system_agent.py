from __future__ import annotations

from core.agents.base_agent import get_logger

logger = get_logger("system_agent")


class SystemAgent:
    async def run(self, query: str) -> str:
        # Import local pour éviter les cycles
        from core.agents.automation.watchdog_agent import (
            get_status,
            get_health_score,
            get_anomalies,
        )

        q = query.lower()

        try:
            if any(w in q for w in ["cpu", "ram", "memoire", "ressource", "process"]):
                return self._format_resources(get_status())

            if any(w in q for w in ["anomalie", "probleme", "erreur", "crash"]):
                return self._format_anomalies(get_anomalies(days=7))

            return self._format_health(get_status(), get_health_score())

        except Exception as e:
            logger.error("SystemAgent error: %s", e)
            return "Impossible de récupérer l'état du système."

    def _format_resources(self, sys_: dict) -> str:
        return (
            f"CPU : {sys_.get('cpu_pct')}% | "
            f"RAM : {sys_.get('ram_pct')}% ({sys_.get('ram_used_mb')} MB) | "
            f"Disque : {sys_.get('disk_pct')}% | "
            f"Process Néron : {sys_.get('process_ram_mb')} MB"
        )

    def _format_health(self, sys_: dict, score: dict) -> str:
        return (
            f"{score['level']} — Score santé : {score['score']}/100\n"
            f"CPU : {sys_.get('cpu_pct')}% | RAM : {sys_.get('ram_pct')}% | "
            f"Disque : {sys_.get('disk_pct')}%"
        )

    def _format_anomalies(self, anomalies: list) -> str:
        if not anomalies:
            return "✅ Aucune anomalie détectée sur les 7 derniers jours."
        lines = [f"🔍 {len(anomalies)} anomalie(s) détectée(s) :"]
        for a in anomalies[:5]:
            lines.append(f"  • {a.get('message', '?')}")
        return "\n".join(lines)
