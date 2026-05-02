# planner.py
from llm import ask

MODEL = "mistral"  # dans ALLOWED_MODELS ✓

MIN_PLAN_LENGTH = 50  # caractères minimum pour qu'un plan soit considéré valide


def plan(task: str, context: str) -> str:
    """
    Génère un plan d'action structuré via le LLM.
    Lève ValueError si la réponse est trop courte pour être exploitable.
    """
    if not task.strip():
        raise ValueError("La tâche ne peut pas être vide.")

    prompt = f"""Tu es un architecte logiciel.

Objectif :
Créer un plan d'action clair et structuré.

Tâche :
{task}

Contexte (fichiers du projet) :
{context}

Donne :
- Les étapes numérotées
- Les fichiers à modifier
- La stratégie globale

Réponds uniquement avec le plan, sans introduction ni conclusion.
"""
    result = ask(MODEL, prompt)  # lève ValueError/RuntimeError si problème

    if len(result) < MIN_PLAN_LENGTH:
        raise ValueError(
            f"Plan retourné trop court ({len(result)} chars). "
            "Le LLM a probablement échoué à générer un plan valide."
        )

    return result
