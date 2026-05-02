# core/control_plane/health.py

import psutil


class HealthManager:
    """
    Surveillance système simple.
    """

    def system(self) -> dict:
        return {
            "cpu": psutil.cpu_percent(),
            "ram": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage("/").percent,
        }

    def process(self) -> dict:
        p = psutil.Process()
        return {
            "memory_mb": p.memory_info().rss / 1024 / 1024,
            "cpu_percent": p.cpu_percent(),
        }
