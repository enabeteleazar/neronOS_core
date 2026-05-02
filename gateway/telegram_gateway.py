# core/gateway/telegram_gateway.py
# Gateway Telegram Néron.
# Utilise InternalGateway directement (pas d'appels HTTP vers soi-même).

from __future__ import annotations

import asyncio
import logging
import os
import time
import unicodedata
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.config import settings
from core.constants import CODE_KEYWORDS
from core.agents.automation.watchdog_agent import get_anomalies, get_health_score, get_status

logger = logging.getLogger("neron.gateway.telegram")

# ── Constantes ────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = settings.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = settings.TELEGRAM_CHAT_ID
ALLOWED_CHAT_IDS = set(filter(None, settings.TELEGRAM_CHAT_ID.split(",")))

_WORKSPACE = Path(
    os.getenv(
        "NERON_WORKSPACE",
        str(Path(__file__).parent.parent.parent / "workspace"),
    )
)

# ── Helpers de normalisation ──────────────────────────────────────────────────


def _normalize(text: str) -> str:
    n = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in n if unicodedata.category(c) != "Mn")


# ─────────────────────────────────────────────────────────────────────────────
# TelegramGateway
# ─────────────────────────────────────────────────────────────────────────────


class TelegramGateway:
    """
    Gateway Telegram Néron.
    Reçoit les messages Telegram et les route via InternalGateway.
    """

    def __init__(self, internal=None) -> None:
        self.internal = internal
        self._app: Application | None = None
        self._agents: dict = {}  # agents optionnels (memory, ha…)

    # ─────────────────────────────────────────────
    # INJECTION
    # ─────────────────────────────────────────────

    def set_internal(self, internal) -> None:
        self.internal = internal

    def set_agents(self, agents: dict) -> None:
        """Injecte les agents optionnels (memory, ha, etc.)."""
        self._agents = agents

    # ─────────────────────────────────────────────
    # LIFECYCLE
    # ─────────────────────────────────────────────

    async def start(self) -> None:
        if not TELEGRAM_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN absent — bot désactivé")
            return
        if not settings.TELEGRAM_ENABLED:
            logger.info("Telegram désactivé dans la config")
            return

        self._app = Application.builder().token(TELEGRAM_TOKEN).build()
        self._register_handlers()
        self._app.add_error_handler(self._on_error)

        await self._app.initialize()

        # ── Coupe toute session existante côté Telegram ───────────────────
        # delete_webhook() ne suffit pas : il ne coupe que les sessions webhook,
        # pas les long-polls getUpdates.
        # get_updates(timeout=0) force Telegram à terminer immédiatement la
        # session getUpdates précédente (le process mort laisse sa connexion
        # ouverte ~30-60s côté serveur Telegram).
        try:
            await self._app.bot.get_updates(offset=-1, timeout=0, limit=1)
            logger.info("Session getUpdates précédente terminée")
        except Exception as e:
            logger.debug("get_updates pré-démarrage : %s (ignoré)", e)

        await self._app.start()

        await self._app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        logger.info("Bot Telegram démarré — 10 commandes enregistrées")

        if settings.TELEGRAM_NOTIFY_START:
            await self._notify("ℹ Néron démarré et prêt.", level="info")

    async def _on_error(self, update: object, context) -> None:
        """
        Error handler global de l'Application.
        Sur Conflict : schedule l'arrêt du polling via create_task()
        pour sortir du network_retry_loop de PTB sans deadlock.
        """
        import asyncio
        from telegram.error import Conflict, NetworkError
        err = context.error

        if isinstance(err, Conflict):
            logger.error(
                "Telegram Conflict : session concurrente détectée — "
                "arrêt du polling en cours."
            )
            # create_task() obligatoire : appeler updater.stop() directement
            # depuis le callback ne sort pas du network_retry_loop de PTB.
            if self._app and self._app.updater:
                asyncio.create_task(self._app.updater.stop())
            return

        if isinstance(err, NetworkError):
            logger.warning("Telegram NetworkError (transitoire) : %s", err)
            return

        logger.exception("Erreur Telegram non gérée : %s", err)

    async def stop(self) -> None:
        if not self._app:
            return
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Bot Telegram arrêté")
        except Exception as e:
            logger.error("Erreur stop TelegramGateway : %s", e)
        finally:
            self._app = None

    # ─────────────────────────────────────────────
    # NOTIFICATIONS SORTANTES
    # ─────────────────────────────────────────────

    async def send_notification(self, message: str, level: str = "info") -> None:
        await self._notify(message, level)

    async def _notify(self, message: str, level: str = "info") -> None:
        if not self._app or not TELEGRAM_CHAT_ID:
            return
        icons = {"info": "ℹ", "warning": "⚠", "alert": "🔴", "error": "❌"}
        icon  = icons.get(level, "📢")
        try:
            await self._app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"{icon} {message}",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error("Erreur notification Telegram : %s", e)

    # ─────────────────────────────────────────────
    # ENREGISTREMENT DES HANDLERS
    # ─────────────────────────────────────────────

    def _register_handlers(self) -> None:
        assert self._app is not None
        add = self._app.add_handler
        add(CommandHandler("start",     self._cmd_start))
        add(CommandHandler("help",      self._cmd_help))
        add(CommandHandler("status",    self._cmd_status))
        add(CommandHandler("memory",    self._cmd_memory))
        add(CommandHandler("ha_reload", self._cmd_ha_reload))
        add(CommandHandler("call",      self._cmd_call))
        add(CommandHandler("workspace", self._cmd_workspace))
        add(CommandHandler("fix",       self._cmd_fix))
        add(CommandHandler("review",    self._cmd_review))
        add(CommandHandler("run",       self._cmd_run))
        add(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

    # ─────────────────────────────────────────────
    # AUTH
    # ─────────────────────────────────────────────

    def _is_authorized(self, update: Update) -> bool:
        if not ALLOWED_CHAT_IDS:
            return True
        return str(update.message.chat_id) in ALLOWED_CHAT_IDS

    async def _unauthorized(self, update: Update) -> None:
        await update.message.reply_text("⛔ Accès non autorisé")
        logger.warning("Accès refusé : chat_id=%s", update.message.chat_id)

    # ─────────────────────────────────────────────
    # COMMANDES
    # ─────────────────────────────────────────────

    async def _cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        await update.message.reply_text(
            "👋 Bonjour ! Je suis <b>Néron</b>, ton assistant IA personnel.\n"
            "Tape /help pour voir les commandes disponibles.",
            parse_mode="HTML",
        )

    async def _cmd_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        await update.message.reply_text(
            "🤖 <b>Néron — Commandes disponibles</b>\n\n"
            "💬 <b>Conversation</b>\n"
            "  Envoyez n'importe quel message pour parler à Néron\n\n"
            "🔧 <b>Code</b>\n"
            "  /fix &lt;fichier.py&gt; — améliore un fichier\n"
            "  /review — auto-review du code\n"
            "  /run &lt;fichier.py&gt; — exécute un script du workspace\n"
            "  /workspace — liste les fichiers du workspace\n\n"
            "🧠 <b>Mémoire</b>\n"
            "  /memory — 5 derniers échanges\n\n"
            "🏠 <b>Home Assistant</b>\n"
            "  /ha_reload — recharge les entités HA\n\n"
            "📊 <b>Système</b>\n"
            "  /status — CPU, RAM, disque, uptime\n\n"
            "📞 <b>Téléphonie</b>\n"
            "  /call [message] — appel vocal via Twilio\n\n"
            "❓ /help — cette aide",
            parse_mode="HTML",
        )

    async def _cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        try:
            st = get_status()
            if "error" in st:
                await update.message.reply_text(f"❌ Erreur système : {st['error']}")
                return
            uptime_min = st.get("uptime_s", 0) // 60
            health     = get_health_score()
            score      = health.get("score", "N/A")
            from core.modules.scheduler import get_jobs
            jobs      = get_jobs()
            jobs_text = (
                "\n".join(f"  • {j['name']} — {j['next_run']}" for j in jobs)
                or "  Aucune"
            )
            await update.message.reply_text(
                f"📊 <b>État Néron</b>\n\n"
                f"🖥 CPU     : {st.get('cpu_pct', '?')}%\n"
                f"💾 RAM     : {st.get('ram_pct', '?')}% ({st.get('ram_used_mb', '?')} MB)\n"
                f"💿 Disque  : {st.get('disk_pct', '?')}%\n"
                f"🧠 Process : {st.get('process_ram_mb', '?')} MB\n"
                f"⏱ Uptime  : {uptime_min} min\n"
                f"❤ Santé   : {score}/100\n\n"
                f"📅 <b>Tâches planifiées</b>\n{jobs_text}",
                parse_mode="HTML",
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Erreur: {e}")

    async def _cmd_memory(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        try:
            mem_agent = self._agents.get("memory")
            if not mem_agent:
                await update.message.reply_text("❌ Agent mémoire non disponible")
                return
            entries = mem_agent.retrieve(limit=5)
            if not entries:
                await update.message.reply_text("📭 Mémoire vide")
                return
            lines = ["🧠 <b>Derniers échanges</b>\n"]
            for e in reversed(entries):
                lines.append(f"👤 {e['input'][:60]}")
                lines.append(f"🤖 {e['response'][:80]}\n")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Erreur: {e}")

    async def _cmd_ha_reload(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        ha_agent = self._agents.get("ha")
        if not ha_agent:
            await update.message.reply_text("❌ Agent Home Assistant non disponible")
            return
        sent = await update.message.reply_text("🔄 Rechargement des entités HA...")
        try:
            count = await ha_agent.reload()
            await sent.edit_text(f"✅ {count} entités HA rechargées")
        except Exception as e:
            await sent.edit_text(f"❌ Erreur HA : {e}")

    async def _cmd_call(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not settings.TWILIO_ENABLED:
            await update.message.reply_text("❌ Twilio non activé (TWILIO_ENABLED=false)")
            return
        message = " ".join(context.args) if context.args else "Appel depuis Néron."
        sent    = await update.message.reply_text("📞 Appel en cours...")
        try:
            from core.agents.communication.twilio_agent import call as twilio_call
            result = twilio_call(message)
            await sent.edit_text(f"✅ Appel passé : {result}")
        except Exception as e:
            await sent.edit_text(f"❌ Erreur appel : {e}")

    async def _cmd_workspace(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        try:
            if not _WORKSPACE.exists():
                await update.message.reply_text(
                    f"📁 Workspace vide ou inexistant : {_WORKSPACE}"
                )
                return
            files = sorted(_WORKSPACE.rglob("*.py"))[:30]
            if not files:
                await update.message.reply_text("📁 Aucun fichier .py dans le workspace")
                return
            lines = [f"📁 <b>Workspace</b> ({len(files)} fichiers)\n"]
            for f in files:
                lines.append(f"  • {f.relative_to(_WORKSPACE)}")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(f"❌ Erreur workspace : {e}")

    async def _cmd_fix(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not context.args:
            await update.message.reply_text("Usage : /fix <fichier.py>")
            return
        filename = context.args[0]
        sent     = await update.message.reply_text(f"🔧 Analyse de {filename}...")
        try:
            response = await self.internal.handle_text(
                f"améliore et corrige le fichier {filename}",
                session_id="telegram_fix",
            )
            await sent.edit_text(response[:4096])
        except Exception as e:
            await sent.edit_text(f"❌ Erreur fix : {e}")

    async def _cmd_review(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        sent = await update.message.reply_text("🔍 Auto-review en cours...")
        try:
            response = await self.internal.handle_text(
                "lance un auto-review complet du code",
                session_id="telegram_review",
            )
            await sent.edit_text(response[:4096])
        except Exception as e:
            await sent.edit_text(f"❌ Erreur review : {e}")

    async def _cmd_run(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return await self._unauthorized(update)
        if not context.args:
            await update.message.reply_text("Usage : /run <fichier.py>")
            return
        filename = context.args[0]
        target   = _WORKSPACE / filename
        if not target.exists():
            await update.message.reply_text(f"❌ Fichier introuvable : {filename}")
            return
        sent = await update.message.reply_text(f"▶ Exécution de {filename}...")
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            output    = stdout.decode("utf-8", errors="replace")[:3000] or "(aucune sortie)"
            await sent.edit_text(f"<pre>{output}</pre>", parse_mode="HTML")
        except asyncio.TimeoutError:
            await sent.edit_text("⏱ Timeout — script interrompu après 30s")
        except Exception as e:
            await sent.edit_text(f"❌ Erreur exécution : {e}")

    # ─────────────────────────────────────────────
    # HANDLER MESSAGES TEXTE
    # ─────────────────────────────────────────────

    async def _handle_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not self._is_authorized(update):
            return await self._unauthorized(update)

        # Signal d'activité pour le watchdog
        try:
            from core.agents.automation import watchdog_agent as _wdog
            _wdog._last_conversation = time.monotonic()
        except Exception:
            pass

        user_message = update.message.text
        await update.message.chat.send_action("typing")
        sent = await update.message.reply_text("⏳ Néron réfléchit...")

        q       = _normalize(user_message)
        is_code = any(_normalize(kw) in q for kw in CODE_KEYWORDS)

        chat_id    = str(update.message.chat_id)
        session_id = f"tg_{chat_id}"

        try:
            if is_code:
                # Réponse non-streaming pour le code
                response = await self.internal.handle_text(user_message, session_id)
                await sent.edit_text(response[:4096])
            else:
                # Réponse streaming pour la conversation
                accumulated = ""
                last_edit   = ""
                last_update = time.time()

                async for token in self.internal.stream(user_message, session_id):
                    accumulated += token
                    now = time.time()
                    if (now - last_update > 2.0) and accumulated != last_edit:
                        try:
                            await sent.edit_text(accumulated or "⏳")
                            last_edit   = accumulated
                            last_update = now
                        except Exception:
                            pass

                # Édition finale
                final = accumulated or "❌ Pas de réponse du LLM"
                if final != last_edit:
                    try:
                        await sent.edit_text(final[:4096])
                    except Exception:
                        pass

        except Exception as e:
            logger.exception("handle_message error : %s", e)
            try:
                await sent.edit_text(f"❌ Erreur: {e}")
            except Exception:
                pass
