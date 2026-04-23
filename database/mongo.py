import logging
import time
import motor.motor_asyncio
from pymongo import ASCENDING
from config import Config

# ── In-memory TTL cache for fsub channel lists ────────────────────────────────
_FSUB_CACHE: dict = {}
_FSUB_CACHE_TTL = 30   # seconds

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(
            Config.MONGO_DB_URL,
            maxPoolSize=200,   # Adjusted for higher traffic
            retryWrites=True
        )
        self._db     = self._client["ForceSubBot"]

        # Collections
        self._users     = self._db["users"]
        self._media     = self._db["media"]
        self._fsub      = self._db["fsub_channels"]
        self._clones    = self._db["clones"]
        self._join_reqs = self._db["join_requests"]
        self._settings  = self._db["bot_settings"]
        self._sessions  = self._db["bot_sessions"]     # Pyrogram StringSessions
        self._states    = self._db["user_states"]      # Transient per-user states (batch, waiting)
        self._batches   = self._db["batch_sessions"]   # Active batch uploads

    # ─────────────────────────────────────────────────────────────────────────
    # Startup indexes
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
            await self._settings.create_index(
                [("bot_id", ASCENDING)], unique=True
            )
            await self._sessions.create_index(
                [("bot_token", ASCENDING)], unique=True
            )
            await self._states.create_index(
                [("bot_id", ASCENDING), ("user_id", ASCENDING)], unique=True
            )
            await self._batches.create_index(
                [("bot_id", ASCENDING), ("user_id", ASCENDING)], unique=True
            )
            logger.info("✅ DB indexes created / verified.")
        except Exception as e:
            logger.error(f"Failed to create DB indexes: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Users
    # ─────────────────────────────────────────────────────────────────────────
    async def add_user(self, bot_id: int, user_id: int, name: str):
        """Upsert-safe user registration."""
        await self._users.update_one(
            {"bot_id": bot_id, "user_id": user_id},
            {"$setOnInsert": {"bot_id": bot_id, "user_id": user_id, "name": name}},
            upsert=True
        )

    async def get_total_users(self, bot_id: int) -> int:
        return await self._users.count_documents({"bot_id": bot_id})

    async def get_all_users(self):
        """Async generator — streams all users without loading into RAM."""
        async for doc in self._users.find():
            yield doc

    async def get_all_users_for_bot(self, bot_id: int):
        """Async generator — streams users for a specific bot."""
        async for doc in self._users.find({"bot_id": bot_id}):
            yield doc

    async def count_all_users(self) -> int:
        return await self._users.estimated_document_count()

    async def count_users_for_bot(self, bot_id: int) -> int:
        return await self._users.count_documents({"bot_id": bot_id})

    async def delete_user(self, bot_id: int, user_id: int):
        """Remove a user record (e.g. bot was blocked)."""
        await self._users.delete_one({"bot_id": bot_id, "user_id": user_id})

    # ─────────────────────────────────────────────────────────────────────────
    # Force-Sub Channels
    # ─────────────────────────────────────────────────────────────────────────
    async def add_fsub_channel(self, bot_id: int, chat_id: int, title: str, username: str = None):
        await self._fsub.update_one(
            {"bot_id": bot_id, "chat_id": chat_id},
            {"$set": {"title": title, "username": username}},
            upsert=True
        )
        _FSUB_CACHE.pop(bot_id, None)

    async def remove_fsub_channel(self, bot_id: int, chat_id: int):
        await self._fsub.delete_one({"bot_id": bot_id, "chat_id": chat_id})
        _FSUB_CACHE.pop(bot_id, None)

    async def update_fsub_link(self, bot_id: int, chat_id: int, custom_link: str):
        await self._fsub.update_one(
            {"bot_id": bot_id, "chat_id": chat_id},
            {"$set": {"custom_link": custom_link}}
        )
        _FSUB_CACHE.pop(bot_id, None)

    async def get_fsub_channels(self, bot_id: int) -> list:
        """Return fsub channel list with TTL cache."""
        now = time.monotonic()
        cached = _FSUB_CACHE.get(bot_id)
        if cached and (now - cached[0]) < _FSUB_CACHE_TTL:
            return cached[1]
        channels = await self._fsub.find({"bot_id": bot_id}).to_list(length=100)
        _FSUB_CACHE[bot_id] = (now, channels)
        return channels

    async def delete_all_fsub_channels(self, bot_id: int):
        """Remove all fsub channels for a bot (e.g. on clone deletion)."""
        await self._fsub.delete_many({"bot_id": bot_id})
        _FSUB_CACHE.pop(bot_id, None)

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
            "bot_id":      bot_id,
            "media_id":    media_id,
            "chat_id":     chat_id,
            "message_ids": ids
        })

    async def get_media(self, bot_id: int, media_id: str):
        return await self._media.find_one({"bot_id": bot_id, "media_id": media_id})

    async def delete_media(self, bot_id: int, media_id: str):
        await self._media.delete_one({"bot_id": bot_id, "media_id": media_id})

    async def delete_all_media(self, bot_id: int):
        """Delete all media for a bot (e.g. on clone deletion)."""
        await self._media.delete_many({"bot_id": bot_id})

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
        """Remove clone record AND purge all associated data."""
        clone = await self._clones.find_one({"bot_token": bot_token})
        if clone and clone.get("bot_id"):
            bid = clone["bot_id"]
            # Cascade-delete all bot-specific data
            await self._users.delete_many({"bot_id": bid})
            await self._media.delete_many({"bot_id": bid})
            await self._fsub.delete_many({"bot_id": bid})
            await self._settings.delete_many({"bot_id": bid})
            await self._sessions.delete_many({"bot_token": bot_token})
            await self._states.delete_many({"bot_id": bid})
            await self._batches.delete_many({"bot_id": bid})
            await self._join_reqs.delete_many({"bot_id": bid})
            _FSUB_CACHE.pop(bid, None)
            logger.info(f"🗑️ Cascade-deleted all data for bot_id={bid}")
        await self._clones.delete_one({"bot_token": bot_token})

    # ─────────────────────────────────────────────────────────────────────────
    # Per-Bot Settings
    # ─────────────────────────────────────────────────────────────────────────
    async def get_bot_setting(self, bot_id: int, key: str, default=None):
        doc = await self._settings.find_one({"bot_id": bot_id})
        if doc:
            return doc.get(key, default)
        return default

    async def set_bot_setting(self, bot_id: int, key: str, value):
        await self._settings.update_one(
            {"bot_id": bot_id},
            {"$set": {key: value}},
            upsert=True
        )

    async def get_all_bot_settings(self, bot_id: int) -> dict:
        doc = await self._settings.find_one({"bot_id": bot_id})
        if doc:
            doc.pop("_id", None)
            return doc
        return {}

    async def delete_bot_setting(self, bot_id: int, key: str):
        await self._settings.update_one(
            {"bot_id": bot_id},
            {"$unset": {key: ""}}
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Pyrogram StringSessions  (replaces .session files)
    # ─────────────────────────────────────────────────────────────────────────
    async def save_session(self, bot_token: str, session_string: str):
        """Persist a Pyrogram StringSession to DB."""
        await self._sessions.update_one(
            {"bot_token": bot_token},
            {"$set": {"session_string": session_string}},
            upsert=True
        )

    async def load_session(self, bot_token: str) -> str | None:
        """Load a saved StringSession, or None if not found."""
        doc = await self._sessions.find_one({"bot_token": bot_token})
        return doc["session_string"] if doc else None

    async def delete_session(self, bot_token: str):
        await self._sessions.delete_one({"bot_token": bot_token})

    # ─────────────────────────────────────────────────────────────────────────
    # User State  (replaces in-memory dicts: WAITING_FOR_LINK etc.)
    # ─────────────────────────────────────────────────────────────────────────
    async def set_user_state(self, bot_id: int, user_id: int, state: str, data: dict = None):
        """
        Store a transient state for a user (e.g. 'waiting_for_link').
        `data` is any extra payload needed (e.g. chat_id for link setting).
        """
        await self._states.update_one(
            {"bot_id": bot_id, "user_id": user_id},
            {"$set": {"state": state, "data": data or {}}},
            upsert=True
        )

    async def get_user_state(self, bot_id: int, user_id: int) -> dict | None:
        """Return the user's current state document, or None."""
        return await self._states.find_one({"bot_id": bot_id, "user_id": user_id})

    async def clear_user_state(self, bot_id: int, user_id: int):
        """Remove any pending state for this user."""
        await self._states.delete_one({"bot_id": bot_id, "user_id": user_id})

    # ─────────────────────────────────────────────────────────────────────────
    # Batch Sessions  (replaces in-memory BATCH_DATA dict)
    # ─────────────────────────────────────────────────────────────────────────
    async def start_batch(self, bot_id: int, user_id: int):
        """Begin a batch upload session for a user."""
        await self._batches.update_one(
            {"bot_id": bot_id, "user_id": user_id},
            {"$set": {"bot_id": bot_id, "user_id": user_id,
                      "chat_id": None, "ids": []}},
            upsert=True
        )

    async def get_batch(self, bot_id: int, user_id: int) -> dict | None:
        """Get the current batch session, or None."""
        return await self._batches.find_one({"bot_id": bot_id, "user_id": user_id})

    async def add_to_batch(self, bot_id: int, user_id: int, chat_id: int, message_id: int):
        """Append a message_id to the batch and set chat_id if first."""
        await self._batches.update_one(
            {"bot_id": bot_id, "user_id": user_id},
            {
                "$push": {"ids": message_id},
                "$setOnInsert": {"chat_id": chat_id}
            },
            upsert=True
        )
        # Also ensure chat_id is set if not already
        await self._batches.update_one(
            {"bot_id": bot_id, "user_id": user_id, "chat_id": None},
            {"$set": {"chat_id": chat_id}}
        )

    async def end_batch(self, bot_id: int, user_id: int) -> dict | None:
        """Retrieve and delete the batch session atomically."""
        return await self._batches.find_one_and_delete(
            {"bot_id": bot_id, "user_id": user_id}
        )

    async def cancel_batch(self, bot_id: int, user_id: int):
        """Delete a batch session without returning data."""
        await self._batches.delete_one({"bot_id": bot_id, "user_id": user_id})

    # ─────────────────────────────────────────────────────────────────────────
    # Global Statistics
    # ─────────────────────────────────────────────────────────────────────────
    async def get_clone_stats(self) -> list[dict]:
        """Return per-clone aggregated stats (users, media, channels)."""
        clones = await self._clones.find().to_list(length=1000)
        result = []
        for c in clones:
            bid = c.get("bot_id")
            if not bid:
                continue
            user_count    = await self._users.count_documents({"bot_id": bid})
            media_count   = await self._media.count_documents({"bot_id": bid})
            channel_count = await self._fsub.count_documents({"bot_id": bid})
            result.append({
                "bot_id":        bid,
                "bot_username":  c.get("bot_username", "unknown"),
                "owner_id":      c.get("user_id"),
                "user_count":    user_count,
                "media_count":   media_count,
                "channel_count": channel_count,
            })
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Pending Media (stored on user doc)
    # ─────────────────────────────────────────────────────────────────────────
    async def set_pending_media(self, bot_id: int, user_id: int, media_id: str):
        await self._users.update_one(
            {"bot_id": bot_id, "user_id": user_id},
            {"$set": {"last_media_id": media_id}}
        )

    async def get_pending_media(self, bot_id: int, user_id: int) -> str | None:
        doc = await self._users.find_one({"bot_id": bot_id, "user_id": user_id})
        return doc.get("last_media_id") if doc else None

    async def clear_pending_media(self, bot_id: int, user_id: int):
        await self._users.update_one(
            {"bot_id": bot_id, "user_id": user_id},
            {"$unset": {"last_media_id": ""}}
        )


db = Database()
