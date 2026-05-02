# core/control_plane/lifecycle.py

import logging

logger = logging.getLogger("neron.lifecycle")


class LifecycleManager:
    """
    Gère le cycle de vie global du système.
    """

    async def start_all(self, registry) -> None:
        logger.info("Starting all services...")

        for name, service in registry._services.items():
            if hasattr(service, "start"):
                logger.info("Starting %s", name)
                await service.start()

    async def stop_all(self, registry) -> None:
        logger.info("Stopping all services...")

        for name, service in registry._services.items():
            if hasattr(service, "stop"):
                logger.info("Stopping %s", name)
                await service.stop()
