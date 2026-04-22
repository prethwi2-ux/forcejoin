import asyncio
import logging
from pyrogram.errors import UserNotParticipant, ChatAdminRequired, ChannelPrivate
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database.mongo import db

logger = logging.getLogger(__name__)

# ── Membership statuses that count as "subscribed" ───────────────────────────
_MEMBER_STATUSES = {
    ChatMemberStatus.OWNER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.RESTRICTED,   # restricted but still in the chat
}


async def _check_single_channel(client, bot_id: int, chat_data: dict, user_id: int) -> bool:
    """
    Return True ONLY if the user is positively confirmed as subscribed.
    Return False in ALL error/unknown cases (fail closed).
    This ensures media is never leaked to unsubscribed users.
    """
    chat_id = chat_data["chat_id"]
    title   = chat_data.get("title", str(chat_id))

    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status in _MEMBER_STATUSES:
            return True
        # Status is BANNED / LEFT — definitely not subscribed
        return False

    except UserNotParticipant:
        # Not a member — check for a pending join request below
        pass

    except (ChatAdminRequired, ChannelPrivate) as e:
        # ⚠️  Bot is NOT an admin in this channel — it cannot check membership.
        # We MUST fail closed here, otherwise every user bypasses the gate.
        logger.error(
            f"[FSUB CONFIG ERROR] Cannot check '{title}' ({chat_id}): {e}. "
            "Make the bot an ADMIN of that channel with 'Add Members' permission!"
        )
        return False

    except Exception as e:
        err = str(e)
        if "PEER_ID_INVALID" in err or "Peer id invalid" in err:
            # Bot hasn't resolved this peer yet — warm up and retry once
            try:
                target = chat_data.get("username") or chat_id
                await client.get_chat(target)
                member = await client.get_chat_member(chat_id, user_id)
                return member.status in _MEMBER_STATUSES
            except UserNotParticipant:
                pass  # Confirmed not a member — fall through to join-request check
            except Exception as inner:
                logger.error(f"[FSUB] Peer resolve failed for '{title}': {inner}")
                return False  # Can't confirm — block access
        else:
            logger.error(f"[FSUB] Unexpected error checking '{title}' for user {user_id}: {e}")
            return False  # Unknown error — block access to be safe

    # ── Join-Request check (for private channels set to "request to join") ────
    if await db.has_pending_request(bot_id, chat_id, user_id):
        logger.info(f"User {user_id} has a pending join-request for '{title}' — granting access.")
        return True

    return False



async def is_subscribed(client, user_id: int):
    """
    Check whether a user is subscribed to ALL mandatory channels for this bot.

    Returns:
        (True, [])                   — fully subscribed
        (False, [missing_channels])  — list of channels still needed
    """
    bot_id = client.me.id
    fsub_channels = await db.get_fsub_channels(bot_id)

    if not fsub_channels:
        return True, []

    # ── Run all membership checks CONCURRENTLY ────────────────────────────────
    # This fires all get_chat_member() calls in parallel instead of serially,
    # which is critical when 100s of users check in at the same time.
    results = await asyncio.gather(
        *[_check_single_channel(client, bot_id, ch, user_id) for ch in fsub_channels],
        return_exceptions=False
    )

    missing = [ch for ch, ok in zip(fsub_channels, results) if not ok]

    logger.info(
        f"SUB CHECK: User {user_id} | "
        f"Total={len(fsub_channels)} | Missing={len(missing)}"
    )

    if missing:
        return False, missing
    return True, []


def _build_channel_link(chat_data: dict) -> str:
    """
    Build the best possible join link for a channel, in priority order:
    1. Custom manual link (invite links for private channels)
    2. Public @username link
    3. Private channel numeric link (t.me/c/...)
    4. Fallback: tell user to search (shouldn't normally happen)
    """
    custom_link = chat_data.get("custom_link")
    if custom_link:
        return custom_link

    username = chat_data.get("username")
    if username:
        return f"https://t.me/{username.lstrip('@')}"

    chat_id = str(chat_data["chat_id"])
    if chat_id.startswith("-100"):
        # Private supergroup / channel  — link to channel home (message 1)
        return f"https://t.me/c/{chat_id[4:]}/1"

    # If we reach here the channel has no username and an unusual ID format.
    # Return a safe placeholder so the button is still shown without crashing.
    return f"https://t.me/c/{chat_id.lstrip('-')}/1"


def get_fsub_buttons(missing_channels: list) -> InlineKeyboardMarkup:
    """Build an inline keyboard with one join button per missing channel."""
    buttons = []
    for chat_data in missing_channels:
        title = chat_data.get("title", "Channel")
        link  = _build_channel_link(chat_data)
        buttons.append([InlineKeyboardButton(f"📢 Join {title}", url=link)])

    buttons.append([InlineKeyboardButton("✅ I've Joined — Check Again", callback_data="check_sub")])
    return InlineKeyboardMarkup(buttons)
