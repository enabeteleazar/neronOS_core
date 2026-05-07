import json
import logging
import os
from collections import defaultdict

log = logging.getLogger(__name__)

# Tokens trop courts pour être significatifs
_MIN_TOKEN_LEN = 3

# Nombre d'utilisations avant qu'un synonyme plus fréquent remplace l'existant
_CORRECTION_THRESHOLD = 3


class SynonymLearner:
    """
    Apprend et persiste les associations mot→entity_id.

    Logique de correction :
      - Premier apprentissage  → le mot est associé à l'entité.
      - Corrections suivantes  → si un autre entity_id est utilisé
        _CORRECTION_THRESHOLD fois de plus que le mapping courant,
        il prend sa place.
    """

    def __init__(self, path: str = "/opt/homeassistant/config/synonyms.json"):
        self.path = path
        # {"token": {"entity_id": str, "count": int}}
        self._data: dict[str, dict] = self._load()

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def learn_from_command(self, raw_text: str, resolved_entity: str) -> None:
        """
        Enregistre les tokens du texte brut comme synonymes de l'entité résolue.
        Met à jour le mapping si l'entité courante est dépassée en fréquence.
        """
        tokens = [t for t in raw_text.lower().split() if len(t) >= _MIN_TOKEN_LEN]

        for token in tokens:
            entry = self._data.get(token)

            if entry is None:
                # Première occurrence
                self._data[token] = {"entity_id": resolved_entity, "count": 1}

            elif entry["entity_id"] == resolved_entity:
                # Renforcement du mapping existant
                entry["count"] += 1

            else:
                # Compétition : un autre entity_id est utilisé
                entry.setdefault("candidates", defaultdict(int))
                entry["candidates"][resolved_entity] += 1

                # Correction si le challenger dépasse le seuil
                if entry["candidates"][resolved_entity] >= entry["count"] + _CORRECTION_THRESHOLD:
                    log.info(
                        "Synonyme '%s' corrigé : %s → %s",
                        token,
                        entry["entity_id"],
                        resolved_entity,
                    )
                    entry["entity_id"] = resolved_entity
                    entry["count"] = entry["candidates"][resolved_entity]
                    entry["candidates"] = defaultdict(int)

        self._save()

    def resolve(self, word: str) -> str | None:
        """Retourne l'entity_id appris pour ce mot, ou None."""
        entry = self._data.get(word.lower().strip())
        return entry["entity_id"] if entry else None

    def all_synonyms(self) -> dict[str, str]:
        """Retourne le dictionnaire token→entity_id courant."""
        return {k: v["entity_id"] for k, v in self._data.items()}

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                # Compatibilité avec l'ancien format {"token": "entity_id"}
                if raw and isinstance(next(iter(raw.values())), str):
                    log.info("Migration de l'ancien format synonyms.json")
                    return {k: {"entity_id": v, "count": 1} for k, v in raw.items()}
                return raw
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Impossible de charger %s : %s", self.path, exc)
        return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            # defaultdict n'est pas sérialisable → conversion
            data_to_save = {}
            for token, entry in self._data.items():
                data_to_save[token] = {
                    "entity_id": entry["entity_id"],
                    "count": entry["count"],
                    "candidates": dict(entry.get("candidates", {})),
                }
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            log.error("Impossible de sauvegarder %s : %s", self.path, exc)
