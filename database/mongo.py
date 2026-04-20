import logging
import motor.motor_asyncio
from pymongo import ASCENDING, DESCENDING
from config import Config

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(Config.MONGO_DB_URL)
        self._db    = self._client["ForceSubBot"]

        # Collections
        self._users     = self._db["users"]
        self._media     = self._db["media"]
        self._fsub      = self._db["fsub_channels"]
        self._clones    = self._db["clones"]
        self._join_reqs = self._db["join_requests"]

    # ─────────────────────────────────────────────────────────────────────────
    # Startup: create indexes for performance
    # ─────────────────────────────────────────────────────────────────────────
    async def setup_indexes(self):
        """Call once at startup to ensure DB is indexed properly."""
        try:
            await self._users.create_index(
                [("bot_id", ASCENDING), ("user_id", ASCENDING)], unique=True
            )
            await self._media.create_index(
                [("bot_id", ASCENDING), ("media_id", ASCENDING)], unique=True
            )
            await self._fsub.create_index(
                [("bot_id", ASCENDING), ("chat_id", ASCENDING)], unique=True
            )
            await self._clones.create_index(
                [("bot_token", ASCENDING)], unique=True
            )
            await self._join_reqs.create_index(
                [("bot_id", ASCENDING), ("chat_id", ASCENDING), ("user_id", ASCENDING)],
                unique=True
            )
            logger.info("✅ DB indexes created / verified.")
        except Exception as e:
            logger.error(f"Failed to create DB indexes: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Users
    # ─────────────────────────────────────────────────────────────────────────
    async def add_user(self, bot_id: int, user_id: int, name: str):
        """Upsert-safe user registration (no race conditions)."""
        await self._users.update_one(
            {"bot_id": bot_id, "user_id": user_id},
            {"$setOnInsert": {"bot_id": bot_id, "user_id": user_id, "name": name}},
            upsert=True
        )

    async def get_total_users(self, bot_id: int) -> int:
        return await self._users.count_documents({"bot_id": bot_id})

    async def get_all_users(self):
        """Return all users across every bot instance."""
        return await self._users.find().to_list(length=1_000_000)

    async def get_all_users_for_bot(self, bot_id: int):
        """Return users belonging to a specific bot instance."""
        return await self._users.find({"bot_id": bot_id}).to_list(length=1_000_000)

    # ─────────────────────────────────────────────────────────────────────────
    # Force-Sub Channels
    # ─────────────────────────────────────────────────────────────────────────
    async def add_fsub_channel(self, bot_id: int, chat_id: int, title: str, username: str = None):
        await self._fsub.update_one(
            {"bot_id": bot_id, "chat_id": chat_id},
            {"$set": {"title": title, "username": username}},
            upsert=True
        )

    async def remove_fsub_channel(self, bot_id: int, chat_id: int):
        await self._fsub.delete_one({"bot_id": bot_id, "chat_id": chat_id})

    async def update_fsub_link(self, bot_id: int, chat_id: int, custom_link: str):
        await self._fsub.update_one(
            {"bot_id": bot_id, "chat_id": chat_id},
            {"$set": {"custom_link": custom_link}}
        )

    async def get_fsub_channels(self, bot_id: int):
        return await self._fsub.find({"bot_id": bot_id}).to_list(length=100)

    # ─────────────────────────────────────────────────────────────────────────
    # Join Requests
    # ─────────────────────────────────────────────────────────────────────────
    async def add_join_request(self, bot_id: int, chat_id: int, user_id: int):
        await self._join_reqs.update_one(
            {"bot_id": bot_id, "chat_id": chat_id, "user_id": user_id},
            {"$set": {"status": "pending"}},
            upsert=True
        )

    async def has_pending_request(self, bot_id: int, chat_id: int, user_id: int) -> bool:
        doc = await self._join_reqs.find_one(
            {"bot_id": bot_id, "chat_id": chat_id, "user_id": user_id}
        )
        return doc is not None

    async def remove_join_request(self, bot_id: int, chat_id: int, user_id: int):
        await self._join_reqs.delete_one(
            {"bot_id": bot_id, "chat_id": chat_id, "user_id": user_id}
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Media
    # ─────────────────────────────────────────────────────────────────────────
    async def save_media(self, bot_id: int, media_id: str, chat_id: int, message_ids):
        ids = message_ids if isinstance(message_ids, list) else [message_ids]
        await self._media.insert_one({
            "bot_id": bot_id,
            "media_id": media_id,
            "chat_id": chat_id,
            "message_ids": ids
        })

    async def get_media(self, bot_id: int, media_id: str):
        return await self._media.find_one({"bot_id": bot_id, "media_id": media_id})

    async def delete_media(self, bot_id: int, media_id: str):
        await self._media.delete_one({"bot_id": bot_id, "media_id": media_id})

    # ─────────────────────────────────────────────────────────────────────────
    # Clone Management
    # ─────────────────────────────────────────────────────────────────────────
    async def add_clone(self, user_id: int, bot_token: str, bot_username: str, bot_id: int = None):
        await self._clones.update_one(
            {"bot_token": bot_token},
            {"$set": {"user_id": user_id, "bot_username": bot_username, "bot_id": bot_id}},
            upsert=True
        )

    async def get_all_clones(self):
        return await self._clones.find().to_list(length=1000)

    async def get_user_clones(self, user_id: int):
        return await self._clones.find({"user_id": user_id}).to_list(length=100)

    async def remove_clone(self, bot_token: str):
        await self._clones.delete_one({"bot_token": bot_token})


db = Database()
