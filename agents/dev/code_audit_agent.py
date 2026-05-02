# agents/code_audit_agent.py
# Néron — Agent d'audit de code autonome.
# Façade légère vers CodeAgent._analyze() avec support multi-fichiers.

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

import httpx

from core.agents.base_agent import BaseAgent, AgentResult
from core.config import settings

# ── Constantes ────────────────────────────────────────────────────────────────

OLLAMA_HOST  = settings.OLLAMA_HOST
OLLAMA_MODEL = getattr(settings, "CODE_AGENT_MODEL", settings.OLLAMA_MODEL)
LLM_TIMEOUT  = settings.LLM_TIMEOUT

_NERON_ROOT = Path(__file__).parent.parent.resolve()
_EXCLUDE_DIRS = {"__pycache__", "venv", ".git", "data", "docs", "scripts"}

# Concurrence max pour les appels LLM parallèles
_AUDIT_CONCURRENCY = 4

_PROMPT_AUDIT = """Tu es un expert en qualité de code Python.
Analyse ce fichier source et retourne un rapport JSON avec exactement ce format (rien d'autre) :
{
  "quality_score": <0-100>,
  "issues": ["issue1", "issue2"],
  "suggestions": ["suggestion1", "suggestion2"],
  "has_docstrings": <true|false>,
  "pep8_ok": <true|false>
}"""


# ── Agent ─────────────────────────────────────────────────────────────────────


class CodeAuditAgent(BaseAgent):
    """
    Agent d'audit de code de Néron.

    Actions disponibles via execute() :
      - "audit_file"   : audite un fichier Python spécifique
      - "audit_folder" : audite tous les fichiers .py d'un dossier
      - "audit_all"    : audite tous les fichiers source de Néron
    """

    def __init__(self) -> None:
        super().__init__(name="code_audit_agent")
        self.logger.info(
            "CodeAuditAgent init — Ollama : %s | modèle : %s",
            OLLAMA_HOST, OLLAMA_MODEL,
        )

    # ── Point d'entrée principal ──────────────────────────────────────────

    async def execute(self, query: str, **kwargs: Any) -> AgentResult:
        """
        Dispatch selon kwargs["action"].
        - action="audit_file"   : kwargs["path"] requis
        - action="audit_folder" : kwargs["folder"] requis
        - action="audit_all"    : audite _NERON_ROOT complet
        """
        start  = self._timer()
        action = kwargs.get("action", "audit_all")
        path   = kwargs.get("path", "")
        folder = kwargs.get("folder", "")

        self.logger.info("action=%r path=%r folder=%r", action, path, folder)

        try:
            if action == "audit_file":
                return await self._audit_file(path, start)
            elif action == "audit_folder":
                return await self._audit_folder(folder, start)
            else:
                return await self._audit_all(start)
        except ValueError as e:
            return self._failure(str(e), latency_ms=self._elapsed_ms(start))
        except Exception as e:
            self.logger.exception("Exception inattendue : %s", e)
            return self._failure(
                f"Erreur inattendue : {e}", latency_ms=self._elapsed_ms(start)
            )

    # ── Actions ───────────────────────────────────────────────────────────

    async def _audit_file(self, path: str, start: float) -> AgentResult:
        """Audite un fichier Python et retourne un rapport qualité."""
        if not path:
            return self._failure(
                "Chemin requis pour 'audit_file'",
                latency_ms=self._elapsed_ms(start),
            )

        target = (_NERON_ROOT / path).resolve()
        if not str(target).startswith(str(_NERON_ROOT)):
            return self._failure(
                f"Chemin non autorisé : {path!r}",
                latency_ms=self._elapsed_ms(start),
            )
        if not target.exists():
            return self._failure(
                f"Fichier introuvable : {path}",
                latency_ms=self._elapsed_ms(start),
            )

        report = await self._analyze_file(target)
        summary = self._format_report_summary(path, report)

        return self._success(
            summary,
            metadata={"action": "audit_file", "path": str(target), "report": report},
            latency_ms=self._elapsed_ms(start),
        )

    async def _audit_folder(self, folder: str, start: float) -> AgentResult:
        """Audite tous les fichiers .py d'un dossier."""
        if not folder:
            return self._failure(
                "Dossier requis pour 'audit_folder'",
                latency_ms=self._elapsed_ms(start),
            )

        # FIX: chemin résolu depuis _NERON_ROOT — plus de dépendance au cwd
        target_dir = (_NERON_ROOT / folder).resolve()
        if not target_dir.exists() or not target_dir.is_dir():
            return self._failure(
                f"Dossier introuvable : {folder}",
                latency_ms=self._elapsed_ms(start),
            )

        files = self._collect_py_files(target_dir)
        if not files:
            return self._success(
                f"Aucun fichier .py trouvé dans {folder}",
                metadata={"action": "audit_folder", "folder": str(target_dir)},
                latency_ms=self._elapsed_ms(start),
            )

        reports = await self._run_parallel_audit(files)
        summary = self._format_global_summary(folder, reports)

        return self._success(
            summary,
            metadata={
                "action":  "audit_folder",
                "folder":  str(target_dir),
                "reports": reports,
                **self._compute_stats(reports),
            },
            latency_ms=self._elapsed_ms(start),
        )

    async def _audit_all(self, start: float) -> AgentResult:
        """Audite tous les fichiers Python de Néron (hors exclusions)."""
        files   = self._collect_py_files(_NERON_ROOT)
        reports = await self._run_parallel_audit(files)
        summary = self._format_global_summary("Néron complet", reports)

        return self._success(
            summary,
            metadata={
                "action":  "audit_all",
                "reports": reports,
                **self._compute_stats(reports),
            },
            latency_ms=self._elapsed_ms(start),
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _run_parallel_audit(self, files: list[Path]) -> list[dict]:
        """Lance les audits en parallèle avec limite de concurrence."""
        sem = asyncio.Semaphore(_AUDIT_CONCURRENCY)
        return list(await asyncio.gather(
            *[self._audit_with_sem(f, sem) for f in files],
            return_exceptions=False,
        ))

    async def _audit_with_sem(self, f: Path, sem: asyncio.Semaphore) -> dict:
        """Audite un fichier sous semaphore."""
        async with sem:
            try:
                rel    = f.relative_to(_NERON_ROOT)
                report = await self._analyze_file(f)
                return {"file": str(rel), **report}
            except Exception as e:
                self.logger.warning("Erreur audit %s : %s", f, e)
                return {"file": str(f), "error": str(e)}

    async def _analyze_file(self, path: Path) -> dict:
        """Appelle le LLM pour analyser un fichier. Retourne le rapport JSON."""
        code   = path.read_text(encoding="utf-8")
        prompt = f"Fichier : {path.name}\n\nCode :\n{code[:3000]}"
        raw    = await self._llm_call(_PROMPT_AUDIT, prompt)
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {"quality_score": None, "issues": [], "suggestions": [], "raw": raw}

    def _collect_py_files(self, root: Path) -> list[Path]:
        """Liste les fichiers .py en excluant les dossiers système."""
        files = []
        for p in root.rglob("*.py"):
            if not any(excl in p.parts for excl in _EXCLUDE_DIRS):
                files.append(p)
        return sorted(files)

    def _compute_stats(self, reports: list[dict]) -> dict:
        """Calcule les statistiques globales d'un ensemble de rapports."""
        scored = [
            r for r in reports
            if isinstance(r.get("quality_score"), (int, float))
        ]
        avg = (
            round(sum(r["quality_score"] for r in scored) / len(scored), 1)
            if scored else None
        )
        return {
            "files_count":  len(reports),
            "avg_score":    avg,
            "total_issues": sum(len(r.get("issues", [])) for r in reports),
        }

    def _format_report_summary(self, path: str, report: dict) -> str:
        """Formate le résumé d'un rapport fichier unique."""
        return (
            f"📊 Audit de `{path}`\n"
            f"Score : {report.get('quality_score', '?')}/100\n"
            f"Issues : {len(report.get('issues', []))}\n"
            f"Suggestions : {len(report.get('suggestions', []))}"
        )

    def _format_global_summary(self, label: str, reports: list[dict]) -> str:
        """Formate le résumé global d'un audit multi-fichiers."""
        stats = self._compute_stats(reports)
        return (
            f"📊 Audit {label} — {stats['files_count']} fichiers\n"
            f"Score moyen : {stats['avg_score']}/100\n"
            f"Issues totales : {stats['total_issues']}"
        )

    async def _llm_call(self, system: str, prompt: str) -> Optional[str]:
        """Appel Ollama /api/chat avec retry x2."""
        payload = {
            "model":    OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            "stream": False,
        }
        for attempt in range(1, 3):
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        connect=5.0, read=LLM_TIMEOUT, write=5.0, pool=5.0
                    )
                ) as client:
                    r = await client.post(f"{OLLAMA_HOST}/api/chat", json=payload)
                    r.raise_for_status()
                    return r.json().get("message", {}).get("content", "").strip()
            except httpx.TimeoutException:
                self.logger.warning("LLM timeout (essai %d)", attempt)
            except Exception as e:
                self.logger.warning("LLM erreur (essai %d) : %s", attempt, e)
        return None
