import json
import logging
import os

log = logging.getLogger(__name__)

# Pièces reconnues (patterns à chercher dans entity_id ou friendly_name).
# On stocke des variantes pour couvrir les espaces ET les underscores.
_ROOM_PATTERNS: list[tuple[str, list[str]]] = [
    ("salon",          ["salon"]),
    ("chambre",        ["chambre"]),
    ("cuisine",        ["cuisine"]),
    ("bureau",         ["bureau"]),
    ("garage",         ["garage"]),
    ("salle_de_bain",  ["salle_de_bain", "salle de bain", "sdb", "bathroom"]),
    ("couloir",        ["couloir", "entree", "entrée"]),
    ("wc",             ["wc", "toilette", "toilettes"]),
    ("cave",           ["cave"]),
    ("grenier",        ["grenier"]),
    ("terrasse",       ["terrasse"]),
    ("jardin",         ["jardin"]),
]


class RoomLearner:

    def __init__(self, path: str = "/opt/homeassistant/config/room_map.json"):
        self.path = path
        self.map: dict[str, list[str]] = self._load()

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------

    def learn(self, states: list[dict]) -> dict[str, list[str]]:
        """
        Met à jour la map pièce→entity_ids à partir des états HA.
        Retourne la map complète.
        """
        for e in states:
            entity_id: str = e["entity_id"]
            friendly: str = (e.get("attributes", {}).get("friendly_name") or "").lower()
            room = self._extract_room(entity_id, friendly)
            if room:
                self.map.setdefault(room, [])
                if entity_id not in self.map[room]:
                    self.map[room].append(entity_id)

        self._save()
        log.debug("RoomLearner : %d pièces connues", len(self.map))
        return self.map

    def get(self, room: str) -> list[str]:
        """Retourne les entity_ids associés à une pièce (liste vide si inconnue)."""
        return self.map.get(room, [])

    def rooms(self) -> list[str]:
        """Liste des pièces connues."""
        return list(self.map.keys())

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _extract_room(self, entity_id: str, friendly: str) -> str | None:
        text = f"{entity_id} {friendly}".lower()
        for room_key, patterns in _ROOM_PATTERNS:
            for p in patterns:
                if p in text:
                    return room_key
        return None

    def _load(self) -> dict[str, list[str]]:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Impossible de charger %s : %s", self.path, exc)
        return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.map, f, indent=2, ensure_ascii=False)
        except OSError as exc:
            log.error("Impossible de sauvegarder %s : %s", self.path, exc)
