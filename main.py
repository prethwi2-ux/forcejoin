import asyncio
import logging
from pyrogram import Client, idle
from config import Config
from manager import bot_manager
from database.mongo import db

# ── Logging configuration ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
# Silence noisy third-party loggers
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("motor").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


async def run_bots():
    # ── 1. Setup DB indexes ───────────────────────────────────────────────────
    logger.info("Setting up database indexes…")
    await db.setup_indexes()

    # ── 2. Start the Master Bot ───────────────────────────────────────────────
    master_bot = Client(
        name="MasterBot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN
    )

    bot_manager.register_handlers(master_bot)
    await master_bot.start()

    me = await master_bot.get_me()
    Config.MASTER_BOT_ID = me.id   # Override with the live, reliable value

    logger.info(f"✅ Master Bot online: @{me.username} (ID: {me.id})")

    # ── 3. Restore all persisted clone bots ───────────────────────────────────
    logger.info("Starting saved clones…")
    await bot_manager.load_all()
    logger.info(f"✅ {len(bot_manager.clients)} clone(s) running.")

    # ── 4. Keep the event loop alive ─────────────────────────────────────────
    logger.info("Bot is running. Press Ctrl+C to stop.")
    await idle()

    # ── 5. Graceful shutdown ──────────────────────────────────────────────────
    logger.info("Shutting down…")
    await master_bot.stop()
    for token, client in list(bot_manager.clients.items()):
        try:
            await client.stop()
        except Exception as e:
            logger.warning(f"Error stopping clone during shutdown: {e}")
    logger.info("All bots stopped. Goodbye!")


if __name__ == "__main__":
    # Validate the most critical config before doing anything
    if not Config.BOT_TOKEN:
        logger.critical("BOT_TOKEN is missing from .env — cannot start!")
        raise SystemExit(1)

    if not Config.MONGO_DB_URL:
        logger.critical("MONGO_DB_URL is missing from .env — cannot start!")
        raise SystemExit(1)

    # Use uvloop on Linux for better async performance
    try:
        import uvloop
        uvloop.install()
        logger.info("uvloop installed for improved performance.")
    except ImportError:
        pass   # uvloop is not available on Windows — that's fine

    try:
        asyncio.run(run_bots())
    except (KeyboardInterrupt, SystemExit):
        pass
