import os
from dotenv import load_dotenv

# Load .env file (optional, for local development)
load_dotenv()

# ---------- TELEGRAM BOT ----------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN environment variable is missing!")

OWNER_ID = int(os.getenv("OWNER_ID", "8104850843"))

# ---------- RENDER WEB SERVICE URL ----------
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
if not RENDER_URL:
    # Fallback for local testing
    RENDER_URL = "http://localhost:8080"

PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_URL = f"{RENDER_URL}/webhook"

# ---------- ORIGINAL API (जिसका प्रॉक्सी बना रहे हो) ----------
ORIGINAL_API_URL = os.getenv("ORIGINAL_API_URL", "https://ayaanmods.site/number.php")
ORIGINAL_API_KEY = os.getenv("ORIGINAL_API_KEY", "annonymous")

# ---------- BRANDING REMOVAL (strings/keys to completely remove) ----------
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

# ---------- YOUR BRANDING (जो तुम दिखाना चाहते हो) ----------
BRANDING = {
    "developer": os.getenv("BRANDING_DEVELOPER", "@Nullprotocol_x"),
    "powered_by": os.getenv("BRANDING_POWERED", "NULL PROTOCOL")
}

# ---------- CACHE ----------
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))   # 5 minutes

# ---------- DATABASE ----------
DB_FILE = os.getenv("DB_FILE", "bot.db")

# ---------- RATE LIMITS (default) ----------
DEFAULT_RATE_LIMIT_PER_MIN = int(os.getenv("DEFAULT_RATE_LIMIT", "60"))

# ---------- SAFETY CHECK (optional warning for missing URL) ----------
if "your-app-name" in RENDER_URL and "localhost" not in RENDER_URL:
    print("⚠️ Warning: RENDER_EXTERNAL_URL seems to be a placeholder. Update it in Render environment variables.")
