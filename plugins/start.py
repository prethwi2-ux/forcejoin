import asyncio
import logging
from pyrogram.types import Message, CallbackQuery
from config import Config
from database.mongo import db
from plugins.force_sub import is_subscribed, get_fsub_buttons

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Auto-Delete Task
# ─────────────────────────────────────────────────────────────────────────────

async def _schedule_delete(client, chat_id: int, message_ids: list[int], delay_secs: int):
    """Background task: wait `delay_secs` then delete the given messages."""
    try:
        await asyncio.sleep(delay_secs)
        for mid in message_ids:
            try:
                await client.delete_messages(chat_id, mid)
            except Exception:
                pass   # Already deleted or unavailable — ignore silently
    except asyncio.CancelledError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Media Delivery Helper
# ─────────────────────────────────────────────────────────────────────────────

async def deliver_media(client, user_id: int, bot_id: int, media: dict) -> bool:
    """
    Copy all messages in a media record to the user.
    If auto_delete_secs > 0 for this bot, schedule deletion of the sent messages.
    Returns True if at least one message was delivered successfully.
    """
    chat_id     = media["chat_id"]
    message_ids = media.get("message_ids") or [media.get("message_id")]

    delivered      = 0
    sent_msg_ids   = []   # Track message IDs of what we actually sent to the user

    for m_id in message_ids:
        if m_id is None:
            continue
        try:
            sent = await client.copy_message(
                chat_id=user_id,
                from_chat_id=chat_id,
                message_id=m_id
            )
            delivered += 1
            if sent:
                sent_msg_ids.append(sent.id)
        except Exception as e:
            err = str(e)
            if "Peer id invalid" in err:
                # Warm up peer cache and retry once
                try:
                    await client.get_chat(chat_id)
                    sent = await client.copy_message(
                        chat_id=user_id,
                        from_chat_id=chat_id,
                        message_id=m_id
                    )
                    delivered += 1
                    if sent:
                        sent_msg_ids.append(sent.id)
                except Exception as inner:
                    logger.error(f"Delivery failed for msg {m_id} after peer resolve: {inner}")
            elif "chat not found" in err.lower() or "channel invalid" in err.lower():
                logger.error(
                    f"Source chat {chat_id} not found. "
                    "Ensure this bot is still an admin there."
                )
            else:
                logger.error(f"Delivery error for msg {m_id}: {e}")

    # ── Auto-Delete: schedule if configured ───────────────────────────────────
    if delivered > 0 and sent_msg_ids:
        auto_secs = await db.get_bot_setting(bot_id, "auto_delete_secs", 0)
        if auto_secs and auto_secs > 0:
            from plugins.admin_settings import _format_secs
            
            # Send the notification message and track its ID for deletion too
            try:
                display_time = _format_secs(auto_secs)
                notif = await client.send_message(
                    chat_id=user_id,
                    text=(
                        f"<b>⏳ Auto-Delete Active!</b>\n\n"
                        f"This media will be automatically deleted in "
                        f"<b>{display_time}</b> due to security reasons. "
                        f"Please save it to your <b>Saved Messages</b> if needed."
                    ),
                    disable_web_page_preview=True
                )
                if notif:
                    sent_msg_ids.append(notif.id)
            except Exception as e:
                logger.warning(f"Failed to send auto-delete notice: {e}")

            asyncio.create_task(
                _schedule_delete(client, user_id, sent_msg_ids, auto_secs)
            )
            logger.info(
                f"Auto-delete scheduled: {len(sent_msg_ids)} msg(s) for user {user_id} "
                f"in {auto_secs}s (bot {bot_id})"
            )

    return delivered > 0


# ─────────────────────────────────────────────────────────────────────────────
# /start handler
# ─────────────────────────────────────────────────────────────────────────────

async def start_handler(client, message: Message):
    user_id = message.from_user.id
    name    = message.from_user.first_name or "there"

    # ── Master Bot shows a simple welcome ────────────────────────────────────
    if client.me.id == Config.MASTER_BOT_ID:
        return await message.reply(
            f"<b>👋 Hello {name}!</b>\n\n"
            "This is the <b>Master Cloner Bot</b>.\n\n"
            "Use /clone to create your own Force Sub bot.\n"
            "<i>I do not serve media files directly.</i>"
        )

    # ── Clone Bot logic ───────────────────────────────────────────────────────
    bot_id = client.me.id
    await db.add_user(bot_id, user_id, name)

    # ── Subscription gate — ALWAYS runs before any media delivery ─────────────
    subscribed, missing_channels = await is_subscribed(client, user_id)

    # ── Deep-link / file delivery ─────────────────────────────────────────────
    text  = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) > 1:
        media_id = parts[1].strip()
        logger.info(
            f"FILE REQUEST: User {user_id} → media_id={media_id} "
            f"| subscribed={subscribed} | on @{client.me.username}"
        )

        if not subscribed:
            # Save what the user wanted — delivered automatically after they join
            await db._users.update_one(
                {"bot_id": bot_id, "user_id": user_id},
                {"$set": {"last_media_id": media_id}}
            )
            return await message.reply(
                Config.FORCE_MSG,
                reply_markup=get_fsub_buttons(missing_channels)
            )

        # User is confirmed subscribed — deliver media
        media = await db.get_media(bot_id, media_id)
        if media:
            success = await deliver_media(client, user_id, bot_id, media)
            if not success:
                await message.reply(
                    "<b>❌ Delivery Failed!</b>\n\n"
                    "I could not retrieve this file. "
                    "Please contact the bot owner."
                )
        else:
            await message.reply(
                "<b>❌ File Not Found!</b>\n\n"
                "This link may have expired or been deleted."
            )
        return

    # ── Plain /start — show welcome or force-sub ───────────────────────────────
    if not subscribed:
        return await message.reply(
            Config.FORCE_MSG,
            reply_markup=get_fsub_buttons(missing_channels)
        )

    await message.reply(Config.START_MSG.format(name=name))



# ─────────────────────────────────────────────────────────────────────────────
# "I've Joined" callback
# ─────────────────────────────────────────────────────────────────────────────

async def check_subscription_callback(client, callback: CallbackQuery):
    user_id = callback.from_user.id
    bot_id  = client.me.id

    await callback.answer()   # acknowledge the tap immediately

    subscribed, missing_channels = await is_subscribed(client, user_id)

    if subscribed:
        # ── Deliver pending media if any ──────────────────────────────────────
        user_data = await db._users.find_one({"bot_id": bot_id, "user_id": user_id})
        pending_media_id = user_data.get("last_media_id") if user_data else None

        if pending_media_id:
            media = await db.get_media(bot_id, pending_media_id)
            if media:
                success = await deliver_media(client, user_id, bot_id, media)
                if success:
                    # Clean up pending request and close the sub-prompt
                    await db._users.update_one(
                        {"bot_id": bot_id, "user_id": user_id},
                        {"$unset": {"last_media_id": ""}}
                    )
                    try:
                        await callback.message.delete()
                    except Exception:
                        pass
                    return
                else:
                    try:
                        await callback.message.edit(
                            "<b>❌ Delivery Failed!</b>\n\n"
                            "Could not send the file. Please contact the bot owner."
                        )
                    except Exception:
                        pass
                    return
            else:
                # Media was deleted — clean up and notify
                await db._users.update_one(
                    {"bot_id": bot_id, "user_id": user_id},
                    {"$unset": {"last_media_id": ""}}
                )

        # No pending media — just confirm subscription
        try:
            await callback.message.edit(
                "<b>✅ You're all set!</b>\n\n"
                "You have joined all mandatory channels.\n"
                "<i>Use your media link again to get your file.</i>"
            )
        except Exception:
            pass

    else:
        # Still missing channels — update the button list (shrinks as they join)
        try:
            await callback.message.edit(
                Config.FORCE_MSG,
                reply_markup=get_fsub_buttons(missing_channels)
            )
        except Exception as e:
            if "MESSAGE_NOT_MODIFIED" not in str(e):
                logger.error(f"Error updating subscription prompt: {e}")
