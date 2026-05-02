# neron_time/time_provider.py

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS  = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]

_DEFAULT_TZ = "Europe/Paris"


class TimeProvider:
    """Fournit l'heure et la date localisées pour Néron."""

    def __init__(self, tz: str = _DEFAULT_TZ) -> None:
        # FIX: guard sur ZoneInfo — fallback sur Europe/Paris si tz invalide
        try:
            self.tz = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, KeyError) as e:
            logger.warning("Timezone invalide %r : %s — fallback sur %s", tz, e, _DEFAULT_TZ)
            self.tz = ZoneInfo(_DEFAULT_TZ)

    def now(self) -> datetime:
        """Retourne le datetime courant localisé."""
        return datetime.now(self.tz)

    def iso(self) -> str:
        """Retourne la date/heure au format ISO 8601."""
        return self.now().isoformat()

    def human(self) -> str:
        """Retourne une représentation lisible : 'lundi 23 mars 2026 à 09h15'."""
        # FIX: now() appelé une seule fois — cohérence garantie
        n    = self.now()
        jour = JOURS[n.weekday()]
        mois = MOIS[n.month - 1]
        return f"{jour} {n.day} {mois} {n.year} à {n.hour:02d}h{n.minute:02d}"

    def timestamp(self) -> float:
        """Retourne le timestamp UNIX."""
        return self.now().timestamp()

    def date(self) -> str:
        """Retourne la date au format JJ/MM/AAAA."""
        return self.now().strftime("%d/%m/%Y")

    def time(self) -> str:
        """Retourne l'heure au format HH:MM:SS."""
        return self.now().strftime("%H:%M:%S")
