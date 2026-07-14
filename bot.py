import os
import json
import hmac
import hashlib
import threading
import requests
from urllib.parse import parse_qsl

from flask import Flask, request, jsonify, send_from_directory
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://YOUR-SERVER-DOMAIN")  # index.html buradan servis edilecek
STATE_FILE = "state.json"

groups = {}

# --------------------------
# Flask (Mini App backend + statik dosya)
# --------------------------
flask_app = Flask(__name__, static_folder=".", static_url_path="")

def check_init_data(init_data: str) -> bool:
    if not init_data or not TOKEN:
        return False
    parsed = dict(parse_qsl(init_data))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return False
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calc_hash, received_hash)

def get_user_id(init_data: str) -> int | None:
    parsed = dict(parse_qsl(init_data))
    user_raw = parsed.get("user")
    if not user_raw:
        return None
    return json.loads(user_raw).get("id")

def is_admin_sync(chat_id: str, user_id: int) -> bool:
    try:
        res = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getChatMember",
            params={"chat_id": chat_id, "user_id": user_id},
            timeout=5
        )
        data = res.json()
        status = data.get("result", {}).get("status")
        return status in ("administrator", "creator")
    except:
        return False
    parsed = dict(parse_qsl(init_data))
    user_raw = parsed.get("user")
    if not user_raw:
        return "Bilinmiyor"
    user = json.loads(user_raw)
    return user.get("first_name", "") + ((" " + user["last_name"]) if user.get("last_name") else "")

@flask_app.route("/")
def serve_index():
    return send_from_directory(".", "index.html")

@flask_app.route("/api/state", methods=["GET"])
def api_state():
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not check_init_data(init_data):
        return jsonify({"error": "invalid init data"}), 403

    chat_id = request.args.get("chat_id", "default")
    group = get_group(chat_id)
    return jsonify({"state": group})

@flask_app.route("/api/action", methods=["POST"])
def api_action():
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not check_init_data(init_data):
        return jsonify({"error": "invalid init data"}), 403

    body = request.get_json(force=True)
    chat_id = body.get("chat_id", "default")
    action = body.get("action")
    name = get_display_name(init_data)

    group = get_group(chat_id)
    message = ""

    if action in ("join", "done") and not group["active"]:
        return jsonify({"state": group, "message": "⛔️ Kayıt kapalı"})

    if action == "join":
        if name in group["listeners"]:
            group["listeners"].remove(name)
        group["participants"][name] = False
        message = "🌸 Katılımın kaydedildi"

    elif action == "leave":
        group["participants"].pop(name, None)
        if name in group["listeners"]:
            group["listeners"].remove(name)
        message = "🗑️ Kaydın silindi"

    elif action == "done":
        if name in group["participants"]:
            group["participants"][name] = True
            message = "✅ Tebrikler, işaretlendi"
        else:
            message = "Henüz katılmadın"

    elif action == "open":
        if not is_admin_sync(chat_id, get_user_id(init_data)):
            return jsonify({"state": group, "message": "⛔️ Sadece yönetici açabilir"})
        group["active"] = True
        message = "🔓 Liste açıldı"

    elif action == "close":
        if not is_admin_sync(chat_id, get_user_id(init_data)):
            return jsonify({"state": group, "message": "⛔️ Sadece yönetici kapatabilir"})
        group["active"] = False
        message = "🔒 Liste kapatıldı"

    save_state()
    return jsonify({"state": group, "message": message})

def run_server():
    flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 1551)))

# --------------------------
# Veri Kaydetme
# --------------------------
def save_state():
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False)

def load_state():
    global groups
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            groups = json.load(f)
    except:
        groups = {}

# --------------------------
# Yardımcı Fonksiyonlar
# --------------------------
async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return True
    user_id = update.effective_user.id
    admins = await context.bot.get_chat_administrators(update.effective_chat.id)
    return any(a.user.id == user_id for a in admins)

def ltr(text: str) -> str:
    return "\u200e" + text

def get_group(chat_id):
    chat_id = str(chat_id)
    if chat_id not in groups:
        groups[chat_id] = {
            "participants": {},
            "listeners": [],
            "active": False,
            "message_id": None
        }
    return groups[chat_id]

# --------------------------
# Mesaj Oluşturma
# --------------------------
def build_text(group):
    text = "*🔸🔶 İTKAN | Kur'an Akademisi 🔶🔸*\n\n"

    text += "*🔸 Katılımcılar:*\n"
    if group["participants"]:
        for i, (name, done) in enumerate(group["participants"].items(), start=1):
            mark = " ✅" if done else ""
            text += f"{i}. {ltr(name)}{mark}\n"
    else:
        text += "Henüz kimse yok\n"

    text += "\n*🔸 Dinleyiciler:*\n"
    if group["listeners"]:
        for i, name in enumerate(group["listeners"], start=1):
            text += f"{i}. {ltr(name)}\n"
    else:
        text += "Henüz kimse yok\n"

    text += (
        "\n*📖 Kur'an kalplere şifa, hayata nurdur.*\n"
        "*Niyet et, adım at, Allah muvaffak eylesin 🤲🏻*\n"
    )

    if group["active"]:
        text += "👇 Lütfen aşağıdan durumunu seç"
    else:
        text += "📕 *Ders sona erdi*"

    return text

def build_keyboard(chat_id):
    webapp_url = f"{WEBAPP_URL}/?chat_id={chat_id}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✋🏻 Katılıyorum", callback_data="join"),
            InlineKeyboardButton("🎧 Dinleyici", callback_data="listen"),
        ],
        [
            InlineKeyboardButton("✅ Okudum", callback_data="done"),
        ],
        [
            InlineKeyboardButton("📋 Renkli Panel", web_app=WebAppInfo(url=webapp_url)),
        ],
        [
            InlineKeyboardButton("⛔️ İlanı Durdur", callback_data="stop"),
        ]
    ])

# --------------------------
# /start Komutu
# --------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except:
        pass

    if not await is_admin(update, context):
        return

    chat_id = str(update.effective_chat.id)
    group = get_group(chat_id)

    if group["active"]:
        if group["message_id"]:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=group["message_id"])
            except:
                pass

        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=build_text(group),
            reply_markup=build_keyboard(chat_id),
            parse_mode="Markdown"
        )
        group["message_id"] = msg.message_id
        save_state()
        return

    group["participants"] = {}
    group["listeners"] = []
    group["active"] = True

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=build_text(group),
        reply_markup=build_keyboard(chat_id),
        parse_mode="Markdown"
    )
    group["message_id"] = msg.message_id
    save_state()

# --------------------------
# Buton İşlemleri (klasik inline butonlar)
# --------------------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = str(query.message.chat.id)
    group = get_group(chat_id)
    name = query.from_user.full_name

    if query.data == "stop":
        if not await is_admin(update, context):
            return
        group["active"] = False
        save_state()
        await query.edit_message_text(build_text(group), reply_markup=None, parse_mode="Markdown")
        return

    if not group["active"]:
        await query.answer("⛔️ Kayıt kapalı")
        return

    if query.data == "join":
        if name in group["participants"]:
            await query.answer("Zaten katılımcısın 🌸")
            return
        if name in group["listeners"]:
            group["listeners"].remove(name)
        group["participants"][name] = False
        await query.answer("🌸 Katılımın kaydedildi")

    elif query.data == "listen":
        if name in group["participants"]:
            await query.answer("Zaten katılımcısın")
            return
        if name not in group["listeners"]:
            group["listeners"].append(name)
            await query.answer("🌷 Dinleyici olarak kaydedildin")

    elif query.data == "done":
        if name not in group["participants"]:
            await query.answer("Henüz katılmadın")
            return
        if group["participants"][name]:
            await query.answer("Zaten işaretlendi")
            return
        group["participants"][name] = True
        await query.answer("✅ Tebrikler, işaretlendi")

    save_state()
    await query.edit_message_text(build_text(group), reply_markup=build_keyboard(chat_id), parse_mode="Markdown")

# --------------------------
# Main
# --------------------------
def main():
    load_state()
    threading.Thread(target=run_server, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.run_polling()

if __name__ == "__main__":
    main()
