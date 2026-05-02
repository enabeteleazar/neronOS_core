import time
from typing import Dict, Any

class WorldModel:
    def __init__(self):
        self._state: Dict[str, Any] = {
            "time": time.time(),
            "agents": {},
            "modules": {},
            "system": {}
        }
        self.last_updated: float = time.time()

    def update(self, category: str, key: str, value: Any):
        if category not in self._state:
            self._state[category] = {}

        self._state[category][key] = value
        self._state["timestamp"] = time.time()

    def get(self):
        return self._state

    def get_category(self, category: str):
        return self._state.get(category,{})


