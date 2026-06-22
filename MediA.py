import os
import shutil
import asyncio
import re
import random
import secrets
from concurrent.futures import ProcessPoolExecutor
from telethon import TelegramClient, events, Button
from telethon.tl.types import ReactionEmoji
import yt_dlp
import static_ffmpeg

ffmpeg_path = static_ffmpeg.add_paths(weak=True)

MESSAGES = {
    "start": "اهلين دز رابط الميديا التريدها عزيزي\nاوف يلا",
    "received": "تم استلام الرابط عزيزي\nشلون تريده",
    "success": "تم تنفيذ طلبك بدون مشاكل\nاوف عزيزي",
    "error": "اكو مشكله فنيه بالبوت\nانتظر شويه",
    "too_large": "عيرك ثكيل هواي وكسي مايكدر\nيشيله مولاي {size}",
    "auth_success": "تم التعرف عليك عزيزي\nاوف تفضل",
    "auth_wrong": "كودك غلط فشل التعرف عليك\nمتطفل ابتعد"
}

bot_owner = None
default_code = "9575"
bot_online = True
authorized_users = {}
owner_states = {}
url_cache = {}
executor = ProcessPoolExecutor(max_workers=100)

API_ID = int(os.environ.get("TELEGRAM_API_ID", 123456))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "your_api_hash_here")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "your_bot_token_here")

client = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

def get_keypad(code="0000", mode="auth"):
    display_code = " ".join(list(code))
    keyboard = [
        [Button.inline("1", f"key_{mode}_1"), Button.inline("2", f"key_{mode}_2"), Button.inline("3", f"key_{mode}_3")],
        [Button.inline("4", f"key_{mode}_4"), Button.inline("5", f"key_{mode}_5"), Button.inline("6", f"key_{mode}_6")],
        [Button.inline("7", f"key_{mode}_7"), Button.inline("8", f"key_{mode}_8"), Button.inline("9", f"key_{mode}_9")],
        [Button.inline("⛔", f"key_{mode}_delete"), Button.inline("0", f"key_{mode}_0"), Button.inline("✅", f"key_{mode}_verify")]
    ]
    if mode == "change":
        return f"عين الكود الافتراضي\n\n{display_code}", keyboard
    return f"{display_code}", keyboard

def get_owner_panel():
    return [
        [Button.inline("تغيير الكود", "owner_change_code"), Button.inline("نقل ملكية البوت", "owner_transfer")],
        [Button.inline("تعطيل الاونلاين", "owner_offline"), Button.inline("تفعيل الاونلاين", "owner_online")]
    ]

def get_media_panel(url):
    token = secrets.token_hex(6)
    url_cache[token] = url
    return [
        [Button.inline("فيديو", f"dl_video_{token}"), Button.inline("صوت", f"dl_audio_{token}")],
        [Button.inline("صورة / البوم صور", f"dl_imgalbum_{token}"), Button.inline("فيديو / البوم فيديو", f"dl_vidalbum_{token}")]
    ]

async def send_slow_message(event, text, buttons=None, reply_to=None, edit_msg=None):
    chunks = []
    remaining = text
    add_words = True
    
    while remaining:
        if add_words:
            match = re.match(r'^(\s*\S+\s+\S+|\s*\S+)', remaining)
            if match:
                chunk = match.group(1)
                chunks.append(chunk)
                remaining = remaining[len(chunk):]
            else:
                break
        else:
            chunk = remaining[:2]
            chunks.append(chunk)
            remaining = remaining[2:]
        add_words = not add_words

    current_text = ""
    msg = edit_msg
    
    for chunk in chunks:
        current_text += chunk
        if not msg:
            if reply_to:
                msg = await event.reply(current_text)
            else:
                msg = await client.send_message(event.chat_id, current_text)
        else:
            await asyncio.sleep(0.1)
            msg = await msg.edit(current_text, buttons=buttons)
            
    if buttons and msg:
        msg = await msg.edit(current_text, buttons=buttons)
    return msg

async def handle_reaction(event, emoji: str):
    await asyncio.sleep(3)
    try:
        await client(functions.messages.SendReactionRequest(
            peer=event.input_chat,
            msg_id=event.id,
            reaction=[ReactionEmoji(emoticon=emoji)]
        ))
    except:
        pass

async def handle_bot_self_reaction(msg):
    emoji = random.choice(["🤣", "😭"])
    try:
        await client(functions.messages.SendReactionRequest(
            peer=msg.input_chat,
            msg_id=msg.id,
            reaction=[ReactionEmoji(emoticon=emoji)]
        ))
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

def make_progress_hook(loop, chat_id, message_id):
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
                        client.edit_message(chat_id, message_id, text),
                        loop
                    )
    return hook

def process_media(url, user_id, mode, reply_to_msg_id, chat_id, progress_hook=None):
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
                    size_mb = round(filesize / (1024 * 1024), 1)
                    shutil.rmtree(user_dir, ignore_errors=True)
                    return f"too_large:{size_mb}", None
                    
                doc_msg = asyncio.run(client.send_file(chat_id, new_file_path, reply_to=reply_to_msg_id))
                last_file_msg_id = doc_msg.id
                
        shutil.rmtree(user_dir)
        return True, last_file_msg_id
    except:
        if os.path.exists(user_dir):
            shutil.rmtree(user_dir)
        return False, None

@client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    global bot_owner, bot_online
    user_id = event.sender_id
    
    if authorized_users.get(user_id, {}).get("verified", False):
        await send_slow_message(event, MESSAGES["start"])
        return
        
    if bot_owner is not None and user_id != bot_owner:
        if not bot_online or not authorized_users.get(user_id, {}).get("verified", False):
            return
            
    asyncio.create_task(handle_reaction(event, "🍓"))
    authorized_users[user_id] = {"code": "", "verified": False}
    code_text, keyboard = get_keypad("0000", "auth")
    await event.reply(code_text, buttons=keyboard)

@client.on(events.NewMessage(func=lambda e: e.text == "ادت"))
async def owner_panel_cmd(event):
    if event.sender_id != bot_owner:
        return
    asyncio.create_task(handle_reaction(event, "🍓"))
    await event.reply(" ", buttons=get_owner_panel())

@client.on(events.CallbackQuery(data=re.compile(b"owner_")))
async def handle_owner_panel(event):
    global bot_online, bot_owner
    if event.sender_id != bot_owner:
        return
    action = event.data.decode().split("_")[1]
    if action == "change":
        owner_states[bot_owner] = {"action": "changing_code", "code": ""}
        code_text, keyboard = get_keypad("0000", "change")
        await event.edit(code_text, buttons=keyboard)
    elif action == "transfer":
        owner_states[bot_owner] = {"action": "transferring"}
        await event.edit("دز ايدي المالك التريد\nتعينه")
    elif action == "offline":
        bot_online = False
        await event.edit("عطلته عن الكل مولاي وشغال\nبس عندك")
    elif action == "online":
        bot_online = True
        await event.edit("صار يشتغل للكل وتدلل يبعدي\nاوف مولاي")

@client.on(events.CallbackQuery(data=re.compile(b"key_")))
async def handle_keypad(event):
    global bot_owner, default_code
    user_id = event.sender_id
    parts = event.data.decode().split("_")
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
            await event.answer("الكود من اربعه ارقام", alert=True)
            return
        current_code += action
        if mode == "auth": authorized_users[user_id]["code"] = current_code
        else: owner_states[user_id]["code"] = current_code
    elif action == "delete":
        if len(current_code) == 0:
            await event.answer("شنو امسح بعد عزيزي\nترى ماكو", alert=True)
            return
        current_code = current_code[:-1]
        if mode == "auth": authorized_users[user_id]["code"] = current_code
        else: owner_states[user_id]["code"] = current_code
    elif action == "verify":
        if len(current_code) < 4:
            await event.answer("من المفروض ان الكود من اربعه ارقام عزيزي", alert=True)
            return
        if mode == "auth":
            if current_code == default_code:
                if bot_owner is None: bot_owner = user_id
                authorized_users[user_id]["verified"] = True
                await event.edit(MESSAGES["auth_success"])
                await send_slow_message(event, MESSAGES["start"], reply_to=event.reply_to_msg_id)
                return
            else:
                authorized_users[user_id]["code"] = ""
                await event.edit(MESSAGES["auth_wrong"])
                await asyncio.sleep(2)
                code_text, keyboard = get_keypad("0000", "auth")
                await event.edit(code_text, buttons=keyboard)
                return
        elif mode == "change":
            default_code = current_code
            owner_states.pop(user_id, None)
            await event.edit("تم تعيين الكود الافتراضي")
            return
            
    display = current_code.ljust(4, '0')
    code_text, keyboard = get_keypad(display, mode)
    await event.edit(code_text, buttons=keyboard)

@client.on(events.NewMessage())
async def main_logic(event):
    global bot_owner, bot_online
    user_id = event.sender_id
    if event.text and event.text.startswith('/start'):
        return

    is_url = event.text.startswith(("http://", "https://")) if event.text else False
    
    if bot_owner is not None and user_id == bot_owner:
        if event.text and user_id in owner_states and owner_states[user_id].get("action") == "transferring":
            asyncio.create_task(handle_reaction(event, "🍓"))
            if event.text.isdigit():
                new_owner = int(event.text)
                bot_owner = new_owner
                owner_states.pop(user_id, None)
                if new_owner not in authorized_users: authorized_users[new_owner] = {}
                authorized_users[new_owner]["verified"] = True
                await event.reply("تم تعيين هذا المالك\nبدون مشاكل")
            else:
                owner_states.pop(user_id, None)
                await event.reply("دز ايدي المالك مو تمضرط وياي\nهوف داضوج")
            return
        if is_url:
            asyncio.create_task(handle_reaction(event, "🍌"))
            await send_slow_message(event, MESSAGES["received"], buttons=get_media_panel(event.text))
        elif event.text:
            asyncio.create_task(handle_reaction(event, "🍓"))
        return
        
    if not bot_online: return
    if user_id not in authorized_users or not authorized_users[user_id].get("verified", False): return
    
    if is_url:
        asyncio.create_task(handle_reaction(event, "🍌"))
        await send_slow_message(event, MESSAGES["received"], buttons=get_media_panel(event.text))
    elif event.text:
        asyncio.create_task(handle_reaction(event, "🍓"))

@client.on(events.CallbackQuery(data=re.compile(b"dl_")))
async def handle_callback(event):
    global bot_owner, bot_online
    user_id = event.sender_id
    
    try:
        await event.edit(buttons=None)
    except:
        pass
        
    if bot_owner is not None and user_id != bot_owner:
        if not bot_online or not authorized_users.get(user_id, {}).get("verified", False):
            return
            
    parts = event.data.decode().split("_")
    mode = parts[1]
    token = "_".join(parts[2:])
    
    target_reply_id = event.reply_to_msg_id if event.reply_to_msg_id else event.message_id
    
    url = url_cache.get(token)
    if not url:
        await send_slow_message(event, MESSAGES["error"], reply_to=target_reply_id)
        return
        
    loop = asyncio.get_event_loop()
    check_result = await loop.run_in_executor(executor, check_link_info, url, mode)
    
    if check_result == "is_video_not_img":
        await send_slow_message(event, "هذا الرابط عبارة عن فيديو\nمو صورة", reply_to=target_reply_id)
        return
    elif check_result == "not_album":
        await send_slow_message(event, "هذا الرابط مو البوم عزيزي\nميديا فردية", reply_to=target_reply_id)
        return
    elif check_result == "error":
        await send_slow_message(event, MESSAGES["error"], reply_to=target_reply_id)
        return

    status_msg = await event.reply("يتم تنفيذ طلبك عزيزي\nانتظر شويه 0%")
    asyncio.create_task(handle_bot_self_reaction(status_msg))
    
    progress_hook = make_progress_hook(loop, event.chat_id, status_msg.id)
    
    result, file_msg_id = await loop.run_in_executor(
        executor, process_media, url, user_id, mode, 
        target_reply_id, event.chat_id, progress_hook
    )
    
    if result == True:
        try:
            await client.edit_message(event.chat_id, status_msg.id, "يتم تنفيذ طلبك عزيزي\nانتظر شويه")
        except:
            pass
        if file_msg_id:
            await send_slow_message(event, MESSAGES["success"])
    elif isinstance(result, str) and result.startswith("too_large:"):
        size_mb = result.split(":")[1]
        await send_slow_message(
            event,
            MESSAGES["too_large"].format(size=f"{size_mb}MB"),
            reply_to=target_reply_id
        )
    else:
        await send_slow_message(event, MESSAGES["error"], reply_to=target_reply_id)

if __name__ == "__main__":
    client.run_until_disconnected()
