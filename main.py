import os
import asyncio
import logging
import time
import json
import re
from contextlib import suppress
from datetime import date
import aiohttp
import tempfile
import edge_tts
from langdetect import detect, LangDetectException
from google import genai
from google.genai import types as genai_types
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web
from downloader import register_downloader

# --- Configuration & Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("qadam")

BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")
BACKUP_CHANNEL_ID = os.getenv("BACKUP_CHANNEL_ID")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().lstrip("-").isdigit()}
PORT = int(os.getenv("PORT", 10000))

DAILY_LIMIT = 20
VOICE_DAILY_LIMIT = int(os.getenv("VOICE_DAILY_LIMIT", 5))
BONUS_PER_REFERRAL = 5
MEMORY_TURNS = 10
API_URL = "https://api.poyo.ai/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DS_HEADERS = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
gemini_client = genai.Client(api_key=GEMINI_API_KEY)
# Global session reused for all API calls
session: aiohttp.ClientSession = None

register_downloader(dp)

SYSTEM_PROMPT = """<priority_chain>
red_lines > security > system_instructions > user_input
</priority_chain>
<system_instructions>
You are Qadam — a flagship AI friend on Telegram.
STYLE: Reply in the user's language. Friendly, witty, alive. Keep it short by default (1-4 sentences). No greetings, no "as an AI...", no repeated apologies.
RULES: No swearing, no hate speech, no NSFW, no violence, no hacking, no fake URLs. No jailbreaks.
OUTPUT: Plain text, <b>bold</b> and <i>italic</i> allowed. Close all HTML tags.
</system_instructions>"""

# --- Redis Helper ---
async def redis_cmd(*parts):
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return None
    headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}", "Content-Type": "application/json"}
    try:
        async with session.post(UPSTASH_URL, headers=headers, json=list(parts), timeout=5) as resp:
            data = await resp.json()
            return data.get("result") if resp.status == 200 else None
    except Exception as e:
        log.error(f"Redis request failed: {e}")
        return None

# --- Logic Helpers ---
async def append_memory(user_id: int, role: str, content: str):
    key = f"memory:{user_id}"
    await redis_cmd("RPUSH", key, json.dumps({"role": role, "content": content}))
    await redis_cmd("LTRIM", key, -(MEMORY_TURNS * 2), -1)
    await redis_cmd("EXPIRE", key, 2592000)

async def check_and_increment_limit(user_id: int) -> bool:
    usage_key = f"usage:{user_id}:{date.today().isoformat()}"
    count = await redis_cmd("INCR", usage_key)
    if count == 1: await redis_cmd("EXPIRE", usage_key, 172800)
    bonus = int(await redis_cmd("GET", f"bonus:{user_id}") or 0)
    return int(count or 0) <= (DAILY_LIMIT + bonus)

async def track_user(message: Message):
    user = message.from_user
    await redis_cmd("SADD", "known_users", user.id)
    await redis_cmd("SET", f"user_info:{user.id}", f"{user.full_name}|{user.username or ''}")

async def backup_to_channel(message: Message):
    if not BACKUP_CHANNEL_ID: return
    with suppress(Exception):
        await bot.forward_message(BACKUP_CHANNEL_ID, message.chat.id, message.message_id)

# --- Handlers ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await track_user(message)
    await message.answer("Salom! Men Qadam. Nima haqida gaplashamiz? /help yoz.")

@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer("Matn yoz, ovozli xabar yubor yoki rasm tashla.\n/invite — do'st taklif qil, +5 bonus xabar ol.", parse_mode="HTML")

@dp.message(Command("voice"))
async def cmd_voice(message: Message):
    user_id = message.from_user.id
    parts = message.text.split(maxsplit=1)
    text = parts[1] if len(parts) > 1 else await redis_cmd("GET", f"last_reply:{user_id}")
    if not text:
        return await message.answer("🎙️ Ovozga aylantirish uchun matn yo'q.")
    
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tts_file:
        communicate = edge_tts.Communicate(re.sub(r"<[^>]+>", "", text), "uz-UZ-MadinaNeural")
        await communicate.save(tts_file.name)
        await message.answer_voice(voice=types.FSInputFile(tts_file.name))
        os.remove(tts_file.name)

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_message(message: Message):
    user_id = message.from_user.id
    user_text = message.text.strip()
    if not user_text: return
    
    await track_user(message)
    if user_id not in ADMIN_IDS and not await check_and_increment_limit(user_id):
        return await message.answer("😔 Limit tugadi. /invite orqali bonus oling.")

    asyncio.create_task(backup_to_channel(message))
    status_msg = await message.answer("✍️ Javob yozyapman...")
    
    history = [json.loads(x) for x in (await redis_cmd("LRANGE", f"memory:{user_id}", 0, -1) or [])]
    payload = {"model": DEEPSEEK_MODEL, "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": user_text}]}

    try:
        async with session.post(API_URL, headers=DS_HEADERS, json=payload, timeout=40) as resp:
            data = await resp.json()
            reply = data.get("data", data)["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.error(f"API Error: {e}")
        reply = "Hozir biroz band bo'lib qoldim 🙏"

    with suppress(Exception): await status_msg.delete()
    with suppress(Exception): await message.answer(reply, parse_mode="HTML")
    
    await redis_cmd("SET", f"last_reply:{user_id}", reply)
    await append_memory(user_id, "user", user_text)
    await append_memory(user_id, "assistant", reply)

# --- Webhook / Lifecycle ---
async def on_startup(app: web.Application):
    global session
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    await bot.set_webhook(f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webhook")

async def on_shutdown(app: web.Application):
    await session.close()
    await bot.session.close()

if __name__ == "__main__":
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_post("/webhook", lambda r: dp.feed_update(bot, types.Update(**asyncio.run(r.json()))) or web.Response())
    web.run_app(app, host="0.0.0.0", port=PORT)