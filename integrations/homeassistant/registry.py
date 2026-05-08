import logging

log = logging.getLogger(__name__)

# Tokens trop courts ou trop génériques pour être des pièces
_IGNORED_TOKENS = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "de", "la", "le", "les"}


class HARegistry:

    def __init__(self):
        self.entities: list[dict] = []
        self.index_by_area: dict[str, list[str]] = {}
        self.index_by_name: dict[str, str] = {}

    def load(self, states: list[dict]) -> None:
        """Charge les états HA et reconstruit les index."""
        self.entities = states
        self.index_by_area = {}
        self.index_by_name = {}

        for e in states:
            entity_id: str = e["entity_id"]
            attrs: dict = e.get("attributes", {})
            name: str = (attrs.get("friendly_name") or "").lower().strip()

            if name:
                self.index_by_name[name] = entity_id

            # Extraction heuristique de la pièce depuis l'object_id
            # On filtre les tokens trop courts ou numériques
            if "." in entity_id:
                _, object_id = entity_id.split(".", 1)
                for part in object_id.split("_"):
                    part = part.lower()
                    if len(part) >= 4 and part not in _IGNORED_TOKENS:
                        self.index_by_area.setdefault(part, [])
                        if entity_id not in self.index_by_area[part]:
                            self.index_by_area[part].append(entity_id)

        log.debug(
            "Registry chargé : %d entités, %d zones, %d noms",
            len(self.entities),
            len(self.index_by_area),
            len(self.index_by_name),
        )

    def by_domain(self, domain: str) -> list[str]:
        """Retourne tous les entity_id d'un domaine donné."""
        return [
            e["entity_id"]
            for e in self.entities
            if e["entity_id"].startswith(f"{domain}.")
        ]

    def by_name(self, name: str) -> str | None:
        """Recherche exacte par friendly_name (insensible à la casse)."""
        return self.index_by_name.get(name.lower().strip())
