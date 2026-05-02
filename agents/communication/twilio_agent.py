# core/agents/communication/twilio_agent.py
# Néron — Agent Twilio

from __future__ import annotations

from core.agents.base_agent import get_logger
from .twilio_tool import call, sms

logger = get_logger("twilio_agent")


class TwilioAgent:
    """
    Agent Twilio :
    - SMS
    - Appels vocaux
    """

    def __init__(self):
        pass

    async def run(self, message: str, mode: str = "sms", to: str | None = None) -> dict:
        """
        mode:
            - sms
            - call
        """
        try:
            if mode == "call":
                return call(message, to)

            return sms(message, to)

        except Exception as e:
            logger.error("TwilioAgent error: %s", e)
            return {"ok": False, "error": str(e)}

    def status(self) -> dict:
        return {
            "agent": "TwilioAgent",
            "available": True,
        }
