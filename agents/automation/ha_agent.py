# agents/ha_agent.py
# Neron Core - Agent Home Assistant (REST API directe)
from __future__ import annotations
import asyncio
import unicodedata
from typing import Any
import httpx
from core.agents.base_agent import BaseAgent, AgentResult
from core.config import settings

# ── Constantes ────────────────────────────────────────────────────────────────
HA_URL              = getattr(settings, "HA_URL",              "http://homeassistant.local:8123")
HA_TOKEN            = getattr(settings, "HA_TOKEN",            "")
HA_ENABLED          = getattr(settings, "HA_ENABLED",          False)
HA_REFRESH_INTERVAL = int(getattr(settings, "HA_REFRESH_INTERVAL", 5))
HA_TIMEOUT          = float(getattr(settings, "HA_TIMEOUT",    10.0))

# ── Mapping actions ───────────────────────────────────────────────────────────
TURN_OFF_KEYS = [
    "eteins", "eteint", "eteignez",
    "desactive", "desactiver",
    "ferme", "fermer",
    "arrete", "arreter",
    "coupe", "couper",
    "stoppe", "stopper",
    "baisse",
]
TURN_ON_KEYS = [
    "allume", "allumer", "allumez",
    "active", "activer",
    "ouvre", "ouvrir",
    "demarre", "demarrer",
    "mets", "mettre",
    "lance", "lancer",
    "monte",
]
STATUS_KEYS = [
    "etat", "statut",
    "quelle est", "quel est",
    "est-ce que", "est ce que",
    "combien",
]

# Marqueurs de pluriel → commande sur TOUTES les entités du domaine
PLURAL_MARKERS = [
    "les ", "toutes", "tous",
    "lumieres", "lampes", "volets", "stores",
]

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "light":               ["lumiere", "lumieres", "lampe", "lampes", "plafonnier",
                            "led", "spot", "ampoule", "eclairage"],
    "cover":               ["volet", "volets", "store", "rideau", "portail"],
    "climate":             ["thermostat", "chauffage", "climatiseur", "clim"],
    "switch":              ["prise", "interrupteur"],
    "fan":                 ["ventilateur", "vmc"],
    "alarm_control_panel": ["alarme"],
    "scene":               ["scene"],
    "media_player":        ["tv", "tele", "musique", "media", "spotify", "radio"],
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().strip())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _detect_domain(q: str) -> str | None:
    for domain, kws in DOMAIN_KEYWORDS.items():
        for kw in kws:
            if kw in q:
                return domain
    return None


def _detect_action(q: str) -> str:
    # Ordre : turn_off → turn_on → get_state (turn_off évalué en premier)
    for k in TURN_OFF_KEYS:
        if k in q:
            return "turn_off"
    for k in TURN_ON_KEYS:
        if k in q:
            return "turn_on"
    for k in STATUS_KEYS:
        if k in q:
            return "get_state"
    return "turn_on"


def _is_plural(q: str) -> bool:
    return any(m in q for m in PLURAL_MARKERS)


def _score_entity(q: str, entity: dict, room_hint: str = "") -> int:
    """
    Score de pertinence. Bonus si le room_hint NLP correspond à l'entité.
    """
    score     = 0
    entity_id = _normalize(entity.get("entity_id", ""))
    friendly  = _normalize(entity.get("attributes", {}).get("friendly_name", ""))
    parts     = entity_id.split(".")

    for word in q.split():
        if len(word) < 3:
            continue
        if word in friendly:
            score += 3
        elif any(word in part for part in parts):
            score += 2
        elif word in entity_id:
            score += 1

    # Bonus room_hint extrait par le NLP (ex: room='chambre')
    if room_hint:
        target = f"{entity_id} {friendly}"
        if room_hint in target:
            score += 4

    return score


def _parse_query(
    query: str,
    ha_states: list,
    entities: dict | None = None,
) -> dict:
    """
    Retourne un dict avec :
      - action, domain, entity_ids (list), friendly, current_state, matched
    entity_ids est toujours une liste (peut contenir plusieurs entités).
    """
    q       = _normalize(query)
    action  = _detect_action(q)
    domain  = _detect_domain(q)
    entities = entities or {}

    # Room hint depuis le NLP pipeline
    room_hint = _normalize(entities.get("room", "") or "")

    # Filtre par domaine
    candidates = ha_states
    if domain:
        filtered   = [e for e in ha_states if e.get("entity_id", "").startswith(domain + ".")]
        candidates = filtered if filtered else ha_states

    # Commande plurielle → toutes les entités du domaine
    if _is_plural(q) and domain and candidates:
        ids      = [e["entity_id"] for e in candidates]
        friendly = f"{len(ids)} entités ({domain})"
        return {
            "action":        action,
            "domain":        domain,
            "entity_ids":    ids,
            "friendly":      friendly,
            "current_state": "—",
            "matched":       True,
        }

    # Score individuel
    scored = [(e, _score_entity(q, e, room_hint)) for e in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)

    best_score = scored[0][1] if scored else 0

    if best_score > 0:
        # Prend tous les ex-æquo au meilleur score
        top    = [e for e, s in scored if s == best_score]
        ids    = [e["entity_id"] for e in top]
        best   = top[0]
        matched = True
    elif candidates and domain:
        # Domaine trouvé mais aucun score positif :
        # → on utilise quand même les entités du domaine (meilleur effort)
        ids     = [e["entity_id"] for e in candidates]
        best    = candidates[0]
        matched = True
    else:
        return {
            "action":        action,
            "domain":        domain or "light",
            "entity_ids":    [],
            "friendly":      "entité inconnue",
            "current_state": "unknown",
            "matched":       False,
        }

    friendly      = best.get("attributes", {}).get("friendly_name", best["entity_id"])
    current_state = best.get("state", "unknown")

    # Si plusieurs entités sélectionnées, on le mentionne
    if len(ids) > 1:
        friendly = f"{friendly} (+{len(ids)-1})"

    return {
        "action":        action,
        "domain":        domain or best["entity_id"].split(".")[0],
        "entity_ids":    ids,
        "friendly":      friendly,
        "current_state": current_state,
        "matched":       matched,
    }


def _build_response(parsed: dict) -> str:
    action   = parsed["action"]
    domain   = parsed["domain"]
    friendly = parsed["friendly"].rstrip(".")

    if action == "get_state":
        return f"{friendly} est actuellement {parsed['current_state']}."

    action_label = "allumé" if action == "turn_on" else "éteint"
    if domain == "cover":
        action_label = "ouvert" if action == "turn_on" else "fermé"
    elif domain == "climate":
        action_label = "activé" if action == "turn_on" else "désactivé"
    elif domain == "media_player":
        action_label = "démarré" if action == "turn_on" else "arrêté"

    return f"J'ai {action_label} {friendly}."


# ── Agent ─────────────────────────────────────────────────────────────────────
class HAAgent(BaseAgent):

    def __init__(self) -> None:
        super().__init__(name="ha_agent")
        self._headers = {
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type":  "application/json",
        }
        self._ha_states:    list                = []
        self._refresh_task: asyncio.Task | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────
    async def on_start(self) -> None:
        if not HA_ENABLED:
            self.logger.warning("HA désactivé (HA_ENABLED=false dans neron.yaml)")
            return
        if not HA_TOKEN:
            self.logger.warning("HA_TOKEN manquant dans neron.yaml")
            return

        self.logger.info("Connexion à %s …", HA_URL)
        self._ha_states = await self.get_states()

        if not self._ha_states:
            self.logger.error(
                "HA: 0 entité chargée — vérifiez HA_URL (%s) et HA_TOKEN", HA_URL
            )
        else:
            self.logger.info("HA states chargés : %d entités", len(self._ha_states))

        self._refresh_task = asyncio.create_task(self._refresh_loop())
        self.logger.info("HA refresh loop démarrée (intervalle : %d min)", HA_REFRESH_INTERVAL)

    async def on_stop(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
            self.logger.info("HA refresh loop arrêtée")

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(HA_REFRESH_INTERVAL * 60)
            await self.reload()

    async def reload(self) -> int:
        states = await self.get_states()
        if states:
            self._ha_states = states
            self.logger.info("HA reload OK — %d entités", len(self._ha_states))
        else:
            self.logger.warning("HA reload — cache conservé (%d entités)", len(self._ha_states))
        return len(self._ha_states)

    # ── Execute ───────────────────────────────────────────────────────────
    async def execute(self, query: str, **kwargs: Any) -> AgentResult:
        if not HA_ENABLED:
            return self._failure("Home Assistant non activé — configurez HA_ENABLED dans neron.yaml")
        if not HA_TOKEN:
            return self._failure("Token HA manquant — configurez HA_TOKEN dans neron.yaml")

        # Entités extraites par le NLP pipeline (room, device, target_state…)
        nlp_entities: dict = kwargs.get("entities", {}) or {}

        self.logger.info("HA action pour : %r  |  NLP entities : %s", query, nlp_entities)
        start = self._timer()

        if not self._ha_states:
            self.logger.warning("Cache HA vide — rechargement live")
            self._ha_states = await self.get_states()
            if not self._ha_states:
                return self._failure(
                    f"Home Assistant inaccessible ({HA_URL}) — vérifiez HA_URL et HA_TOKEN",
                    latency_ms=self._elapsed_ms(start),
                )

        parsed = _parse_query(query, self._ha_states, entities=nlp_entities)
        self.logger.info("Parsed : %s", parsed)

        if not parsed["matched"]:
            return self._failure(
                f"Aucune entité HA trouvée pour : '{query}'",
                latency_ms=self._elapsed_ms(start),
            )

        if parsed["action"] == "get_state":
            return self._success(
                content=_build_response(parsed),
                metadata={
                    "entity_ids":    parsed["entity_ids"],
                    "friendly":      parsed["friendly"],
                    "action":        "get_state",
                    "current_state": parsed["current_state"],
                },
                latency_ms=self._elapsed_ms(start),
            )

        service_url = f"{HA_URL}/api/services/{parsed['domain']}/{parsed['action']}"
        # entity_id accepte une liste dans l'API HA
        payload     = {"entity_id": parsed["entity_ids"]}

        try:
            async with httpx.AsyncClient(timeout=HA_TIMEOUT) as client:
                resp = await client.post(service_url, headers=self._headers, json=payload)
                resp.raise_for_status()
        except httpx.TimeoutException:
            return self._failure("Home Assistant timeout", latency_ms=self._elapsed_ms(start))
        except httpx.ConnectError:
            return self._failure(
                f"Home Assistant inaccessible : {HA_URL}", latency_ms=self._elapsed_ms(start)
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return self._failure("Token HA invalide ou expiré", latency_ms=self._elapsed_ms(start))
            if e.response.status_code == 404:
                return self._failure(
                    f"Service introuvable : {parsed['domain']}/{parsed['action']}",
                    latency_ms=self._elapsed_ms(start),
                )
            return self._failure(
                f"Erreur HA HTTP {e.response.status_code}", latency_ms=self._elapsed_ms(start)
            )
        except Exception as e:
            return self._failure(f"Erreur inattendue HA : {e}", latency_ms=self._elapsed_ms(start))

        latency = self._elapsed_ms(start)
        self.logger.info(
            "HA OK : %s → %s  entities=%s  (%sms)",
            parsed["domain"], parsed["action"], parsed["entity_ids"], latency,
        )

        return self._success(
            content=_build_response(parsed),
            metadata={
                "entity_ids": parsed["entity_ids"],
                "friendly":   parsed["friendly"],
                "action":     parsed["action"],
                "domain":     parsed["domain"],
                "ha_url":     HA_URL,
            },
            latency_ms=latency,
        )

    # ── API HA ────────────────────────────────────────────────────────────
    async def get_states(self) -> list:
        try:
            async with httpx.AsyncClient(timeout=HA_TIMEOUT) as client:
                resp = await client.get(f"{HA_URL}/api/states", headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            self.logger.error(
                "get_states HTTP %d%s", e.response.status_code,
                " — token invalide, vérifiez HA_TOKEN" if e.response.status_code == 401 else "",
            )
            return []
        except httpx.ConnectError:
            self.logger.error("get_states : impossible de joindre %s — vérifiez HA_URL", HA_URL)
            return []
        except Exception as e:
            self.logger.error("get_states : %s", e)
            return []

    async def check_connection(self) -> bool:
        if not HA_ENABLED or not HA_TOKEN:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{HA_URL}/api/", headers=self._headers)
                return resp.status_code == 200
        except Exception:
            return False
