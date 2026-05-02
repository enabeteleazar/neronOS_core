# coder.py
import re
from llm import ask

MODEL = "deepseek-coder:6.7b"  # dans ALLOWED_MODELS ✓

MIN_CODE_LENGTH = 10  # caractères minimum pour du code valide


def code(action_plan: str, file_content: str) -> str:
    """
    Applique le plan au contenu d'un fichier via le LLM.
    Retourne le nouveau code, ou lève ValueError si la réponse est invalide.
    """
    if not file_content.strip():
        raise ValueError("Le contenu du fichier source est vide.")

    prompt = f"""Tu es un expert développeur Python/TypeScript.

PLAN À APPLIQUER :
{action_plan}

CODE SOURCE ORIGINAL :
{file_content}

Applique le plan au code ci-dessus.
Réponds UNIQUEMENT avec le code final complet, sans explication,
sans bloc markdown, sans commentaire en dehors du code.
"""
    result = ask(MODEL, prompt)

    # Extraire le code même si le LLM ajoute du texte autour
    result = _extract_code(result)

    if len(result.strip()) < MIN_CODE_LENGTH:
        raise ValueError(
            f"Code retourné trop court ({len(result.strip())} chars). "
            "Réponse LLM probablement invalide, fichier non modifié."
        )

    return result


def _extract_code(text: str) -> str:
    """
    Extrait le code depuis une réponse LLM potentiellement bavarde.

    Cas gérés :
      - Bloc ```python ... ``` avec ou sans texte avant/après
      - Bloc ``` ... ``` générique
      - Pas de bloc markdown → retourne le texte tel quel
    """
    # Premier bloc ```langage? ... ```
    match = re.search(r"```(?:\w+)?\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).rstrip()

    # Aucun bloc — texte brut
    return text
