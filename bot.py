# import logging
# import random
# import string
# import yaml
# import base64
# import os
# import sqlite3
# import uuid
# import time
# import json
# from pathlib import Path
# from datetime import datetime, timedelta
# from urllib.parse import urlencode, quote
# from typing import Optional, List, Any

# # Import from python-telegram-bot library
# from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
# from telegram.ext import (
#     Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
# )
# from telegram.constants import ParseMode

# # --- Configuration & Constants ---
# BOT_TOKEN = os.getenv("BOT_TOKEN")
# ADMIN_ID = os.getenv("ADMIN")
# CONFIG_FILE = Path("database.yaml")
# USER_DB_FILE = Path("users.yaml")

# # Constants
# GB_TO_BYTES = 1024**3
# DAYS_TO_MS = 24 * 60 * 60 * 1000

# # Conversation states for the /edit command
# SELECT_USER, SELECT_DURATION, SELECT_QUOTA = range(3)


# # --- Logging Setup ---
# logging.basicConfig(
#     format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
# )
# logger = logging.getLogger(__name__)


# # ==============================================================================
# # DATABASE MANAGEMENT FUNCTIONS
# # ==============================================================================

# def _get_db_connection(db_path: Path) -> Optional[tuple[sqlite3.Connection, sqlite3.Cursor]]:
#     """Establishes a connection to the SQLite database."""
#     if not db_path.exists():
#         logger.error(f"Database not found at '{db_path.resolve()}'")
#         return None
#     try:
#         conn = sqlite3.connect(db_path)
#         conn.row_factory = sqlite3.Row
#         cur = conn.cursor()
#         return conn, cur
#     except sqlite3.Error as e:
#         logger.error(f"Database connection error: {e}")
#         return None

# def new_client(db_path: Path, inbound_id: int, email: str, total_gb: float, duration_days: int, reset_days: int) -> bool:
#     """Creates a new client in a specific inbound."""
#     db_conn = _get_db_connection(db_path)
#     if not db_conn: return False
#     conn, cur = db_conn
#     now_ms = int(time.time() * 1000)
#     expiry_ms = now_ms + (duration_days * DAYS_TO_MS)
#     total_bytes = int(total_gb * GB_TO_BYTES)
#     try:
#         inbound = cur.execute("SELECT settings FROM inbounds WHERE id = ?", (inbound_id,)).fetchone()
#         if not inbound:
#             logger.error(f"Inbound ID {inbound_id} not found in {db_path.name}.")
#             return False
#         settings = json.loads(inbound["settings"])
#         new_client_obj = {"comment": "","created_at": now_ms,"email": email,"enable": True,"expiryTime": expiry_ms,"flow": "","id": str(uuid.uuid4()),"limitIp": 0,"reset": reset_days,"subId": str(uuid.uuid4().hex)[:16],"tgId": 0,"totalGB": total_bytes,"updated_at": now_ms}
#         settings.setdefault("clients", []).append(new_client_obj)
#         cur.execute("UPDATE inbounds SET settings = ? WHERE id = ?", (json.dumps(settings, separators=(",", ":")), inbound_id))
#         cur.execute("INSERT INTO client_traffics (inbound_id, enable, email, up, down, all_time, expiry_time, total, reset, last_online) VALUES (?, 1, ?, 0, 0, 0, ?, ?, ?, ?)", (inbound_id, email, expiry_ms, total_bytes, reset_days, now_ms))
#         conn.commit()
#         logger.info(f"Successfully created client '{email}' in {db_path.name}.")
#         return True
#     except (sqlite3.Error, json.JSONDecodeError) as e:
#         conn.rollback()
#         logger.error(f"Error creating client '{email}' in {db_path.name}: {e}")
#         return False
#     finally:
#         conn.close()

# def delete_client(db_path: Path, inbound_id: int, email: str) -> bool:
#     """Deletes a client completely from a database."""
#     db_conn = _get_db_connection(db_path)
#     if not db_conn: return False
#     conn, cur = db_conn
#     try:
#         cur.execute("DELETE FROM client_traffics WHERE email = ? AND inbound_id = ?", (email, inbound_id))
#         inbound = cur.execute("SELECT settings FROM inbounds WHERE id = ?", (inbound_id,)).fetchone()
#         if inbound:
#             settings = json.loads(inbound["settings"])
#             initial_count = len(settings.get("clients", []))
#             settings["clients"] = [c for c in settings.get("clients", []) if c.get("email") != email]
#             if len(settings["clients"]) < initial_count:
#                 cur.execute("UPDATE inbounds SET settings = ? WHERE id = ?", (json.dumps(settings, separators=(",", ":")), inbound_id))
#         conn.commit()
#         logger.info(f"Successfully deleted client '{email}' from {db_path.name}.")
#         return True
#     except (sqlite3.Error, json.JSONDecodeError) as e:
#         conn.rollback()
#         logger.error(f"Error deleting client '{email}' from {db_path.name}: {e}")
#         return False
#     finally:
#         conn.close()

# def edit_client(db_path: Path, inbound_id: int, email: str, new_total_gb: float, new_duration_days: int) -> bool:
#     """Edits an existing client's quota and expiry date."""
#     db_conn = _get_db_connection(db_path)
#     if not db_conn: return False
#     conn, cur = db_conn
#     now_ms = int(time.time() * 1000)
#     new_expiry_ms = now_ms + (new_duration_days * DAYS_TO_MS)
#     new_total_bytes = int(new_total_gb * GB_TO_BYTES)
#     try:
#         # 1. Update client_traffics table
#         cur.execute("UPDATE client_traffics SET total = ?, expiry_time = ? WHERE email = ? AND inbound_id = ?", (new_total_bytes, new_expiry_ms, email, inbound_id))
#         if cur.rowcount == 0:
#             logger.warning(f"No traffic row found for '{email}' in {db_path.name}. Skipping update.")
#             return False
            
#         # 2. Update the settings JSON blob in inbounds table
#         inbound = cur.execute("SELECT settings FROM inbounds WHERE id = ?", (inbound_id,)).fetchone()
#         if inbound:
#             settings = json.loads(inbound["settings"])
#             client_found = False
#             for client in settings.get("clients", []):
#                 if client.get("email") == email:
#                     client["totalGB"] = new_total_bytes
#                     client["expiryTime"] = new_expiry_ms
#                     client_found = True
#                     break
#             if client_found:
#                 cur.execute("UPDATE inbounds SET settings = ? WHERE id = ?", (json.dumps(settings, separators=(",", ":")), inbound_id))
#             else:
#                 logger.warning(f"Client '{email}' not found in inbound settings JSON in {db_path.name}")
        
#         conn.commit()
#         logger.info(f"Successfully edited client '{email}' in {db_path.name}.")
#         return True
#     except (sqlite3.Error, json.JSONDecodeError) as e:
#         conn.rollback()
#         logger.error(f"Error editing client '{email}' in {db_path.name}: {e}")
#         return False
#     finally:
#         conn.close()

# def get_config(db_path: Path, inbound_id: int, email: str) -> Optional[str]:
#     """Generates the VLESS configuration link for a client."""
#     db_conn = _get_db_connection(db_path)
#     if not db_conn: return None
#     conn, cur = db_conn
#     try:
#         inbound = cur.execute("SELECT * FROM inbounds WHERE id = ?", (inbound_id,)).fetchone()
#         if not inbound: return None
#         settings = json.loads(inbound["settings"])
#         stream_settings = json.loads(inbound["stream_settings"])
        
#         external_proxy = stream_settings.get("externalProxy")[0]
#         server_address, port = external_proxy.get("dest"), external_proxy.get("port")
        
#         client_data = next((c for c in settings.get("clients", []) if c.get("email") == email), None)
#         if not client_data: return None
#         client_uuid = client_data.get("id")
        
#         remark = inbound["remark"]
#         reality_settings = stream_settings.get("realitySettings", {})
        
#         params = {
#             "type": stream_settings.get("network"),
#             "encryption": "none",
#         }
        
#         # --- NEW LOGIC: Add TCP path and headerType if they exist ---
#         tcp_settings = stream_settings.get("tcpSettings", {})
#         header = tcp_settings.get("header", {})
#         if header.get("type") == "http":
#             request = header.get("request", {})
#             path_list = request.get("path", [])
#             if path_list and path_list[0]:
#                 params["path"] = path_list[0]
#                 params["headerType"] = "http"

#         params.update({
#             "security": stream_settings.get("security"),
#             "pbk": reality_settings.get("settings", {}).get("publicKey"),
#             "fp": reality_settings.get("settings", {}).get("fingerprint"),
#             "sni": reality_settings.get("serverNames", [""])[0],
#             "sid": reality_settings.get("shortIds", [""])[0],
#             "spx": reality_settings.get("settings", {}).get("spiderX")
#         })

#         params = {k: v for k, v in params.items() if v}
#         base_url = f"vless://{client_uuid}@{server_address}:{port}"
#         query_string = urlencode(params)
#         fragment = quote(remark)
#         return f"{base_url}?{query_string}#{fragment}"
        
#     except (sqlite3.Error, json.JSONDecodeError, IndexError, TypeError) as e:
#         logger.error(f"Error generating config for '{email}' in {db_path.name}: {e}")
#         return None
#     finally:
#         conn.close()

# def get_status(db_path: Path, inbound_id: int, email: str) -> Optional[List[Any]]:
#     """Retrieves the traffic status and expiry time for a client."""
#     db_conn = _get_db_connection(db_path)
#     if not db_conn: return None
#     conn, cur = db_conn
#     try:
#         row = cur.execute("SELECT down, total, expiry_time FROM client_traffics WHERE email = ? AND inbound_id = ?", (email, inbound_id)).fetchone()
#         return [row["down"], row["total"], row["expiry_time"]] if row else None
#     except sqlite3.Error as e:
#         logger.error(f"Error fetching status for '{email}' in {db_path.name}: {e}")
#         return None
#     finally:
#         conn.close()


# # ==============================================================================
# # BOT HELPER FUNCTIONS
# # ==============================================================================

# def load_yaml(file_path: Path) -> dict:
#     """Loads any YAML file safely."""
#     if not file_path.exists():
#         return {}
#     with open(file_path, "r") as f:
#         data = yaml.safe_load(f)
#         return data if data is not None else {}

# def save_yaml(data: dict, file_path: Path) -> None:
#     """Saves data to a YAML file."""
#     with open(file_path, "w") as f:
#         yaml.dump(data, f, indent=2)

# def generate_subscription_id(length: int = 16) -> str:
#     """Generates a random string of lowercase letters and numbers."""
#     chars = string.ascii_lowercase + string.digits
#     return "".join(random.choice(chars) for _ in range(length))

# def get_user_language(update: Update, config: dict) -> str:
#     """Gets user's language, falling back to English if not supported."""
#     lang_code = update.effective_user.language_code
#     return lang_code if lang_code in config.get("welcome", {}) else "en"

# def get_localized_message(key: str, lang: str, config: dict) -> str:
#     """Fetches a message from the config in the correct language."""
#     return config.get(key, {}).get(lang, config.get(key, {}).get("en", "Message not found."))

# def format_time_left(expiry_timestamp_ms: int) -> str:
#     """Formats the time left until a future timestamp into 'Xd Yh Zm'."""
#     now = datetime.now()
#     expires_at = datetime.fromtimestamp(expiry_timestamp_ms / 1000)
#     if expires_at < now: return "Expired"
#     delta = expires_at - now
#     days = delta.days
#     hours, remainder = divmod(delta.seconds, 3600)
#     minutes, _ = divmod(remainder, 60)
#     return f"{days}d {hours}h {minutes}m"

# def create_subscription_file(telegram_id: str, subscription_id: str, config: dict) -> bool:
#     """Gathers all VLESS links, base64-encodes them, and saves to a file."""
#     all_vless_links = []
#     for db_path_str, inbound_id in config['db'].items():
#         link = get_config(Path(db_path_str), inbound_id, telegram_id)
#         if link:
#             all_vless_links.append(link)
#     if not all_vless_links:
#         logger.error(f"No VLESS links generated for user {telegram_id}")
#         return False
#     encoded_content = base64.b64encode("\n".join(all_vless_links).encode('utf-8')).decode('utf-8')
#     sub_dir = Path(config['subscription']['uri'])
#     sub_dir.mkdir(exist_ok=True)
#     (sub_dir / subscription_id).write_text(encoded_content)
#     logger.info(f"Created subscription file '{subscription_id}' for user {telegram_id}")
#     return True

# # ==============================================================================
# # REGISTRATION LOGIC
# # ==============================================================================

# async def register_new_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     """Handles the full registration process for a brand new user."""
#     user = update.effective_user
#     config = load_yaml(CONFIG_FILE).get("settings", {})
#     users_db = load_yaml(USER_DB_FILE)
    
#     logger.info(f"Registering new user: {user.id} ({user.full_name})")
#     defaults = {k.strip(): v.strip() for k, v in (item.split('=') for item in config['default'])}
    
#     telegram_id = str(user.id)
#     subscription_id = generate_subscription_id()
    
#     logger.info(f"Wiping old DB entries for user {telegram_id}...")
#     for db_path_str, inbound_id in config['db'].items():
#         delete_client(Path(db_path_str), inbound_id, telegram_id)

#     for db_path_str, inbound_id in config['db'].items():
#         if not new_client(Path(db_path_str), inbound_id, telegram_id, float(defaults['total_gb']), int(defaults['duration_days']), int(defaults['reset_days'])):
#             await update.message.reply_text("An error occurred during setup. Please contact support.")
#             return

#     if not create_subscription_file(telegram_id, subscription_id, config):
#         await update.message.reply_text("Error creating subscription file. Please contact support.")
#         return

#     users_db[telegram_id] = {"name": user.full_name, "language": get_user_language(update, config), "subscription": subscription_id}
#     save_yaml(users_db, USER_DB_FILE)
    
#     lang = users_db[telegram_id]['language']
#     # --- FIX: Changed URL construction ---
#     sub_url = f"{config['subscription']['url']}/{subscription_id}"
    
#     welcome_msg = get_localized_message("welcome", lang, config).format(
#         quota=f"{defaults['total_gb']} GB", 
#         reset=defaults['reset_days'], 
#         sub_url=f"`{sub_url}`"
#     )
#     await update.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD_MARKUP)

# # ==============================================================================
# # BOT KEYBOARD & COMMAND HANDLERS
# # ==============================================================================

# KEYBOARD_MARKUP = ReplyKeyboardMarkup([["/status"], ["/help", "/contact"]], resize_keyboard=True)

# async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     """Handles the /start command."""
#     user_id = str(update.effective_user.id)
#     if user_id not in load_yaml(USER_DB_FILE):
#         await register_new_user(update, context)
#     else:
#         logger.info(f"Existing user {user_id} used /start.")
#         await status_command(update, context)

# async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     """Handles the /status command."""
#     user_id = str(update.effective_user.id)
#     users_db = load_yaml(USER_DB_FILE)
#     if user_id not in users_db:
#         await register_new_user(update, context)
#         return

#     config = load_yaml(CONFIG_FILE).get("settings", {})
#     total_down_bytes, client_info = 0, None
#     for db_path_str, inbound_id in config['db'].items():
#         status_row = get_status(Path(db_path_str), inbound_id, user_id)
#         if status_row:
#             total_down_bytes += status_row[0]
#             if not client_info:
#                 client_info = {'total': status_row[1], 'expiry': status_row[2]}

#     if not client_info:
#         await update.message.reply_text("Could not retrieve your status. Please contact support.", reply_markup=KEYBOARD_MARKUP)
#         return

#     lang = users_db[user_id]['language']
#     if datetime.now().timestamp() * 1000 > client_info['expiry']:
#         await update.message.reply_text(get_localized_message("trial_end", lang, config), reply_markup=KEYBOARD_MARKUP)
#         return

#     defaults = {k.strip(): v.strip() for k, v in (item.split('=') for item in config['default'])}
#     subscription_id = users_db[user_id]['subscription']
#     # --- FIX: Changed URL construction ---
#     sub_url = f"{config['subscription']['url']}/{subscription_id}"
    
#     used_gb = total_down_bytes / GB_TO_BYTES
#     total_gb = client_info['total'] / GB_TO_BYTES

#     if used_gb > total_gb:
#         message_text = get_localized_message("quota_exceeded", lang, config).format(
#             total_gb=f"{total_gb:.2f}",
#             reset=defaults['reset_days'],
#             expiration_date=format_time_left(client_info['expiry'])
#         )
#     else:
#         message_text = get_localized_message("status", lang, config).format(
#             sub_url=f"`{sub_url}`",
#             used_gb=f"{used_gb:.2f}",
#             total_gb=f"{total_gb:.2f}",
#             reset=defaults['reset_days'],
#             expiration_date=format_time_left(client_info['expiry'])
#         )
#     await update.message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD_MARKUP)

# async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     """Handles the /help command."""
#     config = load_yaml(CONFIG_FILE).get("settings", {})
#     lang = get_user_language(update, config)
#     await update.message.reply_text(
#         get_localized_message("help", lang, config),
#         parse_mode=ParseMode.MARKDOWN,
#         reply_markup=KEYBOARD_MARKUP
#     )

# async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     """Handles the /contact command."""
#     config = load_yaml(CONFIG_FILE).get("settings", {})
#     lang = get_user_language(update, config)
#     await update.message.reply_text(
#         get_localized_message("contact", lang, config),
#         reply_markup=KEYBOARD_MARKUP
#     )

# async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     """Handles non-command text messages."""
#     user_id = str(update.effective_user.id)
#     if user_id not in load_yaml(USER_DB_FILE):
#         await register_new_user(update, context)
#     else:
#         try:
#             await context.bot.delete_message(chat_id=update.message.chat_id, message_id=update.message.message_id)
#         except Exception as e:
#             logger.warning(f"Could not delete message: {e}")

# # ==============================================================================
# # ADMIN /edit COMMAND HANDLERS
# # ==============================================================================

# def build_user_keyboard(users: dict) -> InlineKeyboardMarkup:
#     """Creates an inline keyboard with all users, sorted by name."""
#     buttons = []
#     # Sort users by name, case-insensitively
#     sorted_users = sorted(users.items(), key=lambda item: item[1]['name'].lower())
    
#     for user_id, user_data in sorted_users:
#         buttons.append(InlineKeyboardButton(user_data['name'], callback_data=user_id))
    
#     # Arrange buttons in 4 columns
#     keyboard = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
#     keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
#     return InlineKeyboardMarkup(keyboard)

# async def edit_command_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
#     """Starts the /edit conversation, only for the admin."""
#     user_id = str(update.effective_user.id)
#     if user_id != ADMIN_ID:
#         logger.warning(f"Unauthorized /edit attempt by user {user_id}")
#         return ConversationHandler.END

#     users_db = load_yaml(USER_DB_FILE)
#     if not users_db:
#         await update.message.reply_text("No users found in the database.")
#         return ConversationHandler.END

#     keyboard = build_user_keyboard(users_db)
#     await update.message.reply_text("Please select a user to edit:", reply_markup=keyboard)
#     return SELECT_USER

# async def select_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
#     """Handles the admin selecting a user to edit."""
#     query = update.callback_query
#     await query.answer()
    
#     user_id_to_edit = query.data
#     context.user_data['user_to_edit'] = user_id_to_edit
    
#     users_db = load_yaml(USER_DB_FILE)
#     user_info = users_db.get(user_id_to_edit)
    
#     # Get current status to display to admin
#     config = load_yaml(CONFIG_FILE).get("settings", {})
#     total_down_bytes, client_info = 0, None
#     for db_path_str, inbound_id in config['db'].items():
#         status_row = get_status(Path(db_path_str), inbound_id, user_id_to_edit)
#         if status_row:
#             total_down_bytes += status_row[0]
#             if not client_info: client_info = {'total': status_row[1], 'expiry': status_row[2]}
    
#     used_gb = total_down_bytes / GB_TO_BYTES
#     total_gb = client_info['total'] / GB_TO_BYTES if client_info else 0
#     expiry_date = format_time_left(client_info['expiry']) if client_info else "N/A"

#     details_text = (
#         f"Editing User: *{user_info['name']}*\n"
#         f"Language: `{user_info['language']}`\n"
#         f"Usage: `{used_gb:.2f} GB / {total_gb:.2f} GB`\n"
#         f"Expires in: `{expiry_date}`\n\n"
#         "Set the new expiration date (in days from now):"
#     )
    
#     keyboard = InlineKeyboardMarkup([
#         [InlineKeyboardButton("30 days", callback_data="30"), InlineKeyboardButton("60 days", callback_data="60")],
#         [InlineKeyboardButton("90 days", callback_data="90"), InlineKeyboardButton("120 days", callback_data="120")],
#         [InlineKeyboardButton("180 days", callback_data="180"), InlineKeyboardButton("365 days", callback_data="365")],
#         [InlineKeyboardButton("Cancel", callback_data="cancel")]
#     ])
    
#     await query.edit_message_text(text=details_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
#     return SELECT_DURATION

# async def select_duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
#     """Handles the admin selecting a new duration."""
#     query = update.callback_query
#     await query.answer()
    
#     context.user_data['new_duration'] = int(query.data)
    
#     keyboard = InlineKeyboardMarkup([
#         [InlineKeyboardButton("10 GB", callback_data="10"), InlineKeyboardButton("20 GB", callback_data="20")],
#         [InlineKeyboardButton("50 GB", callback_data="50"), InlineKeyboardButton("100 GB", callback_data="100")],
#         [InlineKeyboardButton("200 GB", callback_data="200"), InlineKeyboardButton("500 GB", callback_data="500")],
#         [InlineKeyboardButton("Cancel", callback_data="cancel")]
#     ])
    
#     await query.edit_message_text(text="Select a new quota (GB):", reply_markup=keyboard)
#     return SELECT_QUOTA

# async def select_quota_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
#     """Handles the final step: selecting quota and applying changes."""
#     query = update.callback_query
#     await query.answer()
    
#     user_id_to_edit = context.user_data['user_to_edit']
#     new_duration = context.user_data['new_duration']
#     new_quota = float(query.data)

#     config = load_yaml(CONFIG_FILE).get("settings", {})
#     success_count = 0
#     for db_path_str, inbound_id in config['db'].items():
#         if edit_client(Path(db_path_str), inbound_id, user_id_to_edit, new_quota, new_duration):
#             success_count += 1
    
#     if success_count > 0:
#         users_db = load_yaml(USER_DB_FILE)
#         user_name = users_db.get(user_id_to_edit, {}).get("name", "Unknown")
#         final_message = f"Successfully updated user *{user_name}*!\nNew Quota: `{new_quota} GB`\nNew Duration: `{new_duration} days`"
#     else:
#         final_message = "Failed to update user in any database. Please check the logs."

#     await query.edit_message_text(text=final_message, parse_mode=ParseMode.MARKDOWN)
    
#     # Clean up user data
#     context.user_data.clear()
#     return ConversationHandler.END

# async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
#     """Cancels the conversation."""
#     query = update.callback_query
#     await query.answer()
#     await query.edit_message_text(text="Operation cancelled.")
#     context.user_data.clear()
#     return ConversationHandler.END


# # ==============================================================================
# # MAIN FUNCTION
# # ==============================================================================

# async def post_init(application: Application) -> None:
#     """Clears any old command menus after the bot initializes."""
#     await application.bot.delete_my_commands()
#     logger.info("Cleared old command menu.")

# def main() -> None:
#     """Start the bot."""
#     if not BOT_TOKEN or not ADMIN_ID:
#         logger.critical("FATAL: BOT_TOKEN or ADMIN environment variable is not set. Exiting.")
#         return

#     application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
#     # Conversation handler for the /edit command
#     conv_handler = ConversationHandler(
#         entry_points=[CommandHandler("edit", edit_command_start)],
#         states={
#             SELECT_USER: [CallbackQueryHandler(select_user_callback, pattern="^[^cancel].*$")],
#             SELECT_DURATION: [CallbackQueryHandler(select_duration_callback, pattern="^[^cancel].*$")],
#             SELECT_QUOTA: [CallbackQueryHandler(select_quota_callback, pattern="^[^cancel].*$")],
#         },
#         fallbacks=[CallbackQueryHandler(cancel_callback, pattern="^cancel$")],
#     )

#     application.add_handler(conv_handler)
#     application.add_handler(CommandHandler("start", start_command))
#     application.add_handler(CommandHandler("status", status_command))
#     application.add_handler(CommandHandler("help", help_command))
#     application.add_handler(CommandHandler("contact", contact_command))
#     application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    
#     logger.info("Bot is starting...")
#     application.run_polling()

# if __name__ == "__main__":
#     main()


import logging
import random
import string
import yaml
import base64
import os
import sqlite3
import uuid
import time
import json
import requests
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote
from typing import Optional, List, Any

# Import from python-telegram-bot library
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler, CallbackQueryHandler
)
from telegram.constants import ParseMode

# --- Configuration & Constants ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN")
CONFIG_FILE = Path("database.yaml")
USER_DB_FILE = Path("users.yaml")

# Conversation states for /edit
SELECT_USER, SELECT_DURATION, SELECT_QUOTA = range(3)
GB_TO_BYTES = 1024**3
DAYS_TO_MS = 24 * 60 * 60 * 1000

# --- Logging Setup ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================================================================
# X-UI API CLASS
# ==============================================================================
class XUI_API:
    """A class to interact with the 3X-UI panel API."""
    def __init__(self, address: str, port: int, panel_path: str, username: str, password: str):
        self.base_url = f"http://{address}:{port}{panel_path}"
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({'Accept': 'application/json'})

    def _login(self) -> bool:
        """Logs into the panel and stores the session cookie."""
        try:
            response = self.session.post(f"{self.base_url}login", data={'username': self.username, 'password': self.password})
            response.raise_for_status()
            if response.json().get("success"):
                logger.info(f"Successfully logged into panel at {self.base_url}")
                return True
            logger.error(f"Panel login failed at {self.base_url}: {response.json().get('msg')}")
            return False
        except requests.RequestException as e:
            logger.error(f"API login request failed for {self.base_url}: {e}")
            return False

    def _api_request(self, method, endpoint, **kwargs):
        """Wrapper for making API requests, handles re-login."""
        url = f"{self.base_url}panel/api/{endpoint}"
        try:
            response = self.session.request(method, url, **kwargs)
            if response.status_code == 401: # Unauthorized, session likely expired
                logger.warning("Session expired. Attempting to re-login...")
                if self._login():
                    response = self.session.request(method, url, **kwargs) # Retry the request
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"API request to {url} failed: {e}")
            return None

    def get_inbound(self, inbound_id: int) -> Optional[dict]:
        """Fetches the full data for a single inbound."""
        return self._api_request("GET", f"inbounds/get/{inbound_id}")

    def update_inbound(self, inbound_id: int, data: dict) -> bool:
        """Updates an entire inbound with new data."""
        response = self._api_request("POST", f"inbounds/update/{inbound_id}", json=data)
        return response and response.get("success")

    def add_client(self, inbound_id: int, new_client_obj: dict) -> bool:
        """Adds a new client to an inbound by updating the inbound."""
        inbound_data = self.get_inbound(inbound_id)
        if not inbound_data or not inbound_data.get("success"):
            logger.error(f"Could not fetch inbound {inbound_id} to add client.")
            return False
        
        inbound_obj = inbound_data["obj"]
        settings = json.loads(inbound_obj["settings"])
        settings.setdefault("clients", []).append(new_client_obj)
        inbound_obj["settings"] = json.dumps(settings)

        return self.update_inbound(inbound_id, inbound_obj)

    def delete_client(self, inbound_id: int, email: str) -> bool:
        """Deletes a client by removing them from the inbound's client list and updating."""
        inbound_data = self.get_inbound(inbound_id)
        if not inbound_data or not inbound_data.get("success"):
            return False

        inbound_obj = inbound_data["obj"]
        settings = json.loads(inbound_obj["settings"])
        clients = settings.get("clients", [])
        
        client_found = any(c.get("email") == email for c in clients)
        if not client_found:
            return True # Client doesn't exist, so deletion is "successful"

        settings["clients"] = [c for c in clients if c.get("email") != email]
        inbound_obj["settings"] = json.dumps(settings)

        return self.update_inbound(inbound_id, inbound_obj)

    def edit_client(self, inbound_id: int, email: str, new_total_gb: float, new_duration_days: int) -> bool:
        """Edits a client's quota and expiry by updating the inbound."""
        inbound_data = self.get_inbound(inbound_id)
        if not inbound_data or not inbound_data.get("success"):
            return False

        inbound_obj = inbound_data["obj"]
        settings = json.loads(inbound_obj["settings"])
        
        client_found = False
        for client in settings.get("clients", []):
            if client.get("email") == email:
                client["totalGB"] = int(new_total_gb * GB_TO_BYTES)
                client["expiryTime"] = int(time.time() * 1000) + (new_duration_days * DAYS_TO_MS)
                client_found = True
                break
        
        if not client_found:
            logger.warning(f"Client {email} not found in inbound {inbound_id} during edit.")
            return False

        inbound_obj["settings"] = json.dumps(settings)
        return self.update_inbound(inbound_id, inbound_obj)

# ==============================================================================
# API-BASED DATABASE WRAPPERS
# ==============================================================================

def new_client(server_config: dict, email: str, total_gb: float, duration_days: int, telegram_id: int) -> bool:
    """Creates a new client using the API."""
    api = XUI_API(
        address=server_config['address'], port=server_config['port'], panel_path=server_config['panel_path'],
        username=load_yaml(CONFIG_FILE)['settings']['subscription']['user'],
        password=load_yaml(CONFIG_FILE)['settings']['subscription']['password']
    )
    if not api._login(): return False
    
    now_ms = int(time.time() * 1000)
    new_client_obj = {
        "id": str(uuid.uuid4()), "email": email, "enable": True, "tgId": telegram_id,
        "totalGB": int(total_gb * GB_TO_BYTES),
        "expiryTime": now_ms + (duration_days * DAYS_TO_MS),
        "flow": "", "limitIp": 0, "reset": 0, "subId": str(uuid.uuid4().hex)[:16]
    }
    return api.add_client(server_config['inbound'], new_client_obj)

def delete_client(server_config: dict, email: str) -> bool:
    """Deletes a client using the API."""
    api = XUI_API(
        address=server_config['address'], port=server_config['port'], panel_path=server_config['panel_path'],
        username=load_yaml(CONFIG_FILE)['settings']['subscription']['user'],
        password=load_yaml(CONFIG_FILE)['settings']['subscription']['password']
    )
    if not api._login(): return False
    return api.delete_client(server_config['inbound'], email)

def edit_client(server_config: dict, email: str, new_total_gb: float, new_duration_days: int) -> bool:
    """Edits a client using the API."""
    api = XUI_API(
        address=server_config['address'], port=server_config['port'], panel_path=server_config['panel_path'],
        username=load_yaml(CONFIG_FILE)['settings']['subscription']['user'],
        password=load_yaml(CONFIG_FILE)['settings']['subscription']['password']
    )
    if not api._login(): return False
    return api.edit_client(server_config['inbound'], email, new_total_gb, new_duration_days)

# ==============================================================================
# DIRECT DB READ-ONLY FUNCTIONS (UNCHANGED)
# ==============================================================================
def get_config(db_path: Path, inbound_id: int, email: str) -> Optional[str]:
    # This function remains as it was, fast and efficient for reading.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        inbound = cur.execute("SELECT * FROM inbounds WHERE id = ?", (inbound_id,)).fetchone()
        if not inbound: return None
        settings = json.loads(inbound["settings"])
        stream_settings = json.loads(inbound["stream_settings"])
        external_proxy = stream_settings.get("externalProxy")[0]
        server_address, port = external_proxy.get("dest"), external_proxy.get("port")
        client_data = next((c for c in settings.get("clients", []) if c.get("email") == email), None)
        if not client_data: return None
        client_uuid = client_data.get("id")
        remark = inbound["remark"]
        reality_settings = stream_settings.get("realitySettings", {})
        params = {"type": stream_settings.get("network"), "encryption": "none"}
        if stream_settings.get("tcpSettings", {}).get("header", {}).get("type") == "http":
            path_list = stream_settings.get("tcpSettings", {}).get("header", {}).get("request", {}).get("path", [])
            if path_list and path_list[0]:
                params["path"] = path_list[0]
                params["headerType"] = "http"
        params.update({"security": stream_settings.get("security"), "pbk": reality_settings.get("settings", {}).get("publicKey"), "fp": reality_settings.get("settings", {}).get("fingerprint"), "sni": reality_settings.get("serverNames", [""])[0], "sid": reality_settings.get("shortIds", [""])[0], "spx": reality_settings.get("settings", {}).get("spiderX")})
        params = {k: v for k, v in params.items() if v}
        base_url = f"vless://{client_uuid}@{server_address}:{port}"
        query_string = urlencode(params)
        fragment = quote(remark)
        return f"{base_url}?{query_string}#{fragment}"
    except Exception as e:
        logger.error(f"Error in direct get_config for {db_path.name}: {e}")
        return None
    finally:
        conn.close()

def get_status(db_path: Path, inbound_id: int, email: str) -> Optional[List[Any]]:
    # This function remains for fast reading.
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        row = cur.execute("SELECT down, total, expiry_time FROM client_traffics WHERE email = ? AND inbound_id = ?", (email, inbound_id)).fetchone()
        return [row["down"], row["total"], row["expiry_time"]] if row else None
    except Exception as e:
        logger.error(f"Error in direct get_status for {db_path.name}: {e}")
        return None
    finally:
        conn.close()

# ==============================================================================
# BOT HELPER FUNCTIONS & HANDLERS (LOGIC UPDATED TO USE WRAPPERS)
# ==============================================================================
# load_yaml, save_yaml, generate_subscription_id, etc. are unchanged...
# ... all other helper functions are unchanged ...
# ... I will only show the functions where the logic was updated ...

def load_yaml(file_path: Path) -> dict: return yaml.safe_load(file_path.read_text()) if file_path.exists() else {}
def save_yaml(data: dict, file_path: Path): file_path.write_text(yaml.dump(data, indent=2))
def generate_subscription_id(length: int = 16) -> str: return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))
def get_user_language(update: Update, config: dict) -> str: return update.effective_user.language_code if update.effective_user.language_code in config.get("welcome", {}) else "en"
def get_localized_message(key: str, lang: str, config: dict) -> str: return config.get(key, {}).get(lang, config.get(key, {}).get("en", "Message not found."))
def format_time_left(expiry_timestamp_ms: int) -> str:
    if expiry_timestamp_ms == 0: return "Unlimited"
    delta = datetime.fromtimestamp(expiry_timestamp_ms / 1000) - datetime.now()
    if delta.total_seconds() < 0: return "Expired"
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{days}d {hours}h {minutes}m"

def create_subscription_file(telegram_id: str, subscription_id: str, config: dict) -> bool:
    all_vless_links = []
    for server_key, server_config in config['db'].items():
        link = get_config(Path(server_config['path']), server_config['inbound'], telegram_id)
        if link:
            all_vless_links.append(link)
    if not all_vless_links:
        logger.error(f"No VLESS links generated for user {telegram_id}")
        return False
    encoded_content = base64.b64encode("\n".join(all_vless_links).encode('utf-8')).decode('utf-8')
    # The subscription URI is no longer in the config, it's just the root
    Path(subscription_id).write_text(encoded_content)
    logger.info(f"Created subscription file '{subscription_id}' for user {telegram_id}")
    return True

async def register_new_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    config = load_yaml(CONFIG_FILE).get("settings", {})
    users_db = load_yaml(USER_DB_FILE)
    logger.info(f"Registering new user: {user.id} ({user.full_name})")
    defaults = {k.strip(): v.strip() for k, v in (item.split('=') for item in config['default'])}
    telegram_id = str(user.id)
    subscription_id = generate_subscription_id()
    
    logger.info(f"Wiping old DB entries for user {telegram_id} via API...")
    for server_key, server_config in config['db'].items():
        delete_client(server_config, telegram_id)

    logger.info(f"Creating new client for user {telegram_id} via API...")
    for server_key, server_config in config['db'].items():
        if not new_client(server_config, telegram_id, float(defaults['total_gb']), int(defaults['duration_days']), user.id):
            await update.message.reply_text(f"An error occurred during setup on server {server_key}. Please contact support.")
            return

    if not create_subscription_file(telegram_id, subscription_id, config):
        await update.message.reply_text("Error creating subscription file. Please contact support.")
        return

    users_db[telegram_id] = {"name": user.full_name, "language": get_user_language(update, config), "subscription": subscription_id}
    save_yaml(users_db, USER_DB_FILE)
    lang = users_db[telegram_id]['language']
    sub_url = f"{config['subscription']['url']}/{subscription_id}"
    welcome_msg = get_localized_message("welcome", lang, config).format(
        quota=f"{defaults['total_gb']} GB", reset=defaults.get('reset_days', 0), sub_url=f"`{sub_url}`")
    await update.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD_MARKUP)

KEYBOARD_MARKUP = ReplyKeyboardMarkup([["/status"], ["/help", "/contact"]], resize_keyboard=True)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if str(update.effective_user.id) not in load_yaml(USER_DB_FILE):
        await register_new_user(update, context)
    else:
        await status_command(update, context)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    users_db = load_yaml(USER_DB_FILE)
    if user_id not in users_db:
        await register_new_user(update, context)
        return

    config = load_yaml(CONFIG_FILE).get("settings", {})
    total_down_bytes, client_info = 0, None
    for server_key, server_config in config['db'].items():
        status_row = get_status(Path(server_config['path']), server_config['inbound'], user_id)
        if status_row:
            total_down_bytes += status_row[0]
            if not client_info: client_info = {'total': status_row[1], 'expiry': status_row[2]}

    if not client_info:
        await update.message.reply_text("Could not retrieve your status. Please contact support.", reply_markup=KEYBOARD_MARKUP)
        return

    lang = users_db[user_id]['language']
    if client_info['expiry'] != 0 and datetime.now().timestamp() * 1000 > client_info['expiry']:
        await update.message.reply_text(get_localized_message("trial_end", lang, config), reply_markup=KEYBOARD_MARKUP)
        return

    defaults = {k.strip(): v.strip() for k, v in (item.split('=') for item in config['default'])}
    subscription_id = users_db[user_id]['subscription']
    sub_url = f"{config['subscription']['url']}/{subscription_id}"
    used_gb = total_down_bytes / GB_TO_BYTES
    total_gb = client_info['total'] / GB_TO_BYTES

    if total_gb > 0 and used_gb > total_gb:
        message_text = get_localized_message("quota_exceeded", lang, config).format(total_gb=f"{total_gb:.2f}", reset=defaults.get('reset_days', 0), expiration_date=format_time_left(client_info['expiry']))
    else:
        total_gb_str = "Unlimited" if total_gb == 0 else f"{total_gb:.2f}"
        message_text = get_localized_message("status", lang, config).format(sub_url=f"`{sub_url}`", used_gb=f"{used_gb:.2f}", total_gb=total_gb_str, reset=defaults.get('reset_days', 0), expiration_date=format_time_left(client_info['expiry']))
        
    await update.message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD_MARKUP)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: await update.message.reply_text(get_localized_message("help", update.effective_user.language_code, load_yaml(CONFIG_FILE).get("settings", {})), parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD_MARKUP)
async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: await update.message.reply_text(get_localized_message("contact", update.effective_user.language_code, load_yaml(CONFIG_FILE).get("settings", {})), reply_markup=KEYBOARD_MARKUP)
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if str(update.effective_user.id) not in load_yaml(USER_DB_FILE):
        await register_new_user(update, context)
    else:
        try: await context.bot.delete_message(chat_id=update.message.chat_id, message_id=update.message.message_id)
        except Exception as e: logger.warning(f"Could not delete message: {e}")

def build_user_keyboard(users: dict) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(user_id, callback_data=user_id) for user_id, user_data in sorted(users.items(), key=lambda item: item[1]['name'].lower())]
    keyboard = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

async def edit_command_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if str(update.effective_user.id) != ADMIN_ID: return ConversationHandler.END
    users_db = load_yaml(USER_DB_FILE)
    if not users_db:
        await update.message.reply_text("No users found.")
        return ConversationHandler.END
    await update.message.reply_text("Select a user to edit:", reply_markup=build_user_keyboard(users_db))
    return SELECT_USER

async def select_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id_to_edit = query.data
    context.user_data['user_to_edit'] = user_id_to_edit
    user_info = load_yaml(USER_DB_FILE).get(user_id_to_edit)
    config = load_yaml(CONFIG_FILE).get("settings", {})
    server_key = next(iter(config['db'])) # Get first server for status check
    server_config = config['db'][server_key]
    status_row = get_status(Path(server_config['path']), server_config['inbound'], user_id_to_edit)
    used_gb = sum(get_status(Path(s['path']), s['inbound'], user_id_to_edit)[0] for s in config['db'].values() if get_status(Path(s['path']), s['inbound'], user_id_to_edit)) / GB_TO_BYTES if status_row else 0
    total_gb = status_row[1] / GB_TO_BYTES if status_row and status_row[1] > 0 else "Unlimited"
    expiry_date = format_time_left(status_row[2]) if status_row else "N/A"
    details_text = (f"Editing: *{user_info['name']}* (`{user_id_to_edit}`)\n"
                    f"Usage: `{used_gb:.2f} GB / {total_gb} GB`\n"
                    f"Expires: `{expiry_date}`\n\nSet new duration:")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("30d", callback_data="30"), InlineKeyboardButton("60d", callback_data="60"), InlineKeyboardButton("90d", callback_data="90")], [InlineKeyboardButton("180d", callback_data="180"), InlineKeyboardButton("365d", callback_data="365"), InlineKeyboardButton("Unlimited", callback_data="0")], [InlineKeyboardButton("Cancel", callback_data="cancel")]])
    await query.edit_message_text(text=details_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    return SELECT_DURATION

async def select_duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['new_duration'] = int(query.data)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("10GB", callback_data="10"), InlineKeyboardButton("20GB", callback_data="20"), InlineKeyboardButton("50GB", callback_data="50")], [InlineKeyboardButton("100GB", callback_data="100"), InlineKeyboardButton("200GB", callback_data="200"), InlineKeyboardButton("500GB", callback_data="500")], [InlineKeyboardButton("Unlimited", callback_data="0")], [InlineKeyboardButton("Cancel", callback_data="cancel")]])
    await query.edit_message_text(text="Select new quota (GB):", reply_markup=keyboard)
    return SELECT_QUOTA

async def select_quota_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = context.user_data['user_to_edit']
    duration = context.user_data['new_duration'] if context.user_data['new_duration'] > 0 else 9999
    quota = float(query.data)
    config = load_yaml(CONFIG_FILE).get("settings", {})
    success_count = 0
    for server_config in config['db'].values():
        if edit_client(server_config, user_id, quota, duration):
            success_count += 1
    msg = f"Updated user *{user_id}*!\nQuota: `{quota if quota > 0 else 'Unlimited'} GB`\nDuration: `{duration if duration < 9999 else 'Unlimited'} days`" if success_count > 0 else "Failed to update user."
    await query.edit_message_text(text=msg, parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.edit_message_text("Operation cancelled.")
    return ConversationHandler.END

async def post_init(application: Application) -> None:
    await application.bot.delete_my_commands()
    logger.info("Cleared old command menu.")

def main() -> None:
    if not BOT_TOKEN or not ADMIN_ID:
        logger.critical("FATAL: BOT_TOKEN or ADMIN environment variable is not set.")
        return

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_command_start)],
        states={
            SELECT_USER: [CallbackQueryHandler(select_user_callback, pattern="^[^cancel].*$")],
            SELECT_DURATION: [CallbackQueryHandler(select_duration_callback, pattern="^[^cancel].*$")],
            SELECT_QUOTA: [CallbackQueryHandler(select_quota_callback, pattern="^[^cancel].*$")],
        },
        fallbacks=[CallbackQueryHandler(cancel_callback, pattern="^cancel$")],
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("contact", contact_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    
    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()