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
import requests

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

# Conversation states for the /edit command
SELECT_USER, SELECT_DURATION, SELECT_QUOTA = range(3)

# Constants
GB_TO_BYTES = 1024**3
DAYS_TO_MS = 24 * 60 * 60 * 1000

# --- Logging Setup ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


# ==============================================================================
# 3X-UI API WRAPPER CLASS
# ==============================================================================

class XUIApi:
    """A wrapper for the 3X-UI panel API."""
    def __init__(self, address: str, panel_path: str, username: str, password: str):
        self.base_url = f"{address.rstrip('/')}{panel_path}"
        self.session = requests.Session()
        self.session.headers.update({'Accept': 'application/json'})
        self.logged_in = self._login(username, password)

    def _login(self, username, password):
        """Logs into the panel and stores the session cookie."""
        try:
            response = self.session.post(f"{self.base_url}login", data={'username': username, 'password': password}, verify=False)
            response.raise_for_status()
            if response.json().get('success'):
                logger.info(f"Successfully logged into panel at {self.base_url}")
                return True
            logger.error(f"Failed to log into panel at {self.base_url}: {response.json().get('msg')}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Error connecting to panel at {self.base_url}: {e}")
            return False

    def get_inbound(self, inbound_id: int) -> Optional[dict]:
        """Retrieves all details for a specific inbound."""
        if not self.logged_in: return None
        try:
            response = self.session.get(f"{self.base_url}panel/api/inbounds/get/{inbound_id}", verify=False)
            response.raise_for_status()
            data = response.json()
            return data.get('obj') if data.get('success') else None
        except requests.exceptions.RequestException as e:
            logger.error(f"API Error getting inbound {inbound_id}: {e}")
            return None

    def add_client(self, inbound_id: int, client_settings: dict) -> bool:
        """Adds a new client to an inbound using the API."""
        if not self.logged_in: return False
        try:
            payload = {'id': inbound_id, 'settings': json.dumps({"clients": [client_settings]})}
            response = self.session.post(f"{self.base_url}panel/api/inbounds/addClient", data=payload, verify=False)
            response.raise_for_status()
            if response.json().get('success'):
                logger.info(f"API: Successfully added client {client_settings['email']} to inbound {inbound_id}")
                return True
            logger.error(f"API Error adding client: {response.json().get('msg')}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"API Error adding client: {e}")
            return False

    def update_inbound_settings(self, inbound_id: int, inbound_data: dict) -> bool:
        """Updates an entire inbound. Used for deleting/editing clients."""
        if not self.logged_in: return False
        try:
            response = self.session.post(f"{self.base_url}panel/api/inbounds/update/{inbound_id}", json=inbound_data, verify=False)
            response.raise_for_status()
            if response.json().get('success'):
                return True
            logger.error(f"API Error updating inbound {inbound_id}: {response.json().get('msg')}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"API Error updating inbound {inbound_id}: {e}")
            return False

    def get_client_traffics(self, email: str) -> Optional[dict]:
        """Gets traffic stats for a client by email."""
        if not self.logged_in: return None
        try:
            response = self.session.get(f"{self.base_url}panel/api/inbounds/getClientTraffics/{email}", verify=False)
            response.raise_for_status()
            data = response.json()
            return data.get('obj') if data.get('success') else None
        except requests.exceptions.RequestException as e:
            logger.error(f"API Error getting traffic for {email}: {e}")
            return None

# ==============================================================================
# API-BASED HELPER FUNCTIONS
# ==============================================================================

def load_yaml(file_path: Path) -> dict:
    if not file_path.exists(): return {}
    with open(file_path, "r") as f:
        data = yaml.safe_load(f)
        return data if data is not None else {}

def save_yaml(data: dict, file_path: Path) -> None:
    with open(file_path, "w") as f:
        yaml.dump(data, f, indent=2)

def generate_subscription_id(length: int = 16) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))

def get_config_from_api(inbound_data: dict, email: str) -> Optional[str]:
    """Reconstructs a VLESS URL from the API response of get_inbound."""
    try:
        stream_settings = json.loads(inbound_data["streamSettings"])
        settings = json.loads(inbound_data["settings"])
        
        external_proxy = stream_settings.get("externalProxy")[0]
        server_address, port = external_proxy.get("dest"), external_proxy.get("port")
        
        client_data = next((c for c in settings.get("clients", []) if c.get("email") == email), None)
        if not client_data: return None
        
        params = {"type": stream_settings.get("network"), "encryption": "none"}
        
        # Safely check for TCP header path
        tcp_settings = stream_settings.get("tcpSettings", {})
        if tcp_settings and tcp_settings.get("header", {}).get("type") == "http":
            path_list = tcp_settings.get("header", {}).get("request", {}).get("path", [])
            if path_list:
                params["path"] = path_list[0]
                params["headerType"] = "http"

        reality_settings = stream_settings.get("realitySettings", {})
        params.update({"security": stream_settings.get("security"),"pbk": reality_settings.get("settings", {}).get("publicKey"),"fp": reality_settings.get("settings", {}).get("fingerprint"),"sni": reality_settings.get("serverNames", [""])[0],"sid": reality_settings.get("shortIds", [""])[0],"spx": reality_settings.get("settings", {}).get("spiderX")})

        params = {k: v for k, v in params.items() if v}
        base_url = f"vless://{client_data['id']}@{server_address}:{port}"
        query_string = urlencode(params)
        return f"{base_url}?{query_string}#{quote(inbound_data['remark'])}"
    except Exception as e:
        logger.error(f"Failed to reconstruct config from API data: {e}")
        return None

def get_user_language(update: Update, config: dict) -> str: return update.effective_user.language_code if update.effective_user.language_code in config.get("welcome", {}) else "en"
def get_localized_message(key: str, lang: str, config: dict) -> str: return config.get(key, {}).get(lang, config.get(key, {}).get("en", "Message not found."))
def format_time_left(expiry_timestamp_ms: int) -> str:
    if expiry_timestamp_ms == 0: return "Unlimited"
    delta = datetime.fromtimestamp(expiry_timestamp_ms / 1000) - datetime.now()
    if delta.total_seconds() < 0: return "Expired"
    return f"{delta.days}d {delta.seconds//3600}h {(delta.seconds//60)%60}m"

# ==============================================================================
# REGISTRATION & SUBSCRIPTION LOGIC (API-POWERED)
# ==============================================================================

async def register_new_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    config = load_yaml(CONFIG_FILE).get("settings", {})
    users_db = load_yaml(USER_DB_FILE)
    
    logger.info(f"Registering new user: {user.id} ({user.full_name})")
    defaults = {k.strip(): v.strip() for k, v in (item.split('=') for item in config['default'])}
    
    telegram_id = str(user.id)
    subscription_id = generate_subscription_id()
    
    now_ms = int(time.time() * 1000)
    expiry_ms = now_ms + (int(defaults['duration_days']) * DAYS_TO_MS)
    
    client_payload = { "id": str(uuid.uuid4()), "email": telegram_id, "enable": True, "tgId": user.id, "totalGB": int(float(defaults['total_gb']) * GB_TO_BYTES), "expiryTime": expiry_ms, "subId": str(uuid.uuid4().hex)[:16], "reset": 0, "flow": "", "limitIp": 0 }

    # This loop dynamically handles ALL servers defined in your YAML
    for server_name, server_config in config['db'].items():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if not api.logged_in: continue
        
        inbound = api.get_inbound(server_config['inbound'])
        if inbound and inbound.get('settings'):
            settings = json.loads(inbound['settings'])
            settings['clients'] = [c for c in settings.get('clients', []) if c.get('email') != telegram_id]
            inbound['settings'] = json.dumps(settings, separators=(",",":"))
            api.update_inbound_settings(server_config['inbound'], inbound)
            
        api.add_client(server_config['inbound'], client_payload)

    all_vless_links = []
    # This loop dynamically handles ALL servers defined in your YAML
    for server_name, server_config in config['db'].items():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if not api.logged_in: continue
        inbound_data = api.get_inbound(server_config['inbound'])
        if inbound_data:
            link = get_config_from_api(inbound_data, telegram_id)
            if link: all_vless_links.append(link)

    if not all_vless_links:
        await update.message.reply_text("Error creating subscription file. Please contact support.")
        return

    encoded_content = base64.b64encode("\n".join(all_vless_links).encode('utf-8')).decode('utf-8')
    sub_dir = Path(config['subscription'].get('uri', 'sub'))
    sub_dir.mkdir(exist_ok=True)
    (sub_dir / subscription_id).write_text(encoded_content)

    users_db[telegram_id] = {"name": user.full_name, "language": get_user_language(update, config), "subscription": subscription_id}
    save_yaml(users_db, USER_DB_FILE)
    
    lang = users_db[telegram_id]['language']
    
    # Get the subscription name from the config, with a default fallback
    subscription_name = config['subscription'].get('name', 'VPN') 
    
    # Construct the new URL with the name appended after a '#'
    sub_url = f"{config['subscription']['url']}/{subscription_id}#{subscription_name}"
    
    welcome_msg = get_localized_message("welcome", lang, config).format(
        quota=f"{defaults['total_gb']} GB", 
        reset=defaults.get('reset_days', 0), 
        sub_url=f"`{sub_url}`"
    )
    await update.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD_MARKUP)

# ==============================================================================
# BOT COMMAND HANDLERS
# ==============================================================================

KEYBOARD_MARKUP = ReplyKeyboardMarkup([["/status"], ["/help", "/contact"]], resize_keyboard=True)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if user_id not in load_yaml(USER_DB_FILE):
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

    # This loop dynamically handles ALL servers defined in your YAML
    for server_config in config['db'].values():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if not api.logged_in: continue
        
        traffic_data = api.get_client_traffics(user_id)
        if traffic_data:
            total_down_bytes += traffic_data.get('down', 0)
            if not client_info:
                client_info = {'total': traffic_data.get('total', 0), 'expiry': traffic_data.get('expiryTime', 0)}

    if not client_info:
        await update.message.reply_text("Could not retrieve your status.", reply_markup=KEYBOARD_MARKUP)
        return
        
    lang = users_db[user_id]['language']
    if client_info['expiry'] != 0 and datetime.now().timestamp() * 1000 > client_info['expiry']:
        await update.message.reply_text(get_localized_message("trial_end", lang, config), reply_markup=KEYBOARD_MARKUP)
        return
    
    subscription_id = users_db[user_id]['subscription']
    sub_url = f"{config['subscription']['url']}/{subscription_id}"
    used_gb = total_down_bytes / GB_TO_BYTES
    total_gb = client_info['total'] / GB_TO_BYTES

    defaults = {k.strip(): v.strip() for k, v in (item.split('=') for item in config['default'])}
    subscription_id = users_db[user_id]['subscription']

    # Get the subscription name from the config, with a default fallback
    subscription_name = config['subscription'].get('name', 'VPN')
    
    # Construct the new URL with the name appended after a '#'
    sub_url = f"{config['subscription']['url']}/{subscription_id}#{subscription_name}"
    
    used_gb = total_down_bytes / GB_TO_BYTES
    total_gb = client_info['total'] / GB_TO_BYTES

    if used_gb > total_gb and total_gb > 0:
        message_text = get_localized_message("quota_exceeded", lang, config).format(
            total_gb=f"{total_gb:.2f}",
            reset=defaults.get('reset_days', 0),
            expiration_date=format_time_left(client_info['expiry'])
        )
    else:
        message_text = get_localized_message("status", lang, config).format(
            sub_url=f"`{sub_url}`", # This variable now contains the full URL
            used_gb=f"{used_gb:.2f}",
            total_gb=f"{total_gb:.2f}",
            reset=defaults.get('reset_days', 0),
            expiration_date=format_time_left(client_info['expiry'])
        )
    await update.message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD_MARKUP)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = load_yaml(CONFIG_FILE).get("settings", {})
    lang = get_user_language(update, config)
    await update.message.reply_text(get_localized_message("help", lang, config), parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD_MARKUP)

async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = load_yaml(CONFIG_FILE).get("settings", {})
    lang = get_user_language(update, config)
    await update.message.reply_text(get_localized_message("contact", lang, config), reply_markup=KEYBOARD_MARKUP)

async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    if user_id not in load_yaml(USER_DB_FILE):
        await register_new_user(update, context)
    else:
        try:
            await context.bot.delete_message(chat_id=update.message.chat_id, message_id=update.message.message_id)
        except Exception as e:
            logger.warning(f"Could not delete message: {e}")

# ==============================================================================
# ADMIN /edit COMMAND
# ==============================================================================

def build_user_keyboard(users: dict) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(user_id, callback_data=user_id) for user_id, data in sorted(users.items(), key=lambda item: item[1]['name'].lower())]
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
    
    users_db = load_yaml(USER_DB_FILE)
    user_info = users_db.get(user_id_to_edit)
    config = load_yaml(CONFIG_FILE).get("settings", {})
    total_down_bytes, client_info = 0, None

    # This loop dynamically handles ALL servers defined in your YAML
    for server_config in config['db'].values():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if api.logged_in:
            traffic = api.get_client_traffics(user_id_to_edit)
            if traffic:
                total_down_bytes += traffic.get('down', 0)
                if not client_info: client_info = {'total': traffic.get('total', 0), 'expiry': traffic.get('expiryTime', 0)}

    used_gb = total_down_bytes / GB_TO_BYTES
    total_gb = client_info['total'] / GB_TO_BYTES if client_info else 0
    expiry_date = format_time_left(client_info['expiry']) if client_info else "N/A"
    
    details_text = (f"Editing *{user_info['name']}* (`{user_id_to_edit}`)\n"
        f"Language: `{user_info['language']}`\n"
        f"Usage: `{used_gb:.2f} GB / {total_gb:.2f} GB`\n"
        f"Expires in: `{expiry_date}`\n\n"
        "Set new expiration (days from now):")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("30d", callback_data="30"), InlineKeyboardButton("90d", callback_data="90"), InlineKeyboardButton("365d", callback_data="365")], 
        [InlineKeyboardButton("Cancel", callback_data="cancel")]
    ])
    await query.edit_message_text(text=details_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    return SELECT_DURATION

async def select_duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['new_duration'] = int(query.data)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("10GB", callback_data="10"), InlineKeyboardButton("50GB", callback_data="50"), InlineKeyboardButton("100GB", callback_data="100")], 
        [InlineKeyboardButton("Cancel", callback_data="cancel")]
    ])
    await query.edit_message_text(text="Select new quota (GB):", reply_markup=keyboard)
    return SELECT_QUOTA

async def select_quota_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    user_id = context.user_data['user_to_edit']
    new_duration = context.user_data['new_duration']
    new_quota = float(query.data)

    config = load_yaml(CONFIG_FILE).get("settings", {})
    now_ms = int(time.time() * 1000)
    new_expiry_ms = now_ms + (new_duration * DAYS_TO_MS)
    new_total_bytes = int(new_quota * GB_TO_BYTES)
    
    # This loop dynamically handles ALL servers defined in your YAML
    for server_config in config['db'].values():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if not api.logged_in: continue
        
        inbound = api.get_inbound(server_config['inbound'])
        if inbound and inbound.get('settings'):
            settings = json.loads(inbound['settings'])
            client_found = False
            for client in settings.get("clients", []):
                if client.get("email") == user_id:
                    client['totalGB'] = new_total_bytes
                    client['expiryTime'] = new_expiry_ms
                    client_found = True
                    break
            if client_found:
                inbound['settings'] = json.dumps(settings, separators=(",",":"))
                api.update_inbound_settings(server_config['inbound'], inbound)
    
    users_db = load_yaml(USER_DB_FILE)
    user_name = users_db.get(user_id, {}).get("name", "Unknown")
    await query.edit_message_text(text=f"User *{user_name}* updated successfully!", parse_mode=ParseMode.MARKDOWN)
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Operation cancelled.")
    context.user_data.clear()
    return ConversationHandler.END

# ==============================================================================
# MAIN FUNCTION
# ==============================================================================

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
    requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
    main()