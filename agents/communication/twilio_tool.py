# core/agents/communication/twilio_tool.py
# Néron — Outil Twilio (SMS + appel)

from __future__ import annotations

import logging
from twilio.rest import Client
from core.config import settings

logger = logging.getLogger("twilio_tool")


def _get_client() -> Client:
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        raise ValueError("Twilio non configuré — vérifiez neron.yaml")
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def call(message: str, to: str | None = None) -> dict:
    """Passe un appel vocal avec TTS Twilio."""
    if not settings.TWILIO_ENABLED:
        return {"ok": False, "error": "Twilio désactivé"}

    to_number = to or settings.TWILIO_TO
    if not to_number:
        return {"ok": False, "error": "Numéro de destination manquant"}

    try:
        client = _get_client()

        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f'<Say language="fr-FR" voice="Polly.Lea">{message}</Say>'
            "<Pause length=\"1\"/>"
            '<Say language="fr-FR" voice="Polly.Lea">Fin du message de Néron.</Say>'
            "</Response>"
        )

        call_obj = client.calls.create(
            twiml=twiml,
            to=to_number,
            from_=settings.TWILIO_FROM,
        )

        logger.info("Call Twilio lancé — SID: %s", call_obj.sid)
        return {"ok": True, "sid": call_obj.sid}

    except Exception as e:
        logger.error("Erreur call Twilio: %s", e)
        return {"ok": False, "error": str(e)}


def sms(message: str, to: str | None = None) -> dict:
    """Envoie un SMS via Twilio."""
    if not settings.TWILIO_ENABLED:
        return {"ok": False, "error": "Twilio désactivé"}

    to_number = to or settings.TWILIO_TO
    if not to_number:
        return {"ok": False, "error": "Numéro de destination manquant"}

    try:
        client = _get_client()

        msg = client.messages.create(
            body=message[:1600],
            to=to_number,
            from_=settings.TWILIO_FROM,
        )

        logger.info("SMS envoyé — SID: %s", msg.sid)
        return {"ok": True, "sid": msg.sid}

    except Exception as e:
        logger.error("Erreur SMS Twilio: %s", e)
        return {"ok": False, "error": str(e)}
