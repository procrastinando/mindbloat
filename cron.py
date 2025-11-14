import logging
import time
import yaml
import base64
import json
import requests
import os
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode
from typing import Optional, Dict
from datetime import datetime, timedelta

# --- Configuration & Constants ---
CONFIG_FILE = Path("database.yaml")
USER_DB_FILE = Path("users.yaml")
GB_TO_BYTES = 1024**3
DAYS_TO_MS = 24 * 60 * 60 * 1000

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def load_yaml(file_path: Path) -> dict:
    if not file_path.exists(): return {}
    with open(file_path, "r", encoding='utf-8') as f:
        data = yaml.safe_load(f)
        return data if data is not None else {}

def format_timedelta(delta: timedelta) -> str:
    """Formats a timedelta object into a 'Xd Yh Zm' string."""
    if delta.total_seconds() < 0: return "Passed"
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{days}d {hours}h {minutes}m"

def calculate_next_reset_time(last_reset_ms: int, interval: str) -> Optional[datetime]:
    """Calculates the next reset datetime based on the last reset time and interval."""
    if interval == "never" or last_reset_ms == 0:
        return None
        
    last_reset_dt = datetime.fromtimestamp(last_reset_ms / 1000)
    
    if interval == "daily":
        return last_reset_dt + timedelta(days=1)
    elif interval == "weekly":
        return last_reset_dt + timedelta(days=7)
    elif interval == "monthly":
        year, month = last_reset_dt.year, last_reset_dt.month
        month += 1
        if month > 12:
            month = 1
            year += 1
        
        try:
            return last_reset_dt.replace(year=year, month=month)
        except ValueError:
            next_month_first_day = last_reset_dt.replace(year=year, month=month, day=1)
            return next_month_first_day - timedelta(days=1)
    return None

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
        if not server_address: return None
        params = {"encryption": "none"}
        security_type = stream_settings.get("security")
        params["security"] = security_type if security_type != "none" else ""
        if security_type == "reality":
            reality_settings = stream_settings.get("realitySettings", {})
            nested_settings = reality_settings.get("settings", {})
            params["pbk"] = nested_settings.get("publicKey"); params["fp"] = nested_settings.get("fingerprint")
            params["sni"] = reality_settings.get("serverNames", [""])[0]; params["sid"] = reality_settings.get("shortIds", [""])[0]
            params["spx"] = nested_settings.get("spiderX")
        elif security_type == "tls":
            tls_settings = stream_settings.get("tlsSettings", {})
            nested_settings = tls_settings.get("settings", {})
            params["sni"] = tls_settings.get("serverName"); params["fp"] = nested_settings.get("fingerprint")
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
            grpc_settings = stream_settings.get("grpcSettings", {}); params["serviceName"] = grpc_settings.get("serviceName")
        elif network_type == "http":
            http_settings = stream_settings.get("httpSettings", {}); params["path"] = http_settings.get("path")
            host_list = http_settings.get("host", [])
            if host_list: params["host"] = host_list[0]
        elif network_type == "xhttp":
            xhttp_settings = stream_settings.get("xhttpSettings", {})
            params["path"] = xhttp_settings.get("path"); params["mode"] = xhttp_settings.get("mode")
            host = xhttp_settings.get("host", "")
            if host: params["host"] = host
        params = {k: v for k, v in params.items() if v is not None and v != ""}
        base_url = f"vless://{client_data['id']}@{server_address}:{port}"
        query_string = urlencode(params, quote_via=quote)
        config_name = inbound_data.get('remark', f"Config-{email.split('#')[0]}")
        return f"{base_url}?{query_string}#{quote(config_name)}"
    except Exception as e:
        logger.error(f"Failed to reconstruct config for email {email}: {e}", exc_info=True)
        return None

# ==============================================================================
# 3X-UI API WRAPPER CLASS (EXPANDED)
# ==============================================================================

class XUIApi:
    def __init__(self, address: str, panel_path: str, username: str, password: str):
        self.base_url = f"{address.rstrip('/')}{panel_path}"
        self.session = requests.Session()
        self.session.headers.update({'Accept': 'application/json'})
        self.logged_in = self._login(username, password)

    def _login(self, username, password):
        try:
            response = self.session.post(f"{self.base_url}login", data={'username': username, 'password': password}, verify=False)
            response.raise_for_status()
            return response.json().get('success')
        except requests.exceptions.RequestException: return False

    def get_inbound(self, inbound_id: int) -> Optional[dict]:
        if not self.logged_in: return None
        try:
            response = self.session.get(f"{self.base_url}panel/api/inbounds/get/{inbound_id}", verify=False)
            response.raise_for_status()
            data = response.json()
            return data.get('obj') if data.get('success') else None
        except requests.exceptions.RequestException: return None
    
    def add_client(self, inbound_id: int, client_settings: dict) -> bool:
        if not self.logged_in: return False
        try:
            payload = {'id': inbound_id, 'settings': json.dumps({"clients": [client_settings]})}
            response = self.session.post(f"{self.base_url}panel/api/inbounds/addClient", data=payload, verify=False)
            response.raise_for_status()
            data = response.json()
            if data.get('success'):
                logger.info(f"API: Successfully ADDED client {client_settings['email']} to inbound {inbound_id}")
                return True
            logger.error(f"API Error adding client: {data.get('msg')}")
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"API Error adding client: {e}")
            return False

    def get_client_traffics(self, email: str) -> Optional[dict]:
        if not self.logged_in: return None
        try:
            encoded_email = quote(email)
            response = self.session.get(f"{self.base_url}panel/api/inbounds/getClientTraffics/{encoded_email}", verify=False)
            response.raise_for_status()
            data = response.json()
            return data.get('obj') if data.get('success') else None
        except requests.exceptions.RequestException: return None


# ==============================================================================
# CORE SYNC LOGIC
# ==============================================================================

def get_or_create_client(api: XUIApi, inbound_data: Dict, client_email: str, user_quota: float, defaults: Dict) -> Optional[Dict]:
    settings = json.loads(inbound_data.get("settings", "{}"))
    existing_client = next((c for c in settings.get("clients", []) if c.get("email") == client_email), None)
    if existing_client: return existing_client

    logger.info(f"Client '{client_email}' not found in inbound {inbound_data['id']}. Creating now...")
    now_ms = int(time.time() * 1000)
    expiry_ms = now_ms + (int(defaults['duration_days']) * DAYS_TO_MS)
    total_bytes = int(user_quota * GB_TO_BYTES)

    new_client_payload = {
        "id": str(uuid.uuid4()), "email": client_email, "enable": True,
        "tgId": client_email.split('#')[0], "totalGB": total_bytes, "expiryTime": expiry_ms,
        "subId": str(uuid.uuid4().hex)[:16], "reset": 0, "flow": "", "limitIp": 0
    }
    
    if api.add_client(inbound_data['id'], new_client_payload):
        return new_client_payload
    else:
        logger.error(f"Failed to create client '{client_email}' on inbound {inbound_data['id']}.")
        return None

def sync_all_subscriptions(config: dict, users_db: dict):
    logger.info("Task: Synchronizing all user subscriptions...")
    count = 0
    sub_dir_path = Path(config['subscription'].get('uri', 'sub'))
    sub_dir_path.mkdir(exist_ok=True)
    defaults = {k.strip(): v.strip() for k, v in (item.split('=') for item in config['default'])}

    apis = {s_name: XUIApi(s_conf['address'], s_conf['panel_path'], config['subscription']['user'], config['subscription']['password']) for s_name, s_conf in config['db'].items()}

    for user_id, user_data in users_db.items():
        subscription_id = user_data.get("subscription")
        if not subscription_id: continue

        total_used_bytes, master_expiry_time = 0, 0
        user_total_gb = user_data.get('quota', float(defaults['total_gb']))
        next_reset_time_to_display = None

        for server_name, server_config in config['db'].items():
            api = apis.get(server_name)
            if not (api and api.logged_in): continue
            inbounds = server_config.get('inbound', [])
            if not isinstance(inbounds, list): inbounds = [inbounds]
            for inbound_id in inbounds:
                traffic_data = api.get_client_traffics(f"{user_id}#{inbound_id}")
                if traffic_data:
                    # --- FIX: Sum both UP and DOWN traffic ---
                    total_used_bytes += traffic_data.get('up', 0) + traffic_data.get('down', 0)
                    if master_expiry_time == 0: master_expiry_time = traffic_data.get('expiryTime', 0)
                
                inbound_details = api.get_inbound(inbound_id)
                if inbound_details:
                    next_reset = calculate_next_reset_time(inbound_details.get('lastTrafficResetTime', 0), inbound_details.get('trafficReset', 'never'))
                    if next_reset and (next_reset_time_to_display is None or next_reset < next_reset_time_to_display):
                        next_reset_time_to_display = next_reset
        
        used_gb = total_used_bytes / GB_TO_BYTES
        remaining_gb = max(0, user_total_gb - used_gb)
        
        expiry_delta = timedelta(milliseconds=(master_expiry_time - (time.time() * 1000))) if master_expiry_time > 0 else timedelta(days=9999)
        time_left_str = format_timedelta(expiry_delta)
        
        dummy_name = f"🌐 {remaining_gb:.2f}/{user_total_gb:.2f} GB"
        if next_reset_time_to_display:
            reset_delta = next_reset_time_to_display - datetime.now()
            dummy_name += f" 🔁 {format_timedelta(reset_delta)}"
        dummy_name += f" ⏳ {time_left_str}"

        dummy_link = f"vless://00000000-0000-0000-0000-000000000000@1.1.1.1:1?type=ws#{quote(dummy_name)}"
        all_vless_links_for_user = [dummy_link]

        for server_name, server_config in config['db'].items():
            api = apis.get(server_name)
            if not (api and api.logged_in): continue
            inbounds = server_config.get('inbound', [])
            if not isinstance(inbounds, list): inbounds = [inbounds]
            for inbound_id in inbounds:
                inbound_data = api.get_inbound(inbound_id)
                if not inbound_data: continue
                client_email = f"{user_id}#{inbound_id}"
                
                client_info = get_or_create_client(api, inbound_data, client_email, user_total_gb, defaults)
                
                if client_info:
                    fresh_inbound_data = api.get_inbound(inbound_id)
                    if fresh_inbound_data:
                        link = get_config_from_api(fresh_inbound_data, client_email)
                        if link: all_vless_links_for_user.append(link)

        subscription_file_path = sub_dir_path / subscription_id
        combined_links = "\n".join(all_vless_links_for_user)
        encoded_content = base64.b64encode(combined_links.encode('utf-8')).decode('utf-8')
        subscription_file_path.write_text(encoded_content, encoding='utf-8')
        if len(all_vless_links_for_user) > 1: count += 1

    logger.info(f"Finished synchronizing {count} subscription files.")


# ==============================================================================
# MAIN LOOP
# ==============================================================================

def main():
    sleep_interval = 120
    logger.info(f"Cron job script started. Synchronizing subscriptions every {sleep_interval} seconds.")
    
    while True:
        try:
            print("-" * 50)
            logger.info("Starting sync run...")
            config = load_yaml(CONFIG_FILE).get("settings", {})
            users_db = load_yaml(USER_DB_FILE)
            if not config or 'db' not in config or not users_db:
                logger.warning("Config ('settings' or 'db' section) or users file is empty or invalid. Skipping run.")
            else:
                sync_all_subscriptions(config, users_db)
            logger.info(f"Sync run finished. Sleeping for {sleep_interval} seconds.")
            time.sleep(sleep_interval)
        except Exception as e:
            logger.error(f"An unexpected error occurred in the main loop: {e}", exc_info=True)
            time.sleep(sleep_interval)

if __name__ == "__main__":
    requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
    main()
