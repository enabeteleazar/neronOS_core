# core/agents/telegram_agent.py Neron Core - Bot Telegram intégré (sans port séparé)

from __future__ import annotations

import asyncio
import json
import os
import time
import unicodedata
from pathlib import Path

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from core.constants import CODE_KEYWORDS
from core.agents.base_agent import get_logger
from core.agents.automation.watchdog_agent import get_anomalies, get_health_score, get_status
from core.config import settings
from core.agents.communication.twilio_agent import call as twilio_call

logger = get_logger("telegram_agent")

# ── Constantes ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = settings.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = settings.TELEGRAM_CHAT_ID
NERON_CORE_URL   = f"http://127.0.0.1:{settings.SERVER_PORT}"
NERON_API_KEY    = settings.API_KEY
ALLOWED_CHAT_IDS = set(filter(None, settings.TELEGRAM_CHAT_ID.split(",")))

_WORKSPACE = Path(os.getenv("NERON_WORKSPACE", str(Path(__file__).parent.parent.parent / "workspace")))

# ── État global ───────────────────────────────────────────────────────────────
_agents: dict              = {}
_telegram_app: Application | None = None

# ── Gestion des agents ────────────────────────────────────────────────────────
def set_agents(agents: dict) -> None:
    global _agents
    _agents = agents

# ── Auth ──────────────────────────────────────────────────────────────────────
def is_authorized(update: Update) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    return str(update.message.chat_id) in ALLOWED_CHAT_IDS

async def unauthorized(update: Update) -> None:
    await update.message.reply_text("⛔ Accès non autorisé")
    logger.warning("Accès refusé: chat_id=%s", update.message.chat_id)

# ── Helpers ───────────────────────────────────────────────────────────────────
def _normalize(text: str) -> str:
    n = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in n if unicodedata.category(c) != "Mn")

async def _post_text(client: httpx.AsyncClient, text: str) -> dict:
    resp = await client.post(
        f"{NERON_CORE_URL}/input/text",
        json={"text": text},
        headers={"X-API-Key": NERON_API_KEY},
    )
    resp.raise_for_status()
    return resp.json()

# ── Commandes ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return await unauthorized(update)
    await update.message.reply_text(
        "👋 Bonjour ! Je suis <b>Néron</b>, ton assistant IA personnel.\n"
        "Tape /help pour voir les commandes disponibles.",
        parse_mode="HTML",
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return await unauthorized(update)
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

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return await unauthorized(update)
    try:
        st     = get_status()
        if "error" in st:
            await update.message.reply_text(f"❌ Erreur système : {st['error']}")
            return
        uptime_min = st.get("uptime_s", 0) // 60
        health     = get_health_score()
        score      = health.get("score", "N/A")
        from core.modules.scheduler import get_jobs
        jobs      = get_jobs()
        jobs_text = "\n".join(f"  • {j['name']} — {j['next_run']}" for j in jobs) or "  Aucune"
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

async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return await unauthorized(update)
    try:
        mem_agent = _agents.get("memory")
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

async def cmd_ha_reload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return await unauthorized(update)
    ha_agent = _agents.get("ha")
    if not ha_agent:
        await update.message.reply_text("❌ Agent Home Assistant non disponible")
        return
    sent = await update.message.reply_text("🔄 Rechargement des entités HA...")
    try:
        count = await ha_agent.reload()
        await sent.edit_text(f"✅ {count} entités HA rechargées")
    except Exception as e:
        await sent.edit_text(f"❌ Erreur HA : {e}")

async def cmd_call(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return await unauthorized(update)
    if not settings.TWILIO_ENABLED:
        await update.message.reply_text("❌ Twilio non activé (TWILIO_ENABLED=false)")
        return
    message = " ".join(context.args) if context.args else "Appel depuis Néron."
    sent    = await update.message.reply_text("📞 Appel en cours...")
    try:
        result = twilio_call(message)
        await sent.edit_text(f"✅ Appel passé : {result}")
    except Exception as e:
        await sent.edit_text(f"❌ Erreur appel : {e}")

async def cmd_workspace(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return await unauthorized(update)
    try:
        if not _WORKSPACE.exists():
            await update.message.reply_text(f"📁 Workspace vide ou inexistant : {_WORKSPACE}")
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

async def cmd_fix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return await unauthorized(update)
    if not context.args:
        await update.message.reply_text("Usage : /fix <fichier.py>")
        return
    filename = context.args[0]
    sent     = await update.message.reply_text(f"🔧 Analyse de {filename}...")
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            data = await _post_text(client, f"améliore et corrige le fichier {filename}")
            await sent.edit_text(data.get("response", "❌ Pas de réponse")[:4096])
    except Exception as e:
        await sent.edit_text(f"❌ Erreur fix : {e}")

async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return await unauthorized(update)
    sent = await update.message.reply_text("🔍 Auto-review en cours...")
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            data = await _post_text(client, "lance un auto-review complet du code")
            await sent.edit_text(data.get("response", "❌ Pas de réponse")[:4096])
    except Exception as e:
        await sent.edit_text(f"❌ Erreur review : {e}")

async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return await unauthorized(update)
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

# ── Handler messages texte ────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return await unauthorized(update)

    try:
        import server.core.agents.watchdog_agent as _wdog_mod
        _wdog_mod._last_conversation = time.monotonic()
    except Exception:
        pass

    user_message = update.message.text
    await update.message.chat.send_action("typing")
    sent = await update.message.reply_text("⏳ Néron réfléchit...")

    q       = _normalize(user_message)
    is_code = any(_normalize(kw) in q for kw in CODE_KEYWORDS)

    try:
        if is_code:
            async with httpx.AsyncClient(timeout=600.0) as client:
                data     = await _post_text(client, user_message)
                response = data.get("response", "❌ Pas de réponse")
                await sent.edit_text(response[:4096], parse_mode=None)
        else:
            accumulated = ""
            last_edit   = ""
            last_update = time.time()
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST",
                    f"{NERON_CORE_URL}/input/stream",
                    json={"text": user_message},
                    headers={"X-API-Key": NERON_API_KEY},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        token = data.get("token", "")
                        done  = data.get("done", False)
                        accumulated += token
                        now = time.time()
                        if (now - last_update > 2.0 or done) and accumulated != last_edit:
                            try:
                                await sent.edit_text(accumulated or "⏳")
                                last_edit   = accumulated
                                last_update = now
                            except Exception:
                                pass
                        if done:
                            break
            if not accumulated:
                await sent.edit_text("❌ Pas de réponse du LLM")
    except Exception as e:
        logger.exception("handle_message error : %s", e)
        await sent.edit_text(f"❌ Erreur: {e}")

# ── Notifications ─────────────────────────────────────────────────────────────
async def send_notification(message: str, level: str = "info") -> None:
    if not _telegram_app or not TELEGRAM_CHAT_ID:
        return
    icons = {"info": "ℹ", "warning": "⚠", "alert": "🔴", "error": "❌"}
    icon  = icons.get(level, "📢")
    try:
        await _telegram_app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"{icon} {message}",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error("Erreur notification Telegram : %s", e)

# ── Lifecycle ─────────────────────────────────────────────────────────────────
async def start_bot() -> None:
    global _telegram_app
    if not TELEGRAM_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN manquant — bot désactivé")
        return

    _telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()

    _telegram_app.add_handler(CommandHandler("start",     cmd_start))
    _telegram_app.add_handler(CommandHandler("help",      cmd_help))
    _telegram_app.add_handler(CommandHandler("status",    cmd_status))
    _telegram_app.add_handler(CommandHandler("memory",    cmd_memory))
    _telegram_app.add_handler(CommandHandler("ha_reload", cmd_ha_reload))
    _telegram_app.add_handler(CommandHandler("call",      cmd_call))
    _telegram_app.add_handler(CommandHandler("workspace", cmd_workspace))
    _telegram_app.add_handler(CommandHandler("fix",       cmd_fix))
    _telegram_app.add_handler(CommandHandler("review",    cmd_review))
    _telegram_app.add_handler(CommandHandler("run",       cmd_run))
    _telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await _telegram_app.initialize()
    await _telegram_app.start()
    try:
        await _telegram_app.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        logger.info("Bot Telegram démarré — 10 commandes enregistrées")
    except Exception as e:
        # Gestion conflict (409) si une autre instance utilise getUpdates
        try:
            import telegram as _telegram_mod
            if isinstance(e, _telegram_mod.error.Conflict):
                logger.warning("Telegram polling conflict: une autre instance est active; polling ignoré")
                return
        except Exception:
            pass
        logger.exception("Erreur lors demarrage du polling Telegram: %s", e)
        raise

async def stop_bot() -> None:
    global _telegram_app
    if not _telegram_app:
        return
    try:
        await _telegram_app.updater.stop()
        await _telegram_app.stop()
        await _telegram_app.shutdown()
        logger.info("Bot Telegram arrêté")
    except Exception as e:
        logger.error("Erreur stop_bot : %s", e)
    finally:
        _telegram_app = None

