import asyncio
import logging
import os
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


async def health_check():
    """Lightweight HTTP server to satisfy Render's health check."""
    async def handle_request(reader, writer):
        await reader.read(100)
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK")
        await writer.drain()
        writer.close()

    port = int(os.environ.get("PORT", 10000))
    try:
        server = await asyncio.start_server(handle_request, "0.0.0.0", port)
        logger.info(f"⚓ Health check server online on port {port}")
        async with server:
            await server.serve_forever()
    except Exception as e:
        logger.error(f"Failed to start health check server: {e}")


async def run_bots():
    # ── 1. Setup DB indexes ───────────────────────────────────────────────────
    logger.info("Setting up database indexes…")
    await db.setup_indexes()

    # ── 2. Start Health Check (for Render) ────────────────────────────────────
    asyncio.create_task(health_check())

    # ── 3. Start the Master Bot (in_memory=True → no .session file) ──────────
    master_bot = Client(
        name="MasterBot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN,
        in_memory=True,          # ← Never write session files to disk
    )

    bot_manager.register_handlers(master_bot)
    await master_bot.start()

    # Persist master session to DB
    await bot_manager._persist_session(Config.BOT_TOKEN, master_bot)

    me = await master_bot.get_me()
    Config.MASTER_BOT_ID = me.id   # Override with the live, reliable value

    logger.info(f"✅ Master Bot online: @{me.username} (ID: {me.id})")

    # ── 4. Restore all persisted clone bots ───────────────────────────────────
    logger.info("Starting saved clones…")
    await bot_manager.load_all()
    logger.info(f"✅ {len(bot_manager.clients)} clone(s) running.")

    # ── 5. Keep the event loop alive ─────────────────────────────────────────
    logger.info("Bot is running. Press Ctrl+C to stop.")
    await idle()

    # ── 6. Graceful shutdown ──────────────────────────────────────────────────
    logger.info("Shutting down…")
    await master_bot.stop()
    for token, client in list(bot_manager.clients.items()):
        try:
            await client.stop()
        except Exception as e:
            logger.warning(f"Error stopping clone during shutdown: {e}")
    logger.info("All bots stopped. Goodbye!")


if __name__ == "__main__":
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
        pass   # Not available on Windows — fine

    try:
        asyncio.run(run_bots())
    except (KeyboardInterrupt, SystemExit):
        pass
