import os
import asyncio
import logging
import json
import re
from contextlib import suppress
from datetime import date
import aiohttp
import tempfile
import edge_tts
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
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")
BACKUP_CHANNEL_ID = os.getenv("BACKUP_CHANNEL_ID")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().lstrip("-").isdigit()}
PORT = int(os.getenv("PORT", 10000))

DAILY_LIMIT = 20
MEMORY_TURNS = 10
API_URL = "https://api.poyo.ai/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DS_HEADERS = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
session: aiohttp.ClientSession = None

register_downloader(dp)

SYSTEM_PROMPT = "You are Qadam, a friendly AI assistant on Telegram. Keep replies short (1-4 sentences), witty, and in the user's language."

# --- Helpers ---
async def redis_cmd(*parts):
    if not UPSTASH_URL: return None
    headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}", "Content-Type": "application/json"}
    try:
        async with session.post(UPSTASH_URL, headers=headers, json=list(parts), timeout=5) as resp:
            data = await resp.json()
            return data.get("result") if resp.status == 200 else None
    except: return None

async def append_memory(user_id, role, content):
    key = f"memory:{user_id}"
    await redis_cmd("RPUSH", key, json.dumps({"role": role, "content": content}))
    await redis_cmd("LTRIM", key, -(MEMORY_TURNS * 2), -1)
    await redis_cmd("EXPIRE", key, 2592000)

async def check_limit(user_id):
    usage_key = f"usage:{user_id}:{date.today().isoformat()}"
    count = await redis_cmd("INCR", usage_key)
    if count == 1: await redis_cmd("EXPIRE", usage_key, 172800)
    return int(count or 0) <= DAILY_LIMIT

# --- Handlers ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Salom! Men Qadam. Nima haqida gaplashamiz?")

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_message(message: Message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS and not await check_limit(user_id):
        return await message.answer("😔 Limit tugadi.")

    status_msg = await message.answer("✍️ Javob yozyapman...")
    history = [json.loads(x) for x in (await redis_cmd("LRANGE", f"memory:{user_id}", 0, -1) or [])]
    
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": message.text}]
    }

    try:
        async with session.post(API_URL, headers=DS_HEADERS, json=payload, timeout=40) as resp:
            data = await resp.json()
            reply = data["choices"][0]["message"]["content"].strip()
    except: reply = "Hozir biroz band bo'lib qoldim 🙏"

    with suppress(Exception): await status_msg.delete()
    await message.answer(reply, parse_mode="HTML")
    await append_memory(user_id, "user", message.text)
    await append_memory(user_id, "assistant", reply)

# --- Webhook Handler ---
async def webhook_handler(request):
    data = await request.json()
    await dp.feed_update(bot, types.Update(**data))
    return web.Response()

async def on_startup(app):
    global session
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/webhook"
    await bot.set_webhook(webhook_url)

async def on_shutdown(app):
    await session.close()
    await bot.session.close()

if __name__ == "__main__":
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_post("/webhook", webhook_handler)
    web.run_app(app, host="0.0.0.0", port=PORT)