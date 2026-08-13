import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def get_category_logger(category_name: str) -> logging.Logger:
    clean_cat = category_name.lower().replace(" ", "_")
    log_file = os.path.join(LOG_DIR, f"{clean_cat}.log")

    logger = logging.getLogger(f"AIAnalyst_{clean_cat}")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8"
        )
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s]: %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def log_event(category: str, message: str, user_id: int = None, level: str = "INFO"):
    logger = get_category_logger(category)
    user_str = f"[User #{user_id}] " if user_id else ""
    log_msg = f"{user_str}{message}"

    if level.upper() == "ERROR":
        logger.error(log_msg)
    elif level.upper() == "WARNING":
        logger.warning(log_msg)
    else:
        logger.info(log_msg)


def log_ai_event(msg: str, user_id: int = None): log_event("AI Logs", msg, user_id)
def log_voice_event(msg: str, user_id: int = None): log_event("Voice Logs", msg, user_id)
def log_upload_event(msg: str, user_id: int = None): log_event("Upload Logs", msg, user_id)
def log_auth_event(msg: str, user_id: int = None): log_event("Authentication Logs", msg, user_id)
def log_query_event(msg: str, user_id: int = None): log_event("Query Logs", msg, user_id)
def log_report_event(msg: str, user_id: int = None): log_event("Report Logs", msg, user_id)
def log_export_event(msg: str, user_id: int = None): log_event("Export Logs", msg, user_id)

logger = get_category_logger("system")
