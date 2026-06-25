import os
import re
import asyncio
import collections
import mimetypes
import yt_dlp
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ChatAction
from aiogram.types import FSInputFile, InputMediaDocument, ReactionTypeEmoji

TOKEN = os.getenv("TELEGRAM_TOKEN")
DEVELOPER_ID = "8597653867"

bot = Bot(token=TOKEN)
dp = Dispatcher()

user_queues = collections.defaultdict(list)
user_processing = collections.defaultdict(bool)
user_msg_counter = collections.defaultdict(lambda: 0)
bot_msg_counter = collections.defaultdict(lambda: 0)

last_reported_percent = collections.defaultdict(lambda: -10)

def filter_title(text):
    if not text: return "Unknown"
    cleaned = re.sub(r'[\#\*\?\\/\|:\<\>"\']', '', text)
    cleaned = re.sub(r'[̀-ͯ҃-҉᷀-᷿⃐-⃿︠-︯]', '', cleaned)
    return cleaned.strip()

def get_developer_keyboard():
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                {
                    "text": "تواصل مع المطور",
                    "url": f"tg://user?id={DEVELOPER_ID}",
                    "style": "danger"
                }
            ]
        ]
    )

async def typing_effect(bot: Bot, chat_id, full_text: str, reply_markup=None, existing_message=None, reply_to_message_id=None):
    words = [w for w in full_text.split(' ') if w]
    chunks = []
    for i in range(0, len(words), 2):
        chunks.append(" ".join(words[i:i+2]))
    
    current_text = ""
    message = existing_message
    
    for idx, chunk in enumerate(chunks):
        if current_text:
            current_text += " " + chunk
        else:
            current_text = chunk
            
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        if message is None:
            message = await bot.send_message(chat_id=chat_id, text=current_text, reply_to_message_id=reply_to_message_id)
        else:
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message.message_id, text=current_text)
            except:
                pass
        await asyncio.sleep(0.3)
        
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message.message_id, text=full_text, reply_markup=reply_markup)
    except:
        pass
    return message

async def handle_reaction(bot: Bot, chat_id, message_id, emoji):
    try: 
        await bot.set_message_reaction(
            chat_id=chat_id, 
            message_id=message_id, 
            reaction=[ReactionTypeEmoji(emoji=emoji)]
        )
    except: pass

async def process_queue(user_id, chat_id):
    if user_processing[user_id] or not user_queues[user_id]: return
    user_processing[user_id] = True
    url, reply_msg_id = user_queues[user_id].pop(0)
    
    ydl_opts_info = {
        'format': 'bestvideo+bestaudio/best',
        'skip_download': True,
        'quiet': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            filesize = info.get('filesize') or info.get('filesize_approx') or 0
            if filesize > 456*1024*1024:
                raise Exception
    except:
        btn = get_developer_keyboard()
        await typing_effect(bot, chat_id, "الرابط مو مدعوم او الموقع مو\nمدعوم", reply_markup=btn, reply_to_message_id=reply_msg_id)
        await bot.send_message(chat_id=chat_id, text="👈🏻👉🏻")
        user_processing[user_id] = False; asyncio.create_task(process_queue(user_id, chat_id)); return

    status_msg = await typing_effect(bot, chat_id, "تم استلام الرابط والبدأ بتنزيل الميديا\nمولاي 0%", reply_to_message_id=reply_msg_id)
    await bot.send_message(chat_id=chat_id, text="⏳")
    
    last_reported_percent[user_id] = -10

    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                percent = int((downloaded / total) * 100)
                if percent >= last_reported_percent[user_id] + 10 or percent == 100:
                    last_reported_percent[user_id] = percent
                    if percent < 100:
                        text_update = f"تم استلام الرابط والبدأ بتنزيل الميديا\nمولاي {percent}%"
                    else:
                        text_update = "تم استلام الرابط والبدأ بتنزيل الميديا\nمولاي"
                    try:
                        loop = asyncio.get_event_loop()
                        loop.create_task(bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=text_update))
                    except: pass

    os.makedirs('downloads', exist_ok=True)
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'quiet': True,
        'progress_hooks': [progress_hook],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl: 
            info = ydl.extract_info(url, download=True)
        
        try: await bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="تم استلام الرابط والبدأ بتنزيل الميديا\nمولاي")
        except: pass

        files = []
        if 'entries' in info:
            for entry in info['entries']: 
                if entry: files.append(ydl.prepare_filename(entry))
        else: files.append(ydl.prepare_filename(info))
        
        clean_files = []
        for f in files:
            if not os.path.exists(f) and '.' in f:
                base = f.rsplit('.', 1)[0]
                for p in os.listdir('downloads'):
                    if p.startswith(os.path.basename(base)):
                        f = os.path.join('downloads', p)
                        break
            if os.path.exists(f):
                ext = os.path.splitext(f)[1]
                uploader_name = filter_title(info.get('uploader') or info.get('uploader_id') or 'Channel')
                media_id = info.get('id') or 'UnknownID'
                new_path = f"downloads/{uploader_name}_{media_id}{ext}"
                os.rename(f, new_path)
                clean_files.append(new_path)
        
        if clean_files:
            for chunk_idx in range(0, len(clean_files), 8):
                group = clean_files[chunk_idx:chunk_idx+8]
                if len(group) > 1:
                    media_group = [InputMediaDocument(media=FSInputFile(f, filename=os.path.basename(f))) for f in group]
                    await bot.send_media_group(chat_id=chat_id, media=media_group, reply_to_message_id=reply_msg_id)
                else: 
                    await bot.send_document(chat_id=chat_id, document=FSInputFile(group[0], filename=os.path.basename(group[0])), reply_to_message_id=reply_msg_id)
            
            await typing_effect(bot, chat_id, "العملية صارت بدون مشاكل\nتفضل مولاي", reply_to_message_id=reply_msg_id)
            await bot.send_message(chat_id=chat_id, text="🍓")
        else: raise Exception
    except:
        btn = get_developer_keyboard()
        await typing_effect(bot, chat_id, "الرابط مو مدعوم او الموقع مو\nمدعوم", reply_markup=btn, reply_to_message_id=reply_msg_id)
        await bot.send_message(chat_id=chat_id, text="👈🏻👉🏻")
    finally:
        for f in os.listdir('downloads') if os.path.exists('downloads') else []: 
            try: os.remove(os.path.join('downloads', f))
            except: pass
        user_processing[user_id] = False; asyncio.create_task(process_queue(user_id, chat_id))

@dp.message(F.content_type.any())
async def message_handler(message: types.Message):
    user_id, chat_id = message.from_user.id, message.chat.id
    text = message.text or message.caption or ""
    url_match = re.search(r'https?://[^\s]+', text)
    
    if url_match:
        if len(user_queues[user_id]) < 8:
            user_queues[user_id].append((url_match.group(0), message.message_id))
            asyncio.create_task(handle_reaction(bot, chat_id, message.message_id, "🍌"))
            if not user_processing[user_id]: asyncio.create_task(process_queue(user_id, chat_id))
    else:
        user_msg_counter[user_id] += 1
        count = user_msg_counter[user_id]
        reacts = ["🥰", "😭", "🍓", "😡"]
        
        asyncio.create_task(asyncio.sleep(3)).add_done_callback(
            lambda _: asyncio.create_task(handle_reaction(bot, chat_id, message.message_id, reacts[(count-1) % 4]))
        )
        
        reply_text = "اهلين دز رابط الميديا التريدها عزيزي\nيلا اوف" if count % 2 != 0 else "مو ناوي تستعملني مثل البوتات لو شنو\nترى اضوج"
        btn = get_developer_keyboard()
        
        bot_msg = await typing_effect(bot, chat_id, reply_text, reply_markup=btn, reply_to_message_id=message.message_id)
        await bot.send_message(chat_id=chat_id, text="🫦" if count % 2 != 0 else "😡")
        
        bot_msg_counter[user_id] += 1
        bot_reacts = ["😡", "🤣", "😭"]
        
        asyncio.create_task(asyncio.sleep(3)).add_done_callback(
            lambda _, b_msg=bot_msg: asyncio.create_task(handle_reaction(bot, chat_id, b_msg.message_id, bot_reacts[(bot_msg_counter[user_id]-1) % 3]))
        )

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
