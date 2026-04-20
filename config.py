import os
import sys
from dotenv import load_dotenv

load_dotenv()

def _get(key: str, default=None, required=False) -> str:
    """Read an env var, strip whitespace, and optionally enforce presence."""
    val = os.getenv(key, default or "").strip()
    if required and not val:
        print(f"[FATAL] Missing required environment variable: {key}")
        sys.exit(1)
    return val

def _get_int(key: str, default: int = 0) -> int:
    """Read an int env var safely."""
    try:
        return int(_get(key, str(default)))
    except ValueError:
        print(f"[WARNING] {key} is not a valid integer. Defaulting to {default}.")
        return default


class Config:
    # ── Core Telegram Credentials ──────────────────────────────────────────────
    API_ID    = _get_int("API_ID")
    API_HASH  = _get("API_HASH",  required=True)
    BOT_TOKEN = _get("BOT_TOKEN", required=True)

    # ── Database ───────────────────────────────────────────────────────────────
    MONGO_DB_URL = _get("MONGO_DB_URL", required=True)

    # ── Admin / Ownership ──────────────────────────────────────────────────────
    OWNER_ID     = _get_int("OWNER_ID")
    LOG_GROUP_ID = _get_int("LOG_GROUP_ID")

    # ── Master Bot ID (derived from token — reliable after startup overwrite) ──
    MASTER_BOT_ID = int(BOT_TOKEN.split(":")[0]) if ":" in BOT_TOKEN else 0

    # ── User-Facing Message Templates ─────────────────────────────────────────
    START_MSG = (
        "<b>👋 Hello {name}!</b>\n\n"
        "Welcome to the <b>Premium Media Bot</b>. 💎\n\n"
        "I can provide you with exclusive media content, but first "
        "you must be a member of our community.\n\n"
        "<i>Join the channels below to unlock access!</i>"
    )

    FORCE_MSG = (
        "<b>⚠️ Subscription Required!</b>\n\n"
        "You must join all mandatory channels to view this content.\n"
        "Click the buttons below to join, then press <b>✅ I've Joined</b>."
    )


# ── Startup Validation ─────────────────────────────────────────────────────────
if Config.API_ID == 0:
    print("[FATAL] API_ID is missing or invalid. Please set it in .env")
    sys.exit(1)

if Config.OWNER_ID == 0:
    print("[WARNING] OWNER_ID is not set. No one will have admin access!")
