from pyrogram.types import Message
from config import Config
import logging

logger = logging.getLogger(__name__)

async def help_handler(client, message: Message):
    user_id = message.from_user.id
    
    # ── Help for Master Bot Owner (Global Admin) ONLY ──────────────────────
    if user_id == Config.OWNER_ID:
        return await message.reply(
            "<b>👑 Master Owner Command Menu</b>\n\n"
            "<b>Bot Management:</b>\n"
            "• /clone - Create a new independent bot\n"
            "• /my_bots - List your running clones\n"
            "• /stop_bot <code>token</code> - Shut down and delete a clone\n\n"
            "<b>Global Admin:</b>\n"
            "• /global_stats - View detailed data for all bots\n"
            "• /broadcast - Send a message to <b>EVERY</b> user on all bots\n\n"
            "<i>Only you, as the Master Admin, can see this menu.</i>"
        )

    # ── For everyone else (including Clone Owners) ────────────────────────
    # As requested: "no bot owner can not use help"
    # We silently ignore or show regular help
    await message.reply(
        "<b>👋 Help Menu</b>\n\n"
        "I am a media delivery bot. To get files, simply click on a shareable link provided by the admin.\n\n"
        "<b>Important:</b> If you are asked to join channels, you must subscribe to all of them "
        "and then click the '✅ I've Joined' button to receive your files."
    )
