"""core/agents/io/weather_agent.py
Neron Core — Agent Météo  v1.1.0

Inspiré de helpers.py (JARVIS) — météo temps réel via Open-Meteo (100 % gratuit,
sans clé API) + géocodage via Nominatim (OpenStreetMap).

Intent déclenché : WEATHER_QUERY
Commandes Telegram : /meteo [ville]

Config optionnelle dans neron.yaml :
  WEATHER_DEFAULT_CITY: "Paris"   # défaut si aucune ville dans la query
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger("agent.weather")

# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_CITY = getattr(settings, "WEATHER_DEFAULT_CITY", "Paris")

_GEOCODE_URL  = "https://nominatim.openstreetmap.org/search"
_WEATHER_URL  = "https://api.open-meteo.com/v1/forecast"

# Codes WMO → description + emoji
_WMO_CODES: dict[int, tuple[str, str]] = {
    0:  ("Ciel dégagé",         "☀️"),
    1:  ("Principalement clair","🌤️"),
    2:  ("Partiellement nuageux","⛅"),
    3:  ("Couvert",             "☁️"),
    45: ("Brouillard",          "🌫️"),
    48: ("Brouillard givrant",  "🌫️"),
    51: ("Bruine légère",       "🌦️"),
    53: ("Bruine modérée",      "🌦️"),
    55: ("Bruine dense",        "🌧️"),
    61: ("Pluie légère",        "🌧️"),
    63: ("Pluie modérée",       "🌧️"),
    65: ("Pluie forte",         "🌧️"),
    71: ("Neige légère",        "🌨️"),
    73: ("Neige modérée",       "❄️"),
    75: ("Neige forte",         "❄️"),
    80: ("Averses légères",     "🌦️"),
    81: ("Averses modérées",    "🌧️"),
    82: ("Averses violentes",   "⛈️"),
    95: ("Orage",               "⛈️"),
    99: ("Orage avec grêle",    "⛈️"),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_city(query: str) -> str:
    """Tente d'extraire une ville de la query.

    Exemples couverts :
      'météo à Lyon'                     → Lyon
      'quel temps fait-il à Marseille'   → Marseille
      'il fait combien à Nice'           → Nice
      'météo Paris'                      → Paris
      'il fait combien'                  → _DEFAULT_CITY
    """
    patterns = [
        # "météo à Lyon", "météo de Paris"
        r"(?:météo|meteo|température|temperature)\s+(?:à|a|de|sur|pour)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-]*?)(?:\s*\?|$)",
        # "quel temps fait-il à Marseille", "il fait combien à Nice"
        r"(?:à|a)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-]{2,})(?:\s*[?]|$)",
        # "météo Lyon" (sans préposition)
        r"(?:météo|meteo)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-]{2,})(?:\s*[?]|$)",
    ]
    for pat in patterns:
        m = re.search(pat, query, re.IGNORECASE)
        if m:
            city = m.group(1).strip().rstrip("?").strip()
            if 2 < len(city) < 50:
                return city
    return _DEFAULT_CITY


async def _geocode(city: str) -> tuple[float, float]:
    """Retourne (lat, lon) pour une ville via Nominatim."""
    async with httpx.AsyncClient(
        timeout=6.0,
        headers={"User-Agent": "Neron-Assistant/1.0 (homebox self-hosted)"},
    ) as client:
        r = await client.get(_GEOCODE_URL, params={
            "q": city, "format": "json", "limit": 1,
        })
        r.raise_for_status()
        data = r.json()
        if not data:
            raise ValueError(f"Ville introuvable : {city}")
        return float(data[0]["lat"]), float(data[0]["lon"])


async def _fetch_weather(lat: float, lon: float) -> dict:
    """Récupère les données météo Open-Meteo."""
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(_WEATHER_URL, params={
            "latitude":  lat,
            "longitude": lon,
            "current":   "temperature_2m,relative_humidity_2m,apparent_temperature,"
                         "weather_code,wind_speed_10m,precipitation",
            "timezone":  "Europe/Paris",
        })
        r.raise_for_status()
        return r.json()


def _format_weather(data: dict, city: str) -> str:
    """Formate la réponse météo en texte lisible."""
    cur  = data.get("current", {})
    code = int(cur.get("weather_code", 0))
    desc, emoji = _WMO_CODES.get(code, ("Inconnu", "🌡️"))

    temp     = cur.get("temperature_2m", "?")
    feels    = cur.get("apparent_temperature", "?")
    humidity = cur.get("relative_humidity_2m", "?")
    wind     = cur.get("wind_speed_10m", "?")
    precip   = cur.get("precipitation", 0)

    lines = [
        f"{emoji} **Météo à {city}** — {desc}",
        f"🌡️ Température : {temp}°C (ressenti {feels}°C)",
        f"💧 Humidité : {humidity}%",
        f"💨 Vent : {wind} km/h",
    ]
    if precip and float(precip) > 0:
        lines.append(f"🌧️ Précipitations : {precip} mm")
    return "\n".join(lines)


# ── Agent ─────────────────────────────────────────────────────────────────────

class WeatherAgent:
    """
    Retourne la météo actuelle pour une ville.
    Utilise Open-Meteo (gratuit, sans clé) + Nominatim pour le géocodage.
    """

    async def run(self, query: str = "") -> str:
        city = _extract_city(query)
        try:
            lat, lon = await _geocode(city)
            data     = await _fetch_weather(lat, lon)
            return _format_weather(data, city)

        except ValueError as e:
            return f"⚠️ {e}"
        except httpx.TimeoutException:
            return "⚠️ Délai dépassé lors de la récupération de la météo."
        except Exception as e:
            logger.error("WeatherAgent error for '%s': %s", city, e)
            return f"Impossible de récupérer la météo pour « {city} »."
