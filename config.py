"""
Reddit Scraper Suite - Configuration
"""
import os
from pathlib import Path

# Load environment variables from .env file if it exists
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

# --- PATHS ---
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "reddit_scraper.db"

# --- SCRAPER SETTINGS ---
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Sources: old.reddit.com for residential IPs, mirrors for data centers
MIRRORS = [
    "https://old.reddit.com",
    "https://redlib.privadency.com",
    "https://redlib.orangenet.cc",
    "https://red.artemislena.eu"
]

# Rate limiting
REQUEST_TIMEOUT = 15
COOLDOWN_SECONDS = 3
RETRY_WAIT = 30

# Media settings
MAX_IMAGES_PER_POST = 10
MAX_VIDEOS_PER_POST = 2
MAX_GALLERY_IMAGES = 15

# Comment settings
MAX_COMMENT_DEPTH = 5

# --- ASYNC SETTINGS ---
ASYNC_MAX_CONCURRENT = 10
ASYNC_BATCH_SIZE = 50

# --- NOTIFICATION SETTINGS ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- DASHBOARD SETTINGS ---
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 8501

# --- SCHEDULER SETTINGS ---
SCHEDULER_TIMEZONE = "Asia/Kolkata"

# --- PROXY SETTINGS ---
# Generic proxy URL (e.g. http://username:password@host:port)
PROXY_URL = os.getenv("PROXY_URL", "")
PROXY_COUNTRY = os.getenv("PROXY_COUNTRY", "")
PROXY_SESSION_ID = os.getenv("PROXY_SESSION_ID", "")
PROXY_AUTO_ROTATE = os.getenv("PROXY_AUTO_ROTATE", "true").lower() in ("true", "1", "yes")

def get_formatted_proxy_url(proxy_url, country=None, session_id=None, force_rotate=False):
    """
    Format ScrapingAnt proxy URL to append country and session ID dynamically.
    For standard proxies, returns the URL unchanged.
    """
    if not proxy_url:
        return proxy_url
    
    import random
    import string
    from urllib.parse import urlparse, urlunparse
    
    try:
        parsed = urlparse(proxy_url)
        username = parsed.username
        
        if not username or not username.startswith("customer-"):
            return proxy_url  # Not ScrapingAnt proxy
            
        # Parse username parts (format: customer-USERNAME[-country-cc][-sessionid-id])
        parts = username.split("-")
        
        # Determine target country
        target_country = country if country is not None else PROXY_COUNTRY
        if target_country and target_country.lower() == "none":
            target_country = ""
            
        # Determine target session ID
        target_session = session_id if session_id is not None else PROXY_SESSION_ID
        if target_session and target_session.lower() == "auto":
            target_session = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        elif target_session and target_session.lower() == "none":
            target_session = ""
            
        # Build new username
        # Keep customer-USERNAME part (typically parts[0] is 'customer' and parts[1] is the username)
        new_username_parts = parts[:2] if len(parts) >= 2 else parts
        
        # Handle country code
        # Check if country was in original username and not overwritten
        original_country = ""
        original_session = ""
        
        i = 2
        while i < len(parts):
            if parts[i] == "country" and i + 1 < len(parts):
                original_country = parts[i+1]
                i += 2
            elif parts[i] == "sessionid" and i + 1 < len(parts):
                original_session = parts[i+1]
                i += 2
            else:
                new_username_parts.append(parts[i])
                i += 1
                
        # Apply country override or retain original if not specified
        final_country = target_country if target_country is not None else original_country
        if final_country:
            new_username_parts.extend(["country", final_country.lower()])
            
        # Apply session ID override or retain original if not specified
        final_session = target_session if target_session is not None else original_session
        if final_session:
            new_username_parts.extend(["sessionid", final_session])
            
        new_username = "-".join(new_username_parts)
        
        # Reconstruct network location
        netloc = f"{new_username}:{parsed.password}@{parsed.hostname}"
        if parsed.port:
            netloc += f":{parsed.port}"
            
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    except Exception:
        return proxy_url

# --- REDDIT OFFICIAL API (OAuth) ---
# Register an app at https://www.reddit.com/prefs/apps. When CLIENT_ID and
# CLIENT_SECRET are set, the scraper uses the official API (oauth.reddit.com)
# instead of the public mirrors. Provide USERNAME/PASSWORD for a "script" app,
# or leave them blank for app-only (client_credentials) read access.
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD", "")
# Reddit requires a unique, descriptive User-Agent for API access.
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "python:reddit-universal-scraper:v1.0 (by /u/unknown)")

# --- DATABASE SETTINGS ---
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# Ensure data directory exists
DATA_DIR.mkdir(exist_ok=True)
