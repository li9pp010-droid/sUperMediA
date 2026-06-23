import os
import shutil
import asyncio
import re
import random
import hashlib
import psycopg2
from psycopg2.extras import DictCursor
import time
from concurrent.futures import ThreadPoolExecutor
import yt_dlp
import static_ffmpeg

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReactionTypeEmoji, InputMediaPhoto, InputMediaVideo, InputMediaDocument
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ButtonStyle
from aiogram.exceptions import TelegramRetryAfter

ffmpeg_path = static_ffmpeg.add_paths(weak=True)

MESSAGES = {
    "start": "اهلين دز رابط الميديا التريدها عزيزي\nاوف يلا",
    "received": "تم استلام الرابط عزيزي\nشلون تريده",
    "success": "تم تنفيذ طلبك بدون مشاكل\nاوف عزيزي",
    "error": "اكو مشكله فنيه بالبوت\nانتظر شويه",
    "too_large": "الحجم ثقيل جداً لا يمكن إرساله: {size}",
    "auth_success": "تم التعرف عليك عزيزي\nاوف تفضل",
    "auth_wrong": "كودك غلط فشل التعرف عليك\nمتطفل ابتعد",
    "lazy_user": "مو ناوي تستعملني مثل البوتات ترى\nازعل منك واكلهم يلزموك"
}

SECRET_SALT = "SECURE_RANDOM_SALT_987654321_ALIVE"
DATABASE_URL = os.environ.get("DATABASE_URL")

if os.path.exists("downloads"):
    try:
        shutil.rmtree("downloads")
    except:
        pass
os.makedirs("downloads", exist_ok=True)

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        verified INTEGER DEFAULT 0,
        msg_count INTEGER DEFAULT 0
    )
    """)
    conn.commit()
    cursor.close()
    conn.close()

init_db()

def get_config(key, default=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM config WHERE key = %s", (key,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else default

def set_config(key, value):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO config (key, value) VALUES (%s, %s)
    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, (key, str(value)))
    conn.commit()
    cursor.close()
    conn.close()

bot_owner = get_config("bot_owner")
if bot_owner:
    bot_owner = int(bot_owner)
default_code = get_config("default_code", "9575")
bot_online = get_config("bot_online", "True") == "True"

owner_states = {}
url_cache = {}
active_downloads = set()
executor = ThreadPoolExecutor(max_workers=10)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token_here")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

def generate_user_url_token(user_id, url):
    raw_str = f"{user_id}_{url}_{SECRET_SALT}"
    return hashlib.md5(raw_str.encode('utf-8')).hexdigest()[:12]

def load_user_auth(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=DictCursor)
    cursor.execute("SELECT verified, msg_count FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return {"code": "", "verified": bool(row['verified']), "msg_count": row['msg_count']}
    return {"code": "", "verified": False, "msg_count": 0}

def save_user_auth(user_id, verified):
    conn = get_db_connection()
    cursor = conn.cursor()
    val = 1 if verified else 0
    cursor.execute("""
    INSERT INTO users (user_id, verified, msg_count) VALUES (%s, %s, 0)
    ON CONFLICT (user_id) DO UPDATE SET verified = EXCLUDED.verified
    """, (user_id, val))
    conn.commit()
    cursor.close()
    conn.close()

def increment_msg_count(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO users (user_id, verified, msg_count) VALUES (%s, 0, 1)
    ON CONFLICT (user_id) DO UPDATE SET msg_count = users.msg_count + 1
    """, (user_id,))
    conn.commit()
    cursor.close()
    conn.close()

def reset_all_users_except_owner():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET verified = 0 WHERE user_id != %s", (bot_owner,))
    conn.commit()
    cursor.close()
    conn.close()

def get_keypad(mode="auth", current_code=""):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1", callback_data=f"key_{mode}_1", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="2", callback_data=f"key_{mode}_2", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="3", callback_data=f"key_{mode}_3", style=ButtonStyle.PRIMARY)
        ],
        [
            InlineKeyboardButton(text="4", callback_data=f"key_{mode}_4", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="5", callback_data=f"key_{mode}_5", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="6", callback_data=f"key_{mode}_6", style=ButtonStyle.PRIMARY)
        ],
        [
            InlineKeyboardButton(text="7", callback_data=f"key_{mode}_7", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="8", callback_data=f"key_{mode}_8", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="9", callback_data=f"key_{mode}_9", style=ButtonStyle.PRIMARY)
        ],
        [
            InlineKeyboardButton(text="⛔", callback_data=f"key_{mode}_delete", style=ButtonStyle.DANGER),
            InlineKeyboardButton(text="0", callback_data=f"key_{mode}_0", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="✅", callback_data=f"key_{mode}_verify", style=ButtonStyle.SUCCESS)
        ]
    ])
    display = current_code + "0" * (4 - len(current_code))
    spaced_display = " ".join(list(display))
    return f"عين الكود الافتراضي : <tg-spoiler>\u200e{spaced_display}</tg-spoiler>", keyboard

def get_owner_panel():
    global bot_online
    online_text = "تعطيل الاونلاين" if bot_online else "تفعيل الاونلاين"
    online_style = ButtonStyle.DANGER if bot_online else ButtonStyle.SUCCESS
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="تغيير الكود", callback_data="owner_change_code", style=ButtonStyle.DANGER),
            InlineKeyboardButton(text="نقل ملكية البوت", callback_data="owner_transfer", style=ButtonStyle.DANGER)
        ],
        [
            InlineKeyboardButton(text=online_text, callback_data="owner_toggle_online", style=online_style)
        ]
    ])

def get_media_panel(user_id, url):
    token = generate_user_url_token(user_id, url)
    url_cache[token] = url
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="فيديو", callback_data=f"dl_video_{token}", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="صوت", callback_data=f"dl_audio_{token}", style=ButtonStyle.PRIMARY)
        ],
        [
            InlineKeyboardButton(text="صورة / البوم صور", callback_data=f"dl_imgalbum_{token}", style=ButtonStyle.DANGER),
            InlineKeyboardButton(text="فيديو / البوم فيديو", callback_data=f"dl_vidalbum_{token}", style=ButtonStyle.DANGER)
        ]
    ])

def split_text_pattern(text):
    tokens = re.split(r'(\s+)', text)
    chunks = []
    i = 0
    pattern_words = True
    while i < len(tokens):
        current_chunk = ""
        if pattern_words:
            word_count = 0
            while i < len(tokens) and word_count < 2:
                token = tokens[i]
                current_chunk += token
                if token.strip():
                    word_count += 1
                i += 1
            pattern_words = False
        else:
            char_count = 0
            while i < len(tokens) and char_count < 3:
                token = tokens[i]
                if not token.strip():
                    current_chunk += token
                    i += 1
                    continue
                available = 3 - char_count
                if len(token) <= available:
                    current_chunk += token
                    char_count += len(token)
                    i += 1
                else:
                    current_chunk += token[:available]
                    tokens[i] = token[available:]
                    char_count += available
            pattern_words = True
        if current_chunk:
            chunks.append(current_chunk)
    return chunks

async def send_slow_message(chat_id, text, buttons=None, reply_to_message_id=None, fast=False):
    kwargs = {}
    if reply_to_message_id:
        kwargs['reply_to_message_id'] = reply_to_message_id
    if fast or not text.strip():
        res = await bot.send_message(chat_id=chat_id, text=text if text.strip() else " ", reply_markup=buttons, **kwargs)
        asyncio.create_task(handle_bot_self_reaction(chat_id, res.message_id))
        return res.message_id

    chunks = split_text_pattern(text)
    if not chunks:
        res = await bot.send_message(chat_id=chat_id, text=" ", reply_markup=buttons, **kwargs)
        asyncio.create_task(handle_bot_self_reaction(chat_id, res.message_id))
        return res.message_id

    current_text = chunks[0]
    try:
        msg = await bot.send_message(chat_id=chat_id, text=current_text, **kwargs)
        asyncio.create_task(handle_bot_self_reaction(chat_id, msg.message_id))
    except:
        res = await bot.send_message(chat_id=chat_id, text=text, reply_markup=buttons, **kwargs)
        asyncio.create_task(handle_bot_self_reaction(chat_id, res.message_id))
        return res.message_id

    for chunk in chunks[1:]:
        await asyncio.sleep(0.03)
        current_text += chunk
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=current_text)
        except:
            pass
    if buttons:
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg.message_id, reply_markup=buttons)
        except:
            pass
    return msg.message_id

async def handle_reaction(chat_id, message_id, is_url: bool):
    await asyncio.sleep(3)
    emoji = "🍌" if is_url else random.choice(["🍓", "🥰"])
    try:
        await bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=[ReactionTypeEmoji(emoji=emoji)])
    except:
        pass

async def handle_bot_self_reaction(chat_id, message_id):
    await asyncio.sleep(3)
    emoji = random.choice(["😭", "🤣"])
    try:
        await bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=[ReactionTypeEmoji(emoji=emoji)])
    except:
        pass

def check_link_info(url, mode):
    ydl_opts = {
        'quiet': True, 
        'extract_flat': True,
        'playlist_items': '1-35',
        'match_filter': lambda info, *, incomplete: 'is_live' not in info or not info['is_live']
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if not info:
                return "error"
            is_playlist = 'entries' in info or info.get('_type') == 'playlist'
            if mode == 'imgalbum':
                ext = info.get('ext', '').lower()
                if not is_playlist and ext and ext not in ['jpg', 'jpeg', 'png', 'webp']:
                    return "is_video_not_img"
            if mode in ['imgalbum', 'vidalbum']:
                if not is_playlist:
                    return "not_album"
            return "ok"
        except:
            return "error"

def make_progress_hook(loop, chat_id, message_id):
    state = {"last_percent": -1, "last_update_time": 0}
    def hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_approx', 0)
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                percent = int((downloaded / total) * 100)
                current_time = time.time()
                if percent != state["last_percent"] and percent % 5 == 0 and (current_time - state["last_update_time"] > 2):
                    state["last_percent"] = percent
                    state["last_update_time"] = current_time
                    text = f"يتم تنفيذ طلبك عزيزي\nانتظر شويه {percent}%"
                    asyncio.run_coroutine_threadsafe(
                        bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text),
                        loop
                    )
    return hook

async def process_media_async(url, user_id, mode, reply_to_msg_id, chat_id, progress_hook=None):
    user_dir = os.path.join("downloads", f"{user_id}_{mode}_{int(time.time())}")
    os.makedirs(user_dir, exist_ok=True)
    
    ydl_opts = {
        'quiet': True,
        'restrictfilenames': True,
        'max_filesize': 456 * 1024 * 1024,
        'playlist_items': '1-35',
        'yes_playlist': True if 'album' in mode or 'list' in url else False,
        'ffmpeg_location': shutil.which('ffmpeg') or os.environ.get('FFMPEG_BINARY'),
        'match_filter': lambda info, *, incomplete: 'is_live' not in info or not info['is_live']
    }
    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]
    
    if mode == 'audio':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(user_dir, '%(uploader)s - %(title)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3'
            }],
        })
    elif mode == 'imgalbum':
        ydl_opts.update({
            'format': 'bestimage/best',
            'outtmpl': os.path.join(user_dir, '%(uploader)s - %(title)s.%(ext)s'),
        })
    else:
        ydl_opts.update({
            'format': 'best',
            'outtmpl': os.path.join(user_dir, '%(uploader)s - %(title)s.%(ext)s'),
        })
        
    loop = asyncio.get_running_loop()
    try:
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)
        
        await loop.run_in_executor(executor, download)
        
        files = []
        total_folder_size = 0
        for root, _, filenames in os.walk(user_dir):
            for filename in filenames:
                fp = os.path.join(root, filename)
                files.append(fp)
                total_folder_size += os.path.getsize(fp)
                
        if total_folder_size > 456 * 1024 * 1024:
            size_mb = round(total_folder_size / (1024 * 1024), 1)
            return f"too_large:{size_mb}", None
            
        if not files:
            return False, None
            
        sorted_files = sorted(files)
        media_group = []
        
        from aiogram.types import FSInputFile
        
        for file_path in sorted_files:
            dir_name = os.path.dirname(file_path)
            base_name = os.path.basename(file_path)
            name_part, ext_part = os.path.splitext(base_name)
            
            clean_name = re.sub(r'[\\/:*?"<>|#]', ' ', name_part)
            clean_name = re.sub(r'\s+', ' ', clean_name).strip()
            clean_name = os.path.basename(clean_name)
            
            new_file_path = os.path.join(dir_name, f"{clean_name}{ext_part}")
            os.rename(file_path, new_file_path)
            
            ext = ext_part.lower()
            input_file = FSInputFile(new_file_path)
            
            if mode == 'imgalbum' or ext in ['.jpg', '.jpeg', '.png', '.webp']:
                media_group.append(InputMediaPhoto(media=input_file))
            elif mode == 'vidalbum' or ext in ['.mp4', '.mkv', '.mov', '.avi', '.webm']:
                media_group.append(InputMediaVideo(media=input_file))
            else:
                media_group.append(InputMediaDocument(media=input_file))

        if not media_group:
            return False, None

        chunks = [media_group[i:i + 10] for i in range(0, len(media_group), 10)]
        last_file_msg_id = None
        
        for chunk in chunks:
            while True:
                try:
                    msgs = await bot.send_media_group(chat_id=chat_id, media=chunk, reply_to_message_id=reply_to_msg_id)
                    if msgs:
                        last_file_msg_id = msgs[-1].message_id
                        asyncio.create_task(handle_bot_self_reaction(chat_id, msgs[-1].message_id))
                    break
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except:
                    return False, None
            
            await asyncio.sleep(2.0)
            
        return True, last_file_msg_id
    except:
        return False, None
    finally:
        if os.path.exists(user_dir):
            try:
                await loop.run_in_executor(executor, shutil.rmtree, user_dir)
            except:
                pass

@dp.message(F.text == '/start')
async def cmd_start(message: Message):
    global bot_owner, bot_online
    user_id = message.from_user.id
    auth_data = load_user_auth(user_id)
        
    if auth_data["verified"]:
        if bot_owner is not None and user_id != bot_owner and not bot_online:
            return
        asyncio.create_task(handle_reaction(message.chat.id, message.message_id, is_url=False))
        if auth_data["msg_count"] == 0:
            await send_slow_message(message.chat.id, MESSAGES["start"], reply_to_message_id=message.message_id)
            increment_msg_count(user_id)
        else:
            await send_slow_message(message.chat.id, MESSAGES["lazy_user"], reply_to_message_id=message.message_id)
        return
        
    if bot_owner is not None and user_id != bot_owner:
        if not bot_online:
            return
            
    asyncio.create_task(handle_reaction(message.chat.id, message.message_id, is_url=False))
    owner_states[user_id] = {"action": "auth", "code": "", "expires_at": time.time() + 300}
    code_text, keyboard = get_keypad("auth", "")
    await send_slow_message(message.chat.id, code_text, buttons=keyboard, reply_to_message_id=message.message_id, fast=True)

@dp.message(F.text == 'ادت')
async def owner_panel_cmd(message: Message):
    if bot_owner is not None and message.from_user.id != bot_owner:
        return
    asyncio.create_task(handle_reaction(message.chat.id, message.message_id, is_url=False))
    await send_slow_message(message.chat.id, "لوحة التحكم للمطور", buttons=get_owner_panel(), reply_to_message_id=message.message_id)

@dp.callback_query(F.data.startswith('owner_'))
async def handle_owner_panel(callback: CallbackQuery):
    global bot_online, bot_owner
    if callback.from_user.id != bot_owner:
        await callback.answer("لن تستطيع تغيير شيء هنا لانها\nللمطور فقط", show_alert=True)
        return
        
    action = callback.data.split("_")[1]
    if action == "change":
        owner_states[bot_owner] = {"action": "change", "code": "", "expires_at": time.time() + 300}
        code_text, keyboard = get_keypad("change", "")
        try:
            await bot.delete_message(chat_id=callback.message.chat.id, message_id=callback.message.message_id)
        except:
            pass
        await send_slow_message(callback.message.chat.id, code_text, buttons=keyboard, reply_to_message_id=callback.message.message_id, fast=True)
    elif action == "transfer":
        owner_states[bot_owner] = {"action": "transferring", "expires_at": time.time() + 300}
        try:
            await bot.delete_message(chat_id=callback.message.chat.id, message_id=callback.message.message_id)
        except:
            pass
        await send_slow_message(callback.message.chat.id, "دز ايدي المالك التريد\nتعينه", reply_to_message_id=callback.message.message_id)
    elif action == "toggle":
        bot_online = not bot_online
        set_config("bot_online", str(bot_online))
        try:
            await bot.edit_message_reply_markup(
                chat_id=callback.message.chat.id,
                message_id=callback.message.message_id,
                reply_markup=get_owner_panel()
            )
        except:
            pass
        if bot_online:
            await send_slow_message(callback.message.chat.id, "صار يشتغل للكل وتدلل يبعدي\nاوف مولاي", reply_to_message_id=callback.message.message_id)
        else:
            await send_slow_message(callback.message.chat.id, "عطلته عن الكل مولاي وشغال\nبس عندك", reply_to_message_id=callback.message.message_id)
    await callback.answer()

@dp.callback_query(F.data.startswith('key_'))
async def handle_keypad(callback: CallbackQuery):
    global bot_owner, default_code
    user_id = callback.from_user.id
    parts = callback.data.split("_")
    mode = parts[1]
    action = parts[2]
    
    if mode == "change" and user_id != bot_owner:
        await callback.answer("لن تستطيع تغيير شيء هنا لانها\nللمطور فقط", show_alert=True)
        return
        
    if user_id not in owner_states or time.time() > owner_states[user_id].get("expires_at", 0):
        owner_states[user_id] = {"action": mode, "code": "", "expires_at": time.time() + 300}
    
    current_code = owner_states[user_id].get("code", "")
        
    if action.isdigit():
        if len(current_code) >= 4:
            await callback.answer("الكود من اربعه ارقام", show_alert=True)
            return
        current_code += action
        owner_states[user_id]["code"] = current_code
        code_text, keyboard = get_keypad(mode, current_code)
        try:
            await bot.edit_message_text(chat_id=callback.message.chat.id, message_id=callback.message.message_id, text=code_text, reply_markup=keyboard)
        except:
            pass
        await callback.answer()
        return
    elif action == "delete":
        if len(current_code) == 0:
            await callback.answer("شنو امسح بعد عزيزي\nترى ماكو", show_alert=True)
            return
        current_code = current_code[:-1]
        owner_states[user_id]["code"] = current_code
        code_text, keyboard = get_keypad(mode, current_code)
        try:
            await bot.edit_message_text(chat_id=callback.message.chat.id, message_id=callback.message.message_id, text=code_text, reply_markup=keyboard)
        except:
            pass
        await callback.answer()
        return
    elif action == "verify":
        if len(current_code) < 4:
            await callback.answer("من المفروض ان الكود من اربعه ارقام عزيزي", show_alert=True)
            return
        if mode == "auth":
            if current_code == default_code:
                if bot_owner is None:
                    bot_owner = user_id
                    set_config("bot_owner", bot_owner)
                save_user_auth(user_id, True)
                owner_states.pop(user_id, None)
                
                try:
                    await bot.delete_message(chat_id=callback.message.chat.id, message_id=callback.message.message_id)
                except:
                    pass
                    
                ref_id = callback.message.reply_to_message.message_id if callback.message.reply_to_message else None
                await send_slow_message(callback.message.chat.id, MESSAGES["auth_success"], reply_to_message_id=ref_id)
                await send_slow_message(callback.message.chat.id, MESSAGES["start"], reply_to_message_id=ref_id)
                return
            else:
                owner_states[user_id]["code"] = ""
                code_text, keyboard = get_keypad(mode, "")
                try:
                    await bot.edit_message_text(chat_id=callback.message.chat.id, message_id=callback.message.message_id, text=code_text, reply_markup=keyboard)
                except:
                    pass
                await callback.answer(MESSAGES["auth_wrong"], show_alert=True)
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
                await bot.delete_message(chat_id=callback.message.chat.id, message_id=callback.message.message_id)
            except:
                pass
                
            ref_id = callback.message.reply_to_message.message_id if callback.message.reply_to_message else None
            await send_slow_message(callback.message.chat.id, "تم تعيين الكود الافتراضي", reply_to_message_id=ref_id)
            return
    await callback.answer()

@dp.message()
async def main_logic(message: Message):
    global bot_owner, bot_online
    user_id = message.from_user.id
    
    if not message.text:
        return

    is_url = message.text.startswith(("http://", "https://"))
    
    if bot_owner is not None and user_id == bot_owner:
        if user_id in owner_states and owner_states[user_id].get("action") == "transferring" and time.time() <= owner_states[user_id].get("expires_at", 0):
            asyncio.create_task(handle_reaction(message.chat.id, message.message_id, is_url=False))
            if message.text.isdigit():
                new_owner = int(message.text)
                save_user_auth(bot_owner, False)
                bot_owner = new_owner
                set_config("bot_owner", bot_owner)
                save_user_auth(new_owner, True)
                owner_states.pop(user_id, None)
                await send_slow_message(message.chat.id, "تم تعيين هذا المالك\nبدون مشاكل", reply_to_message_id=message.message_id)
            else:
                owner_states.pop(user_id, None)
                await send_slow_message(message.chat.id, "دز ايدي المالك المظبوط", reply_to_message_id=message.message_id)
            return
        if is_url:
            asyncio.create_task(handle_reaction(message.chat.id, message.message_id, is_url=True))
            await send_slow_message(message.chat.id, MESSAGES["received"], buttons=get_media_panel(user_id, message.text), reply_to_message_id=message.message_id)
        else:
            asyncio.create_task(handle_reaction(message.chat.id, message.message_id, is_url=False))
        return
        
    auth_data = load_user_auth(user_id)
        
    if not auth_data["verified"]:
        if bot_owner is not None and not bot_online:
            return
        asyncio.create_task(handle_reaction(message.chat.id, message.message_id, is_url=False))
        owner_states[user_id] = {"action": "auth", "code": "", "expires_at": time.time() + 300}
        code_text, keyboard = get_keypad("auth", "")
        await send_slow_message(message.chat.id, code_text, buttons=keyboard, reply_to_message_id=message.message_id, fast=True)
        return

    if not bot_online:
        return
    
    if is_url:
        asyncio.create_task(handle_reaction(message.chat.id, message.message_id, is_url=True))
        await send_slow_message(message.chat.id, MESSAGES["received"], buttons=get_media_panel(user_id, message.text), reply_to_message_id=message.message_id)
    else:
        asyncio.create_task(handle_reaction(message.chat.id, message.message_id, is_url=False))
        if auth_data["msg_count"] == 0:
            await send_slow_message(message.chat.id, MESSAGES["start"], reply_to_message_id=message.message_id)
        else:
            await send_slow_message(message.chat.id, MESSAGES["lazy_user"], reply_to_message_id=message.message_id)
        increment_msg_count(user_id)

@dp.callback_query(F.data.startswith('dl_'))
async def handle_callback(callback: CallbackQuery):
    global bot_owner, bot_online
    user_id = callback.from_user.id
    
    token_match = callback.data.split("_")
    if len(token_match) < 3:
        return
    token = "_".join(token_match[2:])
    
    if token in active_downloads:
        await callback.answer("انتظر ثواني جاري العمل على طلبك الحالي...", show_alert=True)
        return
        
    if bot_owner is not None and user_id != bot_owner:
        auth_data = load_user_auth(user_id)
        if not bot_online or not auth_data["verified"]:
            return

    url = url_cache.get(token)
    if not url:
        await send_slow_message(callback.message.chat.id, MESSAGES["error"], reply_to_message_id=callback.message.message_id)
        return

    active_downloads.add(token)
    mode = token_match[1]
    orig_reply_id = callback.message.reply_to_message.message_id if callback.message.reply_to_message else callback.message.message_id
    status_msg_id = None
    loop = asyncio.get_running_loop()
    
    try:
        check_result = await loop.run_in_executor(executor, check_link_info, url, mode)
        
        if check_result == "is_video_not_img":
            await send_slow_message(callback.message.chat.id, "هذا الرابط عبارة عن فيديو\nمو صورة", reply_to_message_id=orig_reply_id)
            return
        elif check_result == "not_album":
            await send_slow_message(callback.message.chat.id, "هذا الرابط مو البوم عزيزي\nميديا فردية", reply_to_message_id=orig_reply_id)
            return
        elif check_result == "error":
            await send_slow_message(callback.message.chat.id, MESSAGES["error"], reply_to_message_id=orig_reply_id)
            return

        status_msg_id = await send_slow_message(chat_id=callback.message.chat.id, text="يتم تنفيذ طلبك عزيزي\nانتظر شويه 0%", reply_to_message_id=orig_reply_id)
        progress_hook = make_progress_hook(loop, callback.message.chat.id, status_msg_id)
        
        result, file_msg_id = await process_media_async(
            url, user_id, mode, orig_reply_id, callback.message.chat.id, progress_hook
        )
        
        if result == True:
            try:
                await bot.edit_message_text(chat_id=callback.message.chat.id, message_id=status_msg_id, text="تم تحميل الملفات بنجاح!")
            except:
                pass
            if file_msg_id:
                await send_slow_message(callback.message.chat.id, MESSAGES["success"], reply_to_message_id=file_msg_id)
        elif isinstance(result, str) and result.startswith("too_large:"):
            try:
                await bot.delete_message(chat_id=callback.message.chat.id, message_id=status_msg_id)
            except:
                pass
            size_mb = result.split(":")[1]
            await send_slow_message(callback.message.chat.id, MESSAGES["too_large"].format(size=f"{size_mb}MB"), reply_to_message_id=orig_reply_id)
        else:
            try:
                await bot.delete_message(chat_id=callback.message.chat.id, message_id=status_msg_id)
            except:
                pass
            await send_slow_message(callback.message.chat.id, MESSAGES["error"], reply_to_message_id=orig_reply_id)
            
    except Exception as e:
        if status_msg_id:
            try:
                await bot.delete_message(chat_id=callback.message.chat.id, message_id=status_msg_id)
            except:
                pass
        await send_slow_message(callback.message.chat.id, MESSAGES["error"], reply_to_message_id=orig_reply_id)
    finally:
        active_downloads.discard(token)
        url_cache.pop(token, None)
        await callback.answer()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
