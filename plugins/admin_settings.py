import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ChatType, ChatMemberStatus
from database.mongo import db
from utils.checks import is_bot_owner

logger = logging.getLogger(__name__)

# State management for setting custom links
# Format: {user_id: {chat_id: int, bot_id: int}}
WAITING_FOR_LINK = {}

# State management for custom auto-delete time input
# Format: {user_id: bot_id}
WAITING_FOR_DELETE_TIME = {}

# ── Auto-Delete preset options (label → seconds) ───────────────────────────
AUTO_DELETE_OPTIONS = [
    ("🚫 Off",    0),
    ("1 min",    60),
    ("5 min",   300),
    ("10 min",  600),
    ("30 min", 1800),
    ("1 hour", 3600),
]

# Manual Registration in manager.py
async def is_bot_owner_wrapper(client, user_id):
    return await is_bot_owner(client, user_id)

# Register manually in manager.py
async def settings_panel(client, message: Message):
    from config import Config
    if client.me.id == Config.MASTER_BOT_ID:
        return # Ignore on Main Bot
    
    if not await is_bot_owner_wrapper(client, message.from_user.id):
        return await message.reply(f"<b>❌ Access Denied!</b>\n\nYour ID (<code>{message.from_user.id}</code>) is not authorized for this bot.")
    
    await message.reply(
        "<b>🛠️ Admin Control Panel</b>\n\nManage your channels and media settings here.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Manage Channels", callback_data="manage_channels")],
            [InlineKeyboardButton("📁 Manage Media",    callback_data="manage_media_0")],
            [InlineKeyboardButton("⏱️ Auto Delete",     callback_data="auto_delete_menu")],
            [InlineKeyboardButton("📊 Bot Statistics",  callback_data="stats_panel")],
            [InlineKeyboardButton("❌ Close",           callback_data="close_panel")]
        ])
    )

# Register manually in manager.py
async def manage_channels_menu(client, callback: CallbackQuery):
    if not await is_bot_owner(client, callback.from_user.id):
        return await callback.answer("❌ Access Denied!", show_alert=True)
        
    channels = await db.get_fsub_channels(client.me.id)
    text = "<b>📢 Mandatory Channels</b>\n\nManage your subscription requirements below."
    
    buttons = []
    for chat in channels:
        # Row 1: Channel Title + Remove Button
        buttons.append([
            InlineKeyboardButton(f"📍 {chat['title']}", callback_data=f"ignore"),
            InlineKeyboardButton("🗑️ Remove", callback_data=f"remove_chan_{chat['chat_id']}")
        ])
        # Row 2: Link management
        link_status = "✅ Set" if chat.get("custom_link") else "❌ Not Set"
        buttons.append([
            InlineKeyboardButton(f"🔗 Link: {link_status}", callback_data=f"set_link_{chat['chat_id']}"),
            InlineKeyboardButton("🔄 Clear Link", callback_data=f"clear_link_{chat['chat_id']}")
        ])
    
    buttons.append([InlineKeyboardButton("➕ Add Channel", callback_data="add_channel_prompt")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_settings")])
    
    await callback.message.edit(text, reply_markup=InlineKeyboardMarkup(buttons))

# Register manually in manager.py
async def set_link_prompt(client, callback: CallbackQuery):
    if not await is_bot_owner(client, callback.from_user.id):
        return await callback.answer("❌ Access Denied!", show_alert=True)

    # callback.data format: "set_link_<chat_id>" — chat_id may be negative
    chat_id = int(callback.data[len("set_link_"):])
    WAITING_FOR_LINK[callback.from_user.id] = {"chat_id": chat_id, "bot_id": client.me.id}
    
    await callback.message.edit(
        "<b>🔗 Set Custom Invite Link</b>\n\n"
        "Send the invite URL for this channel.\n\n"
        "<i>Example: https://t.me/+AbcDeFg123</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="manage_channels")]])
    )

async def clear_link_callback(client, callback: CallbackQuery):
    if not await is_bot_owner(client, callback.from_user.id):
        return await callback.answer("❌ Access Denied!", show_alert=True)

    # callback.data format: "clear_link_<chat_id>"
    chat_id = int(callback.data[len("clear_link_"):])
    await db.update_fsub_link(client.me.id, chat_id, None)
    await callback.answer("✅ Custom link cleared!", show_alert=True)
    await manage_channels_menu(client, callback)

# Register manually in manager.py
async def remove_channel_callback(client, callback: CallbackQuery):
    if not await is_bot_owner(client, callback.from_user.id):
        return await callback.answer("❌ Access Denied!", show_alert=True)

    # callback.data format: "remove_chan_<chat_id>"
    chat_id = int(callback.data[len("remove_chan_"):])
    await db.remove_fsub_channel(client.me.id, chat_id)
    await callback.answer("✅ Channel removed!", show_alert=True)
    await manage_channels_menu(client, callback)

# Register manually in manager.py
async def add_channel_prompt(client, callback: CallbackQuery):
    await callback.message.edit(
        "<b>➕ Add New Channel or Group</b>\n\n"
        "You can add requirements in two ways:\n\n"
        "1️⃣ <b>Forward a message</b> from the channel/group here.\n"
        "2️⃣ <b>Send the Username or ID</b> (e.g., @MyChannel or -100123456).\n\n"
        "💡 <b>Tip for Private Groups:</b>\n"
        "- Add the bot as an Admin in the group.\n"
        "- Send a message in the group and check your terminal for the ID.\n"
        "- Paste that ID here directly.\n\n"
        "<i>To add multiple at once, separate them with commas!</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="manage_channels")]])
    )

# Manual registration in manager.py
async def handle_channel_input(client, message: Message):
    user_id = message.from_user.id
    if not await is_bot_owner(client, user_id):
        return
    
    # CASE 0: Setting a custom link
    if user_id in WAITING_FOR_LINK and WAITING_FOR_LINK[user_id]["bot_id"] == client.me.id:
        link = message.text.strip()
        if not link.startswith(("http://", "https://", "t.me/")):
            return await message.reply("<b>❌ Invalid Link!</b>\nPlease send a valid URL starting with https:// or t.me/")
        
        chat_id = WAITING_FOR_LINK[user_id]["chat_id"]
        await db.update_fsub_link(client.me.id, chat_id, link)
        del WAITING_FOR_LINK[user_id]
        
        return await message.reply(
            f"<b>✅ Custom Link Saved!</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Channels", callback_data="manage_channels")]])
        )

    # CASE 1: Setting a custom auto-delete time
    if user_id in WAITING_FOR_DELETE_TIME and WAITING_FOR_DELETE_TIME[user_id] == client.me.id:
        raw = (message.text or "").strip()
        secs = _parse_time_input(raw)
        if secs is None:
            return await message.reply(
                "<b>❌ Invalid time!</b>\n\n"
                "Examples: <code>30</code> (seconds), <code>2m</code> (minutes), <code>1h</code> (hour)\n"
                "Min: 10 seconds | Max: 24 hours"
            )
        del WAITING_FOR_DELETE_TIME[user_id]
        await db.set_bot_setting(client.me.id, "auto_delete_secs", secs)

        display = _format_secs(secs)
        return await message.reply(
            f"<b>✅ Auto-Delete set to {display}!</b>\n\n"
            f"<i>All media sent to users will be deleted after {display}.</i>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Settings", callback_data="back_to_settings")]])
        )

    # Handle /commands separately
    if message.text and message.text.startswith("/"):
        return

    channels_to_add = []
    
    # CASE 2: Forwarded message
    if message.forward_from_chat:
        f_type = message.forward_from_chat.type
        if f_type in [ChatType.CHANNEL, ChatType.SUPERGROUP, ChatType.GROUP]:
            channels_to_add.append((message.forward_from_chat.id, message.forward_from_chat.title, getattr(message.forward_from_chat, "username", None)))
        else:
            return await message.reply(f"<b>❌ Error:</b> Forwarded from a {f_type}. Only Channels/Groups are supported.")
    
    # CASE 3: Text input (usernames/IDs)
    elif message.text:
        inputs = [i.strip() for i in message.text.split(",")]
        for item in inputs:
            try:
                chat = await client.get_chat(item)
                if chat.type in [ChatType.CHANNEL, ChatType.SUPERGROUP, ChatType.GROUP]:
                    channels_to_add.append((chat.id, chat.title, getattr(chat, "username", None)))
                else:
                    await message.reply(f"<b>❌ Error:</b> {item} is a {chat.type}. Only Channels/Groups are supported.")
            except Exception as e:
                await message.reply(f"<b>❌ Error for {item}:</b> {str(e)}")

    # Process and verify admin status for each
    for chat_id, title, username in channels_to_add:
        try:
            member = await client.get_chat_member(chat_id, "me")
            if member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                await db.add_fsub_channel(client.me.id, chat_id, title, username)
                await message.reply(f"<b>✅ Success!</b>\nAdded: <b>{title}</b>")
            else:
                await message.reply(f"<b>❌ Error for {title}:</b> I am not an administrator there (Status: {member.status}).")
        except Exception as e:
            if "Peer id invalid" in str(e):
                try:
                    await client.get_chat(chat_id)
                    member = await client.get_chat_member(chat_id, "me")
                    if member.status in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]:
                        await db.add_fsub_channel(client.me.id, chat_id, title, username)
                        await message.reply(f"<b>✅ Success!</b>\nAdded: <b>{title}</b>")
                except Exception as inner_e:
                    await message.reply(f"<b>❌ Error for {title}:</b> Could not verify admin status. {str(inner_e)}\n\n<i>Note: Ensure the bot is an Administrator in the chat!</i>")
            else:
                await message.reply(f"<b>❌ Error for {title}:</b> Could not verify admin status. {str(e)}\n\n<i>Note: Use numeric IDs for private groups/channels.</i>")

# Register manually in manager.py
async def manage_media_menu(client, callback: CallbackQuery, skip: int = None):
    if not await is_bot_owner(client, callback.from_user.id):
        return await callback.answer("❌ Access Denied!", show_alert=True)

    if skip is None:
        try:
            skip = int(callback.data.split("_")[2])
        except (IndexError, ValueError):
            skip = 0
    
    media_list = await db._media.find({"bot_id": client.me.id}).sort("_id", -1).skip(skip).limit(5).to_list(length=5)
    
    text = f"<b>📁 Media Management</b>\n\nShowing items {skip+1} to {skip+len(media_list)}."
    
    buttons = []
    for m in media_list:
        buttons.append([InlineKeyboardButton(f"🗑️ Delete {m['media_id']}", callback_data=f"del_med_{m['media_id']}")])
    
    nav = []
    if skip > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"manage_media_{skip-5}"))
    if len(media_list) == 5:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"manage_media_{skip+5}"))
    if nav: buttons.append(nav)
        
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_settings")])
    await callback.message.edit(text, reply_markup=InlineKeyboardMarkup(buttons))

# Register manually in manager.py
async def delete_media_callback(client, callback: CallbackQuery):
    if not await is_bot_owner(client, callback.from_user.id):
        return await callback.answer("❌ Access Denied!", show_alert=True)

    # callback.data format: "del_med_<media_id>"
    media_id = callback.data[len("del_med_"):]
    await db.delete_media(client.me.id, media_id)
    await callback.answer("✅ Media deleted!", show_alert=True)
    await manage_media_menu(client, callback, skip=0)

# Register manually in manager.py
async def stats_panel_callback(client, callback: CallbackQuery):
    bot_id = client.me.id
    total_users    = await db.get_total_users(bot_id)
    total_media    = await db._media.count_documents({"bot_id": bot_id})
    total_channels = await db._fsub.count_documents({"bot_id": bot_id})

    # Show auto-delete setting too
    auto_secs = await db.get_bot_setting(bot_id, "auto_delete_secs", 0)
    auto_display = _format_secs(auto_secs) if auto_secs else "Off"
    
    text = (
        f"<b>📊 Bot Statistics</b>\n\n"
        f"👤 <b>Users:</b> {total_users}\n"
        f"📁 <b>Media:</b> {total_media}\n"
        f"📢 <b>Channels:</b> {total_channels}\n"
        f"⏱️ <b>Auto-Delete:</b> {auto_display}"
    )
    await callback.message.edit(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_settings")]])
    )

# Register manually in manager.py
async def back_to_settings(client, callback: CallbackQuery):
    await callback.message.edit(
        "<b>🛠️ Admin Control Panel</b>\n\nManage your channels and media settings here.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Manage Channels", callback_data="manage_channels")],
            [InlineKeyboardButton("📁 Manage Media",    callback_data="manage_media_0")],
            [InlineKeyboardButton("⏱️ Auto Delete",     callback_data="auto_delete_menu")],
            [InlineKeyboardButton("📊 Bot Statistics",  callback_data="stats_panel")],
            [InlineKeyboardButton("❌ Close",           callback_data="close_panel")]
        ])
    )

# Register manually in manager.py
async def close_panel(client, callback: CallbackQuery):
    await callback.message.delete()


# ─────────────────────────────────────────────────────────────────────────────
# ⏱️ Auto-Delete Settings Panel  (NEW)
# ─────────────────────────────────────────────────────────────────────────────

def _format_secs(secs: int) -> str:
    """Convert seconds to a human-readable string."""
    if secs <= 0:
        return "Off"
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        m = secs // 60
        return f"{m} min"
    h = secs // 3600
    return f"{h} hr"


def _parse_time_input(raw: str):
    """
    Parse user-provided time string into seconds.
    Accepts: plain integer (seconds), or suffixed values like 2m, 1h, 30s.
    Returns None on failure.
    """
    raw = raw.strip().lower()
    try:
        if raw.endswith("h"):
            val = int(raw[:-1]) * 3600
        elif raw.endswith("m"):
            val = int(raw[:-1]) * 60
        elif raw.endswith("s"):
            val = int(raw[:-1])
        else:
            val = int(raw)
        if 10 <= val <= 86400:   # 10 seconds to 24 hours
            return val
        return None
    except ValueError:
        return None


# Register manually in manager.py
async def auto_delete_menu(client, callback: CallbackQuery):
    """Show the auto-delete settings panel."""
    if not await is_bot_owner(client, callback.from_user.id):
        return await callback.answer("❌ Access Denied!", show_alert=True)

    current = await db.get_bot_setting(client.me.id, "auto_delete_secs", 0)
    current_display = _format_secs(current) if current else "Off"

    buttons = []
    row = []
    for label, secs in AUTO_DELETE_OPTIONS:
        tick = "✅ " if current == secs else ""
        row.append(InlineKeyboardButton(f"{tick}{label}", callback_data=f"set_autodel_{secs}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("✏️ Custom Time", callback_data="autodel_custom")])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_settings")])

    await callback.message.edit(
        f"<b>⏱️ Auto-Delete Settings</b>\n\n"
        f"<b>Current:</b> {current_display}\n\n"
        f"Choose how long after delivery the media message should be automatically deleted.\n"
        f"<i>Select 'Off' to disable.</i>",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# Register manually in manager.py
async def set_auto_delete_callback(client, callback: CallbackQuery):
    """Handle preset auto-delete time selection."""
    if not await is_bot_owner(client, callback.from_user.id):
        return await callback.answer("❌ Access Denied!", show_alert=True)

    # callback.data: "set_autodel_<seconds>"
    secs = int(callback.data[len("set_autodel_"):])
    await db.set_bot_setting(client.me.id, "auto_delete_secs", secs)

    display = _format_secs(secs) if secs else "Off"
    await callback.answer(f"✅ Auto-Delete set to {display}!", show_alert=True)
    await auto_delete_menu(client, callback)


# Register manually in manager.py
async def autodel_custom_prompt(client, callback: CallbackQuery):
    """Prompt the user to type a custom auto-delete time."""
    if not await is_bot_owner(client, callback.from_user.id):
        return await callback.answer("❌ Access Denied!", show_alert=True)

    WAITING_FOR_DELETE_TIME[callback.from_user.id] = client.me.id

    await callback.message.edit(
        "<b>✏️ Custom Auto-Delete Time</b>\n\n"
        "Type the time you want:\n\n"
        "• <code>30</code> → 30 seconds\n"
        "• <code>2m</code> → 2 minutes\n"
        "• <code>1h</code> → 1 hour\n\n"
        "<i>Min: 10 seconds | Max: 24 hours</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="auto_delete_menu")]])
    )
