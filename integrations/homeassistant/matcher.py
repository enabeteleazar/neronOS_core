import logging
from .registry import HARegistry
from .room_learner import RoomLearner
from .synonym_learner import SynonymLearner

log = logging.getLogger(__name__)

# Alias français → domaine HA
ALIASES: dict[str, str] = {
    "lumiere":   "light",
    "lumieres":  "light",
    "lumière":   "light",
    "lumières":  "light",
    "lampe":     "light",
    "lampes":    "light",
    "volet":     "cover",
    "volets":    "cover",
    "prise":     "switch",
    "prises":    "switch",
    "chauffage": "climate",
    "thermostat": "climate",
}


class SmartMatcher:
    """
    Résout un texte libre en une liste d'entity_ids Home Assistant.

    Priorité de résolution :
      1. Synonyme appris (SynonymLearner)
      2. Alias français → domaine HA  (ex. "lampe" → "light")
      3. Pièce apprise (RoomLearner)  filtré par domaine si alias trouvé
      4. Match domaine global
      5. Fuzzy : mot présent dans entity_id ou friendly_name
      6. Liste vide

    Retourne toujours list[str].
    """

    def __init__(
        self,
        registry: HARegistry,
        room_learner: RoomLearner | None = None,
        synonym_learner: SynonymLearner | None = None,
    ):
        self.registry = registry
        self.room_learner = room_learner
        self.synonym_learner = synonym_learner

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def resolve(
        self,
        text: str,
        room: str | None = None,
        device: str | None = None,
    ) -> list[str]:
        """
        Résout le texte en entity_ids.

        Args:
            text:   Texte brut de la commande utilisateur.
            room:   Pièce explicitement identifiée en amont (optionnel).
            device: Domaine HA forcé en amont (optionnel).

        Returns:
            Liste d'entity_ids correspondants, vide si aucun match.
        """
        text_l = text.lower().strip()
        tokens = text_l.split()

        log.debug("resolve(%r, room=%r, device=%r) — %d entités",
                  text_l, room, device, len(self.registry.entities))

        # 1. Synonyme appris
        if self.synonym_learner:
            for token in tokens:
                resolved = self.synonym_learner.resolve(token)
                if resolved:
                    log.debug("Synonyme '%s' → %s", token, resolved)
                    return [resolved]

        # 2. Alias français → domaine
        if device is None:
            for token in tokens:
                if token in ALIASES:
                    device = ALIASES[token]
                    log.debug("Alias '%s' → domaine '%s'", token, device)
                    break

        # 3. Pièce apprise (room learner)
        if self.room_learner:
            # Pièce fournie explicitement
            room_to_check = room
            # Ou pièce trouvée dans le texte
            if room_to_check is None:
                for r in self.room_learner.rooms():
                    if r in text_l or r.replace("_", " ") in text_l:
                        room_to_check = r
                        break

            if room_to_check:
                candidates = self.room_learner.get(room_to_check)
                if candidates:
                    if device:
                        filtered = [c for c in candidates if c.startswith(f"{device}.")]
                        if filtered:
                            log.debug("Room+domaine → %s", filtered)
                            return filtered
                    log.debug("Room → %s", candidates)
                    return candidates

        # 4. Match domaine global
        if device:
            matches = self.registry.by_domain(device)
            if matches:
                log.debug("Domaine '%s' → %d entités", device, len(matches))
                return matches

        # 5. Fuzzy : token présent dans entity_id ou friendly_name
        matches = self._fuzzy_match(tokens)
        if matches:
            log.debug("Fuzzy → %s", matches)
            return matches

        log.debug("Aucun résultat pour %r", text_l)
        return []

    def best(self, text: str, room: str | None = None, device: str | None = None) -> str | None:
        """
        Comme resolve() mais retourne un seul entity_id (le meilleur candidat).
        Pratique pour les commandes à cible unique.
        """
        results = self.resolve(text, room=room, device=device)
        if not results:
            return None
        return self._best_candidate(results, text.lower())

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _fuzzy_match(self, tokens: list[str]) -> list[str]:
        seen: set[str] = set()
        results: list[str] = []

        for e in self.registry.entities:
            entity_id: str = e["entity_id"]
            friendly: str = (e.get("attributes", {}).get("friendly_name") or "").lower()
            target = f"{entity_id} {friendly}"

            for token in tokens:
                if len(token) >= 3 and token in target and entity_id not in seen:
                    results.append(entity_id)
                    seen.add(entity_id)
                    break

        return results

    def _best_candidate(self, candidates: list[str], text: str) -> str:
        """Choisit le candidat dont l'entity_id contient le plus de tokens du texte."""
        tokens = text.split()
        best = candidates[0]
        best_score = 0

        for c in candidates:
            score = sum(1 for t in tokens if t in c)
            if score > best_score:
                best_score = score
                best = c

        return best
