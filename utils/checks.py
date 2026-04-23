import logging
from pyrogram import Client
from config import Config
from database.mongo import db
import time

logger = logging.getLogger(__name__)


async def is_bot_owner(client: Client, user_id: int) -> bool:
    """
    Check whether a user is authorized to manage this bot instance.

    Priority order:
    1. Global Owner (OWNER_ID in config) — always has full access.
    2. If this IS the Master Bot, only the global owner is authorised.
    3. Whoever cloned this bot (stored in the clones collection).
    """
    user_id = int(user_id)

    # 1. Global master admin always wins
    if user_id == int(Config.OWNER_ID):
        logger.info(
            f"👑 MASTER OWNER INTERACTION:\n"
            f"   • Bot: @{client.me.username} (ID: {client.me.id})\n"
            f"   • Owner ID: {user_id}\n"
            f"   • Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return True

    # 2. No-one else controls the Master Bot itself
    if client.me.id == Config.MASTER_BOT_ID:
        logger.warning(
            f"OWNER CHECK: User {user_id} tried to access Master Bot but is not OWNER_ID."
        )
        return False

    # 3. Check whether this user created this clone
    clone_info = await db._clones.find_one({
        "$or": [
            {"bot_id": client.me.id},
            {"bot_username": client.me.username}
        ]
    })

    if clone_info and int(clone_info.get("user_id", -1)) == user_id:
        logger.info(
            f"👑 CLONE OWNER INTERACTION:\n"
            f"   • Bot: @{client.me.username} (ID: {client.me.id})\n"
            f"   • Owner ID: {user_id}\n"
            f"   • Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return True

    logger.warning(
        f"OWNER CHECK FAILED: @{client.me.username} (ID: {client.me.id}) — "
        f"User {user_id} is not authorised."
    )
    return False
