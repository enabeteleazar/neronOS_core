# agents/code_agent.py
# Néron Core — Agent Développeur Autonome

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from core.agents.base_agent import BaseAgent, AgentResult
from core.config import settings

# ── Constantes ────────────────────────────────────────────────────────────────

OLLAMA_HOST  = settings.OLLAMA_HOST
OLLAMA_MODEL = getattr(settings, "CODE_AGENT_MODEL", settings.OLLAMA_MODEL)
LLM_TIMEOUT  = settings.LLM_TIMEOUT

# Répertoire racine de Néron — toute écriture hors de là est bloquée
_NERON_ROOT = Path(__file__).parent.parent.resolve()

# FIX: _WORKSPACE externalisé via variable d'env
_WORKSPACE  = Path(
    os.getenv("NERON_WORKSPACE", "/mnt/usb-storage/neron/workspace")
)
_BACKUP_DIR      = _NERON_ROOT / "data" / "code_backups"
_SANDBOX_TIMEOUT = 10  # secondes

_PY_EXT = {".py"}

_EXCLUDE_DIRS = {"__pycache__", "venv", ".git", "data", "docs", "scripts"}

# Semaphore pour limiter la concurrence des appels LLM en self_review
_REVIEW_CONCURRENCY = 4

# ── Prompts LLM ───────────────────────────────────────────────────────────────

_PROMPT_GENERATE = """Tu es un expert Python. Génère uniquement du code Python propre,
documenté avec des docstrings, sans explications ni balises markdown.
Juste le code brut, prêt à être écrit dans un fichier .py."""

_PROMPT_IMPROVE = """Tu es un expert en qualité de code Python.
Analyse le code fourni et retourne une version améliorée.
Règles strictes :
- Corriger les bugs évidents
- Ajouter les docstrings manquantes
- Respecter PEP8
- Ne pas changer la logique métier sans raison explicite
- Retourner UNIQUEMENT le code Python amélioré, sans explications, sans balises markdown."""

_PROMPT_ANALYZE = """Tu es un expert Python. Analyse ce fichier source et retourne
un rapport JSON avec exactement ce format (rien d'autre) :
{
  "quality_score": <0-100>,
  "issues": ["issue1", "issue2"],
  "suggestions": ["suggestion1", "suggestion2"],
  "has_docstrings": <true|false>,
  "pep8_ok": <true|false>
}"""


# ── Utilitaires internes ──────────────────────────────────────────────────────

def _safe_path(raw: str, generated: bool = False) -> Path:
    """
    Valide et retourne le chemin.
    - Si generated=True : écrit dans _WORKSPACE
    - Sinon : doit rester dans _NERON_ROOT
    """
    if generated:
        target = (_WORKSPACE / Path(raw).name).resolve()
        _WORKSPACE.mkdir(parents=True, exist_ok=True)
        return target
    target = (_NERON_ROOT / raw).resolve()
    if not str(target).startswith(str(_NERON_ROOT)):
        raise ValueError(f"Chemin non autorisé (hors workspace) : {raw!r}")
    return target


def _backup(path: Path) -> Optional[Path]:
    """Sauvegarde un fichier avant modification. Retourne le chemin du backup."""
    if not path.exists():
        return None
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # FIX: supporte les chemins dans _WORKSPACE et dans _NERON_ROOT
    try:
        rel = path.relative_to(_NERON_ROOT)
    except ValueError:
        rel = path.relative_to(_WORKSPACE.parent)

    safe_name = str(rel).replace("/", "_").replace("\\", "_")
    dest      = _BACKUP_DIR / f"{safe_name}.{ts}.bak"
    shutil.copy2(path, dest)

    # Rotation : garder les 10 derniers backups de ce fichier
    existing = sorted(_BACKUP_DIR.glob(f"{safe_name}.*.bak"))
    for old in existing[:-10]:
        old.unlink(missing_ok=True)
    return dest


def _rollback(path: Path) -> bool:
    """
    Restaure le backup le plus récent pour ce fichier.
    FIX: supporte les chemins dans _WORKSPACE et dans _NERON_ROOT.
    """
    try:
        rel = path.relative_to(_NERON_ROOT)
    except ValueError:
        rel = path.relative_to(_WORKSPACE.parent)

    safe_name = str(rel).replace("/", "_").replace("\\", "_")
    backups   = sorted(_BACKUP_DIR.glob(f"{safe_name}.*.bak"))
    if not backups:
        return False
    shutil.copy2(backups[-1], path)
    return True


def _strip_markdown_fences(code: str) -> str:
    """
    Supprime les balises markdown que certains modèles ajoutent.
    FIX: fonction dédiée et testable au lieu de triple re.sub inline.
    """
    code = re.sub(r"^```python\s*\n?", "", code.strip())
    code = re.sub(r"^```\s*\n?",       "", code.strip())
    code = re.sub(r"\n?```$",          "", code.strip())
    return code.strip()


async def _sandbox_test(code: str) -> dict:
    """
    Exécute le code dans un subprocess isolé (async).
    FIX: subprocess.run() bloquant remplacé par asyncio.create_subprocess_exec().
    Retourne {"ok": bool, "stdout": str, "stderr": str}
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp = Path(f.name)
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", str(tmp),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_SANDBOX_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return {"ok": False, "stdout": "", "stderr": f"Timeout ({_SANDBOX_TIMEOUT}s)"}
        return {
            "ok":     proc.returncode == 0,
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
        }
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}
    finally:
        tmp.unlink(missing_ok=True)


async def _check_syntax(code: str) -> dict:
    """
    Vérifie la syntaxe Python sans exécuter le code (async).
    FIX: subprocess.run() bloquant remplacé par asyncio.create_subprocess_exec().
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp = Path(f.name)
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-m", "py_compile", str(tmp),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        return {"ok": proc.returncode == 0, "stderr": stderr.decode()}
    finally:
        tmp.unlink(missing_ok=True)


# ── Agent principal ───────────────────────────────────────────────────────────


class CodeAgent(BaseAgent):
    """
    Agent développeur autonome de Néron.

    Actions disponibles via execute() :
      - "generate"    : génère un nouveau fichier Python
      - "improve"     : améliore un fichier existant (backup + sandbox)
      - "analyze"     : analyse un fichier et retourne un rapport
      - "read"        : lit le contenu d'un fichier source
      - "self_review" : passe en revue tous les fichiers source de Néron
      - "rollback"    : restaure le dernier backup d'un fichier
    """

    def __init__(self) -> None:
        super().__init__(name="code_agent")
        self.logger.info(
            "CodeAgent init — Ollama : %s | modèle : %s", OLLAMA_HOST, OLLAMA_MODEL
        )

    # ── Point d'entrée principal ──────────────────────────────────────────

    async def execute(self, query: str, **kwargs: Any) -> AgentResult:
        """
        Dispatch selon kwargs["action"]. Si aucune action explicite,
        détecte l'intention depuis le texte de la query.
        """
        start  = self._timer()
        action = kwargs.get("action") or self._detect_action(query)
        path   = kwargs.get("path", "")

        self.logger.info(
            "action=%r path=%r query=%r", action, path, query[:60]
        )

        try:
            if action == "generate":
                return await self._generate(query, path, start)
            elif action == "improve":
                return await self._improve(path, query, start)
            elif action == "analyze":
                return await self._analyze(path, start)
            elif action == "read":
                return await self._read(path, start)
            elif action == "self_review":
                return await self._self_review(start)
            elif action == "rollback":
                return await self._do_rollback(path, start)
            else:
                return await self._generate(query, path, start)
        except ValueError as e:
            return self._failure(str(e), latency_ms=self._elapsed_ms(start))
        except Exception as e:
            self.logger.exception("Exception inattendue : %s", e)
            return self._failure(f"Erreur inattendue : {e}", latency_ms=self._elapsed_ms(start))

    # ── Healthcheck (requis par watchdog_agent) ───────────────────────────

    async def check_connection(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{OLLAMA_HOST}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    async def reload(self) -> bool:
        return await self.check_connection()

    # ── Actions ───────────────────────────────────────────────────────────

    async def _generate(self, query: str, path: str, start: float) -> AgentResult:
        """Génère du code Python et l'écrit si un path est fourni."""
        code = await self._llm_call(_PROMPT_GENERATE, query)
        if not code:
            return self._failure(
                "LLM n'a pas retourné de code",
                latency_ms=self._elapsed_ms(start),
            )

        # FIX: _strip_markdown_fences() dédiée au lieu de triple re.sub inline
        code        = _strip_markdown_fences(code)
        result_meta: dict[str, Any] = {"action": "generate", "code_length": len(code)}

        if path:
            write_result = await self._write_code(path, code)
            if not write_result["ok"]:
                return self._failure(
                    write_result["error"], latency_ms=self._elapsed_ms(start)
                )
            result_meta["path"]    = write_result["path"]
            result_meta["sandbox"] = write_result.get("sandbox", {})
            summary = (
                f"Fichier généré : {write_result['path']}\n\n"
                f"```python\n{code[:500]}\n```"
            )
        else:
            summary = f"```python\n{code}\n```"

        return self._success(
            summary, metadata=result_meta, latency_ms=self._elapsed_ms(start)
        )

    async def _improve(self, path: str, context: str, start: float) -> AgentResult:
        """Améliore un fichier existant. Backup + syntax check + écriture."""
        if not path:
            return self._failure(
                "Chemin de fichier requis pour 'improve'",
                latency_ms=self._elapsed_ms(start),
            )

        safe = _safe_path(path)
        if not safe.exists():
            return self._failure(
                f"Fichier introuvable : {path}", latency_ms=self._elapsed_ms(start)
            )

        original = safe.read_text(encoding="utf-8")
        prompt   = f"Contexte : {context}\n\nCode à améliorer :\n{original}"
        improved = await self._llm_call(_PROMPT_IMPROVE, prompt)

        if not improved or improved.strip() == original.strip():
            return self._success(
                f"Aucune amélioration nécessaire pour {path}",
                metadata={"action": "improve", "changed": False},
                latency_ms=self._elapsed_ms(start),
            )

        syntax = await _check_syntax(improved)
        if not syntax["ok"]:
            return self._failure(
                f"Syntaxe invalide dans le code amélioré : {syntax['stderr'][:200]}",
                latency_ms=self._elapsed_ms(start),
            )

        backup_path = _backup(safe)
        safe.write_text(improved, encoding="utf-8")
        self.logger.info("Fichier amélioré : %s (backup : %s)", safe, backup_path)

        return self._success(
            f"✅ {path} amélioré.\nBackup : {backup_path.name if backup_path else 'N/A'}",
            metadata={
                "action":       "improve",
                "path":         str(safe),
                "backup":       str(backup_path) if backup_path else None,
                "changed":      True,
                "lines_before": len(original.splitlines()),
                "lines_after":  len(improved.splitlines()),
            },
            latency_ms=self._elapsed_ms(start),
        )

    async def _analyze(self, path: str, start: float) -> AgentResult:
        """Analyse un fichier et retourne un rapport qualité JSON."""
        if not path:
            return self._failure(
                "Chemin de fichier requis pour 'analyze'",
                latency_ms=self._elapsed_ms(start),
            )

        safe = _safe_path(path)
        if not safe.exists():
            return self._failure(
                f"Fichier introuvable : {path}", latency_ms=self._elapsed_ms(start)
            )

        code   = safe.read_text(encoding="utf-8")
        prompt = f"Fichier : {path}\n\nCode :\n{code}"
        raw    = await self._llm_call(_PROMPT_ANALYZE, prompt)

        try:
            report = json.loads(raw or "{}")
        except json.JSONDecodeError:
            report = {"raw": raw, "parse_error": True}

        summary = (
            f"📊 Analyse de `{path}`\n"
            f"Score : {report.get('quality_score', '?')}/100\n"
            f"Issues : {len(report.get('issues', []))}\n"
            f"Suggestions : {len(report.get('suggestions', []))}"
        )
        return self._success(
            summary,
            metadata={"action": "analyze", "path": str(safe), "report": report},
            latency_ms=self._elapsed_ms(start),
        )

    async def _read(self, path: str, start: float) -> AgentResult:
        """Lit le contenu d'un fichier source."""
        if not path:
            return self._failure(
                "Chemin de fichier requis pour 'read'",
                latency_ms=self._elapsed_ms(start),
            )

        safe = _safe_path(path)
        if not safe.exists():
            return self._failure(
                f"Fichier introuvable : {path}", latency_ms=self._elapsed_ms(start)
            )

        content = safe.read_text(encoding="utf-8")
        return self._success(
            content,
            metadata={
                "action": "read",
                "path":   str(safe),
                "lines":  len(content.splitlines()),
            },
            latency_ms=self._elapsed_ms(start),
        )

    async def _self_review(self, start: float) -> AgentResult:
        """
        Passe en revue tous les fichiers Python de Néron.
        FIX: appels LLM parallélisés via asyncio.gather() + Semaphore
        pour éviter de saturer Ollama.
        """
        files   = self._list_source_files()
        sem     = asyncio.Semaphore(_REVIEW_CONCURRENCY)
        reports = await asyncio.gather(
            *[self._review_file(f, sem) for f in files],
            return_exceptions=False,
        )

        scored = [
            r for r in reports
            if isinstance(r.get("quality_score"), (int, float))
        ]
        avg          = round(sum(r["quality_score"] for r in scored) / len(scored), 1) if scored else None
        total_issues = sum(len(r.get("issues", [])) for r in reports)

        summary = (
            f"🔍 Auto-review Néron — {len(files)} fichiers analysés\n"
            f"Score moyen : {avg}/100\n"
            f"Issues totales : {total_issues}"
        )
        return self._success(
            summary,
            metadata={
                "action":       "self_review",
                "files_count":  len(files),
                "avg_score":    avg,
                "total_issues": total_issues,
                "reports":      reports,
            },
            latency_ms=self._elapsed_ms(start),
        )

    async def _review_file(self, f: Path, sem: asyncio.Semaphore) -> dict:
        """Analyse un fichier unique dans le cadre d'un self_review."""
        async with sem:
            try:
                code   = f.read_text(encoding="utf-8")
                prompt = f"Fichier : {f.name}\n\nCode :\n{code[:3000]}"
                raw    = await self._llm_call(_PROMPT_ANALYZE, prompt)
                try:
                    report = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    report = {"quality_score": None, "issues": [], "suggestions": []}

                return {
                    "file":          str(f.relative_to(_NERON_ROOT)),
                    "quality_score": report.get("quality_score"),
                    "issues":        report.get("issues", []),
                    "suggestions":   report.get("suggestions", []),
                }
            except Exception as e:
                self.logger.warning("self_review: erreur sur %s : %s", f, e)
                return {"file": str(f), "error": str(e)}

    async def _do_rollback(self, path: str, start: float) -> AgentResult:
        """
        Restaure le dernier backup d'un fichier.
        FIX: rendu async pour uniformité avec les autres actions.
        """
        if not path:
            return self._failure(
                "Chemin requis pour 'rollback'", latency_ms=self._elapsed_ms(start)
            )
        try:
            safe = _safe_path(path)
            ok   = _rollback(safe)
            if ok:
                return self._success(
                    f"✅ Rollback effectué pour {path}",
                    metadata={"action": "rollback", "path": str(safe)},
                    latency_ms=self._elapsed_ms(start),
                )
            return self._failure(
                f"Aucun backup trouvé pour {path}",
                latency_ms=self._elapsed_ms(start),
            )
        except ValueError as e:
            return self._failure(str(e), latency_ms=self._elapsed_ms(start))

    # ── Helpers ───────────────────────────────────────────────────────────

    def _detect_action(self, query: str) -> str:
        """Détecte l'action depuis le texte de la query (normalisation unicode)."""
        def norm(t: str) -> str:
            n = unicodedata.normalize("NFD", t.lower())
            return "".join(c for c in n if unicodedata.category(c) != "Mn")

        q = norm(query)

        if any(w in q for w in ("genere", "cree", "ecris", "generer", "creer")):
            return "generate"
        if any(w in q for w in ("ameliore", "optimise", "corrige", "refactorise")):
            return "improve"
        if any(w in q for w in ("analyse", "inspecte", "qualite", "rapport")):
            return "analyze"
        if any(w in q for w in ("self review", "auto review", "revue", "passe en revue")):
            return "self_review"
        if any(w in q for w in ("rollback", "restaure", "annule")):
            return "rollback"
        if any(w in q for w in ("lis le fichier", "montre le fichier", "affiche le fichier")):
            return "read"
        return "generate"

    async def _write_code(self, path: str, code: str) -> dict:
        """
        Valide, sauvegarde et écrit du code.
        FIX: rendu async pour utiliser _check_syntax() et _sandbox_test() async.
        FIX: chmod 0o644 au lieu de 0o755 — fichier Python pas exécutable par défaut.
        """
        try:
            safe = _safe_path(path, generated=True)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        if safe.suffix not in _PY_EXT:
            safe = safe.with_suffix(".py")

        syntax = await _check_syntax(code)
        if not syntax["ok"]:
            return {"ok": False, "error": f"Syntaxe invalide : {syntax['stderr'][:200]}"}

        backup = _backup(safe)
        safe.parent.mkdir(parents=True, exist_ok=True)
        safe.write_text(code, encoding="utf-8")
        # FIX: 0o644 — lecture/écriture owner, lecture seule groupe/autres
        safe.chmod(0o644)

        sandbox = await _sandbox_test(code)
        if not sandbox["ok"]:
            self.logger.warning(
                "Sandbox KO pour %s : %s", safe, sandbox["stderr"][:100]
            )

        return {
            "ok":      True,
            "path":    str(safe),
            "backup":  str(backup) if backup else None,
            "sandbox": sandbox,
        }

    def _list_source_files(self) -> list[Path]:
        """Liste tous les fichiers .py de Néron, hors dossiers exclus."""
        files = []
        for p in _NERON_ROOT.rglob("*.py"):
            if p.name == "__init__.py":
                continue
            if not any(excl in p.parts for excl in _EXCLUDE_DIRS):
                files.append(p)
        return sorted(files)

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
