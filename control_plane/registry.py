# core/control_plane/registry.py

class Registry:
    """
    Registre central de tous les services Néron.
    """

    def __init__(self) -> None:
        self._services: dict[str, object] = {}
        self._agents: dict[str, object] = {}

    # ───────────────────────────────
    # SERVICES
    # ───────────────────────────────

    def register_service(self, name: str, service: object) -> None:
        self._services[name] = service

    def get_service(self, name: str):
        return self._services.get(name)

    def list_services(self) -> list[str]:
        return list(self._services.keys())

    # ───────────────────────────────
    # AGENTS
    # ───────────────────────────────

    def register_agent(self, name: str, agent: object) -> None:
        self._agents[name] = agent

    def get_agent(self, name: str):
        return self._agents.get(name)

    def list_agents(self) -> list[str]:
        return list(self._agents.keys())
