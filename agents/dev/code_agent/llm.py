# llm.py
import subprocess
import shutil

# ── Filtre anti-LLM invalide ──────────────────────────────────────────────────
ALLOWED_MODELS = {
    "mistral",
    "deepseek-coder:6.7b",
    "deepseek-coder-v2:16b",
    "qwen2.5-coder:7b",
    "llama3",
    "codellama",
}

def ask(model: str, prompt: str) -> str:
    """
    Appelle un modèle Ollama local.
    Lève ValueError si le modèle n'est pas dans la whitelist.
    Lève RuntimeError si Ollama n'est pas disponible ou si la commande échoue.
    """
    # 1. Whitelist du modèle
    if model not in ALLOWED_MODELS:
        raise ValueError(
            f"Modèle '{model}' non autorisé. "
            f"Modèles valides : {sorted(ALLOWED_MODELS)}"
        )

    # 2. Vérifier qu'ollama est installé
    if not shutil.which("ollama"):
        raise RuntimeError("Ollama n'est pas installé ou absent du PATH.")

    # 3. Appel sans timeout
    result = subprocess.run(
        ["ollama", "run", model],
        input=prompt,
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Ollama a retourné une erreur (code {result.returncode}) : "
            f"{result.stderr.strip()}"
        )

    output = result.stdout.strip()
    if not output:
        raise RuntimeError(f"Le modèle '{model}' a retourné une réponse vide.")

    return output
