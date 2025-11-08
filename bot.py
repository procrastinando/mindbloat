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
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlencode, quote
from typing import Optional, List, Any

# Import from python-telegram-bot library
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

# --- Configuration & Constants ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CONFIG_FILE = Path("database.yaml")
USER_DB_FILE = Path("users.yaml")

# Constants
GB_TO_BYTES = 1024**3
DAYS_TO_MS = 24 * 60 * 60 * 1000

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# ==============================================================================
# DATABASE MANAGEMENT FUNCTIONS
# ==============================================================================

def _get_db_connection(db_path: Path) -> Optional[tuple[sqlite3.Connection, sqlite3.Cursor]]:
    """Establishes a connection to the SQLite database."""
    if not db_path.exists():
        logger.error(f"Database not found at '{db_path.resolve()}'")
        return None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        return conn, cur
    except sqlite3.Error as e:
        logger.error(f"Database connection error: {e}")
        return None

# --- THIS IS THE KEY FUNCTION THAT WAS CORRECTED ---
def new_client(db_path: Path, inbound_id: int, email: str, total_gb: float, duration_days: int, reset_days: int) -> bool:
    """Creates a new client in a specific inbound."""
    db_conn = _get_db_connection(db_path)
    if not db_conn: return False
    conn, cur = db_conn
    now_ms = int(time.time() * 1000)
    expiry_ms = now_ms + (duration_days * DAYS_TO_MS)
    total_bytes = int(total_gb * GB_TO_BYTES)
    try:
        inbound = cur.execute("SELECT settings FROM inbounds WHERE id = ?", (inbound_id,)).fetchone()
        if not inbound:
            logger.error(f"Inbound ID {inbound_id} not found in {db_path.name}.")
            return False
            
        settings = json.loads(inbound["settings"])
        
        # This new_client_obj now perfectly matches the 13-key structure required by the panel.
        new_client_obj = {
            "comment": "",
            "created_at": now_ms,
            "email": email,
            "enable": True,
            "expiryTime": expiry_ms,
            "flow": "",  # The critical missing key
            "id": str(uuid.uuid4()),
            "limitIp": 0,
            "reset": reset_days,
            "subId": str(uuid.uuid4().hex)[:16],
            "tgId": 0,
            "totalGB": total_bytes,
            "updated_at": now_ms
        }
        
        settings.setdefault("clients", []).append(new_client_obj)
        cur.execute("UPDATE inbounds SET settings = ? WHERE id = ?", (json.dumps(settings, separators=(",", ":")), inbound_id))
        cur.execute("INSERT INTO client_traffics (inbound_id, enable, email, up, down, all_time, expiry_time, total, reset, last_online) VALUES (?, 1, ?, 0, 0, 0, ?, ?, ?, ?)", (inbound_id, email, expiry_ms, total_bytes, reset_days, now_ms))
        conn.commit()
        logger.info(f"Successfully created client '{email}' in {db_path.name}.")
        return True
    except (sqlite3.Error, json.JSONDecodeError) as e:
        conn.rollback()
        logger.error(f"Error creating client '{email}' in {db_path.name}: {e}")
        return False
    finally:
        conn.close()

def delete_client(db_path: Path, inbound_id: int, email: str) -> bool:
    """Deletes a client completely from a database."""
    db_conn = _get_db_connection(db_path)
    if not db_conn: return False
    conn, cur = db_conn
    try:
        cur.execute("DELETE FROM client_traffics WHERE email = ? AND inbound_id = ?", (email, inbound_id))
        inbound = cur.execute("SELECT settings FROM inbounds WHERE id = ?", (inbound_id,)).fetchone()
        if inbound:
            settings = json.loads(inbound["settings"])
            initial_count = len(settings.get("clients", []))
            settings["clients"] = [c for c in settings.get("clients", []) if c.get("email") != email]
            if len(settings["clients"]) < initial_count:
                cur.execute("UPDATE inbounds SET settings = ? WHERE id = ?", (json.dumps(settings, separators=(",", ":")), inbound_id))
        conn.commit()
        logger.info(f"Successfully deleted client '{email}' from {db_path.name}.")
        return True
    except (sqlite3.Error, json.JSONDecodeError) as e:
        conn.rollback()
        logger.error(f"Error deleting client '{email}' from {db_path.name}: {e}")
        return False
    finally:
        conn.close()

def get_config(db_path: Path, inbound_id: int, email: str) -> Optional[str]:
    """Generates the VLESS configuration link for a client."""
    db_conn = _get_db_connection(db_path)
    if not db_conn: return None
    conn, cur = db_conn
    try:
        inbound = cur.execute("SELECT * FROM inbounds WHERE id = ?", (inbound_id,)).fetchone()
        if not inbound:
            logger.error(f"Inbound ID {inbound_id} not found in {db_path.name}.")
            return None
        
        settings = json.loads(inbound["settings"])
        stream_settings = json.loads(inbound["stream_settings"])
        
        external_proxy = stream_settings.get("externalProxy")
        if not external_proxy or not isinstance(external_proxy, list) or not external_proxy[0]:
            logger.error(f"externalProxy is missing or invalid in inbound {inbound_id} of {db_path.name}")
            return None
        
        server_address = external_proxy[0].get("dest")
        port = external_proxy[0].get("port")
        if not server_address or not port:
            logger.error(f"'dest' or 'port' is missing in externalProxy for inbound {inbound_id} of {db_path.name}")
            return None

        client_data = next((c for c in settings.get("clients", []) if c.get("email") == email), None)
        if not client_data:
            logger.error(f"Client '{email}' not found in settings JSON for inbound {inbound_id} of {db_path.name}.")
            return None
            
        client_uuid = client_data.get("id")
        
        remark = inbound["remark"]
        reality_settings = stream_settings.get("realitySettings", {})
        
        params = {
            "type": stream_settings.get("network"),
            "encryption": "none",
            "security": stream_settings.get("security"),
            "pbk": reality_settings.get("settings", {}).get("publicKey"),
            "fp": reality_settings.get("settings", {}).get("fingerprint"),
            "sni": reality_settings.get("serverNames", [""])[0],
            "sid": reality_settings.get("shortIds", [""])[0],
            "spx": reality_settings.get("settings", {}).get("spiderX")
        }
        params = {k: v for k, v in params.items() if v}
        
        base_url = f"vless://{client_uuid}@{server_address}:{port}"
        query_string = urlencode(params)
        fragment = quote(remark)
        return f"{base_url}?{query_string}#{fragment}"
        
    except (sqlite3.Error, json.JSONDecodeError, IndexError) as e:
        logger.error(f"Error generating config for '{email}' in {db_path.name}: {e}")
        return None
    finally:
        conn.close()

def get_status(db_path: Path, inbound_id: int, email: str) -> Optional[List[Any]]:
    """Retrieves the traffic status and expiry time for a client."""
    db_conn = _get_db_connection(db_path)
    if not db_conn: return None
    conn, cur = db_conn
    try:
        row = cur.execute("SELECT down, total, expiry_time FROM client_traffics WHERE email = ? AND inbound_id = ?", (email, inbound_id)).fetchone()
        return [row["down"], row["total"], row["expiry_time"]] if row else None
    except sqlite3.Error as e:
        logger.error(f"Error fetching status for '{email}' in {db_path.name}: {e}")
        return None
    finally:
        conn.close()


# ==============================================================================
# BOT HELPER FUNCTIONS
# ==============================================================================

def load_yaml(file_path: Path) -> dict:
    """Loads any YAML file safely."""
    if not file_path.exists():
        return {}
    with open(file_path, "r") as f:
        data = yaml.safe_load(f)
        return data if data is not None else {}

def save_yaml(data: dict, file_path: Path) -> None:
    """Saves data to a YAML file."""
    with open(file_path, "w") as f:
        yaml.dump(data, f, indent=2)

def generate_subscription_id(length: int = 16) -> str:
    """Generates a random string of lowercase letters and numbers."""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))

def get_user_language(update: Update, config: dict) -> str:
    """Gets user's language, falling back to English if not supported."""
    lang_code = update.effective_user.language_code
    return lang_code if lang_code in config.get("welcome", {}) else "en"

def get_localized_message(key: str, lang: str, config: dict) -> str:
    """Fetches a message from the config in the correct language."""
    return config.get(key, {}).get(lang, config.get(key, {}).get("en", "Message not found."))

def format_time_left(expiry_timestamp_ms: int) -> str:
    """Formats the time left until a future timestamp into 'Xd Yh Zm'."""
    now = datetime.now()
    expires_at = datetime.fromtimestamp(expiry_timestamp_ms / 1000)
    if expires_at < now: return "Expired"
    delta = expires_at - now
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{days}d {hours}h {minutes}m"

def create_subscription_file(telegram_id: str, subscription_id: str, config: dict) -> bool:
    """Gathers all VLESS links, base64-encodes them, and saves to a file."""
    all_vless_links = []
    for db_path_str, inbound_id in config['db'].items():
        link = get_config(Path(db_path_str), inbound_id, telegram_id)
        if link:
            all_vless_links.append(link)
    if not all_vless_links:
        logger.error(f"No VLESS links generated for user {telegram_id}")
        return False
    encoded_content = base64.b64encode("\n".join(all_vless_links).encode('utf-8')).decode('utf-8')
    sub_dir = Path(config['subscription']['uri'])
    sub_dir.mkdir(exist_ok=True)
    (sub_dir / subscription_id).write_text(encoded_content)
    logger.info(f"Created subscription file '{subscription_id}' for user {telegram_id}")
    return True

# ==============================================================================
# REGISTRATION LOGIC
# ==============================================================================

async def register_new_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the full registration process for a brand new user."""
    user = update.effective_user
    config = load_yaml(CONFIG_FILE).get("settings", {})
    users_db = load_yaml(USER_DB_FILE)
    
    logger.info(f"Registering new user: {user.id} ({user.full_name})")
    defaults = {k.strip(): v.strip() for k, v in (item.split('=') for item in config['default'])}
    
    telegram_id = str(user.id)
    subscription_id = generate_subscription_id()
    
    logger.info(f"Wiping old DB entries for user {telegram_id}...")
    for db_path_str, inbound_id in config['db'].items():
        delete_client(Path(db_path_str), inbound_id, telegram_id)

    for db_path_str, inbound_id in config['db'].items():
        if not new_client(Path(db_path_str), inbound_id, telegram_id, float(defaults['total_gb']), int(defaults['duration_days']), int(defaults['reset_days'])):
            await update.message.reply_text("An error occurred during setup. Please contact support.")
            return

    if not create_subscription_file(telegram_id, subscription_id, config):
        await update.message.reply_text("Error creating subscription file. Please contact support.")
        return

    users_db[telegram_id] = {"name": user.full_name, "language": get_user_language(update, config), "subscription": subscription_id}
    save_yaml(users_db, USER_DB_FILE)
    
    lang = users_db[telegram_id]['language']
    sub_url = f"{config['subscription']['url']}/{config['subscription']['uri']}/{subscription_id}"
    
    welcome_msg = get_localized_message("welcome", lang, config).format(
        quota=f"{defaults['total_gb']} GB", 
        reset=defaults['reset_days'], 
        sub_url=f"`{sub_url}`"
    )
    await update.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD_MARKUP)

# ==============================================================================
# BOT KEYBOARD & COMMAND HANDLERS
# ==============================================================================

KEYBOARD_MARKUP = ReplyKeyboardMarkup([["/status"], ["/help", "/contact"]], resize_keyboard=True)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    user_id = str(update.effective_user.id)
    if user_id not in load_yaml(USER_DB_FILE):
        await register_new_user(update, context)
    else:
        logger.info(f"Existing user {user_id} used /start.")
        await status_command(update, context)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /status command."""
    user_id = str(update.effective_user.id)
    users_db = load_yaml(USER_DB_FILE)
    if user_id not in users_db:
        await register_new_user(update, context)
        return

    config = load_yaml(CONFIG_FILE).get("settings", {})
    total_down_bytes, client_info = 0, None
    for db_path_str, inbound_id in config['db'].items():
        # The get_status in the bot needs expiry_time, so we'll use a local version of it
        status_row = get_status(Path(db_path_str), inbound_id, user_id)
        if status_row:
            total_down_bytes += status_row[0] # down
            if not client_info:
                client_info = {'total': status_row[1], 'expiry': status_row[2]}

    if not client_info:
        await update.message.reply_text("Could not retrieve your status. Please contact support.", reply_markup=KEYBOARD_MARKUP)
        return

    lang = users_db[user_id]['language']
    if datetime.now().timestamp() * 1000 > client_info['expiry']:
        await update.message.reply_text(get_localized_message("trial_end", lang, config), reply_markup=KEYBOARD_MARKUP)
        return

    defaults = {k.strip(): v.strip() for k, v in (item.split('=') for item in config['default'])}
    subscription_id = users_db[user_id]['subscription']
    sub_url = f"{config['subscription']['url']}/{config['subscription']['uri']}/{subscription_id}"
    
    used_gb = total_down_bytes / GB_TO_BYTES
    total_gb = client_info['total'] / GB_TO_BYTES

    # --- NEW LOGIC TO HANDLE QUOTA EXCEEDED ---
    if used_gb > total_gb:
        # User has exceeded their quota, send the special message
        message_text = get_localized_message("quota_exceeded", lang, config).format(
            total_gb=f"{total_gb:.2f}",
            reset=defaults['reset_days'],
            expiration_date=format_time_left(client_info['expiry'])
        )
    else:
        # User is within their quota, send the normal status message
        message_text = get_localized_message("status", lang, config).format(
            sub_url=f"`{sub_url}`",
            used_gb=f"{used_gb:.2f}",
            total_gb=f"{total_gb:.2f}",
            reset=defaults['reset_days'],
            expiration_date=format_time_left(client_info['expiry'])
        )
        
    await update.message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD_MARKUP)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /help command."""
    config = load_yaml(CONFIG_FILE).get("settings", {})
    lang = get_user_language(update, config)
    await update.message.reply_text(
        get_localized_message("help", lang, config),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=KEYBOARD_MARKUP
    )

async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /contact command."""
    config = load_yaml(CONFIG_FILE).get("settings", {})
    lang = get_user_language(update, config)
    await update.message.reply_text(
        get_localized_message("contact", lang, config),
        reply_markup=KEYBOARD_MARKUP
    )

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles non-command text messages."""
    user_id = str(update.effective_user.id)
    if user_id not in load_yaml(USER_DB_FILE):
        await register_new_user(update, context)
    else:
        try:
            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=update.message.message_id)
        except Exception as e:
            logger.warning(f"Could not delete message: {e}")

# ==============================================================================
# MAIN FUNCTION
# ==============================================================================

async def post_init(application: Application) -> None:
    """Clears any old command menus after the bot initializes."""
    await application.bot.delete_my_commands()
    logger.info("Cleared old command menu.")

def main() -> None:
    """Start the bot."""
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("contact", contact_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    
    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()