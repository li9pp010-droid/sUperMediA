import os
import asyncio
import glob
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from telegram import Update, ReactionTypeEmoji, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import yt_dlp

TOKEN = os.getenv("BOT_TOKEN")
MAX_SIZE_BYTES = 567 * 1024 * 1024

executor = ThreadPoolExecutor(max_workers=20)

with sqlite3.connect("bot_data.db") as conn:
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, verified INTEGER DEFAULT 0, msg_count INTEGER DEFAULT 0)")
    conn.commit()

def get_config(key, default=None):
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row[0] if row else default

def set_config(key, value):
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

bot_owner = get_config("bot_owner")
if bot_owner:
    bot_owner = int(bot_owner)
default_code = get_config("default_code", "9575")
bot_online = get_config("bot_online", "True") == "True"

owner_states = {}

def load_user_auth(user_id):
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT verified, msg_count FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return {"verified": bool(row[0]), "msg_count": row[1]}
        return {"verified": False, "msg_count": 0}

def save_user_auth(user_id, verified):
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id, verified, msg_count) VALUES (?, ?, 0)", (user_id, 1 if verified else 0))
        cursor.execute("UPDATE users SET verified = ? WHERE user_id = ?", (1 if verified else 0, user_id))
        conn.commit()

def increment_msg_count(user_id):
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id, verified, msg_count) VALUES (?, 0, 0)", (user_id,))
        cursor.execute("UPDATE users SET msg_count = msg_count + 1 WHERE user_id = ?", (user_id,))
        conn.commit()

def reset_msg_count(user_id):
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id, verified, msg_count) VALUES (?, 0, 0)", (user_id,))
        cursor.execute("UPDATE users SET msg_count = 0 WHERE user_id = ?", (user_id,))
        conn.commit()

def reset_all_users_except_owner():
    with sqlite3.connect("bot_data.db") as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET verified = 0 WHERE user_id != ?", (bot_owner,))
        conn.commit()

def get_keypad(mode="auth", current_code=""):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(text="1", callback_data=f"key_{mode}_1"),
            InlineKeyboardButton(text="2", callback_data=f"key_{mode}_2"),
            InlineKeyboardButton(text="3", callback_data=f"key_{mode}_3")
        ],
        [
            InlineKeyboardButton(text="4", callback_data=f"key_{mode}_4"),
            InlineKeyboardButton(text="5", callback_data=f"key_{mode}_5"),
            InlineKeyboardButton(text="6", callback_data=f"key_{mode}_6")
        ],
        [
            InlineKeyboardButton(text="7", callback_data=f"key_{mode}_7"),
            InlineKeyboardButton(text="8", callback_data=f"key_{mode}_8"),
            InlineKeyboardButton(text="9", callback_data=f"key_{mode}_9")
        ],
        [
            InlineKeyboardButton(text="⛔", callback_data=f"key_{mode}_delete"),
            InlineKeyboardButton(text="0", callback_data=f"key_{mode}_0"),
            InlineKeyboardButton(text="✅", callback_data=f"key_{mode}_verify")
        ]
    ])
    display = current_code + "0" * (4 - len(current_code))
    spaced_display = " ".join(list(display))
    return f"عين الكود الافتراضي : {spaced_display}", keyboard

def get_owner_panel():
    global bot_online
    online_text = "تعطيل الاونلاين" if bot_online else "تفعيل الاونلاين"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(text="تغيير الكود", callback_data="owner_change_code"),
            InlineKeyboardButton(text="نقل ملكية البوت", callback_data="owner_transfer")
        ],
        [
            InlineKeyboardButton(text=online_text, callback_data="owner_toggle_online")
        ]
    ])

def get_media_info(url):
    ydl_opts = {
        'extract_flat': False,
        'skip_download': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)

def download_media(ydl_opts, url):
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)

async def send_animated_text(update: Update, text: str, reply_to_id: int):
    lines = text.split('\n')
    current_display = ""
    msg = None
    
    for l_idx, line in enumerate(lines):
        words = []
        current_word = ""
        for char in line:
            current_word += char
            if char == ' ' or char == 'ء':
                words.append(current_word)
                current_word = ""
        if current_word:
            words.append(current_word)
            
        paired_words = []
        temp_pair = ""
        for w_idx, word in enumerate(words):
            temp_pair += word
            if (w_idx + 1) % 2 == 0 or (w_idx + 1) == len(words):
                paired_words.append(temp_pair)
                temp_pair = ""
                
        for p_idx, pair in enumerate(paired_words):
            if not pair.strip() and p_idx == 0 and len(paired_words) == 1:
                continue
            
            if current_display == "":
                current_display = pair
            else:
                if p_idx == 0 and l_idx > 0:
                    current_display += "\n" + pair
                else:
                    current_display += pair
            
            if msg is None:
                if update.message:
                    msg = await update.message.reply_text(current_display, reply_to_message_id=reply_to_id)
                else:
                    msg = await update.callback_query.message.reply_text(current_display, reply_to_message_id=reply_to_id)
            else:
                await asyncio.sleep(0.1)
                try:
                    await msg.edit_text(current_display)
                except Exception:
                    pass
    return msg

async def add_strawberry_reactions(context, chat_id, user_msg_id, bot_msg_id):
    await asyncio.sleep(3)
    for msg_id in [user_msg_id, bot_msg_id]:
        if msg_id:
            try:
                await context.bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=msg_id,
                    reaction=[ReactionTypeEmoji(emoji="🍓")]
                )
            except Exception:
                pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_owner, bot_online
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id
    
    auth_data = load_user_auth(user_id)
    
    if auth_data["verified"]:
        if bot_owner is not None and user_id != bot_owner and not bot_online:
            return
        
        if auth_data["msg_count"] == 0:
            bot_msg = await send_animated_text(update, "اهلين دز رابط الميديا التريدها عزيزي\nاوف يلا", user_msg_id)
            increment_msg_count(user_id)
        else:
            bot_msg = await send_animated_text(update, "مو ناوي تستعملني مثل البوتات ترى\nازعل منك واكلهم يلزموك", user_msg_id)
        
        if bot_msg:
            asyncio.create_task(add_strawberry_reactions(context, chat_id, user_msg_id, bot_msg.message_id))
        return

    if bot_owner is not None and user_id != bot_owner and not bot_online:
        return

    owner_states[user_id] = {"action": "auth", "code": "", "expires_at": time.time() + 300}
    code_text, keyboard = get_keypad("auth", "")
    await update.message.reply_text(code_text, reply_markup=keyboard, reply_to_message_id=user_msg_id)

async def owner_panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_owner
    if bot_owner is not None and update.effective_user.id != bot_owner:
        return
    user_msg_id = update.message.message_id
    await update.message.reply_text("لوحة التحكم للمطور", reply_markup=get_owner_panel(), reply_to_message_id=user_msg_id)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_owner, bot_online
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_msg_id = update.message.message_id
    text = update.message.text.strip()
    
    if bot_owner is not None and user_id == bot_owner:
        if user_id in owner_states and owner_states[user_id].get("action") == "transferring" and time.time() <= owner_states[user_id].get("expires_at", 0):
            if text.isdigit():
                new_owner = int(text)
                save_user_auth(bot_owner, False)
                bot_owner = new_owner
                set_config("bot_owner", bot_owner)
                save_user_auth(new_owner, True)
                owner_states.pop(user_id, None)
                await send_animated_text(update, "تم تعيين هذا المالك\nبدون مشاكل", user_msg_id)
            else:
                owner_states.pop(user_id, None)
                await send_animated_text(update, "دز ايدي المالك المظبوط", user_msg_id)
            return

    auth_data = load_user_auth(user_id)
    if not auth_data["verified"]:
        if bot_owner is not None and not bot_online:
            return
        owner_states[user_id] = {"action": "auth", "code": "", "expires_at": time.time() + 300}
        code_text, keyboard = get_keypad("auth", "")
        await update.message.reply_text(code_text, reply_markup=keyboard, reply_to_message_id=user_msg_id)
        return

    if bot_owner is not None and user_id != bot_owner and not bot_online:
        return

    url = text
    if not (url.startswith("http://") or url.startswith("https://")):
        if auth_data["msg_count"] == 0:
            bot_msg = await send_animated_text(update, "اهلين دز رابط الميديا التريدها عزيزي\nاوف يلا", user_msg_id)
            increment_msg_count(user_id)
        else:
            bot_msg = await send_animated_text(update, "مو ناوي تستعملني مثل البوتات ترى\nازعل منك واكلهم يلزموك", user_msg_id)
            increment_msg_count(user_id)
        if bot_msg:
            asyncio.create_task(add_strawberry_reactions(context, chat_id, user_msg_id, bot_msg.message_id))
        return

    reset_msg_count(user_id)

    try:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(executor, get_media_info, url)
    except Exception:
        bot_msg = await send_animated_text(update, "الرابط غير مدعوم او الموقع\nغير مدعوم", user_msg_id)
        await update.message.reply_text("🫧")
        if bot_msg:
            asyncio.create_task(add_strawberry_reactions(context, chat_id, user_msg_id, bot_msg.message_id))
        return

    msg3 = await send_animated_text(update, "دانفذ طلبك انتظر مولاي\nبليز", user_msg_id)
    msg4 = await update.message.reply_text("🫦")

    async def delete_waiting_messages():
        for m in [msg3, msg4]:
            try:
                await m.delete()
            except Exception:
                pass

    def create_progress_hook(tg_loop, message_obj):
        last_percent = ""
        def hook(d):
            nonlocal last_percent
            if d['status'] == 'downloading':
                percent = d.get('_percent_str', '0.0%')
                clean_percent = "".join(c for c in percent if c.isdigit() or c=='.' or c=='%').strip()
                if clean_percent and clean_percent != last_percent:
                    last_percent = clean_percent
                    new_text = f"دانفذ طلبك انتظر مولاي\nبليز {clean_percent}"
                    asyncio.run_coroutine_threadsafe(message_obj.edit_text(new_text), tg_loop)
        return hook

    if 'entries' in info and not info.get('formats'):
        ydl_opts = {
            'format': 'bestvideo+bestaudio/bestvideo/best',
            'outtmpl': 'downloads/%(channel)s - %(id)s_%(index)s.%(ext)s',
            'max_filesize': MAX_SIZE_BYTES,
            'windowsfilenames': True,
            'trim_file_name': 100,
            'progress_hooks': [create_progress_hook(loop, msg3)],
        }
        try:
            download_info = await loop.run_in_executor(executor, download_media, ydl_opts, url)
            await delete_waiting_messages()
            
            first_sent_msg_id = None
            
            for entry in download_info.get('entries', []):
                if not entry:
                    continue
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    file_path = ydl.prepare_filename(entry)
                
                base_name = os.path.splitext(file_path)[0]
                matching_files = [f for f in glob.glob(f"{base_name}.*") if not f.endswith('.part') and not f.endswith('.ytdl')]
                
                if matching_files:
                    real_file_path = matching_files[0]
                    
                    if os.path.getsize(real_file_path) > MAX_SIZE_BYTES:
                        if os.path.exists(real_file_path):
                            os.remove(real_file_path)
                        continue
                    
                    with open(real_file_path, 'rb') as doc_file:
                        sent_doc = await update.message.reply_document(document=doc_file, reply_to_message_id=user_msg_id)
                        if not first_sent_msg_id:
                            first_sent_msg_id = sent_doc.message_id
                            
                    if os.path.exists(real_file_path):
                        os.remove(real_file_path)
            
            if first_sent_msg_id:
                asyncio.create_task(add_strawberry_reactions(context, chat_id, user_msg_id, first_sent_msg_id))
            return
        except Exception:
            bot_msg = await send_animated_text(update, "الرابط غير مدعوم او الموقع\nغير مدعوم", user_msg_id)
            await update.message.reply_text("🫧")
            await delete_waiting_messages()
            if bot_msg:
                asyncio.create_task(add_strawberry_reactions(context, chat_id, user_msg_id, bot_msg.message_id))
            return

    ydl_opts = {
        'format': 'bestvideo+bestaudio/bestvideo/best',
        'outtmpl': 'downloads/%(channel)s - %(id)s.%(ext)s',
        'max_filesize': MAX_SIZE_BYTES,
        'windowsfilenames': True,
        'trim_file_name': 100,
        'progress_hooks': [create_progress_hook(loop, msg3)],
    }
    
    try:
        loop = asyncio.get_event_loop()
        download_info = await loop.run_in_executor(executor, download_media, ydl_opts, url)
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            filename = ydl.prepare_filename(download_info)
            
        base_name = os.path.splitext(filename)[0]
        matching_files = [f for f in glob.glob(f"{base_name}.*") if not f.endswith('.part') and not f.endswith('.ytdl')]

        if matching_files:
            real_filename = matching_files[0]

            if os.path.getsize(real_filename) > MAX_SIZE_BYTES:
                os.remove(real_filename)
                bot_msg = await send_animated_text(update, "ماكدر اشيل عير اطول من كسي\nالعفو منك مولاي", user_msg_id)
                await update.message.reply_text("🧸")
                await delete_waiting_messages()
                if bot_msg:
                    asyncio.create_task(add_strawberry_reactions(context, chat_id, user_msg_id, bot_msg.message_id))
                return
            
            with open(real_filename, 'rb') as document:
                sent_msg = await update.message.reply_document(document=document, reply_to_message_id=user_msg_id)
                asyncio.create_task(add_strawberry_reactions(context, chat_id, user_msg_id, sent_msg.message_id))
            
            os.remove(real_filename)
            await delete_waiting_messages()
            
    except yt_dlp.utils.MaxFileSizeReached:
        bot_msg = await send_animated_text(update, "ماكدر اشيل عير اطول من كسي\nالعفو منك مولاي", user_msg_id)
        await update.message.reply_text("🧸")
        await delete_waiting_messages()
        if bot_msg:
            asyncio.create_task(add_strawberry_reactions(context, chat_id, user_msg_id, bot_msg.message_id))
    except Exception:
        bot_msg = await send_animated_text(update, "الرابط غير مدعوم او الموقع\nغير مدعوم", user_msg_id)
        await update.message.reply_text("🫧")
        await delete_waiting_messages()
        if bot_msg:
            asyncio.create_task(add_strawberry_reactions(context, chat_id, user_msg_id, bot_msg.message_id))

async def handle_owner_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_online, bot_owner
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id != bot_owner:
        await query.answer("لن تستطيع تغيير شيء هنا لانها\nللمطور فقط", show_alert=True)
        return
        
    action = query.data.split("_")[1]
    await query.answer()
    
    if action == "change":
        owner_states[bot_owner] = {"action": "change", "code": "", "expires_at": time.time() + 300}
        code_text, keyboard = get_keypad("change", "")
        await query.message.edit_text(code_text, reply_markup=keyboard)
    elif action == "transfer":
        owner_states[bot_owner] = {"action": "transferring", "expires_at": time.time() + 300}
        await query.message.edit_text("دز ايدي المالك التريد\nتعينه")
    elif action == "toggle":
        bot_online = not bot_online
        set_config("bot_online", str(bot_online))
        try:
            await query.message.edit_reply_markup(reply_markup=get_owner_panel())
        except Exception:
            pass
        if bot_online:
            await query.message.reply_text("صار يشتغل للكل وتدلل يبعدي\nاوف مولاي")
        else:
            await query.message.reply_text("عطلته عن الكل مولاي وشغال\nبس عندك")

async def handle_keypad_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global bot_owner, default_code
    query = update.callback_query
    user_id = query.from_user.id
    parts = query.data.split("_")
    mode = parts[1]
    action = parts[2]
    
    if mode == "change" and user_id != bot_owner:
        await query.answer("لن تستطيع تغيير شيء هنا لانها\nللمطور فقط", show_alert=True)
        return
        
    if user_id not in owner_states or time.time() > owner_states[user_id].get("expires_at", 0):
        owner_states[user_id] = {"action": mode, "code": "", "expires_at": time.time() + 300}
        
    current_code = owner_states[user_id].get("code", "")
    
    if action.isdigit():
        if len(current_code) >= 4:
            await query.answer("الكود من اربعه ارقام", show_alert=True)
            return
        current_code += action
        owner_states[user_id]["code"] = current_code
        code_text, keyboard = get_keypad(mode, current_code)
        try:
            await query.message.edit_text(code_text, reply_markup=keyboard)
        except Exception:
            pass
        await query.answer()
        return
    elif action == "delete":
        if len(current_code) == 0:
            await query.answer("شنو امسح بعد عزيزي\nترى ماكو", show_alert=True)
            return
        current_code = current_code[:-1]
        owner_states[user_id]["code"] = current_code
        code_text, keyboard = get_keypad(mode, current_code)
        try:
            await query.message.edit_text(code_text, reply_markup=keyboard)
        except Exception:
            pass
        await query.answer()
        return
    elif action == "verify":
        if len(current_code) < 4:
            await query.answer("من المفروض ان الكود من اربعه ارقام عزيزي", show_alert=True)
            return
        if mode == "auth":
            if current_code == default_code:
                if bot_owner is None:
                    bot_owner = user_id
                    set_config("bot_owner", bot_owner)
                save_user_auth(user_id, True)
                owner_states.pop(user_id, None)
                
                try:
                    await query.message.delete()
                except Exception:
                    pass
                    
                reset_msg_count(user_id)
                await query.message.reply_text("تم التعرف عليك عزيزي\nاوف تفضل")
                return
            else:
                owner_states[user_id]["code"] = ""
                code_text, keyboard = get_keypad(mode, "")
                try:
                    await query.message.edit_text(code_text, reply_markup=keyboard)
                except Exception:
                    pass
                await query.answer("كودك غلط فشل التعرف عليك\nمتطفل ابتعد", show_alert=True)
                return
        elif mode == "change":
            if user_id != bot_owner:
                return
            default_code = current_code
            set_config("default_code", default_code)
            reset_all_users_except_owner()
            if bot_owner is not None:
                save_user_auth(bot_owner, True)
            owner_states.pop(user_id, None)
            
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.reply_text("تم تعيين الكود الافتراضي")
            return
    await query.answer()

def main():
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
        
    if not TOKEN:
        return
        
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("edit", owner_panel_cmd))
    app.add_handler(MessageHandler(filters.TEXT & filters.Caption("ادت"), owner_panel_cmd))
    app.add_handler(CallbackQueryHandler(handle_owner_callbacks, pattern="^owner_"))
    app.add_handler(CallbackQueryHandler(handle_keypad_callbacks, pattern="^key_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()

if __name__ == '__main__':
    main()
