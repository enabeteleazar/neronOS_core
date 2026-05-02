# file_manager.py
import os

# ── Workspace sandbox ─────────────────────────────────────────────────────────
# Toute opération d'écriture est INTERDITE en dehors de ce répertoire.
# Seul agent.py définit WORKSPACE — ce module l'importe jamais directement
# pour éviter les imports circulaires. La variable est injectée via set_workspace().

_WORKSPACE: str | None = None

ALLOWED_EXTENSIONS = (".py", ".ts", ".tsx", ".js")


def set_workspace(path: str) -> None:
    """Doit être appelé une seule fois au démarrage, avant tout write_file."""
    global _WORKSPACE
    resolved = os.path.realpath(path)
    if not os.path.isdir(resolved):
        raise NotADirectoryError(f"Workspace invalide : '{resolved}' n'est pas un dossier.")
    _WORKSPACE = resolved


def _require_workspace() -> str:
    if _WORKSPACE is None:
        raise RuntimeError("Workspace non initialisé. Appeler set_workspace() d'abord.")
    return _WORKSPACE


def _safe_path(path: str) -> str:
    """
    Résout le chemin absolu et vérifie qu'il reste dans le workspace.
    Protège contre les path traversal (../../etc/passwd etc.).
    """
    workspace = _require_workspace()
    resolved = os.path.realpath(path)
    if not resolved.startswith(workspace + os.sep) and resolved != workspace:
        raise PermissionError(
            f"Accès refusé : '{resolved}' est en dehors du workspace '{workspace}'."
        )
    return resolved


# ── API publique ──────────────────────────────────────────────────────────────

def list_files(path: str) -> list[str]:
    """Liste les fichiers source dans path (lecture seule, pas de vérification workspace)."""
    files = []
    for root, _, filenames in os.walk(path):
        for f in filenames:
            if f.endswith(ALLOWED_EXTENSIONS):
                files.append(os.path.join(root, f))
    return files


def read_file(path: str) -> str:
    """Lecture — autorisée partout (lecture seule = pas de risque d'écrasement)."""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def write_file(path: str, content: str) -> None:
    """
    Écriture UNIQUEMENT dans le workspace défini par set_workspace().
    Lève PermissionError si le chemin cible est en dehors.
    """
    safe = _safe_path(path)

    # Créer les dossiers intermédiaires si nécessaire
    os.makedirs(os.path.dirname(safe), exist_ok=True)

    with open(safe, "w", encoding="utf-8") as f:
        f.write(content)
