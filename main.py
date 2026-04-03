import os
import json
import sqlite3
import threading
import time
import secrets
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

load_dotenv()

# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", 8080))
WEBHOOK_URL = f"{RENDER_URL}/webhook"

ORIGINAL_API_URL = os.getenv("ORIGINAL_API_URL")
ORIGINAL_API_KEY = os.getenv("ORIGINAL_API_KEY")
BLACKLIST_KEYS = [k.strip() for k in os.getenv("BLACKLIST_KEYS", "").split(",")]

# Cache for proxy (fast)
cache = {}
CACHE_TTL = 300

# ---------- DATABASE ----------
conn = sqlite3.connect("bot.db", check_same_thread=False)
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users
             (user_id INTEGER PRIMARY KEY,
              username TEXT, first_name TEXT, last_name TEXT,
              is_banned INTEGER DEFAULT 0,
              is_owner INTEGER DEFAULT 0,
              joined_at TEXT)''')
c.execute('''CREATE TABLE IF NOT EXISTS api_keys
             (key TEXT PRIMARY KEY,
              created_by INTEGER,
              created_at TEXT,
              expires_at TEXT,
              rate_limit_per_min INTEGER DEFAULT 60,
              allowed_endpoints TEXT DEFAULT '*',
              is_active INTEGER DEFAULT 1,
              custom_name TEXT)''')
conn.commit()

# ---------- USER HELPERS ----------
def get_user(user_id, username="", first_name="", last_name=""):
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT INTO users (user_id, username, first_name, last_name, joined_at) VALUES (?,?,?,?,?)",
                  (user_id, username, first_name, last_name, datetime.now().isoformat()))
        conn.commit()
        return {"user_id": user_id, "username": username, "first_name": first_name, "last_name": last_name, "is_banned": 0, "is_owner": 0}
    return {"user_id": user[0], "username": user[1], "first_name": user[2], "last_name": user[3], "is_banned": user[4], "is_owner": user[5]}

def is_admin(user_id):
    if user_id == OWNER_ID:
        return True
    c.execute("SELECT is_owner FROM users WHERE user_id=?", (user_id,))
    res = c.fetchone()
    return res and res[0] == 1

def get_all_users(offset=0, limit=15):
    c.execute("SELECT * FROM users ORDER BY joined_at LIMIT ? OFFSET ?", (limit, offset))
    return c.fetchall()

def count_users():
    c.execute("SELECT COUNT(*) FROM users")
    return c.fetchone()[0]

def toggle_ban(user_id):
    c.execute("UPDATE users SET is_banned = NOT is_banned WHERE user_id=?", (user_id,))
    conn.commit()

def set_owner(user_id, is_owner):
    c.execute("UPDATE users SET is_owner = ? WHERE user_id=?", (1 if is_owner else 0, user_id))
    conn.commit()

# ---------- API KEY HELPERS ----------
def generate_random_key():
    return f"ak_{secrets.token_hex(16)}"

def create_api_key(key, created_by, expires_days=30, rate_limit=60, allowed_endpoints="*", custom_name=""):
    expires_at = (datetime.now() + timedelta(days=expires_days)).isoformat()
    c.execute("INSERT OR REPLACE INTO api_keys (key, created_by, created_at, expires_at, rate_limit_per_min, allowed_endpoints, is_active, custom_name) VALUES (?,?,?,?,?,?,?,?)",
              (key, created_by, datetime.now().isoformat(), expires_at, rate_limit, allowed_endpoints, 1, custom_name))
    conn.commit()

def validate_api_key(key):
    c.execute("SELECT created_by, expires_at, rate_limit_per_min, is_active FROM api_keys WHERE key=?", (key,))
    row = c.fetchone()
    if not row:
        return False, None, None
    created_by, expires_at, rate_limit, is_active = row
    if not is_active or datetime.now() > datetime.fromisoformat(expires_at):
        return False, None, None
    return True, created_by, rate_limit

def list_api_keys(created_by=None):
    if created_by:
        c.execute("SELECT key, expires_at, rate_limit_per_min, custom_name, is_active FROM api_keys WHERE created_by=?", (created_by,))
    else:
        c.execute("SELECT key, expires_at, rate_limit_per_min, custom_name, is_active, created_by FROM api_keys")
    return c.fetchall()

def delete_api_key(key):
    c.execute("DELETE FROM api_keys WHERE key=?", (key,))
    conn.commit()

def toggle_api_key_status(key):
    c.execute("UPDATE api_keys SET is_active = NOT is_active WHERE key=?", (key,))
    conn.commit()

# ---------- PROXY API (FAST) ----------
app = Flask(__name__)

def remove_branding(data):
    if isinstance(data, str):
        for term in BLACKLIST_KEYS:
            if term.lower() in data.lower():
                return ""
        return data
    if isinstance(data, list):
        return [remove_branding(item) for item in data if remove_branding(item) != ""]
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            skip = False
            for term in BLACKLIST_KEYS:
                if term.lower() in k.lower():
                    skip = True
                    break
            if skip:
                continue
            cleaned_val = remove_branding(v)
            if cleaned_val != "" and cleaned_val is not None:
                cleaned[k] = cleaned_val
        return cleaned
    return data

@app.route('/api/v1')
def proxy_api():
    key = request.args.get('key')
    number = request.args.get('number')
    if not key or not number:
        return jsonify({"error": "Missing key or number parameter"}), 400
    valid, _, _ = validate_api_key(key)
    if not valid:
        return jsonify({"error": "Invalid or expired API key"}), 403
    cache_key = f"num_{number}"
    now = time.time()
    if cache_key in cache and (now - cache[cache_key]['ts']) < CACHE_TTL:
        return app.response_class(response=cache[cache_key]['data'], status=200, mimetype='application/json')
    try:
        url = f"{ORIGINAL_API_URL}?key={ORIGINAL_API_KEY}&number={number}"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        cleaned = remove_branding(data)
        cleaned["branding"] = {
            "developer": os.getenv("BRANDING_DEVELOPER"),
            "powered_by": os.getenv("BRANDING_POWERED")
        }
        pretty_json = json.dumps(cleaned, indent=2, ensure_ascii=False)
        cache[cache_key] = {'data': pretty_json, 'ts': now}
        return app.response_class(response=pretty_json, status=200, mimetype='application/json')
    except Exception as e:
        return jsonify({"error": str(e)}), 502

@app.route('/health')
def health():
    return jsonify({"status": "ok"})

# ---------- TELEGRAM BOT SETUP ----------
application = Application.builder().token(BOT_TOKEN).build()

async def send_message(chat_id, text, reply_markup=None):
    await application.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup)

# ---------- BOT COMMANDS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.username, user.first_name, user.last_name)
    text = (f"✨ <b>Hello {user.first_name}!</b> ✨\n\n"
            "Welcome to <b>NumberInfo API</b> – premium phone number intelligence.\n\n"
            "🔑 <b>Get API Access:</b>\n"
            "/genkey – Generate a free random API key (30 days)\n"
            "/mykeys – View your active keys\n"
            "/apihelp – How to use the API\n\n"
            "🛠 <b>Admin Commands</b> (owners only):\n"
            "/admin – Full control panel\n"
            "/customkey – Create custom API key with expiry\n\n"
            "💡 <i>Support: @Nullprotocol_x</i>")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Generate Key", callback_data="genkey")],
        [InlineKeyboardButton("📘 API Docs", callback_data="apihelp")],
        [InlineKeyboardButton("👤 My Keys", callback_data="mykeys")]
    ])
    await send_message(user.id, text, reply_markup=keyboard)

async def genkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    new_key = generate_random_key()
    create_api_key(new_key, user_id, expires_days=30, rate_limit=60, custom_name="Free Trial")
    text = (f"✅ <b>API Key Generated!</b>\n\n<code>{new_key}</code>\n\n"
            f"🔹 <b>Usage:</b>\n<code>{RENDER_URL}/api/v1?key={new_key}&number=9876543210</code>\n\n"
            "⏳ Expires: 30 days\n⚡ Rate: 60/min\n\nUse /mykeys to see all your keys.")
    await send_message(user_id, text)

async def mykeys_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keys = list_api_keys(created_by=user_id)
    if not keys:
        await send_message(user_id, "No API keys. Use /genkey to create one.")
        return
    text = "<b>🔑 Your API Keys</b>\n\n"
    for k, expires, rate, name, active in keys:
        status = "✅ Active" if active else "❌ Inactive"
        text += f"<b>{name or 'Key'}</b> (<code>{k[:20]}...</code>)\n   Expires: {expires[:10]}\n   Rate: {rate}/min | {status}\n\n"
    await send_message(user_id, text)

async def apihelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (f"📘 <b>API Documentation</b>\n\n"
            f"<b>Endpoint:</b>\n<code>{RENDER_URL}/api/v1?key=YOUR_KEY&number=9876543210</code>\n\n"
            "<b>Parameters:</b>\n• key – Your API key\n• number – 10-digit number\n\n"
            "<b>Response:</b> JSON with number info + branding\n\n"
            "<b>Rate Limits:</b> 60 requests per minute\n<b>Support:</b> @Nullprotocol_x")
    await send_message(update.effective_user.id, text)

# ---------- ADMIN: CUSTOM KEY ----------
async def customkey_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await send_message(user_id, "⛔ Admin only.")
        return
    context.user_data['custom_key_step'] = 'awaiting_key'
    await send_message(user_id, "🔧 <b>Create Custom API Key</b>\n\nSend desired key (e.g., <code>my_super_key</code>).\nType <code>cancel</code> to abort.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_custom")]]))

async def cancel_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop('custom_key_step', None)
    await query.edit_message_text("Cancelled.")
    await admin_panel(update, context)

async def handle_custom_key_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get('custom_key_step')
    if not step:
        return
    user_id = update.effective_user.id
    text = update.message.text.strip()
    if text.lower() == 'cancel':
        context.user_data.pop('custom_key_step', None)
        await send_message(user_id, "Cancelled.")
        return
    if step == 'awaiting_key':
        context.user_data['custom_key'] = text
        context.user_data['custom_key_step'] = 'awaiting_expiry'
        await send_message(user_id, "Enter expiry in <b>days</b> (e.g., 30) or 'never'.")
    elif step == 'awaiting_expiry':
        expiry_days = 3650 if text.lower() == 'never' else int(text)
        context.user_data['custom_expiry_days'] = expiry_days
        context.user_data['custom_key_step'] = 'awaiting_ratelimit'
        await send_message(user_id, "Enter rate limit (requests per minute). Default 60.")
    elif step == 'awaiting_ratelimit':
        rate = int(text) if text.isdigit() else 60
        context.user_data['custom_ratelimit'] = rate
        context.user_data['custom_key_step'] = 'awaiting_name'
        await send_message(user_id, "Enter a custom name/label for this key (e.g., 'VIP Plan').")
    elif step == 'awaiting_name':
        name = text
        key = context.user_data['custom_key']
        expiry_days = context.user_data['custom_expiry_days']
        rate = context.user_data['custom_ratelimit']
        create_api_key(key, user_id, expires_days=expiry_days, rate_limit=rate, custom_name=name)
        await send_message(user_id, f"✅ Custom key <code>{key}</code> created.\nExpires in {expiry_days} days.\nRate: {rate}/min")
        context.user_data.pop('custom_key_step', None)
        await admin_panel(update, context)

# ---------- ADMIN PANEL ----------
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await send_message(user_id, "⛔ Access Denied.")
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("👥 Users", callback_data="admin_userlist_0")],
        [InlineKeyboardButton("🚫 Ban/Unban", callback_data="admin_ban")],
        [InlineKeyboardButton("👑 Owners", callback_data="admin_owners")],
        [InlineKeyboardButton("🔑 All API Keys", callback_data="admin_listkeys")],
        [InlineKeyboardButton("➕ Custom Key", callback_data="admin_customkey")],
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("❌ Close", callback_data="close_panel")]
    ])
    await send_message(user_id, "🛡️ <b>Admin Control Center</b>", reply_markup=keyboard)

async def list_all_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keys = list_api_keys()
    if not keys:
        await query.edit_message_text("No API keys found.")
        return
    text = "🔑 <b>All API Keys</b>\n\n"
    for k, expires, rate, name, active, created_by in keys:
        status = "✅" if active else "❌"
        text += f"{status} <code>{k[:24]}...</code> | {name or 'Unnamed'} | Exp: {expires[:10]} | {rate}/min | Owner: {created_by}\n"
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]))

async def close_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Panel closed. Use /admin to reopen.")
    await start(update, context)

# ---------- BROADCAST, USER LIST, BAN, OWNERS ----------
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Text", callback_data="broadcast_text")],
        [InlineKeyboardButton("🖼 Photo", callback_data="broadcast_photo")],
        [InlineKeyboardButton("🎥 Video", callback_data="broadcast_video")],
        [InlineKeyboardButton("📄 Document", callback_data="broadcast_document")],
        [InlineKeyboardButton("😀 Sticker", callback_data="broadcast_sticker")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
    ])
    await query.edit_message_text("Select broadcast type:", reply_markup=keyboard)

async def broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE, media_type):
    query = update.callback_query
    await query.answer()
    context.user_data['broadcast_step'] = media_type
    await query.edit_message_text(f"Send the {media_type}.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]]))

async def handle_broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get('broadcast_step')
    if not step:
        return
    admin_id = update.effective_user.id
    msg = update.message
    c.execute("SELECT user_id FROM users WHERE is_banned=0")
    users = c.fetchall()
    success = 0
    for (uid,) in users:
        try:
            if step == 'text':
                await application.bot.send_message(uid, msg.text, parse_mode='HTML')
            elif step == 'photo':
                await application.bot.send_photo(uid, msg.photo[-1].file_id, caption=msg.caption or "")
            elif step == 'video':
                await application.bot.send_video(uid, msg.video.file_id, caption=msg.caption or "")
            elif step == 'document':
                await application.bot.send_document(uid, msg.document.file_id)
            elif step == 'sticker':
                await application.bot.send_sticker(uid, msg.sticker.file_id)
            success += 1
        except:
            pass
        time.sleep(0.05)
    context.user_data.pop('broadcast_step', None)
    await send_message(admin_id, f"✅ Broadcast sent to {success} users.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]))

async def user_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split('_')[-1])
    limit = 15
    offset = page * limit
    total = count_users()
    users = get_all_users(offset, limit)
    text = f"👥 Users – Page {page+1}/{((total-1)//limit)+1}\n\n"
    for u in users:
        text += f"🆔 <code>{u[0]}</code> | {u[2] or 'No name'} {'👑' if u[5] else ''}\n   @{u[1] or 'no username'} | {'🚫 Banned' if u[4] else '✅ Active'}\n\n"
    keyboard = []
    if page > 0:
        keyboard.append([InlineKeyboardButton("◀️ Prev", callback_data=f"admin_userlist_{page-1}")])
    if (page+1)*limit < total:
        keyboard.append([InlineKeyboardButton("Next ▶️", callback_data=f"admin_userlist_{page+1}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def ban_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    c.execute("SELECT user_id, first_name, is_banned FROM users LIMIT 50")
    users = c.fetchall()
    keyboard = [[InlineKeyboardButton(f"{name or uid} ({'Banned' if banned else 'Active'})", callback_data=f"ban_toggle_{uid}")] for uid, name, banned in users]
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    await query.edit_message_text("Select user to ban/unban:", reply_markup=InlineKeyboardMarkup(keyboard))

async def toggle_ban_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = int(query.data.split('_')[-1])
    toggle_ban(uid)
    await query.edit_message_text(f"Toggled ban for user {uid}.")
    await ban_menu(update, context)

async def owners_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    c.execute("SELECT user_id, first_name FROM users WHERE is_owner=1")
    owners = c.fetchall()
    text = "👑 Owners:\n" + "\n".join([f"• {name or uid} (<code>{uid}</code>)" for uid, name in owners])
    keyboard = [
        [InlineKeyboardButton("➕ Add Owner", callback_data="admin_addowner")],
        [InlineKeyboardButton("➖ Remove Owner", callback_data="admin_removeowner")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
    ]
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def add_owner_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != OWNER_ID:
        await query.edit_message_text("Only main owner can add owners.")
        return
    context.user_data['owner_action'] = 'add'
    await query.edit_message_text("Send user ID to add as owner:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]]))

async def remove_owner_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != OWNER_ID:
        await query.edit_message_text("Only main owner can remove owners.")
        return
    context.user_data['owner_action'] = 'remove'
    await query.edit_message_text("Send user ID to remove from owners:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_back")]]))

async def handle_owner_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get('owner_action')
    if not action:
        return
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        return
    target_id = int(update.message.text.strip())
    if action == 'add':
        set_owner(target_id, True)
        await send_message(user_id, f"Owner added: {target_id}")
    else:
        if target_id == OWNER_ID:
            await send_message(user_id, "Cannot remove main owner.")
        else:
            set_owner(target_id, False)
            await send_message(user_id, f"Owner removed: {target_id}")
    context.user_data.pop('owner_action', None)
    await admin_panel(update, context)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    total = count_users()
    c.execute("SELECT COUNT(*) FROM users WHERE is_banned=1")
    banned = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE is_owner=1")
    owners = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM api_keys")
    keys_count = c.fetchone()[0]
    text = f"📊 Stats\n\nUsers: {total}\nBanned: {banned}\nOwners: {owners}\nAPI Keys: {keys_count}\nCache size: {len(cache)}"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]))

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await admin_panel(update, context)

# ---------- REGISTER HANDLERS ----------
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("admin", admin_panel))
application.add_handler(CommandHandler("genkey", genkey_command))
application.add_handler(CommandHandler("mykeys", mykeys_command))
application.add_handler(CommandHandler("apihelp", apihelp_command))
application.add_handler(CommandHandler("customkey", customkey_start))

application.add_handler(CallbackQueryHandler(admin_back, pattern="admin_back"))
application.add_handler(CallbackQueryHandler(close_panel, pattern="close_panel"))
application.add_handler(CallbackQueryHandler(broadcast_start, pattern="admin_broadcast"))
application.add_handler(CallbackQueryHandler(lambda u,c: broadcast_media(u,c,'text'), pattern="broadcast_text"))
application.add_handler(CallbackQueryHandler(lambda u,c: broadcast_media(u,c,'photo'), pattern="broadcast_photo"))
application.add_handler(CallbackQueryHandler(lambda u,c: broadcast_media(u,c,'video'), pattern="broadcast_video"))
application.add_handler(CallbackQueryHandler(lambda u,c: broadcast_media(u,c,'document'), pattern="broadcast_document"))
application.add_handler(CallbackQueryHandler(lambda u,c: broadcast_media(u,c,'sticker'), pattern="broadcast_sticker"))
application.add_handler(CallbackQueryHandler(user_list, pattern="admin_userlist_"))
application.add_handler(CallbackQueryHandler(ban_menu, pattern="admin_ban"))
application.add_handler(CallbackQueryHandler(toggle_ban_cb, pattern="ban_toggle_"))
application.add_handler(CallbackQueryHandler(owners_menu, pattern="admin_owners"))
application.add_handler(CallbackQueryHandler(add_owner_prompt, pattern="admin_addowner"))
application.add_handler(CallbackQueryHandler(remove_owner_prompt, pattern="admin_removeowner"))
application.add_handler(CallbackQueryHandler(stats, pattern="admin_stats"))
application.add_handler(CallbackQueryHandler(list_all_keys, pattern="admin_listkeys"))
application.add_handler(CallbackQueryHandler(cancel_custom, pattern="cancel_custom"))
application.add_handler(CallbackQueryHandler(lambda u,c: admin_panel(u,c), pattern="admin_customkey"))

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_key_input))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_owner_id))
application.add_handler(MessageHandler(filters.PHOTO, handle_broadcast_content))
application.add_handler(MessageHandler(filters.VIDEO, handle_broadcast_content))
application.add_handler(MessageHandler(filters.Document.ALL, handle_broadcast_content))
application.add_handler(MessageHandler(filters.Sticker.ALL, handle_broadcast_content))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_content))

# ---------- SELF-PING ----------
def self_ping():
    while True:
        time.sleep(300)
        try:
            requests.get(f"{RENDER_URL}/health", timeout=5)
        except:
            pass

threading.Thread(target=self_ping, daemon=True).start()

# ---------- WEBHOOK ----------
@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.process_update(update)
    return "ok", 200

def set_webhook():
    requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}")
    print("Webhook set")

# ---------- MAIN ----------
if __name__ == '__main__':
    set_webhook()
    from werkzeug.serving import run_simple
    threading.Thread(target=lambda: run_simple('0.0.0.0', PORT, app, use_reloader=False, threaded=True)).start()
    while True:
        time.sleep(3600)
