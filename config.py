import os
from dotenv import load_dotenv

load_dotenv()

# ---------- TELEGRAM BOT ----------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN environment variable is missing!")

OWNER_ID = int(os.getenv("OWNER_ID", "8104850843"))

# ---------- RENDER URL (same as your app) ----------
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://proxyapi-89pj.onrender.com")
PORT = int(os.getenv("PORT", "8080"))

# ---------- ORIGINAL API ----------
ORIGINAL_API_URL = os.getenv("ORIGINAL_API_URL", "https://ayaanmods.site/number.php")
ORIGINAL_API_KEY = os.getenv("ORIGINAL_API_KEY", "annonymous")

# ---------- BRANDING REMOVAL (keys to completely remove) ----------
BLACKLIST_KEYS = [
    "channel_name",
    "copyright",
    "signature",
    "owner",
    "channel_link",
    "branding",
    "credit",
    "source"
]

# ---------- YOUR BRANDING ----------
BRANDING = {
    "developer": os.getenv("BRANDING_DEVELOPER", "@Nullprotocol_x"),
    "powered_by": os.getenv("BRANDING_POWERED", "NULL PROTOCOL")
}

# ---------- CACHE ----------
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))

# ---------- DATABASE ----------
DB_FILE = os.getenv("DB_FILE", "bot.db")

# ---------- RATE LIMITS ----------
DEFAULT_RATE_LIMIT_PER_MIN = int(os.getenv("DEFAULT_RATE_LIMIT", "60"))

print("✅ Config loaded successfully.")
