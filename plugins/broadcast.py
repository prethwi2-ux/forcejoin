import asyncio
import logging
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated, PeerIdInvalid
from pyrogram.types import Message
from config import Config
from database.mongo import db
from utils.checks import is_bot_owner

logger = logging.getLogger(__name__)


async def _send_to_user(target_client, user_id: int, broadcast_msg: Message, master_client) -> bool:
    """
    Send a broadcast message to a single user.
    If target_client is the same as master_client, use copy_message (cleanest).
    If target_client is a clone, forward text+caption.
    Returns True on success, False on failure.
    """
    try:
        if target_client.me.id == master_client.me.id:
            # Same bot — copy preserves formatting perfectly
            await target_client.copy_message(
                chat_id=user_id,
                from_chat_id=broadcast_msg.chat.id,
                message_id=broadcast_msg.id
            )
        else:
            # Clone bot — copy_message won't work cross-bot for private chats,
            # so we forward what we can.
            text = broadcast_msg.text or broadcast_msg.caption or ""
            entities = broadcast_msg.entities or broadcast_msg.caption_entities

            if broadcast_msg.photo:
                await target_client.send_photo(
                    chat_id=user_id,
                    photo=broadcast_msg.photo.file_id,
                    caption=text,
                    caption_entities=entities
                )
            elif broadcast_msg.video:
                await target_client.send_video(
                    chat_id=user_id,
                    video=broadcast_msg.video.file_id,
                    caption=text,
                    caption_entities=entities
                )
            elif broadcast_msg.document:
                await target_client.send_document(
                    chat_id=user_id,
                    document=broadcast_msg.document.file_id,
                    caption=text,
                    caption_entities=entities
                )
            elif text:
                await target_client.send_message(
                    chat_id=user_id,
                    text=text,
                    entities=entities
                )
            else:
                # Media type not supported for cross-bot broadcast (e.g. sticker)
                return False

        return True

    except FloodWait as e:
        logger.warning(f"FloodWait {e.value}s while sending to {user_id}. Sleeping…")
        await asyncio.sleep(e.value + 1)
        # Retry once after flood wait — using the same client/method
        try:
            return await _send_to_user(target_client, user_id, broadcast_msg, master_client)
        except Exception:
            return False

    except (UserIsBlocked, InputUserDeactivated, PeerIdInvalid):
        # These users can't receive messages — skip silently
        return False

    except Exception as e:
        logger.error(f"Broadcast error for user {user_id}: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Global Broadcast — Master Bot / OWNER_ID only
# ─────────────────────────────────────────────────────────────────────────────

async def broadcast_handler(client, message: Message, bot_manager):
    """
    Global broadcast — only the owner can use this (on master bot).
    Sends to ALL users across ALL bots.
    Usage: Reply to a message with /broadcast, or /broadcast <text>.
    """
    if message.from_user.id != Config.OWNER_ID:
        return

    # Determine what to broadcast
    if message.reply_to_message:
        broadcast_msg = message.reply_to_message
    else:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            return await message.reply(
                "<b>Usage:</b>\n"
                "• Reply to any message with /broadcast\n"
                "• Or: /broadcast Your message here"
            )
        broadcast_msg = message

    # Build a map of bot_id → client (master + all running clones)
    id_to_client = {client.me.id: client}
    for token, c in bot_manager.clients.items():
        try:
            id_to_client[c.me.id] = c
        except Exception as e:
            logger.warning(f"Could not map clone client during broadcast: {e}")

    total_users = await db.count_all_users()

    status_msg = await message.reply(
        f"<b>🚀 Global Broadcast Starting…</b>\n\n"
        f"<b>Total recipients:</b> {total_users}"
    )

    sent    = 0
    failed  = 0
    i       = 0

    async for user in db.get_all_users():
        i += 1
        u_id = user["user_id"]
        b_id = user.get("bot_id")

        target_client = id_to_client.get(b_id, client)
        success = await _send_to_user(target_client, u_id, broadcast_msg, client)
        if success:
            sent += 1
        else:
            failed += 1

        await asyncio.sleep(0.05)

        if i % 50 == 0:
            try:
                await status_msg.edit(
                    f"<b>🚀 Global Broadcast In Progress…</b>\n\n"
                    f"📊 <b>Progress:</b> {i} / {total_users}\n"
                    f"✅ <b>Sent:</b> {sent}\n"
                    f"❌ <b>Failed:</b> {failed}"
                )
            except Exception:
                pass

    try:
        await status_msg.edit(
            f"<b>✅ Global Broadcast Complete!</b>\n\n"
            f"📊 <b>Total:</b> {total_users}\n"
            f"✅ <b>Sent:</b> {sent}\n"
            f"❌ <b>Failed:</b> {failed}"
        )
    except Exception:
        pass

    logger.info(f"GLOBAL BROADCAST DONE | Total={total_users} Sent={sent} Failed={failed}")


# ─────────────────────────────────────────────────────────────────────────────
# Clone Broadcast — Clone Bot owner broadcasts to their OWN users only
# ─────────────────────────────────────────────────────────────────────────────

async def clone_broadcast_handler(client, message: Message):
    """
    Clone bot broadcast — only the clone bot's owner can use this.
    Sends only to users registered under this specific bot.
    Usage: Reply to a message with /broadcast, or /broadcast <text>.
    """
    # Only the owner of this specific clone can broadcast
    if not await is_bot_owner(client, message.from_user.id):
        return await message.reply(
            "<b>❌ Access Denied!</b>\n\n"
            "Only the owner of this bot can send broadcasts."
        )

    # Determine what to broadcast
    if message.reply_to_message:
        broadcast_msg = message.reply_to_message
    else:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            return await message.reply(
                "<b>Usage:</b>\n"
                "• Reply to any message with /broadcast\n"
                "• Or: /broadcast Your message here"
            )
        broadcast_msg = message

    bot_id      = client.me.id
    total_users = await db.count_users_for_bot(bot_id)

    status_msg = await message.reply(
        f"<b>📢 Broadcast Starting…</b>\n\n"
        f"<b>Your bot's users:</b> {total_users}"
    )

    sent   = 0
    failed = 0
    i      = 0

    async for user in db.get_all_users_for_bot(bot_id):
        i += 1
        u_id = user["user_id"]

        try:
            text     = broadcast_msg.text or broadcast_msg.caption or ""
            entities = broadcast_msg.entities or broadcast_msg.caption_entities

            if broadcast_msg.photo:
                await client.send_photo(
                    chat_id=u_id,
                    photo=broadcast_msg.photo.file_id,
                    caption=text,
                    caption_entities=entities
                )
            elif broadcast_msg.video:
                await client.send_video(
                    chat_id=u_id,
                    video=broadcast_msg.video.file_id,
                    caption=text,
                    caption_entities=entities
                )
            elif broadcast_msg.document:
                await client.send_document(
                    chat_id=u_id,
                    document=broadcast_msg.document.file_id,
                    caption=text,
                    caption_entities=entities
                )
            elif text:
                await client.send_message(
                    chat_id=u_id,
                    text=text,
                    entities=entities
                )
            else:
                failed += 1
                continue

            sent += 1

        except FloodWait as e:
            logger.warning(f"FloodWait {e.value}s (clone broadcast to {u_id}). Sleeping…")
            await asyncio.sleep(e.value + 1)
            failed += 1

        except (UserIsBlocked, InputUserDeactivated, PeerIdInvalid):
            failed += 1

        except Exception as e:
            logger.error(f"Clone broadcast error for user {u_id}: {e}")
            failed += 1

        await asyncio.sleep(0.05)

        if i % 50 == 0:
            try:
                await status_msg.edit(
                    f"<b>📢 Broadcast In Progress…</b>\n\n"
                    f"📊 <b>Progress:</b> {i} / {total_users}\n"
                    f"✅ <b>Sent:</b> {sent}\n"
                    f"❌ <b>Failed:</b> {failed}"
                )
            except Exception:
                pass

    try:
        await status_msg.edit(
            f"<b>✅ Broadcast Complete!</b>\n\n"
            f"📊 <b>Total:</b> {total_users}\n"
            f"✅ <b>Sent:</b> {sent}\n"
            f"❌ <b>Failed:</b> {failed}"
        )
    except Exception:
        pass

    logger.info(
        f"CLONE BROADCAST DONE | bot={bot_id} Total={total_users} "
        f"Sent={sent} Failed={failed}"
    )
