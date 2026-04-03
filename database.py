import sqlite3
import secrets
from datetime import datetime, timedelta
from config import DB_FILE

# ---------- DATABASE CONNECTION (thread-safe for Flask) ----------
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()

# ---------- CREATE TABLES (if not exist) ----------
c.execute('''CREATE TABLE IF NOT EXISTS users
             (user_id INTEGER PRIMARY KEY,
              username TEXT,
              first_name TEXT,
              last_name TEXT,
              is_banned INTEGER DEFAULT 0,
              is_owner INTEGER DEFAULT 0,
              joined_at TEXT)''')

c.execute('''CREATE TABLE IF NOT EXISTS api_keys
             (key TEXT PRIMARY KEY,
              created_by INTEGER,
              created_at TEXT,
              expires_at TEXT,
              rate_limit_per_min INTEGER DEFAULT 60,
              is_active INTEGER DEFAULT 1,
              custom_name TEXT)''')
conn.commit()

# ---------- USER HELPER FUNCTIONS ----------
def get_user(user_id, username="", first_name="", last_name=""):
    """Get or create user, return dict"""
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT INTO users (user_id, username, first_name, last_name, joined_at) VALUES (?,?,?,?,?)",
                  (user_id, username, first_name, last_name, datetime.now().isoformat()))
        conn.commit()
        return {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "is_banned": 0,
            "is_owner": 0
        }
    return {
        "user_id": user[0],
        "username": user[1],
        "first_name": user[2],
        "last_name": user[3],
        "is_banned": user[4],
        "is_owner": user[5]
    }

def is_admin(user_id, owner_id):
    """Check if user is admin (owner or added owner)"""
    if user_id == owner_id:
        return True
    c.execute("SELECT is_owner FROM users WHERE user_id=?", (user_id,))
    res = c.fetchone()
    return res is not None and res[0] == 1

def get_all_users(offset=0, limit=15):
    """Get paginated user list"""
    c.execute("SELECT * FROM users ORDER BY joined_at LIMIT ? OFFSET ?", (limit, offset))
    return c.fetchall()

def count_users():
    """Total number of users"""
    c.execute("SELECT COUNT(*) FROM users")
    return c.fetchone()[0]

def toggle_ban(user_id):
    """Toggle user ban status"""
    c.execute("UPDATE users SET is_banned = NOT is_banned WHERE user_id=?", (user_id,))
    conn.commit()

def set_owner(user_id, is_owner):
    """Set owner status (True/False)"""
    c.execute("UPDATE users SET is_owner = ? WHERE user_id=?", (1 if is_owner else 0, user_id))
    conn.commit()

# ---------- API KEY HELPER FUNCTIONS ----------
def generate_random_key():
    """Generate key like ak_<32 hex chars>"""
    return f"ak_{secrets.token_hex(16)}"

def create_api_key(key, created_by, expires_days=30, rate_limit=60, custom_name=""):
    """Create or replace an API key"""
    expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()
    c.execute("INSERT OR REPLACE INTO api_keys (key, created_by, created_at, expires_at, rate_limit_per_min, is_active, custom_name) VALUES (?,?,?,?,?,?,?)",
              (key, created_by, datetime.now().isoformat(), expires_at, rate_limit, 1, custom_name))
    conn.commit()

def validate_api_key(key):
    """Validate key: returns (valid, created_by, rate_limit)"""
    c.execute("SELECT created_by, expires_at, rate_limit_per_min, is_active FROM api_keys WHERE key=?", (key,))
    row = c.fetchone()
    if not row:
        return False, None, None
    created_by, expires_at, rate_limit, is_active = row
    if not is_active or datetime.now() > datetime.fromisoformat(expires_at):
        return False, None, None
    return True, created_by, rate_limit

def list_api_keys(created_by=None):
    """List all keys or keys of a specific user"""
    if created_by:
        c.execute("SELECT key, expires_at, rate_limit_per_min, custom_name, is_active FROM api_keys WHERE created_by=?", (created_by,))
    else:
        c.execute("SELECT key, expires_at, rate_limit_per_min, custom_name, is_active, created_by FROM api_keys")
    return c.fetchall()

def delete_api_key(key):
    """Delete an API key"""
    c.execute("DELETE FROM api_keys WHERE key=?", (key,))
    conn.commit()

def toggle_api_key_status(key):
    """Activate/deactivate a key"""
    c.execute("UPDATE api_keys SET is_active = NOT is_active WHERE key=?", (key,))
    conn.commit()

# ---------- DATABASE CLEANUP (optional, for graceful shutdown) ----------
def close_db():
    conn.close()
