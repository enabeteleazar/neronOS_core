# core/control_plane/events.py

from collections import defaultdict
from typing import Callable, Any


class EventBus:
    """
    Bus d'événements interne simple (sync).
    """

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable]] = defaultdict(list)

    def on(self, event: str, callback: Callable) -> None:
        self._listeners[event].append(callback)

    def emit(self, event: str, data: Any = None) -> None:
        for cb in self._listeners.get(event, []):
            try:
                cb(data)
            except Exception:
                pass
