import logging
import time
import yaml
import base64
import sqlite3
import json
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlencode, quote
from typing import Optional, List, Any
import os

# --- Configuration & Constants ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
CONFIG_FILE = Path("database.yaml")
USER_DB_FILE = Path("users.yaml")
GB_TO_BYTES = 1024**3
WARNING_THRESHOLD = 90.0 # 90%

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# ==============================================================================
# HELPER & DATABASE FUNCTIONS
# ==============================================================================

def load_yaml(file_path: Path) -> dict:
    if not file_path.exists(): return {}
    with open(file_path, "r") as f:
        data = yaml.safe_load(f)
        return data if data is not None else {}

def save_yaml(data: dict, file_path: Path) -> None:
    with open(file_path, "w") as f:
        yaml.dump(data, f, indent=2)

def _get_db_connection(db_path: Path) -> Optional[tuple[sqlite3.Connection, sqlite3.Cursor]]:
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

def get_config(db_path: Path, inbound_id: int, email: str) -> Optional[str]:
    """Generates the VLESS configuration link for a client."""
    db_conn = _get_db_connection(db_path)
    if not db_conn: return None
    conn, cur = db_conn
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
            if path_list:
                params["path"] = path_list[0]
                params["headerType"] = "http"
        params.update({"security": stream_settings.get("security"),"pbk": reality_settings.get("settings", {}).get("publicKey"),"fp": reality_settings.get("settings", {}).get("fingerprint"),"sni": reality_settings.get("serverNames", [""])[0],"sid": reality_settings.get("shortIds", [""])[0],"spx": reality_settings.get("settings", {}).get("spiderX")})
        params = {k: v for k, v in params.items() if v}
        base_url = f"vless://{client_uuid}@{server_address}:{port}"
        query_string = urlencode(params)
        return f"{base_url}?{query_string}#{quote(remark)}"
    except Exception as e:
        logger.error(f"Error in get_config for '{email}' in {db_path.name}: {e}")
        return None
    finally:
        conn.close()

def get_status(db_path: Path, inbound_id: int, email: str) -> Optional[List[Any]]:
    """Retrieves the traffic status for a client."""
    db_conn = _get_db_connection(db_path)
    if not db_conn: return None
    conn, cur = db_conn
    try:
        row = cur.execute("SELECT down, total FROM client_traffics WHERE email = ? AND inbound_id = ?", (email, inbound_id)).fetchone()
        return [row["down"], row["total"]] if row else None
    except sqlite3.Error as e:
        logger.error(f"Error fetching status for '{email}' in {db_path.name}: {e}")
        return None
    finally:
        conn.close()

def update_user_download(db_path: Path, inbound_id: int, email: str, new_download_bytes: int):
    """Updates the 'down' traffic for a specific user."""
    db_conn = _get_db_connection(db_path)
    if not db_conn: return
    conn, cur = db_conn
    try:
        cur.execute("UPDATE client_traffics SET down = ? WHERE email = ? AND inbound_id = ?", (new_download_bytes, email, inbound_id))
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Failed to update download for '{email}' in {db_path.name}: {e}")
    finally:
        conn.close()

def send_telegram_message(user_id: str, message: str):
    """Sends a message to a user via the Telegram Bot API."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set. Cannot send message.")
        return
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': user_id,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        response = requests.post(url, data=payload)
        response.raise_for_status()
        if not response.json().get('ok'):
            logger.error(f"Telegram API error for user {user_id}: {response.text}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send message to user {user_id}: {e}")

# ==============================================================================
# CRON JOB TASKS
# ==============================================================================

def regenerate_all_subscriptions(config: dict, users_db: dict):
    """Regenerates all subscription files."""
    logger.info("Task: Regenerating all subscription files...")
    count = 0
    sub_dir = Path(config['subscription']['uri'])
    sub_dir.mkdir(exist_ok=True)
    
    for telegram_id, user_data in users_db.items():
        subscription_id = user_data.get("subscription")
        if not subscription_id:
            continue
            
        all_vless_links = []
        for db_path_str, inbound_id in config['db'].items():
            link = get_config(Path(db_path_str), inbound_id, telegram_id)
            if link:
                all_vless_links.append(link)
        
        if all_vless_links:
            encoded_content = base64.b64encode("\n".join(all_vless_links).encode('utf-8')).decode('utf-8')
            (sub_dir / subscription_id).write_text(encoded_content)
            count += 1
    logger.info(f"Finished regenerating {count} subscription files.")


def check_quotas_and_warn(config: dict, users_db: dict) -> dict:
    """Checks quotas, sends warnings, and enforces limits. Returns the updated users_db."""
    logger.info("Task: Checking quotas and sending warnings...")
    users_updated = 0
    
    for telegram_id, user_data in users_db.items():
        sum_of_down, total_quota = 0, 0
        
        for db_path_str, inbound_id in config['db'].items():
            status = get_status(Path(db_path_str), inbound_id, telegram_id)
            if status:
                sum_of_down += status[0]
                if total_quota == 0: total_quota = status[1]
        
        if total_quota > 0:
            usage_percent = (sum_of_down / total_quota) * 100
            warning_sent = user_data.get('quota_warning_sent', False)
            
            # 1. Check if warning needs to be sent
            if usage_percent >= WARNING_THRESHOLD and not warning_sent:
                logger.info(f"User {telegram_id} has reached {usage_percent:.2f}% of quota. Sending warning.")
                lang = user_data.get("language", "en")
                message = config['quota_warning'].get(lang, config['quota_warning']['en']).format(
                    used_gb=f"{(sum_of_down / GB_TO_BYTES):.2f}",
                    total_gb=f"{(total_quota / GB_TO_BYTES):.2f}"
                )
                send_telegram_message(telegram_id, message)
                users_db[telegram_id]['quota_warning_sent'] = True
                users_updated += 1

            # 2. Check if the warning flag needs to be reset
            elif usage_percent < WARNING_THRESHOLD and warning_sent:
                logger.info(f"User {telegram_id}'s quota has been reset. Resetting warning flag.")
                users_db[telegram_id]['quota_warning_sent'] = False
                users_updated += 1

            # 3. Enforce the limit if quota is exceeded
            if sum_of_down > total_quota:
                logger.warning(f"User {telegram_id} exceeded quota! Enforcing limit.")
                for db_path_str, inbound_id in config['db'].items():
                    update_user_download(Path(db_path_str), inbound_id, telegram_id, sum_of_down)

    logger.info(f"Finished quota checks. {users_updated} user(s) had their warning status updated.")
    return users_db

# ==============================================================================
# MAIN LOOP
# ==============================================================================

def main():
    """Main loop to run cron jobs."""
    if not BOT_TOKEN:
        logger.critical("FATAL: BOT_TOKEN environment variable not set. Cannot send warnings. Exiting.")
        return

    logger.info("Cron job script started. Running tasks every 60 seconds.")
    while True:
        try:
            print("-" * 50)
            logger.info(f"Starting cron run at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            config = load_yaml(CONFIG_FILE).get("settings", {})
            users_db = load_yaml(USER_DB_FILE)
            
            if not config or not users_db:
                logger.warning("Config or users file is empty. Skipping run.")
            else:
                regenerate_all_subscriptions(config, users_db)
                # This function now returns the potentially modified users_db
                users_db = check_quotas_and_warn(config, users_db)
                # Save any changes made to the users_db (like setting the warning flag)
                save_yaml(users_db, USER_DB_FILE)

            logger.info("Cron run finished.")
            time.sleep(60)
            
        except Exception as e:
            logger.error(f"An unexpected error occurred in the main loop: {e}", exc_info=True)
            time.sleep(60)

if __name__ == "__main__":
    main()