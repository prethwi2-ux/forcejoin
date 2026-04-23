import logging
import re
from pyrogram import Client, filters
from pyrogram.types import Message, ChatJoinRequest
from pyrogram.handlers import MessageHandler, CallbackQueryHandler, ChatJoinRequestHandler
from database.mongo import db
from config import Config

# ── Plugin imports ────────────────────────────────────────────────────────────
from plugins.start import start_handler, check_subscription_callback
from plugins.admin_settings import (
    settings_panel, manage_channels_menu, remove_channel_callback,
    add_channel_prompt, handle_channel_input, manage_media_menu,
    delete_media_callback, stats_panel_callback, back_to_settings,
    close_panel, set_link_prompt, clear_link_callback,
    auto_delete_menu, set_auto_delete_callback, autodel_custom_prompt,
)
from plugins.media_handler import (
    post_media, show_stats, ping_pong,
    batch_command, done_command, handle_batch_input
)
from plugins.clone import (
    clone_command, handle_token, global_stats,
    my_bots, stop_bot_command, TOKEN_REGEX
)
from plugins.join_request import handle_join_request
from plugins.broadcast import broadcast_handler, clone_broadcast_handler
from plugins.help import help_handler

logger = logging.getLogger(__name__)


class BotManager:
    def __init__(self):
        # Maps bot_token → running Client instance
        self.clients: dict[str, Client] = {}

    # ─────────────────────────────────────────────────────────────────────────
    # Build a Client using a StringSession stored in MongoDB
    # ─────────────────────────────────────────────────────────────────────────
    async def _build_client(self, bot_token: str) -> Client:
        """
        Create a Pyrogram Client backed by a StringSession.
        If a saved session string exists in DB, use it (avoids re-auth).
        After connecting, save the updated session string back to DB
        so no .session files are ever written to disk.
        """
        from pyrogram.types import TermsOfService  # noqa
        try:
            from pyrogram import StringSession
        except ImportError:
            from pyrogram.storage import StringSession  # older pyrogram

        saved = await db.load_session(bot_token)
        session = StringSession(saved) if saved else StringSession()

        client = Client(
            name=bot_token.split(":")[0],        # used as internal identifier
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            bot_token=bot_token,
            session_string=saved or "",           # empty string = fresh session
            in_memory=True,                       # ← no .session file on disk
        )
        return client

    async def _persist_session(self, bot_token: str, client: Client):
        """Export and save the session string to MongoDB after a successful start."""
        try:
            session_string = await client.export_session_string()
            await db.save_session(bot_token, session_string)
            logger.info(f"💾 Session persisted to DB for token {bot_token[:12]}…")
        except Exception as e:
            logger.warning(f"Could not persist session: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Handler Registration
    # ─────────────────────────────────────────────────────────────────────────

    def register_handlers(self, client: Client):
        """Attach all command and callback handlers to a client instance."""

        # ── Commands ──────────────────────────────────────────────────────────

        @client.on_message(filters.regex(r"^/start") & filters.private)
        async def _start(c, m): await start_handler(c, m)

        @client.on_message(filters.regex(r"^/help") & filters.private)
        async def _help(c, m): await help_handler(c, m)

        @client.on_message(filters.regex(r"^/settings") & filters.private)
        async def _settings(c, m): await settings_panel(c, m)

        @client.on_message(filters.regex(r"^/post") & filters.private)
        async def _post(c, m): await post_media(c, m)

        @client.on_message(filters.regex(r"^/ping") & filters.private)
        async def _ping(c, m): await ping_pong(c, m)

        @client.on_message(filters.regex(r"^/stats") & filters.private)
        async def _stats(c, m): await show_stats(c, m)

        @client.on_message(filters.regex(r"^/batch") & filters.private)
        async def _batch(c, m): await batch_command(c, m)

        @client.on_message(filters.regex(r"^/done") & filters.private)
        async def _done(c, m): await done_command(c, m)

        @client.on_message(filters.regex(r"^/clone") & filters.private)
        async def _clone(c, m): await clone_command(c, m)

        @client.on_message(filters.regex(r"^/my_bots") & filters.private)
        async def _my_bots(c, m): await my_bots(c, m)

        @client.on_message(filters.regex(r"^/stop_bot") & filters.private)
        async def _stop_bot(c, m): await stop_bot_command(c, m)

        @client.on_message(filters.regex(r"^/global_stats") & filters.private)
        async def _g_stats(c, m): await global_stats(c, m)

        # /broadcast: global (master bot, owner only) vs clone (clone bot owner)
        @client.on_message(filters.regex(r"^/broadcast") & filters.private)
        async def _broadcast(c, m):
            if c.me.id == Config.MASTER_BOT_ID:
                await broadcast_handler(c, m, self)
            else:
                await clone_broadcast_handler(c, m)

        # Auto-detect bot token input
        @client.on_message(filters.regex(TOKEN_REGEX) & filters.private)
        async def _token(c, m): await handle_token(c, m)

        # ── Callbacks ─────────────────────────────────────────────────────────

        @client.on_callback_query(filters.regex("^check_sub$"))
        async def _sub_cb(c, cb): await check_subscription_callback(c, cb)

        @client.on_callback_query(filters.regex("^manage_channels$"))
        async def _m_chan(c, cb): await manage_channels_menu(c, cb)

        @client.on_callback_query(filters.regex("^add_channel_prompt$"))
        async def _a_chan(c, cb): await add_channel_prompt(c, cb)

        @client.on_callback_query(filters.regex("^remove_chan_"))
        async def _r_chan(c, cb): await remove_channel_callback(c, cb)

        @client.on_callback_query(filters.regex("^set_link_"))
        async def _s_link(c, cb): await set_link_prompt(c, cb)

        @client.on_callback_query(filters.regex("^clear_link_"))
        async def _c_link(c, cb): await clear_link_callback(c, cb)

        @client.on_callback_query(filters.regex("^manage_media_"))
        async def _m_med(c, cb): await manage_media_menu(c, cb)

        @client.on_callback_query(filters.regex("^del_med_"))
        async def _d_med(c, cb): await delete_media_callback(c, cb)

        @client.on_callback_query(filters.regex("^stats_panel$"))
        async def _stats_cb(c, cb): await stats_panel_callback(c, cb)

        @client.on_callback_query(filters.regex("^back_to_settings$"))
        async def _back(c, cb): await back_to_settings(c, cb)

        @client.on_callback_query(filters.regex("^close_panel$"))
        async def _close(c, cb): await close_panel(c, cb)

        # Auto-Delete callbacks
        @client.on_callback_query(filters.regex("^auto_delete_menu$"))
        async def _ad_menu(c, cb): await auto_delete_menu(c, cb)

        @client.on_callback_query(filters.regex("^set_autodel_"))
        async def _set_ad(c, cb): await set_auto_delete_callback(c, cb)

        @client.on_callback_query(filters.regex("^autodel_custom$"))
        async def _custom_ad(c, cb): await autodel_custom_prompt(c, cb)

        # ── General message input (non-command) ───────────────────────────────
        @client.on_message(
            filters.private
            & ~filters.command([
                "start", "help", "clone", "settings", "post", "ping",
                "batch", "done", "stop_bot", "my_bots",
                "global_stats", "stats", "broadcast"
            ]),
            group=1
        )
        async def _input(c, m):
            await handle_batch_input(c, m)
            await handle_channel_input(c, m)

        # ── Join Requests ─────────────────────────────────────────────────────
        @client.on_chat_join_request()
        async def _join(c, r): await handle_join_request(c, r)

    # ─────────────────────────────────────────────────────────────────────────
    # Clone lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    async def start_clone(self, user_id: int, bot_token: str):
        """Start a new clone bot. Returns (success: bool, message: str)."""
        if bot_token in self.clients:
            return False, "This bot is already running!"

        try:
            client = Client(
                name=bot_token.split(":")[0],
                api_id=Config.API_ID,
                api_hash=Config.API_HASH,
                bot_token=bot_token,
                in_memory=True,          # ← Session stays in RAM / DB, never on disk
            )

            self.register_handlers(client)
            await client.start()
            me = await client.get_me()

            # Persist session string to DB right away
            await self._persist_session(bot_token, client)

            # Warm up the peer cache for all configured channels
            logger.info(f"🔥 Warming up peers for @{me.username}…")
            fsub_channels = await db.get_fsub_channels(me.id)
            for chat in fsub_channels:
                try:
                    await client.get_chat(chat["chat_id"])
                    logger.info(f"  ✅ Resolved: {chat['title']}")
                except Exception as e:
                    logger.warning(f"  ⚠️ Could not resolve {chat['title']}: {e}")

            self.clients[bot_token] = client

            # Persist the clone record in DB
            await db.add_clone(user_id, bot_token, me.username, me.id)

            logger.info(f"🟢 Clone ONLINE: @{me.username} (ID: {me.id})")
            return True, f"@{me.username}"

        except Exception as e:
            logger.error(f"🔴 Clone startup failed: {e}")
            return False, str(e)

    async def stop_clone(self, bot_token: str) -> bool:
        """Stop a running clone and cascade-delete all its data from DB."""
        if bot_token not in self.clients:
            return False

        try:
            await self.clients[bot_token].stop()
        except Exception as e:
            logger.warning(f"Error stopping clone: {e}")
        finally:
            del self.clients[bot_token]

        # Cascade-delete: users, media, channels, settings, session, states, batches
        await db.remove_clone(bot_token)
        logger.info(f"🔴 Clone STOPPED + data purged: token={bot_token[:12]}…")
        return True

    async def load_all(self):
        """Re-start all persisted clones on bot startup."""
        clones = await db.get_all_clones()
        logger.info(f"📂 Loading {len(clones)} saved clone(s)…")
        for c in clones:
            try:
                logger.info(f"  ↩️  Restarting: @{c.get('bot_username', '?')}")
                await self.start_clone(c["user_id"], c["bot_token"])
            except Exception as e:
                logger.error(
                    f"  ❌ Failed to restart @{c.get('bot_username', '?')}: {e}"
                )


bot_manager = BotManager()
