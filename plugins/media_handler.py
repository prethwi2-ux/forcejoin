import uuid
import logging
from pyrogram.types import Message
from config import Config
from database.mongo import db
from utils.checks import is_bot_owner

logger = logging.getLogger(__name__)

# ── In-memory batch state ─────────────────────────────────────────────────────
# Format: { user_id: {"bot_id": int, "chat_id": int | None, "ids": [int]} }
# Note: this state is lost on bot restart. That is an acceptable trade-off
#       because batches are short-lived (minutes, not hours).
BATCH_DATA: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# /ping
# ─────────────────────────────────────────────────────────────────────────────

async def ping_pong(client, message: Message):
    await message.reply("<b>Pong!</b> 🏓")


# ─────────────────────────────────────────────────────────────────────────────
# /post — store a single message as shareable media
# ─────────────────────────────────────────────────────────────────────────────

async def post_media(client, message: Message):
    # Master Bot doesn't serve media
    if client.me.id == Config.MASTER_BOT_ID:
        return

    if not await is_bot_owner(client, message.from_user.id):
        return await message.reply(
            "<b>❌ Access Denied!</b>\n\nYou are not authorised to manage this bot."
        )

    if not message.reply_to_message:
        return await message.reply(
            "<b>How to use /post:</b>\n\n"
            "1. Forward or send a file to this chat.\n"
            "2. Reply to that file with <code>/post</code>.\n\n"
            "<i>This saves the file and gives you a shareable link.</i>"
        )

    reply      = message.reply_to_message
    chat_id    = reply.chat.id
    message_id = reply.id
    bot_id     = client.me.id

    media_id   = str(uuid.uuid4())[:8]
    await db.save_media(bot_id, media_id, chat_id, [message_id])

    share_link = f"https://t.me/{client.me.username}?start={media_id}"
    await message.reply(
        f"<b>✅ File Saved!</b>\n\n"
        f"<b>Share Link:</b> <code>{share_link}</code>\n\n"
        f"<i>Anyone who clicks this link will be force-subscribed before receiving the file.</i>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /batch — store multiple files under one link
# ─────────────────────────────────────────────────────────────────────────────

async def batch_command(client, message: Message):
    if not await is_bot_owner(client, message.from_user.id):
        return

    user_id = message.from_user.id
    BATCH_DATA[user_id] = {"bot_id": client.me.id, "ids": [], "chat_id": None}

    await message.reply(
        "<b>📦 Batch Mode Activated!</b>\n\n"
        "Forward or send all the files you want to group here.\n"
        "When you're done, send <code>/done</code> to get your link.\n\n"
        "<i>⚠️ All files must come from the same source chat.</i>"
    )


async def handle_batch_input(client, message: Message):
    """Silently collect messages into the active batch session."""
    user_id = message.from_user.id

    # Not in a batch session for this bot
    if user_id not in BATCH_DATA or BATCH_DATA[user_id]["bot_id"] != client.me.id:
        return

    # Ignore commands
    if message.text and message.text.startswith("/"):
        return

    chat_id = message.chat.id

    # First file sets the source chat
    if BATCH_DATA[user_id]["chat_id"] is None:
        BATCH_DATA[user_id]["chat_id"] = chat_id
    elif BATCH_DATA[user_id]["chat_id"] != chat_id:
        # Files must all be from the same chat
        await message.reply(
            "<b>❌ Wrong source!</b>\n\n"
            "All files in a batch must come from the <b>same chat</b>.\n"
            "This file was skipped."
        )
        return

    BATCH_DATA[user_id]["ids"].append(message.id)
    count = len(BATCH_DATA[user_id]["ids"])

    # Give feedback every 5 files so the owner knows things are working,
    # but avoid spamming a reply on every single message.
    if count == 1 or count % 5 == 0:
        await message.reply(
            f"<b>✅ {count} file(s) added.</b>  Send more or <code>/done</code> to finish.",
            quote=True
        )


async def done_command(client, message: Message):
    user_id = message.from_user.id

    if user_id not in BATCH_DATA or BATCH_DATA[user_id]["bot_id"] != client.me.id:
        return await message.reply(
            "<b>❌ No active batch!</b>\n\nUse /batch to start one first."
        )

    data = BATCH_DATA.pop(user_id)

    if not data["ids"]:
        return await message.reply(
            "<b>❌ Empty batch!</b>\n\nYou didn't add any files. Use /batch to try again."
        )

    media_id   = str(uuid.uuid4())[:8]
    await db.save_media(client.me.id, media_id, data["chat_id"], data["ids"])

    share_link = f"https://t.me/{client.me.username}?start={media_id}"
    await message.reply(
        f"<b>✅ Batch Saved!</b>\n\n"
        f"<b>Files:</b> {len(data['ids'])}\n"
        f"<b>Share Link:</b> <code>{share_link}</code>\n\n"
        f"<i>Users will receive all {len(data['ids'])} file(s) at once after subscribing.</i>"
    )


# ─────────────────────────────────────────────────────────────────────────────
# /stats
# ─────────────────────────────────────────────────────────────────────────────

async def show_stats(client, message: Message):
    if not await is_bot_owner(client, message.from_user.id):
        return

    bot_id       = client.me.id
    total_users  = await db.get_total_users(bot_id)
    total_media  = await db._media.count_documents({"bot_id": bot_id})
    total_chans  = await db._fsub.count_documents({"bot_id": bot_id})

    await message.reply(
        f"<b>📊 Bot Statistics</b>\n\n"
        f"👤 <b>Users:</b> {total_users}\n"
        f"📁 <b>Stored Files:</b> {total_media}\n"
        f"📢 <b>Mandatory Channels:</b> {total_chans}"
    )
