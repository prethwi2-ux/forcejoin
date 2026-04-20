import asyncio
import logging
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated, PeerIdInvalid
from pyrogram.types import Message
from config import Config
from database.mongo import db

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


async def broadcast_handler(client, message: Message, bot_manager):
    """
    Global broadcast — only the owner can use this.
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
        # Create a fake "broadcast" from the command text
        broadcast_msg = message

    # Build a map of bot_id → client (master + all running clones)
    id_to_client = {client.me.id: client}
    for token, c in bot_manager.clients.items():
        try:
            id_to_client[c.me.id] = c
        except Exception as e:
            logger.warning(f"Could not map clone client during broadcast: {e}")

    # Fetch all users across all bots
    all_users   = await db.get_all_users()
    total_users = len(all_users)

    status_msg = await message.reply(
        f"<b>🚀 Broadcast Starting…</b>\n\n"
        f"<b>Total recipients:</b> {total_users}"
    )

    sent    = 0
    failed  = 0
    blocked = 0

    for i, user in enumerate(all_users, start=1):
        u_id = user["user_id"]
        b_id = user.get("bot_id")

        # Use the user's own bot; fall back to master if clone is offline
        target_client = id_to_client.get(b_id, client)

        success = await _send_to_user(target_client, u_id, broadcast_msg, client)
        if success:
            sent += 1
        else:
            # Distinguish blocked vs other failures via exception type —
            # _send_to_user already handled FloodWait; count the rest as failed.
            failed += 1

        # Add a small delay to avoid hitting rate limits
        await asyncio.sleep(0.05)

        # Update progress every 50 users
        if i % 50 == 0:
            try:
                await status_msg.edit(
                    f"<b>🚀 Broadcast In Progress…</b>\n\n"
                    f"📊 <b>Progress:</b> {i} / {total_users}\n"
                    f"✅ <b>Sent:</b> {sent}\n"
                    f"❌ <b>Failed:</b> {failed}"
                )
            except Exception:
                pass

    # Final report
    try:
        await status_msg.edit(
            f"<b>✅ Broadcast Complete!</b>\n\n"
            f"📊 <b>Total:</b> {total_users}\n"
            f"✅ <b>Sent:</b> {sent}\n"
            f"❌ <b>Failed:</b> {failed}"
        )
    except Exception:
        pass

    logger.info(f"BROADCAST DONE | Total={total_users} Sent={sent} Failed={failed}")
