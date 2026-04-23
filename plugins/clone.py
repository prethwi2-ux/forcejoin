import re
import logging
from pyrogram.types import Message
from config import Config
from database.mongo import db

logger = logging.getLogger(__name__)

# Regex for Bot Token validation (digits:35+ alphanumeric chars)
TOKEN_REGEX = r"^\d+:[A-Za-z0-9_-]{35,}$"
_token_re   = re.compile(TOKEN_REGEX)


def is_master(client) -> bool:
    return client.me.id == Config.MASTER_BOT_ID


# ─────────────────────────────────────────────────────────────────────────────
# /clone
# ─────────────────────────────────────────────────────────────────────────────

async def clone_command(client, message: Message):
    if not is_master(client):
        return await message.reply("<b>❌ This command only works on the Master Bot.</b>")

    await message.reply(
        "<b>🚀 Create Your Own Force Sub Bot!</b>\n\n"
        "Follow these steps:\n"
        "1. Open @BotFather and create a new bot.\n"
        "2. Copy the <b>API Token</b> it gives you.\n"
        "3. Paste the token here — your bot will start automatically!\n\n"
        "<b>Important:</b> Add your bot as an <b>Admin</b> in all channels "
        "you want to use as force-subscribe channels.\n\n"
        "<i>Each cloned bot is fully independent and managed only by you.</i>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Token handler — auto-detected from message text
# ─────────────────────────────────────────────────────────────────────────────

async def handle_token(client, message: Message):
    if not is_master(client):
        return

    token   = message.text.strip()
    user_id = message.from_user.id

    # Validate token format before doing anything
    if not _token_re.match(token):
        return await message.reply(
            "<b>❌ Invalid Token Format!</b>\n\n"
            "A bot token looks like: <code>123456789:ABCdefGhIjKlmnOPQrstUvwXYZ</code>\n\n"
            "Please copy it directly from @BotFather."
        )

    # Don't allow cloning the master bot itself
    if token == Config.BOT_TOKEN:
        return await message.reply(
            "<b>❌ That is the Master Bot token!</b>\n\n"
            "Please use a different bot token."
        )

    msg = await message.reply("<b>⌛ Starting your bot…</b>\nThis may take a few seconds.")

    from manager import bot_manager   # local import to avoid circular dependency
    success, result = await bot_manager.start_clone(user_id, token)

    if success:
        await msg.edit(
            f"<b>✅ Bot Started!</b>\n\n"
            f"Your bot {result} is now online.\n\n"
            f"<b>Next steps:</b>\n"
            f"1. Open your bot.\n"
            f"2. Send /settings to configure channels and media."
        )
    else:
        await msg.edit(
            f"<b>❌ Failed to Start Bot!</b>\n\n"
            f"<code>{result}</code>\n\n"
            f"Common causes:\n"
            f"• The token is invalid or already revoked.\n"
            f"• The bot is already running (use /my_bots to check)."
        )


# ─────────────────────────────────────────────────────────────────────────────
# /my_bots — list your running clones
# ─────────────────────────────────────────────────────────────────────────────

async def my_bots(client, message: Message):
    if not is_master(client):
        return

    user_id = message.from_user.id
    clones  = await db.get_user_clones(user_id)

    if not clones:
        return await message.reply(
            "<b>❌ No bots found!</b>\n\n"
            "You haven't created any bots yet. Use /clone to get started."
        )

    lines = ["<b>🤖 Your Cloned Bots:</b>\n"]
    for c in clones:
        username = c.get("bot_username", "unknown")
        lines.append(f"• @{username}")

    lines.append("\n<i>To stop a bot, send /stop_bot and the bot's token.</i>")
    await message.reply("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# /stop_bot — shut down a running clone
# Usage: /stop_bot <bot_token>
# ─────────────────────────────────────────────────────────────────────────────

async def stop_bot_command(client, message: Message):
    if not is_master(client):
        return

    user_id = message.from_user.id
    parts   = (message.text or "").split(maxsplit=1)

    if len(parts) < 2:
        return await message.reply(
            "<b>Usage:</b> <code>/stop_bot &lt;bot_token&gt;</code>\n\n"
            "Use /my_bots to see your running bots."
        )

    token = parts[1].strip()

    # Ensure this token belongs to the requesting user
    clone = await db._clones.find_one({"bot_token": token, "user_id": user_id})
    if not clone and user_id != Config.OWNER_ID:
        return await message.reply(
            "<b>❌ Not Found!</b>\n\n"
            "That token doesn't match any of your bots."
        )

    from manager import bot_manager
    stopped = await bot_manager.stop_clone(token)

    if stopped:
        await message.reply(
            f"<b>✅ Bot Stopped!</b>\n\n"
            f"@{clone.get('bot_username', 'your bot')} has been shut down.\n"
            f"Use /clone to start a new one."
        )
    else:
        await message.reply(
            "<b>⚠️ Bot Was Not Running.</b>\n\n"
            "It may have already been stopped or never started."
        )


# ─────────────────────────────────────────────────────────────────────────────
# /global_stats — master admin only — ENHANCED
# ─────────────────────────────────────────────────────────────────────────────

async def global_stats(client, message: Message):
    if not is_master(client):
        return
    if message.from_user.id != Config.OWNER_ID:
        return

    from manager import bot_manager  # local import to avoid circular dependency

    # Collect all per-bot data
    clone_stats   = await db.get_clone_stats()
    total_users   = await db._users.count_documents({})
    total_media   = await db._media.count_documents({})
    total_channels= await db._fsub.count_documents({})
    total_clones  = len(clone_stats)

    # Build per-bot breakdown
    lines = [
        "📊 <b>Global Statistics</b>\n",
        f"🤖 <b>Total Cloned Bots:</b> {total_clones}",
        f"👥 <b>Total Users:</b> {total_users}",
        f"📁 <b>Total Stored Media:</b> {total_media}",
        f"📢 <b>Total Fsub Channels:</b> {total_channels}",
        "",
        "━━━━━━━━━━━━━━━━━━━",
        "📋 <b>Per-Bot Breakdown:</b>\n",
    ]

    for i, cs in enumerate(clone_stats, 1):
        username = cs["bot_username"]
        # Check if clone is currently online
        is_online = any(
            True for c in bot_manager.clients.values()
            if getattr(c.me, "id", None) == cs["bot_id"]
        )
        status = "🟢 Online" if is_online else "🔴 Offline"
        lines.append(
            f"<b>{i}. @{username}</b> [{status}]\n"
            f"   👤 Users: <b>{cs['user_count']}</b>  "
            f"📁 Media: <b>{cs['media_count']}</b>  "
            f"📢 Channels: <b>{cs['channel_count']}</b>\n"
            f"   👑 Owner ID: <code>{cs['owner_id']}</code>"
        )

    if not clone_stats:
        lines.append("<i>No cloned bots found yet.</i>")

    await message.reply("\n".join(lines))
