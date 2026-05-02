from pathlib import Path
import os
import logging
from logging.handlers import RotatingFileHandler

# =========================
# CONFIG
# =========================

LOGS_DIR = Path("/var/log/neron")
LOG_FILE = LOGS_DIR / "neron.log"

LOG_MAX_MB = 10
LOG_BACKUP_COUNT = 5

# =========================
# INIT DOSSIER
# =========================

LOGS_DIR.mkdir(parents=True, exist_ok=True)

if not os.access(LOGS_DIR, os.W_OK):
    raise PermissionError(f"Logs directory not writable: {LOGS_DIR}")

# =========================
# LOGGER CENTRAL
# =========================

logger = logging.getLogger("neron")
logger.setLevel(logging.INFO)

# ⚠️ évite duplication si import multiple
if not logger.handlers:

    # File handler avec rotation
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_MB * 1024 * 1024,
        backupCount=LOG_BACKUP_COUNT
    )

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    # Console handler (optionnel mais utile debug)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
