# PATCH : core/agents/communication/telegram_agent.py
# Ajouter les commandes suivantes dans la méthode _register_handlers()
# (ou équivalent selon votre version).
#
# Ce fichier est un DIFF partiel — intégrer les blocs ci-dessous dans
# le handler existant, ne pas remplacer tout le fichier.
#
# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS À AJOUTER en tête de fichier :
# ─────────────────────────────────────────────────────────────────────────────
#
# from core.agents.io.news_agent    import NewsAgent
# from core.agents.io.weather_agent import WeatherAgent
# from core.agents.core.todo_agent  import TodoAgent
# from core.agents.io.wiki_agent    import WikiAgent
#
# _news_agent    = NewsAgent()
# _weather_agent = WeatherAgent()
# _todo_agent    = TodoAgent()
# _wiki_agent    = WikiAgent()
#
# ─────────────────────────────────────────────────────────────────────────────
# COMMANDES À ENREGISTRER dans _register_handlers() :
# ─────────────────────────────────────────────────────────────────────────────

"""
Exemples de handlers à câbler avec python-telegram-bot v20+ (ApplicationBuilder).
Adapter selon votre version et votre architecture de bot.
"""

from telegram import Update
from telegram.ext import ContextTypes


# ── /news [catégorie] ─────────────────────────────────────────────────────────

async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Envoie les titres d'actualité. Ex: /news tech"""
    from core.agents.io.news_agent import NewsAgent
    args  = " ".join(context.args) if context.args else ""
    reply = await NewsAgent().run(args)
    await update.message.reply_text(reply, parse_mode="Markdown")


# ── /meteo [ville] ───────────────────────────────────────────────────────────

async def cmd_meteo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Affiche la météo. Ex: /meteo Lyon"""
    from core.agents.io.weather_agent import WeatherAgent
    args  = " ".join(context.args) if context.args else ""
    query = f"météo à {args}" if args else "météo"
    reply = await WeatherAgent().run(query)
    await update.message.reply_text(reply, parse_mode="Markdown")


# ── /todo [add|done|clear|list] ──────────────────────────────────────────────

async def cmd_todo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Gère la todo list.
    Ex: /todo add Faire les courses
        /todo done 3
        /todo clear
        /todo         (affiche la liste)
    """
    from core.agents.core.todo_agent import TodoAgent
    args  = " ".join(context.args) if context.args else ""
    agent = TodoAgent()
    reply = await agent.handle_command(args)
    await update.message.reply_text(reply, parse_mode="Markdown")


# ── /wiki <sujet> ─────────────────────────────────────────────────────────────

async def cmd_wiki(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Résumé Wikipédia. Ex: /wiki intelligence artificielle"""
    from core.agents.io.wiki_agent import WikiAgent
    args  = " ".join(context.args) if context.args else ""
    query = f"qu'est-ce que {args}" if args else ""
    reply = await WikiAgent().run(query)
    await update.message.reply_text(reply, parse_mode="Markdown", disable_web_page_preview=True)


# ─────────────────────────────────────────────────────────────────────────────
# ENREGISTREMENT — à ajouter dans _register_handlers() :
# ─────────────────────────────────────────────────────────────────────────────
#
#   from telegram.ext import CommandHandler
#   application.add_handler(CommandHandler("news",  cmd_news))
#   application.add_handler(CommandHandler("meteo", cmd_meteo))
#   application.add_handler(CommandHandler("todo",  cmd_todo))
#   application.add_handler(CommandHandler("wiki",  cmd_wiki))
