import logging
import random
import string
import yaml
import base64
import os
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
from telegram.error import Forbidden, BadRequest

# Import for translation
from translate import Translator

# --- Configuration & Constants ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN")
CONFIG_FILE = Path("database.yaml")
USER_DB_FILE = Path("users.yaml")

# Conversation states
SELECT_USER, SELECT_DURATION, SELECT_QUOTA = range(3)
NEW_GET_ID, NEW_GET_NAME, NEW_GET_LANG = range(3, 6)
DELETE_USER_SELECT = range(6, 7)
GET_BROADCAST_MESSAGE = range(7, 8)


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
        if not self.logged_in: return False
        try:
            payload = {'id': inbound_id, 'settings': json.dumps({"clients": [client_settings]})}
            response = self.session.post(f"{self.base_url}panel/api/inbounds/addClient", data=payload, verify=False)
            response.raise_for_status()
            data = response.json()
            if data.get('success'):
                logger.info(f"API: Successfully added client {client_settings['email']} to inbound {inbound_id}")
                return True
            logger.error(f"API Error adding client: {data.get('msg')}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"API Error adding client: {e}")
            return False

    def update_client(self, client_uuid: str, inbound_id: int, client_settings: dict) -> bool:
        if not self.logged_in: return False
        try:
            payload = {
                "id": inbound_id,
                "settings": json.dumps({"clients": [client_settings]}, separators=(",",":"))
            }
            response = self.session.post(f"{self.base_url}panel/api/inbounds/updateClient/{client_uuid}", json=payload, verify=False)
            response.raise_for_status()
            data = response.json()
            if data.get('success'):
                logger.info(f"API: Successfully updated client {client_uuid}")
                return True
            logger.error(f"API Error updating client {client_uuid}: {data.get('msg')}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"API Error updating client {client_uuid}: {e}")
            return False

    def delete_client(self, inbound_id: int, client_uuid: str) -> bool:
        if not self.logged_in: return False
        try:
            url = f"{self.base_url}panel/api/inbounds/{inbound_id}/delClient/{client_uuid}"
            response = self.session.post(url, verify=False)
            response.raise_for_status()
            data = response.json()
            if data.get('success'):
                logger.info(f"API: Successfully deleted client {client_uuid} from inbound {inbound_id}")
                return True
            logger.error(f"API Error deleting client {client_uuid}: {data.get('msg')}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"API Error deleting client {client_uuid}: {e}")
            return False

    def get_client_traffics(self, email: str) -> Optional[dict]:
        if not self.logged_in: return None
        try:
            encoded_email = quote(email)
            response = self.session.get(f"{self.base_url}panel/api/inbounds/getClientTraffics/{encoded_email}", verify=False)
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
    with open(file_path, "r", encoding='utf-8') as f:
        data = yaml.safe_load(f)
        return data if data is not None else {}

def save_yaml(data: dict, file_path: Path) -> None:
    with open(file_path, "w", encoding='utf-8') as f:
        yaml.dump(data, f, indent=2, allow_unicode=True)

def generate_subscription_id(length: int = 16) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))

def get_config_from_api(inbound_data: dict, email: str) -> Optional[str]:
    try:
        stream_settings = json.loads(inbound_data.get("streamSettings", "{}"))
        settings = json.loads(inbound_data.get("settings", "{}"))
        client_data = next((c for c in settings.get("clients", []) if c.get("email") == email), None)
        if not client_data: return None
        listen_ip = inbound_data.get("listen")
        port = inbound_data.get("port")
        server_address = listen_ip if listen_ip and listen_ip not in ["127.0.0.1", "0.0.0.0", ""] else None
        if stream_settings.get("externalProxy"):
            proxy = stream_settings.get("externalProxy", [{}])[0]
            server_address = proxy.get("dest", server_address)
            port = proxy.get("port", port)
        if not server_address:
            logger.warning(f"Could not determine public server address for inbound {inbound_data['id']}. Skipping.")
            return None
        params = {"encryption": "none"}
        security_type = stream_settings.get("security")
        params["security"] = security_type if security_type != "none" else ""
        if security_type == "reality":
            reality_settings = stream_settings.get("realitySettings", {})
            nested_settings = reality_settings.get("settings", {})
            params["pbk"] = nested_settings.get("publicKey")
            params["fp"] = nested_settings.get("fingerprint")
            params["sni"] = reality_settings.get("serverNames", [""])[0]
            params["sid"] = reality_settings.get("shortIds", [""])[0]
            params["spx"] = nested_settings.get("spiderX")
        elif security_type == "tls":
            tls_settings = stream_settings.get("tlsSettings", {})
            nested_settings = tls_settings.get("settings", {})
            params["sni"] = tls_settings.get("serverName")
            params["fp"] = nested_settings.get("fingerprint")
            alpn_list = tls_settings.get("alpn", [])
            if alpn_list: params["alpn"] = ",".join(alpn_list)
        network_type = stream_settings.get("network")
        params["type"] = network_type
        if network_type == "tcp":
            tcp_settings = stream_settings.get("tcpSettings", {})
            if tcp_settings.get("header", {}).get("type") == "http":
                params["headerType"] = "http"
                path_list = tcp_settings.get("header", {}).get("request", {}).get("path", [])
                if path_list: params["path"] = path_list[0]
        elif network_type == "ws":
            ws_settings = stream_settings.get("wsSettings", {})
            params["path"] = ws_settings.get("path")
            host = ws_settings.get("headers", {}).get("Host")
            if host: params["host"] = host
        elif network_type == "grpc":
            grpc_settings = stream_settings.get("grpcSettings", {})
            params["serviceName"] = grpc_settings.get("serviceName")
        elif network_type == "http":
            http_settings = stream_settings.get("httpSettings", {})
            params["path"] = http_settings.get("path")
            host_list = http_settings.get("host", [])
            if host_list: params["host"] = host_list[0]
        elif network_type == "xhttp":
            xhttp_settings = stream_settings.get("xhttpSettings", {})
            params["path"] = xhttp_settings.get("path")
            params["mode"] = xhttp_settings.get("mode")
            host = xhttp_settings.get("host", "")
            if host: params["host"] = host
        params = {k: v for k, v in params.items() if v is not None and v != ""}
        base_url = f"vless://{client_data['id']}@{server_address}:{port}"
        query_string = urlencode(params, quote_via=quote)
        config_name = inbound_data.get('remark', f"Config-{email.split('#')[0]}")
        return f"{base_url}?{query_string}#{quote(config_name)}"
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        logger.error(f"Failed to reconstruct config for email {email} from API data: {e}", exc_info=True)
        return None

def get_user_language_from_update(update: Update, config: dict) -> str:
    return update.effective_user.language_code if update.effective_user.language_code in config.get("welcome", {}) else "en"
    
def get_localized_message(key: str, lang: str, config: dict) -> str: return config.get(key, {}).get(lang, config.get(key, {}).get("en", "Message not found."))

def format_timedelta(delta: timedelta) -> str:
    if delta.total_seconds() < 0: return "Expired"
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{days}d {hours}h {minutes}m"

# ==============================================================================
# REGISTRATION & SUBSCRIPTION LOGIC (API-POWERED)
# ==============================================================================

async def register_new_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    config_data = load_yaml(CONFIG_FILE)
    config = config_data.get("settings", {})
    users_db = load_yaml(USER_DB_FILE)
    
    user_id = str(user.id)
    logger.info(f"Registering new user: {user_id} ({user.full_name})")
    
    defaults = {k.strip(): v.strip() for k, v in (item.split('=') for item in config['default'])}
    
    now_ms = int(time.time() * 1000)
    expiry_ms = now_ms + (int(defaults['duration_days']) * DAYS_TO_MS)
    total_bytes = int(float(defaults['total_gb']) * GB_TO_BYTES)
    
    for server_name, server_config in config['db'].items():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if not api.logged_in: continue
        inbounds = server_config.get('inbound', [])
        if not isinstance(inbounds, list): inbounds = [inbounds]
        for inbound_id in inbounds:
            client_email = f"{user_id}#{inbound_id}"
            client_payload = { "id": str(uuid.uuid4()), "email": client_email, "enable": True, "tgId": user.id, "totalGB": total_bytes, "expiryTime": expiry_ms, "subId": str(uuid.uuid4().hex)[:16], "reset": 0, "flow": "", "limitIp": 0 }
            api.add_client(inbound_id, client_payload)

    all_vless_links = []
    for server_name, server_config in config['db'].items():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if not api.logged_in: continue
        inbounds = server_config.get('inbound', [])
        if not isinstance(inbounds, list): inbounds = [inbounds]
        for inbound_id in inbounds:
            inbound_data = api.get_inbound(inbound_id)
            if inbound_data:
                link = get_config_from_api(inbound_data, f"{user_id}#{inbound_id}")
                if link: all_vless_links.append(link)

    if not all_vless_links:
        await update.message.reply_text("Error creating subscription file. Please contact support.")
        return

    subscription_id = generate_subscription_id()
    encoded_content = base64.b64encode("\n".join(all_vless_links).encode('utf-8')).decode('utf-8')
    sub_dir = Path(config['subscription'].get('uri', 'sub'))
    sub_dir.mkdir(exist_ok=True)
    (sub_dir / subscription_id).write_text(encoded_content, encoding='utf-8')

    users_db[user_id] = {
        "name": user.full_name, 
        "language": get_user_language_from_update(update, config), 
        "subscription": subscription_id,
        "quota": float(defaults['total_gb'])
    }
    save_yaml(users_db, USER_DB_FILE)
    
    lang = users_db[user_id]['language']
    subscription_name = config['subscription'].get('name', 'VPN') 
    sub_url = f"{config['subscription']['url']}/{subscription_id}#{quote(subscription_name)}"
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

    if user_id == ADMIN_ID and user_id not in users_db:
        admin_msg = ("🤖 Bot is running.\n"
                     "You are the admin, but you don't have a personal subscription managed by this bot. "
                     "To create one for yourself, please use the /start command.\n\n"
                     "*Admin commands:*\n/new - /edit - /delete - /broadcast")
        await update.message.reply_text(admin_msg, parse_mode=ParseMode.MARKDOWN)
        return
    
    if user_id not in users_db:
        await register_new_user(update, context)
        return

    config_data = load_yaml(CONFIG_FILE)
    config = config_data.get("settings", {})
    total_used_bytes = 0 # <-- FIX: Renamed variable
    master_client_info = None
    found_on_panels = False

    for server_name, server_config in config['db'].items():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if not api.logged_in: continue
        inbounds = server_config.get('inbound', [])
        if not isinstance(inbounds, list): inbounds = [inbounds]
        for inbound_id in inbounds:
            client_email = f"{user_id}#{inbound_id}"
            traffic_data = api.get_client_traffics(client_email)
            if traffic_data:
                found_on_panels = True
                # --- FIX: Sum both UP and DOWN traffic ---
                total_used_bytes += traffic_data.get('up', 0) + traffic_data.get('down', 0)
                if master_client_info is None:
                    master_client_info = {'expiry': traffic_data.get('expiryTime', 0)}
    
    if not found_on_panels:
        await update.message.reply_text("Could not retrieve your status. Please contact support.", reply_markup=KEYBOARD_MARKUP)
        return

    lang = users_db[user_id]['language']
    expiry_delta = timedelta(milliseconds=(master_client_info['expiry'] - (time.time() * 1000))) if master_client_info['expiry'] > 0 else timedelta(days=9999)
    
    if expiry_delta.total_seconds() < 0:
        await update.message.reply_text(get_localized_message("trial_end", lang, config), reply_markup=KEYBOARD_MARKUP)
        return
    
    defaults = {k.strip(): v.strip() for k, v in (item.split('=') for item in config['default'])}
    subscription_id = users_db[user_id]['subscription']
    subscription_name = config['subscription'].get('name', 'VPN')
    sub_url = f"{config['subscription']['url']}/{subscription_id}#{quote(subscription_name)}"
    
    used_gb = total_used_bytes / GB_TO_BYTES # <-- FIX: Use correct variable
    total_gb = users_db[user_id].get('quota', float(defaults['total_gb']))
    remaining_gb = max(0, total_gb - used_gb)

    message_key = "quota_exceeded" if total_gb > 0 and remaining_gb <= 0 else "status"
    
    message_text = get_localized_message(message_key, lang, config).format(
        sub_url=f"`{sub_url}`",
        used_gb=f"{remaining_gb:.2f}", 
        total_gb=f"{total_gb:.2f}",
        reset=defaults.get('reset_days', 0),
        expiration_date=format_timedelta(expiry_delta)
    )
    
    if user_id == ADMIN_ID:
        message_text += "\n\n*Admin commands:*\n/new - /edit - /delete - /broadcast"
    else:
        message_text += f"\n\nID: `{user_id}`"

    await update.message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD_MARKUP)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = load_yaml(CONFIG_FILE).get("settings", {})
    users_db = load_yaml(USER_DB_FILE)
    user_id = str(update.effective_user.id)
    lang = users_db.get(user_id, {}).get('language', 'en')
    await update.message.reply_text(get_localized_message("help", lang, config), parse_mode=ParseMode.MARKDOWN, reply_markup=KEYBOARD_MARKUP)


async def contact_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config = load_yaml(CONFIG_FILE).get("settings", {})
    users_db = load_yaml(USER_DB_FILE)
    user_id = str(update.effective_user.id)
    lang = users_db.get(user_id, {}).get('language', 'en')
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

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(text="Operation cancelled.")
    elif update.message:
        await update.message.reply_text("Operation cancelled.")
    context.user_data.clear()
    return ConversationHandler.END

# ==============================================================================
# ADMIN /edit COMMAND
# ==============================================================================

def build_user_keyboard(users: dict, prefix: str) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(f"{data['name']} ({user_id})", callback_data=f"{prefix}{user_id}") for user_id, data in sorted(users.items(), key=lambda item: item[1]['name'].lower())]
    keyboard = [buttons[i:i + 3] for i in range(0, len(buttons), 3)]
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

async def edit_command_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if str(update.effective_user.id) != ADMIN_ID: return ConversationHandler.END
    users_db = load_yaml(USER_DB_FILE)
    if not users_db:
        await update.message.reply_text("No users found.")
        return ConversationHandler.END
    await update.message.reply_text("Select a user to edit:", reply_markup=build_user_keyboard(users_db, "edit_user_"))
    return SELECT_USER

async def select_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id_to_edit = query.data.replace("edit_user_", "")
    context.user_data['user_to_edit'] = user_id_to_edit
    
    users_db = load_yaml(USER_DB_FILE)
    user_info = users_db.get(user_id_to_edit)
    config_data = load_yaml(CONFIG_FILE)
    config = config_data.get("settings", {})
    
    total_used_bytes, client_info = 0, None # <-- FIX: Renamed variable
    for server_config in config['db'].values():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if not api.logged_in: continue
        inbounds = server_config.get('inbound', [])
        if not isinstance(inbounds, list): inbounds = [inbounds]
        for inbound_id in inbounds:
            traffic = api.get_client_traffics(f"{user_id_to_edit}#{inbound_id}")
            if traffic:
                # --- FIX: Sum both UP and DOWN traffic ---
                total_used_bytes += traffic.get('up', 0) + traffic.get('down', 0)
                if not client_info: client_info = {'expiry': traffic.get('expiryTime', 0)}
    
    used_gb = total_used_bytes / GB_TO_BYTES # <-- FIX: Use correct variable
    total_gb = users_db[user_id_to_edit].get('quota', 0)
    remaining_gb = max(0, total_gb - used_gb)
    
    expiry_delta = timedelta(milliseconds=(client_info['expiry'] - (time.time() * 1000))) if client_info and client_info['expiry'] > 0 else timedelta(days=9999)
    expiry_date = "N/A" if not client_info else format_timedelta(expiry_delta)
    
    details_text = (f"Editing *{user_info['name']}* (`{user_id_to_edit}`)\n"
        f"Language: `{user_info['language']}`\n"
        f"Data Left: `{remaining_gb:.2f} GB / {total_gb:.2f} GB`\n"
        f"Expires in: `{expiry_date}`\n\n"
        "Set new expiration (days from now):")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("30d", callback_data="edit_dur_30"), InlineKeyboardButton("90d", callback_data="edit_dur_90"), InlineKeyboardButton("365d", callback_data="edit_dur_365")], 
        [InlineKeyboardButton("Cancel", callback_data="cancel")]
    ])
    await query.edit_message_text(text=details_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    return SELECT_DURATION

async def select_duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['new_duration'] = int(query.data.replace("edit_dur_", ""))
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("2.1GB", callback_data="edit_quota_2.1"), InlineKeyboardButton("3.5GB", callback_data="edit_quota_3.5"), InlineKeyboardButton("7GB", callback_data="edit_quota_7")], 
        [InlineKeyboardButton("14GB", callback_data="edit_quota_14"), InlineKeyboardButton("35GB", callback_data="edit_quota_35"), InlineKeyboardButton("70GB", callback_data="edit_quota_70")], 
        [InlineKeyboardButton("Cancel", callback_data="cancel")]
    ])
    await query.edit_message_text(text="Select new quota (GB):", reply_markup=keyboard)
    return SELECT_QUOTA

async def select_quota_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    user_id = context.user_data['user_to_edit']
    new_duration = context.user_data['new_duration']
    new_quota = float(query.data.replace("edit_quota_", ""))
    
    config_data = load_yaml(CONFIG_FILE)
    config = config_data.get("settings", {})
    now_ms = int(time.time() * 1000)
    new_expiry_ms = now_ms + (new_duration * DAYS_TO_MS)
    new_total_bytes = int(new_quota * GB_TO_BYTES)
    
    success_count = 0
    for server_config in config['db'].values():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if not api.logged_in: continue
        inbounds = server_config.get('inbound', [])
        if not isinstance(inbounds, list): inbounds = [inbounds]
        for inbound_id in inbounds:
            inbound_data = api.get_inbound(inbound_id)
            if not inbound_data: continue
            settings = json.loads(inbound_data.get("settings", "{}"))
            client_email = f"{user_id}#{inbound_id}"
            client_to_update = next((c for c in settings.get("clients", []) if c.get("email") == client_email), None)
            if client_to_update:
                client_uuid = client_to_update['id']
                client_to_update['totalGB'] = new_total_bytes
                client_to_update['expiryTime'] = new_expiry_ms
                if api.update_client(client_uuid, inbound_id, client_to_update):
                    success_count += 1
            else:
                logger.warning(f"Client {client_email} not found in inbound {inbound_id} during edit.")
    
    users_db = load_yaml(USER_DB_FILE)
    users_db[user_id]['quota'] = new_quota
    save_yaml(users_db, USER_DB_FILE)
    
    user_name = users_db.get(user_id, {}).get("name", "Unknown")
    await query.edit_message_text(text=f"User *{user_name}* updated successfully! ({success_count} clients modified)", parse_mode=ParseMode.MARKDOWN)
    context.user_data.clear()
    return ConversationHandler.END

# ... (The rest of the admin commands and main function remain the same)
# ==============================================================================
# ADMIN /new COMMAND
# ==============================================================================

async def new_command_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if str(update.effective_user.id) != ADMIN_ID: return ConversationHandler.END
    await update.message.reply_text("Please enter the new user's ID (can be any string, e.g., 'friend_1' or a Telegram ID).\n\nOr /cancel to abort.")
    return NEW_GET_ID

async def new_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.message.text.strip()
    if not user_id: return NEW_GET_ID
    users_db = load_yaml(USER_DB_FILE)
    if user_id in users_db:
        await update.message.reply_text("This ID already exists. Please choose another one or /cancel.")
        return NEW_GET_ID
    context.user_data['new_user_id'] = user_id
    await update.message.reply_text("Great. Now, what is the user's name? Or /cancel.")
    return NEW_GET_NAME

async def new_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['new_user_name'] = update.message.text.strip()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("English", callback_data="en"), InlineKeyboardButton("Español", callback_data="es")],
        [InlineKeyboardButton("Français", callback_data="fr"), InlineKeyboardButton("Русский", callback_data="ru")],
        [InlineKeyboardButton("中文(简体)", callback_data="zh-hans")],
        [InlineKeyboardButton("Cancel", callback_data="cancel")]
    ])
    await update.message.reply_text("Finally, select a language for the user.", reply_markup=keyboard)
    return NEW_GET_LANG

async def new_get_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    lang = query.data
    user_id = context.user_data['new_user_id']
    user_name = context.user_data['new_user_name']
    
    await query.edit_message_text(f"Creating user '{user_name}' with ID '{user_id}'. Please wait...")

    config_data = load_yaml(CONFIG_FILE)
    config = config_data.get("settings", {})
    users_db = load_yaml(USER_DB_FILE)
    defaults = {k.strip(): v.strip() for k, v in (item.split('=') for item in config['default'])}
    
    now_ms = int(time.time() * 1000)
    expiry_ms = now_ms + (int(defaults['duration_days']) * DAYS_TO_MS)
    total_bytes = int(float(defaults['total_gb']) * GB_TO_BYTES)
    
    for server_name, server_config in config['db'].items():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if not api.logged_in: continue
        inbounds = server_config.get('inbound', [])
        if not isinstance(inbounds, list): inbounds = [inbounds]
        for inbound_id in inbounds:
            client_email = f"{user_id}#{inbound_id}"
            client_payload = { "id": str(uuid.uuid4()), "email": client_email, "enable": True, "tgId": "", "totalGB": total_bytes, "expiryTime": expiry_ms, "subId": str(uuid.uuid4().hex)[:16], "reset": 0, "flow": "", "limitIp": 0 }
            api.add_client(inbound_id, client_payload)
            
    all_vless_links = []
    for server_name, server_config in config['db'].items():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if not api.logged_in: continue
        inbounds = server_config.get('inbound', [])
        if not isinstance(inbounds, list): inbounds = [inbounds]
        for inbound_id in inbounds:
            inbound_data = api.get_inbound(inbound_id)
            if inbound_data:
                link = get_config_from_api(inbound_data, f"{user_id}#{inbound_id}")
                if link: all_vless_links.append(link)

    if not all_vless_links:
        await query.edit_message_text("Error creating subscription file. Please contact support.")
        return ConversationHandler.END

    subscription_id = generate_subscription_id()
    encoded_content = base64.b64encode("\n".join(all_vless_links).encode('utf-8')).decode('utf-8')
    sub_dir = Path(config['subscription'].get('uri', 'sub'))
    sub_dir.mkdir(exist_ok=True)
    (sub_dir / subscription_id).write_text(encoded_content, encoding='utf-8')

    users_db[user_id] = {"name": user_name, "language": lang, "subscription": subscription_id, "quota": float(defaults['total_gb'])}
    save_yaml(users_db, USER_DB_FILE)
    
    subscription_name = config['subscription'].get('name', 'VPN') 
    sub_url = f"{config['subscription']['url']}/{subscription_id}#{quote(subscription_name)}"
    welcome_msg = get_localized_message("welcome", lang, config).format(
        quota=f"{defaults['total_gb']} GB", 
        reset=defaults.get('reset_days', 0), 
        sub_url=f"`{sub_url}`"
    )
    await query.edit_message_text(f"User *{user_name}* created successfully! Here is their welcome message to forward:", parse_mode=ParseMode.MARKDOWN)
    await query.message.reply_text(welcome_msg, parse_mode=ParseMode.MARKDOWN)
    context.user_data.clear()
    return ConversationHandler.END


# ==============================================================================
# ADMIN /delete COMMAND
# ==============================================================================

async def delete_command_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if str(update.effective_user.id) != ADMIN_ID: return ConversationHandler.END
    users_db = load_yaml(USER_DB_FILE)
    if not users_db:
        await update.message.reply_text("No users found to delete.")
        return ConversationHandler.END
    await update.message.reply_text("Select a user to DELETE. This action is irreversible.", reply_markup=build_user_keyboard(users_db, "delete_user_"))
    return DELETE_USER_SELECT

async def delete_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id_to_delete = query.data.replace("delete_user_", "")
    users_db = load_yaml(USER_DB_FILE)
    user_info = users_db.get(user_id_to_delete)
    if not user_info:
        await query.edit_message_text("User not found in database.")
        return ConversationHandler.END
    await query.edit_message_text(f"Deleting user *{user_info['name']}* (`{user_id_to_delete}`)... Please wait.", parse_mode=ParseMode.MARKDOWN)
    config_data = load_yaml(CONFIG_FILE)
    config = config_data.get("settings", {})
    deleted_count = 0
    for server_name, server_config in config['db'].items():
        api = XUIApi(server_config['address'], server_config['panel_path'], config['subscription']['user'], config['subscription']['password'])
        if not api.logged_in: continue
        inbounds = server_config.get('inbound', [])
        if not isinstance(inbounds, list): inbounds = [inbounds]
        for inbound_id in inbounds:
            inbound_data = api.get_inbound(inbound_id)
            if not inbound_data: continue
            settings = json.loads(inbound_data.get("settings", "{}"))
            client_email = f"{user_id_to_delete}#{inbound_id}"
            client_to_delete = next((c for c in settings.get("clients", []) if c.get("email") == client_email), None)
            if client_to_delete and 'id' in client_to_delete:
                if api.delete_client(inbound_id, client_to_delete['id']):
                    deleted_count += 1
            else:
                logger.warning(f"Client {client_email} not found in inbound {inbound_id} during deletion.")
    subscription_id = users_db[user_id_to_delete]['subscription']
    del users_db[user_id_to_delete]
    save_yaml(users_db, USER_DB_FILE)
    sub_file = Path(config['subscription'].get('uri', 'sub')) / subscription_id
    if sub_file.exists(): sub_file.unlink()
    await query.edit_message_text(f"Successfully deleted user *{user_info['name']}*.\n({deleted_count} panel clients removed).", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

# ==============================================================================
# ADMIN /broadcast COMMAND
# ==============================================================================

async def broadcast_command_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if str(update.effective_user.id) != ADMIN_ID:
        return ConversationHandler.END
    await update.message.reply_text("Please send the message you want to broadcast. You can include media and a caption. Or /cancel to abort.")
    return GET_BROADCAST_MESSAGE

async def broadcast_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    await message.reply_text("Processing broadcast, please wait...")

    users_db = load_yaml(USER_DB_FILE)
    if not users_db:
        await message.reply_text("There are no users to broadcast to.")
        return ConversationHandler.END

    original_text = message.text or message.caption or ""
    all_langs = {user['language'] for user in users_db.values()}
    
    translations = {}
    if original_text:
        for lang in all_langs:
            try:
                if lang.startswith('en'): translations[lang] = original_text; continue
                translator = Translator(to_lang=lang, from_lang='en')
                translated_text = translator.translate(original_text)
                translations[lang] = translated_text
                logger.info(f"Translated broadcast message to {lang}")
            except Exception as e:
                logger.error(f"Could not translate to {lang}: {e}")
                translations[lang] = f"(Could not translate)\n\n{original_text}"
    
    success_count = 0
    fail_count = 0
    for user_id, user_data in users_db.items():
        lang = user_data.get("language", "en")
        translated_caption = translations.get(lang, original_text)

        try:
            if message.photo: await context.bot.send_photo(chat_id=user_id, photo=message.photo[-1].file_id, caption=translated_caption)
            elif message.video: await context.bot.send_video(chat_id=user_id, video=message.video.file_id, caption=translated_caption)
            elif message.document: await context.bot.send_document(chat_id=user_id, document=message.document.file_id, caption=translated_caption)
            elif message.audio: await context.bot.send_audio(chat_id=user_id, audio=message.audio.file_id, caption=translated_caption)
            elif message.text: await context.bot.send_message(chat_id=user_id, text=translated_caption)
            else: await context.bot.copy_message(chat_id=user_id, from_chat_id=message.chat_id, message_id=message.message_id)
            success_count += 1
            time.sleep(0.1)
        except (Forbidden, BadRequest) as e:
            logger.warning(f"Failed to send broadcast to {user_id}: {e}")
            fail_count += 1
        except Exception as e:
            logger.error(f"An unexpected error occurred sending broadcast to {user_id}: {e}")
            fail_count += 1

    await message.reply_text(f"Broadcast finished!\n\nSent successfully to: {success_count} users.\nFailed for: {fail_count} users.")
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
    
    conv_handlers = [
        ConversationHandler(
            entry_points=[CommandHandler("edit", edit_command_start)],
            states={
                SELECT_USER: [CallbackQueryHandler(select_user_callback, pattern="^edit_user_")],
                SELECT_DURATION: [CallbackQueryHandler(select_duration_callback, pattern="^edit_dur_")],
                SELECT_QUOTA: [CallbackQueryHandler(select_quota_callback, pattern="^edit_quota_")],
            }, fallbacks=[CallbackQueryHandler(cancel_callback, pattern="^cancel$")]
        ),
        ConversationHandler(
            entry_points=[CommandHandler("new", new_command_start)],
            states={
                NEW_GET_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_get_id)],
                NEW_GET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_get_name)],
                NEW_GET_LANG: [CallbackQueryHandler(new_get_lang, pattern="^(en|es|fr|ru|zh-hans)$")],
            }, fallbacks=[CallbackQueryHandler(cancel_callback, pattern="^cancel$"), CommandHandler("cancel", cancel_callback)]
        ),
        ConversationHandler(
            entry_points=[CommandHandler("delete", delete_command_start)],
            states={DELETE_USER_SELECT: [CallbackQueryHandler(delete_user_callback, pattern="^delete_user_")]},
            fallbacks=[CallbackQueryHandler(cancel_callback, pattern="^cancel$")]
        ),
        ConversationHandler(
            entry_points=[CommandHandler("broadcast", broadcast_command_start)],
            states={GET_BROADCAST_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_receive_message)]},
            fallbacks=[CommandHandler("cancel", cancel_callback)]
        )
    ]
    
    application.add_handlers(conv_handlers)
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