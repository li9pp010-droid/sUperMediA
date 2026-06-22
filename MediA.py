import os
import shutil
import asyncio
import re
import random
from concurrent.futures import ProcessPoolExecutor
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReactionTypeEmoji
import yt_dlp
import static_ffmpeg

ffmpeg_path = static_ffmpeg.add_paths(weak=True)

MESSAGES = {
    "start": "اهلين دز رابط الميديا التريدها عزيزي\nاوف يلا",
    "received": "تم استلام الرابط عزيزي\nشلون تريده",
    "success": "تم تنفيذ طلبك بدون مشاكل\nاوف عزيزي",
    "error": "اكو مشكله فنيه بالبوت\nانتظر شويه",
    "too_large": "عيرك ثكيل هواي وكسي مايكدر\nيشيله مولاي",
    "auth_success": "تم التعرف عليك عزيزي\nاوف تفضل",
    "auth_wrong": "كودك غلط فشل التعرف عليك\nمتطفل ابتعد"
}

bot_owner = None
default_code = "9575"
bot_online = True
authorized_users = {}
owner_states = {}
executor = ProcessPoolExecutor(max_workers=300)
bot = Bot(token=os.environ.get("BOT_TOKEN", "your_bot_token_here"))
dp = Dispatcher()

def get_keypad(code="0000", mode="auth"):
    display_code = " ".join(list(code))
    keyboard = [
        [InlineKeyboardButton(text="1", callback_data=f"key_{mode}_1"), InlineKeyboardButton(text="2", callback_data=f"key_{mode}_2"), InlineKeyboardButton(text="3", callback_data=f"key_{mode}_3")],
        [InlineKeyboardButton(text="4", callback_data=f"key_{mode}_4"), InlineKeyboardButton(text="5", callback_data=f"key_{mode}_5"), InlineKeyboardButton(text="6", callback_data=f"key_{mode}_6")],
        [InlineKeyboardButton(text="7", callback_data=f"key_{mode}_7"), InlineKeyboardButton(text="8", callback_data=f"key_{mode}_8"), InlineKeyboardButton(text="9", callback_data=f"key_{mode}_9")],
        [InlineKeyboardButton(text="⛔", callback_data=f"key_{mode}_delete"), InlineKeyboardButton(text="0", callback_data=f"key_{mode}_0"), InlineKeyboardButton(text="✅", callback_data=f"key_{mode}_verify")]
    ]
    if mode == "change":
        return f"عين الكود الافتراضي\n\n||{display_code}||", InlineKeyboardMarkup(inline_keyboard=keyboard)
    return f"||{display_code}||", InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_owner_panel():
    keyboard = [
        [InlineKeyboardButton(text="تغيير الكود", callback_data="owner_change_code"), InlineKeyboardButton(text="نقل ملكية البوت", callback_data="owner_transfer")],
        [InlineKeyboardButton(text="تعطيل الاونلاين", callback_data="owner_offline"), InlineKeyboardButton(text="تفعيل الاونلاين", callback_data="owner_online")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_media_panel(url):
    keyboard = [
        [InlineKeyboardButton(text="فيديو", callback_data=f"dl_video_{url}"), InlineKeyboardButton(text="صوت", callback_data=f"dl_audio_{url}")],
        [InlineKeyboardButton(text="صورة / البوم صور", callback_data=f"dl_imgalbum_{url}"), InlineKeyboardButton(text="فيديو / البوم فيديو", callback_data=f"dl_vidalbum_{url}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def send_slow_message(message: types.Message, text: str, reply_markup=None, reply_to_id=None, edit_msg_id=None):
    lines = text.split('\n')
    chunks = []
    for line in lines:
        words = line.split()
        for i in range(0, len(words), 2):
            chunks.append(" ".join(words[i:i+2]))
        if line != lines[-1]:
            chunks.append("\n")
            
    target_id = reply_to_id if reply_to_id else message.message_id
    current_text = ""
    msg = None
    
    for chunk in chunks:
        if chunk == "\n":
            current_text += "\n"
            continue
        current_text += (" " if current_text and not current_text.endswith("\n") else "") + chunk
        
        if edit_msg_id and not msg:
            try:
                await message.bot.edit_message_text(chat_id=message.chat.id, message_id=edit_msg_id, text=current_text)
                msg = types.Message(message_id=edit_msg_id, date=message.date, chat=message.chat, from_user=message.from_user)
            except:
                msg = await message.bot.send_message(chat_id=message.chat.id, text=current_text, reply_to_message_id=target_id)
        elif not msg:
            msg = await message.bot.send_message(chat_id=message.chat.id, text=current_text, reply_to_message_id=target_id)
        else:
            await asyncio.sleep(0.3)
            await msg.edit_text(current_text, reply_markup=reply_markup)
            
    if reply_markup and msg:
        await msg.edit_reply_markup(reply_markup=reply_markup)
    return msg

async def handle_reaction(message: types.Message, emoji: str):
    await asyncio.sleep(3)
    try:
        await message.react(reactions=[ReactionTypeEmoji(emoji=emoji)])
    except:
        pass

async def handle_bot_self_reaction(message: types.Message):
    emoji = random.choice(["🤣", "😭"])
    try:
        await message.react(reactions=[ReactionTypeEmoji(emoji=emoji)])
    except:
        pass

def check_link_info(url, mode):
    ydl_opts = {'quiet': True, 'extract_flat': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if not info:
                return "error"
            is_playlist = 'entries' in info or info.get('_type') == 'playlist'
            if mode == 'imgalbum':
                if not is_playlist and not info.get('ext') in ['jpg', 'jpeg', 'png', 'webp']:
                    return "is_video_not_img"
            if mode in ['imgalbum', 'vidalbum']:
                if not is_playlist:
                    return "not_album"
            return "ok"
        except:
            return "error"

def make_progress_hook(loop, bot_token, chat_id, message_id):
    local_bot = Bot(token=bot_token)
    last_percent = -1
    
    def hook(d):
        nonlocal last_percent
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_approx', 0)
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                percent = int((downloaded / total) * 100)
                if percent != last_percent and percent % 5 == 0:
                    last_percent = percent
                    text = f"يتم تنفيذ طلبك عزيزي\nانتظر شويه {percent}%"
                    asyncio.run_coroutine_threadsafe(
                        local_bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text),
                        loop
                    )
    return hook

def process_media(url, user_id, mode, bot_token, reply_to_msg_id, chat_id, progress_hook=None):
    local_bot = Bot(token=bot_token)
    user_dir = os.path.join("downloads", str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    
    ydl_opts = {
        'quiet': True,
        'restrictfilenames': False,
        'yes_playlist': True if 'album' in mode or 'list' in url else False,
        'ffmpeg_location': shutil.which('ffmpeg') or os.environ.get('FFMPEG_BINARY')
    }
    
    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]
    
    if mode == 'audio':
        ydl_opts.update({
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(user_dir, '%(uploader)s - %(title)s.%(ext)s'),
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'best'}],
        })
    elif mode == 'imgalbum':
        ydl_opts.update({
            'format': 'best',
            'skip_download': True,
            'writethumbnail': True,
            'outtmpl': os.path.join(user_dir, '%(uploader)s - %(title)s.%(ext)s'),
        })
    else:
        ydl_opts.update({
            'format': 'bestvideo+bestaudio/best' if mode == 'video' else 'best',
            'outtmpl': os.path.join(user_dir, '%(uploader)s - %(title)s.%(ext)s'),
        })
        
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
            
            files = []
            for root, _, filenames in os.walk(user_dir):
                for filename in filenames:
                    files.append(os.path.join(root, filename))
                    
            if not files:
                return False, None
                
            last_file_msg_id = None
            for file_path in sorted(files):
                base_name = os.path.basename(file_path)
                clean_name = re.sub(r'[\\/:*?"<>|#]', ' ', base_name)
                clean_name = re.sub(r'\s+', ' ', clean_name).strip()
                new_file_path = os.path.join(os.path.dirname(file_path), clean_name)
                os.rename(file_path, new_file_path)
                
                filesize = os.path.getsize(new_file_path)
                if filesize > 456 * 1024 * 1024:
                    continue
                    
                if mode == 'imgalbum' or new_file_path.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    doc_msg = asyncio.run(local_bot.send_photo(chat_id=chat_id, photo=types.FSInputFile(new_file_path), reply_to_message_id=reply_to_msg_id))
                else:
                    doc_msg = asyncio.run(local_bot.send_document(chat_id=chat_id, document=types.FSInputFile(new_file_path), reply_to_message_id=reply_to_msg_id))
                last_file_msg_id = doc_msg.message_id
                
        shutil.rmtree(user_dir)
        return True, last_file_msg_id
    except:
        if os.path.exists(user_dir):
            shutil.rmtree(user_dir)
        return False, None

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    global bot_owner, bot_online
    if bot_owner is not None and message.from_user.id != bot_owner:
        if not bot_online or not authorized_users.get(message.from_user.id, {}).get("verified", False):
            return
    asyncio.create_task(handle_reaction(message, "🍓"))
    authorized_users[message.from_user.id] = {"code": "", "verified": False}
    code_text, keyboard = get_keypad("0000", "auth")
    await message.reply(code_text, reply_markup=keyboard, parse_mode="MarkdownV2")

@dp.message(F.text == "ادت")
async def owner_panel_cmd(message: types.Message):
    if message.from_user.id != bot_owner:
        return
    asyncio.create_task(handle_reaction(message, "🍓"))
    await message.reply(text=" ", reply_markup=get_owner_panel())

@dp.callback_query(F.data.startswith("owner_"))
async def handle_owner_panel(callback: types.CallbackQuery):
    global bot_online, bot_owner
    if callback.from_user.id != bot_owner:
        return
    action = callback.data.split("_")[1]
    if action == "change":
        owner_states[bot_owner] = {"action": "changing_code", "code": ""}
        code_text, keyboard = get_keypad("0000", "change")
        await callback.message.edit_text(code_text, reply_markup=keyboard, parse_mode="MarkdownV2")
    elif action == "transfer":
        owner_states[bot_owner] = {"action": "transferring"}
        await callback.message.edit_text("دز ايدي المالك التريد\nتعينه")
    elif action == "offline":
        bot_online = False
        await callback.message.edit_text("عطلته عن الكل مولاي وشغال\nبس عندك")
    elif action == "online":
        bot_online = True
        await callback.message.edit_text("صار يشتغل للكل وتدلل يبعدي\nاوف مولاي")

@dp.callback_query(F.data.startswith("key_"))
async def handle_keypad(callback: types.CallbackQuery):
    global bot_owner, default_code
    user_id = callback.from_user.id
    parts = callback.data.split("_")
    mode = parts[1]
    action = parts[2]
    if mode == "change" and user_id != bot_owner:
        return
    if mode == "auth":
        if user_id not in authorized_users: authorized_users[user_id] = {"code": "", "verified": False}
        current_code = authorized_users[user_id]["code"]
    else:
        if user_id not in owner_states: owner_states[user_id] = {"action": "changing_code", "code": ""}
        current_code = owner_states[user_id]["code"]
    if action.isdigit():
        if len(current_code) >= 4:
            await callback.answer("الكود من اربعه ارقام", show_alert=True)
            return
        current_code += action
        if mode == "auth": authorized_users[user_id]["code"] = current_code
        else: owner_states[user_id]["code"] = current_code
    elif action == "delete":
        if len(current_code) == 0:
            await callback.answer("شنو امسح بعد عزيزي\nترى ماكو", show_alert=True)
            return
        current_code = current_code[:-1]
        if mode == "auth": authorized_users[user_id]["code"] = current_code
        else: owner_states[user_id]["code"] = current_code
    elif action == "verify":
        if len(current_code) < 4:
            await callback.answer("من المفروض ان الكود من اربعه ارقام عزيزي", show_alert=True)
            return
        if mode == "auth":
            if current_code == default_code:
                if bot_owner is None: bot_owner = user_id
                authorized_users[user_id]["verified"] = True
                await callback.message.edit_text(MESSAGES["auth_success"])
                await send_slow_message(callback.message, MESSAGES["start"], reply_to_id=callback.message.reply_to_message.message_id if callback.message.reply_to_message else None)
                return
            else:
                authorized_users[user_id]["code"] = ""
                await callback.message.edit_text(MESSAGES["auth_wrong"])
                await asyncio.sleep(2)
                code_text, keyboard = get_keypad("0000", "auth")
                await callback.message.edit_text(code_text, reply_markup=keyboard, parse_mode="MarkdownV2")
                return
        elif mode == "change":
            default_code = current_code
            owner_states.pop(user_id, None)
            await callback.message.edit_text("تم تعيين الكود الافتراضي")
            return
    display = current_code.ljust(4, '0')
    code_text, keyboard = get_keypad(display, mode)
    await callback.message.edit_text(code_text, reply_markup=keyboard, parse_mode="MarkdownV2")

@dp.message()
async def main_logic(message: types.Message):
    global bot_owner, bot_online
    user_id = message.from_user.id
    is_url = message.text.startswith(("http://", "https://")) if message.text else False
    
    if bot_owner is not None and user_id == bot_owner:
        if message.text and user_id in owner_states and owner_states[user_id].get("action") == "transferring":
            asyncio.create_task(handle_reaction(message, "🍓"))
            if message.text.isdigit():
                new_owner = int(message.text)
                bot_owner = new_owner
                owner_states.pop(user_id, None)
                if new_owner not in authorized_users: authorized_users[new_owner] = {}
                authorized_users[new_owner]["verified"] = True
                await message.reply("تم تعيين هذا المالك\nبدون مشاكل")
            else:
                owner_states.pop(user_id, None)
                await message.reply("دز ايدي المالك مو تمضرط وياي\nهوف داضوج")
            return
        if is_url:
            asyncio.create_task(handle_reaction(message, "🍌"))
            await send_slow_message(message, MESSAGES["received"], reply_markup=get_media_panel(message.text))
        elif message.text:
            asyncio.create_task(handle_reaction(message, "🍓"))
        return
        
    if not bot_online: return
    if user_id not in authorized_users or not authorized_users[user_id].get("verified", False): return
    
    if is_url:
        asyncio.create_task(handle_reaction(message, "🍌"))
        await send_slow_message(message, MESSAGES["received"], reply_markup=get_media_panel(message.text))
    elif message.text:
        asyncio.create_task(handle_reaction(message, "🍓"))

@dp.callback_query(F.data.startswith("dl_"))
async def handle_callback(callback: types.CallbackQuery):
    global bot_owner, bot_online
    user_id = callback.from_user.id
    if bot_owner is not None and user_id != bot_owner:
        if not bot_online or not authorized_users.get(user_id, {}).get("verified", False):
            return
    parts = callback.data.split("_")
    mode = parts[1]
    url = "_".join(parts[2:])
    
    target_reply_id = callback.message.reply_to_message.message_id if callback.message.reply_to_message else callback.message.message_id
    
    loop = asyncio.get_event_loop()
    check_result = await loop.run_in_executor(executor, check_link_info, url, mode)
    
    if check_result == "is_video_not_img":
        await send_slow_message(callback.message, "هذا الرابط عبارة عن فيديو\nمو صورة", reply_to_id=target_reply_id)
        return
    elif check_result == "not_album":
        await send_slow_message(callback.message, "هذا الرابط مو البوم عزيزي\nميديا فردية", reply_to_id=target_reply_id)
        return
    elif check_result == "error":
        await send_slow_message(callback.message, MESSAGES["error"], reply_to_id=target_reply_id)
        return

    status_msg = await callback.message.reply("يتم تنفيذ طلبك عزيزي\nانتظر شويه 0%")
    asyncio.create_task(handle_bot_self_reaction(status_msg))
    
    progress_hook = make_progress_hook(loop, bot.token, callback.message.chat.id, status_msg.message_id)
    
    result, file_msg_id = await loop.run_in_executor(
        executor, process_media, url, callback.from_user.id, mode, bot.token, 
        target_reply_id, callback.message.chat.id, progress_hook
    )
    
    if result == True:
        try:
            await bot.edit_message_text(chat_id=callback.message.chat.id, message_id=status_msg.message_id, text="يتم تنفيذ طلبك عزيزي\nانتظر شويه")
        except:
            pass
        if file_msg_id:
            fake_msg = types.Message(message_id=file_msg_id, date=status_msg.date, chat=status_msg.chat, from_user=status_msg.from_user)
            await send_slow_message(fake_msg, MESSAGES["success"])
    elif result == "too_large":
        await send_slow_message(callback.message, MESSAGES["too_large"], reply_to_id=target_reply_id)
    else:
        await send_slow_message(callback.message, MESSAGES["error"], reply_to_id=target_reply_id)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
