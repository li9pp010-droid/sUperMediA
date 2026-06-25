import os
import re
import asyncio
import collections
import mimetypes
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction

TOKEN = os.getenv("TELEGRAM_TOKEN")
DEVELOPER_ID = "8597653867"

user_queues = collections.defaultdict(list)
user_processing = collections.defaultdict(bool)
user_msg_counter = collections.defaultdict(lambda: 0)
bot_msg_counter = collections.defaultdict(lambda: 0)

def filter_title(text):
    if not text: return "Unknown"
    cleaned = re.sub(r'[\#\*\?\\/\|:\<\>"\']', '', text)
    cleaned = re.sub(r'[̀-ͯ҃-҉᷀-᷿⃐-⃿︠-︯]', '', cleaned)
    return cleaned.strip()

async def typing_effect(bot, chat_id, full_text: str, reply_markup=None, existing_message=None):
    words = full_text.split()
    chunks, i, toggle = [], 0, True
    while i < len(words):
        if toggle:
            chunks.append(" ".join(words[i:i+2]))
            i += 2; toggle = False
        else:
            word = words[i]
            chunks.append(word[:3])
            words[i] = word[3:]
            if not words[i]: i += 1
            toggle = True
    current_text, message = "", existing_message
    for idx, chunk in enumerate(chunks):
        if not chunk: continue
        current_text = (current_text + " " + chunk).strip() if idx % 2 == 0 else current_text + chunk
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        if message is None: message = await bot.send_message(chat_id=chat_id, text=current_text)
        else:
            try: await message.edit_text(text=current_text)
            except: pass
        await asyncio.sleep(0.3)
    try: await message.edit_text(text=full_text, reply_markup=reply_markup)
    except: pass
    return message

async def handle_reaction(bot, chat_id, message_id, emoji):
    try: await bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=[{"type": "emoji", "emoji": emoji}])
    except: pass

async def process_queue(user_id, chat_id, context):
    if user_processing[user_id] or not user_queues[user_id]: return
    user_processing[user_id] = True
    url, _ = user_queues[user_id].pop(0)
    try:
        with yt_dlp.YoutubeDL({'skip_download': True, 'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            if (info.get('filesize') or info.get('filesize_approx') or 0) > 456*1024*1024:
                raise Exception
    except:
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("تواصل مع المطور", url=f"tg://user?id={DEVELOPER_ID}")]])
        err_msg = await typing_effect(context.bot, chat_id, "الرابط مو مدعوم او الموقع مو\nمدعوم", reply_markup=btn)
        await context.bot.send_message(chat_id=chat_id, text="👈🏻👉🏻")
        user_processing[user_id] = False; asyncio.create_task(process_queue(user_id, chat_id, context)); return

    status_msg = await typing_effect(context.bot, chat_id, "تم استلام الرابط والبدأ بتنزيل الميديا\nمولاي 0%")
    await context.bot.send_message(chat_id=chat_id, text="⏳")
    
    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                percent = int((downloaded / total) * 100)
                text_update = f"تم استلام الرابط والبدأ بتنزيل الميديا\nمولاي {percent}%" if percent < 100 else "تم استلام الرابط والبدأ بتنزيل الميديا\nمولاي"
                try:
                    loop = asyncio.get_event_loop()
                    loop.create_task(context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text=text_update))
                except: pass

    os.makedirs('downloads', exist_ok=True)
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': 'downloads/%(id)s.%(ext)s',
        'quiet': True,
        'progress_hooks': [progress_hook],
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl: info = ydl.extract_info(url, download=True)
        
        try: await context.bot.edit_message_text(chat_id=chat_id, message_id=status_msg.message_id, text="تم استلام الرابط والبدأ بتنزيل الميديا\nمولاي")
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
                ext = os.path.splitext(f)[1] or '.mp4'
                new_path = f"downloads/{filter_title(info.get('uploader') or 'Channel')}_{info.get('id')}_{len(clean_files)+1}{ext}"
                os.rename(f, new_path); clean_files.append(new_path)
        
        if clean_files:
            for chunk_idx in range(0, len(clean_files), 8):
                group = clean_files[chunk_idx:chunk_idx+8]
                if len(group) > 1:
                    await context.bot.send_media_group(chat_id=chat_id, media=[InputMediaDocument(open(f, 'rb'), filename=os.path.basename(f)) for f in group])
                else: await context.bot.send_document(chat_id=chat_id, document=open(group[0], 'rb'), filename=os.path.basename(group[0]))
            
            await typing_effect(context.bot, chat_id, "العملية صارت بدون مشاكل\nتفضل مولاي")
            await context.bot.send_message(chat_id=chat_id, text="🍓")
        else: raise Exception
    except:
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("تواصل مع المطور", url=f"tg://user?id={DEVELOPER_ID}")]])
        await typing_effect(context.bot, chat_id, "الرابط مو مدعوم او الموقع مو\nمدعوم", reply_markup=btn)
        await context.bot.send_message(chat_id=chat_id, text="👈🏻👉🏻")
    finally:
        for f in os.listdir('downloads') if os.path.exists('downloads') else []: 
            try: os.remove(os.path.join('downloads', f))
            except: pass
        user_processing[user_id] = False; asyncio.create_task(process_queue(user_id, chat_id, context))

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    user_id, chat_id = update.message.from_user.id, update.message.chat.id
    text = update.message.text or ""
    url_match = re.search(r'https?://[^\s]+', text)
    
    if url_match:
        if len(user_queues[user_id]) < 8:
            user_queues[user_id].append((url_match.group(0), update.message.message_id))
            asyncio.create_task(handle_reaction(context.bot, chat_id, update.message.message_id, "🍌"))
            if not user_processing[user_id]: asyncio.create_task(process_queue(user_id, chat_id, context))
    else:
        user_msg_counter[user_id] += 1
        count = user_msg_counter[user_id]
        reacts = ["🥰", "😭", "🍓", "😡"]
        asyncio.create_task(asyncio.sleep(3)).add_done_callback(lambda _: asyncio.create_task(handle_reaction(context.bot, chat_id, update.message.message_id, reacts[(count-1) % 4])))
        
        reply_text = "اهلين دز رابط الميديا التريدها عزيزي\nيلا اوف" if count % 2 != 0 else "مو ناوي تستعملني مثل البوتات لو شنو\nترى اضوج"
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("تواصل مع المطور", url=f"tg://user?id={DEVELOPER_ID}")]])
        bot_msg = await typing_effect(context.bot, chat_id, reply_text, reply_markup=btn)
        await context.bot.send_message(chat_id=chat_id, text="🫦" if count % 2 != 0 else "😡")
        
        bot_msg_counter[user_id] += 1
        bot_reacts = ["😡", "🤣", "😭"]
        asyncio.create_task(asyncio.sleep(3)).add_done_callback(lambda _: asyncio.create_task(handle_reaction(context.bot, chat_id, bot_msg.message_id, bot_reacts[(bot_msg_counter[user_id]-1) % 3])))

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, message_handler))
    app.run_polling()

if __name__ == '__main__':
    main()
