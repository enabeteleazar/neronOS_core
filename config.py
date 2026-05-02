# neron_core/config.py
# Loader de configuration — neron.yaml (priorité) + .env (fallback)

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

NERON_DIR = Path(os.getenv("NERON_DIR", Path(__file__).parent.parent))
YAML_PATH = Path("/etc/neron/neron.yaml")

# Niveaux de log valides — utilisé pour valider LOG_LEVEL
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _load_yaml() -> dict:
    if yaml is None:
        return {}
    if not YAML_PATH.exists():
        return {}
    with open(YAML_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # FIX: %s au lieu de f-string dans le log
    logger.info("Config chargée depuis %s", YAML_PATH)
    return data


def _get(cfg: dict, *keys: str, fallback_env: str = "", default: Any = None) -> Any:
    value = cfg
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            value = None
            break
    if value is not None and value != "":
        return value
    if fallback_env:
        env_value = os.getenv(fallback_env)
        if env_value is not None and env_value != "":
            return env_value
    return default


# Exposé pour scheduler.py qui accède à settings._cfg
_cfg = _load_yaml()


class Config:
    # ── General ───────────────────────────────────────────────────────────
    VERSION  = _get(_cfg, "neron", "version",   fallback_env="NERON_VERSION",  default="2.1.0")
    API_KEY  = _get(_cfg, "neron", "api_key",   fallback_env="NERON_API_KEY",  default="changez_moi")

    # FIX: validation du log_level — avertit si valeur invalide (ex: "LOGGER")
    _raw_log_level = str(_get(_cfg, "neron", "log_level", fallback_env="LOG_LEVEL", default="INFO")).upper()
    LOG_LEVEL      = _raw_log_level if _raw_log_level in _VALID_LOG_LEVELS else "INFO"

    # ── Serveur ───────────────────────────────────────────────────────────
    SERVER_HOST = _get(_cfg, "server", "host", fallback_env="SERVER_HOST",     default="0.0.0.0")
    SERVER_PORT = int(_get(_cfg, "server", "port", fallback_env="NERON_CORE_HTTP", default=8010))

    # ── LLM ───────────────────────────────────────────────────────────────
    OLLAMA_MODEL    = _get(_cfg, "llm", "model",       fallback_env="OLLAMA_MODEL", default="llama3.2:1b")
    OLLAMA_HOST     = _get(_cfg, "llm", "host",        fallback_env="OLLAMA_HOST",  default="http://localhost:11434")
    SYSTEM_PROMPT   = _cfg.get("neron", {}).get("system_prompt", "Tu es Néron, un assistant IA personnel.")
    LLM_TIMEOUT     = float(_get(_cfg, "llm", "timeout",     fallback_env="LLM_TIMEOUT", default=120))
    LLM_TEMPERATURE = float(_get(_cfg, "llm", "temperature",                             default=0.7))
    LLM_MAX_TOKENS  = int(_get(_cfg, "llm",   "max_tokens",                              default=2048))

    # Section lue par NéronLLMClient
    _neron_llm_cfg = _cfg.get("neron_llm", {})
    NERON_LLM: dict = {
        "url":     _neron_llm_cfg.get("url",     "http://localhost:8765"),
        "timeout": float(_neron_llm_cfg.get("timeout", 30)),
        "retry":   int(_neron_llm_cfg.get("retry",   2)),
    }

    # ── STT (désactivé — conservé pour compatibilité) ─────────────────────
    WHISPER_MODEL         = _get(_cfg, "stt", "model",          fallback_env="WHISPER_MODEL",         default="base")
    WHISPER_LANG          = _get(_cfg, "stt", "language",       fallback_env="WHISPER_LANGUAGE",      default="fr")
    STT_TIMEOUT           = int(_get(_cfg, "stt", "timeout",    fallback_env="STT_TIMEOUT",           default=60))
    AUDIO_MAX_MB          = int(_get(_cfg, "stt", "max_size_mb", fallback_env="AUDIO_MAX_SIZE_MB",    default=10))
    WHISPER_DOWNLOAD_ROOT = _get(_cfg, "stt", "download_root",  fallback_env="WHISPER_DOWNLOAD_ROOT",
                                 default=str(NERON_DIR / "data" / "models"))

    # ── TTS (désactivé — conservé pour compatibilité) ─────────────────────
    TTS_ENGINE    = _get(_cfg, "tts", "engine",   fallback_env="TTS_ENGINE",   default="pyttsx3")
    TTS_LANGUAGE  = _get(_cfg, "tts", "language", fallback_env="TTS_LANGUAGE", default="fr")
    TTS_RATE      = int(_get(_cfg, "tts", "rate",      fallback_env="TTS_RATE",      default=150))
    TTS_MAX_CHARS = int(_get(_cfg, "tts", "max_chars", fallback_env="TTS_MAX_CHARS", default=1000))

    # ── Telegram ──────────────────────────────────────────────────────────
    TELEGRAM_ENABLED      = str(_get(_cfg, "telegram", "enabled",         fallback_env="TELEGRAM_ENABLED",   default=False)).lower() == "true"
    TELEGRAM_BOT_TOKEN    = _get(_cfg, "telegram", "bot_token",           fallback_env="TELEGRAM_BOT_TOKEN", default="")
    TELEGRAM_CHAT_ID      = _get(_cfg, "telegram", "chat_id",             fallback_env="TELEGRAM_CHAT_ID",   default="")
    TELEGRAM_NOTIFY_START = str(_get(_cfg, "telegram", "notify_on_start", default=True)).lower() != "false"

    # ── Watchdog ──────────────────────────────────────────────────────────
    WATCHDOG_ENABLED        = str(_get(_cfg, "watchdog", "enabled",             fallback_env="WATCHDOG_ENABLED",        default=False)).lower() == "true"
    WATCHDOG_INTERVAL       = int(_get(_cfg, "watchdog", "check_interval",      fallback_env="WATCHDOG_INTERVAL",       default=30))
    WATCHDOG_MAX_RETRIES    = int(_get(_cfg, "watchdog", "restart_max_retries",                                         default=3))
    WATCHDOG_ALERT_TG       = str(_get(_cfg, "watchdog", "alert_telegram",      default=True)).lower() != "false"
    WATCHDOG_BOT_TOKEN      = _get(_cfg, "watchdog", "bot_token",               fallback_env="WATCHDOG_BOT_TOKEN",      default="")
    WATCHDOG_CHAT_ID        = _get(_cfg, "watchdog", "chat_id",                 fallback_env="WATCHDOG_CHAT_ID",        default="")
    WATCHDOG_CPU_ALERT      = float(_get(_cfg, "watchdog", "cpu_alert",         fallback_env="WATCHDOG_CPU_ALERT",      default=85))
    WATCHDOG_RAM_ALERT      = float(_get(_cfg, "watchdog", "ram_alert",         fallback_env="WATCHDOG_RAM_ALERT",      default=85))
    WATCHDOG_DISK_ALERT     = float(_get(_cfg, "watchdog", "disk_alert",        fallback_env="WATCHDOG_DISK_ALERT",     default=90))
    WATCHDOG_CPU_TEMP_ALERT = float(_get(_cfg, "watchdog", "cpu_temp_alert",    fallback_env="WATCHDOG_CPU_TEMP_ALERT", default=75))

    # ── Mémoire ───────────────────────────────────────────────────────────
    MEMORY_DB_PATH   = NERON_DIR / _get(_cfg, "memory", "db_path",        default="data/memory.db")
    MEMORY_RETENTION = int(_get(_cfg, "memory", "retention_days",         default=30))
    MEMORY_MAX_ROWS  = int(_get(_cfg, "memory", "max_rows",               default=10_000))

    # ── SearXNG ───────────────────────────────────────────────────────────
    SEARXNG_URL         = _get(_cfg, "searxng", "url",         fallback_env="SEARXNG_URL",          default="http://localhost:8080")
    SEARXNG_TIMEOUT     = float(_get(_cfg, "searxng", "timeout",   fallback_env="SEARXNG_TIMEOUT",    default=10.0))
    SEARXNG_MAX_RESULTS = int(_get(_cfg, "searxng", "max_results", fallback_env="SEARXNG_MAX_RESULTS", default=5))

    # ── Home Assistant ────────────────────────────────────────────────────
    HA_ENABLED  = str(_get(_cfg, "home_assistant", "enabled", fallback_env="HA_ENABLED", default=False)).lower() == "true"
    HA_URL      = _get(_cfg, "home_assistant", "url",   fallback_env="HA_URL",   default="http://homeassistant.local:8123")
    HA_TOKEN    = _get(_cfg, "home_assistant", "token", fallback_env="HA_TOKEN", default="")
    HA_TIMEOUT  = float(_get(_cfg, "home_assistant", "timeout", fallback_env="HA_TIMEOUT", default=10.0))

    # ── Code Agent ────────────────────────────────────────────────────────
    CODE_AGENT_MODEL = (
        _get(_cfg, "code_agent", "model", default=None)
        or _get(_cfg, "llm", "model", default="llama3.2:1b")
    )

    # ── Twilio ────────────────────────────────────────────────────────────
    TWILIO_ENABLED     = str(_get(_cfg, "twilio", "enabled",     default=False)).lower() == "true"
    TWILIO_ACCOUNT_SID = _get(_cfg, "twilio", "account_sid",     default="")
    TWILIO_AUTH_TOKEN  = _get(_cfg, "twilio", "auth_token",      default="")
    TWILIO_FROM        = _get(_cfg, "twilio", "from_number",     default="")
    TWILIO_TO          = _get(_cfg, "twilio", "to_number",       default="")

    # ── Logs ──────────────────────────────────────────────────────────────
    LOGS_DIR         = Path("/var/log/neron")
    LOG_FILE         = LOGS_DIR / "neron.log"
    LOG_WATCHDOG     = LOGS_DIR / "watchdog.log"
    LOG_MAX_MB       = int(_get(_cfg, "logs", "max_size_mb",          default=10))
    LOG_BACKUP_COUNT = int(_get(_cfg, "logs", "backup_count",         default=5))

    # ── Deprecated ────────────────────────────────────────────────────────
    # NERON_WATCHDOG_URL : supprimé — system_agent.py appelle maintenant
    # les fonctions natives de watchdog_agent.py directement.


def _validate_config() -> None:
    """Avertit au démarrage si des valeurs de config sont invalides ou dangereuses."""
    raw = str(_get(_cfg, "neron", "log_level", fallback_env="LOG_LEVEL", default="INFO")).upper()
    if raw not in _VALID_LOG_LEVELS:
        logger.warning(
            "log_level invalide : %r — valeurs acceptées : %s. Fallback sur INFO.",
            raw, ", ".join(sorted(_VALID_LOG_LEVELS)),
        )
    if settings.API_KEY == "changez_moi":
        logger.warning("API_KEY par défaut détectée — pensez à la changer dans neron.yaml")


settings = Config()
_validate_config()


def print_config() -> None:
    source = f"neron.yaml ({YAML_PATH})" if YAML_PATH.exists() else ".env / variables d'environnement"
    print(f"\n{'='*60}")
    print(f"  Neron AI - Configuration active")
    print(f"  Source : {source}")
    print(f"{'='*60}")
    print(f"  Version       : {settings.VERSION}")
    print(f"  Log level     : {settings.LOG_LEVEL}")
    print(f"  API port      : {settings.SERVER_PORT}")
    print(f"  LLM model     : {settings.OLLAMA_MODEL}")
    print(f"  LLM host      : {settings.OLLAMA_HOST}")
    print(f"  Telegram      : {'actif' if settings.TELEGRAM_ENABLED else 'désactivé'}")
    print(f"  Watchdog      : {'actif' if settings.WATCHDOG_ENABLED else 'désactivé'}")
    print(f"  SearXNG       : {settings.SEARXNG_URL}")
    print(f"  Memory DB     : {settings.MEMORY_DB_PATH}")
    print(f"  Logs dir      : {settings.LOGS_DIR}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    print_config()
