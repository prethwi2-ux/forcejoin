import logging
from pyrogram.types import ChatJoinRequest
from database.mongo import db
from plugins.force_sub import is_subscribed, get_fsub_buttons
from plugins.start import deliver_media

logger = logging.getLogger(__name__)


async def handle_join_request(client, request: ChatJoinRequest):
    """
    Called whenever a user requests to join one of the bot's force-subscribe channels.
    We record the request and, if the user is now fully subscribed, deliver their media.
    """
    bot_id = client.me.id

    # Only handle requests for channels configured for THIS bot
    fsub_channels = await db.get_fsub_channels(bot_id)
    fsub_ids      = {c["chat_id"] for c in fsub_channels}

    if request.chat.id not in fsub_ids:
        return   # Not our channel — ignore

    user_id = request.from_user.id
    name    = request.from_user.first_name or "User"

    # Register user and record their join request
    await db.add_user(bot_id, user_id, name)
    await db.add_join_request(bot_id, request.chat.id, user_id)

    logger.info(
        f"JOIN REQUEST: User {user_id} ({name}) → "
        f"Chat {request.chat.id} ({request.chat.title}) on @{client.me.username}"
    )

    # Check whether the user has a pending media request
    user_data = await db._users.find_one({"bot_id": bot_id, "user_id": user_id})
    pending_media_id = user_data.get("last_media_id") if user_data else None

    if not pending_media_id:
        return   # No media to deliver — nothing more to do

    # Check overall subscription (join requests count as subscribed)
    subscribed, missing_channels = await is_subscribed(client, user_id)

    if subscribed:
        media = await db.get_media(bot_id, pending_media_id)
        if not media:
            return   # Media was deleted — nothing to send

        try:
            await client.send_message(
                user_id,
                "<b>✨ Access Granted!</b>\n\nYou're now verified. Sending your file now…"
            )
        except Exception as e:
            # The user may have never started the bot (can't receive messages)
            logger.warning(f"Could not send verification message to {user_id}: {e}")
            return   # Can't message them, so stop here

        success = await deliver_media(client, user_id, bot_id, media)
        if success:
            await db._users.update_one(
                {"bot_id": bot_id, "user_id": user_id},
                {"$unset": {"last_media_id": ""}}
            )
        else:
            try:
                await client.send_message(
                    user_id,
                    "<b>❌ Delivery Failed!</b>\n\n"
                    "Your subscription was verified, but I couldn't send the file. "
                    "Please contact the bot owner."
                )
            except Exception:
                pass

    else:
        # Still missing some channels — remind them
        remaining_titles = ", ".join(f"<b>{c['title']}</b>" for c in missing_channels)
        try:
            await client.send_message(
                user_id,
                f"<b>✅ Request received for {request.chat.title}!</b>\n\n"
                f"You still need to join: {remaining_titles}\n\n"
                f"Join them then come back and press the check button.",
                reply_markup=get_fsub_buttons(missing_channels)
            )
        except Exception as e:
            logger.warning(f"Could not notify {user_id} about remaining channels: {e}")
