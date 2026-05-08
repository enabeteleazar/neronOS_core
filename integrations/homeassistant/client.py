import httpx


class HomeAssistantClient:

    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def get_states(self) -> list[dict]:
        """Récupère toutes les entités Home Assistant."""
        async with httpx.AsyncClient(headers=self._headers, timeout=self.timeout) as client:
            r = await client.get(f"{self.base_url}/api/states")
            r.raise_for_status()
            return r.json()

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_ids: str | list[str],
        extra_data: dict | None = None,
    ) -> dict:
        """Appelle un service Home Assistant."""
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]

        payload = {"entity_id": entity_ids}
        if extra_data:
            payload.update(extra_data)

        async with httpx.AsyncClient(headers=self._headers, timeout=self.timeout) as client:
            r = await client.post(
                f"{self.base_url}/api/services/{domain}/{service}",
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    async def get_state(self, entity_id: str) -> dict:
        """Récupère l'état d'une entité précise."""
        async with httpx.AsyncClient(headers=self._headers, timeout=self.timeout) as client:
            r = await client.get(f"{self.base_url}/api/states/{entity_id}")
            r.raise_for_status()
            return r.json()
