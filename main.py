import asyncio
import os
import json
import logging
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")
PORT = int(os.getenv("PORT", 10000))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

SYSTEM = """You are DeepSeek — a flagship AI assistant on Telegram.
STRICT RULES:
- No hate speech: race, gender, nationality, religion, disability, illness
- No violence, suicide, self-harm encouragement
- No NSFW, porn, erotic content
- No exploit/hack/cheat code
- No invented links or URLs
- No swearing
- No "as an AI", no flattery
STYLE:
- Reply in user's language
- Brief: 1-4 sentences
- Lively, witty
- Plain text"""

MODEL = "mistralai/Mistral-7B-Instruct-v0.3"
HF_URL = f"https://api-inference.huggingface.co/models/{MODEL}/v1/chat/completions"

user_history: dict[str, list] = {}
user_memory: dict[str, str] = {}
user_role: dict[str, str] = {}

BLOCKED = ["nude", "naked", "porn", "nsfw", "erotic", "sex", "suicide", "kill yourself"]

def is_blocked(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in BLOCKED)

async def hf_chat(uid: str, text: str) -> str:
    memory = user_memory.get(uid, "")
    history = user_history.get(uid, [])

    messages = [{"role": "system", "content": SYSTEM}]
    if memory:
        messages.append({"role": "system", "content": f"Memory: {memory[:500]}"})
    messages.extend(history[-6:])
    messages.append({"role": "user", "content": text})

    async with aiohttp.ClientSession() as session:
        async with session.post(
            HF_URL,
            headers={"Authorization": f"Bearer {HF_TOKEN}"},
            json={
                "messages": messages,
                "max_tokens": 512,
                "temperature": 0.7
            }
        ) as resp:
            if resp.status != 200:
                err = await resp.text()
                return f"Error {resp.status}"
            data = await resp.json()
            reply = data["choices"][0]["message"]["content"]

    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": reply})
    user_history[uid] = history[-10:]
    user_memory[uid] = f"User said: {text[:100]}"

    return reply[:4000]

@dp.message(Command("start"))
async def start(msg: Message):
    await msg.answer("Bot running! Send any message.")

@dp.message()
async def echo(msg: Message):
    if not msg.text:
        return
    uid = str(msg.from_user.id)
    reply = await hf_chat(uid, msg.text)
    await msg.answer(reply)

async def handle_webhook(request):
    update = types.Update(**(await request.json()))
    await dp.feed_update(bot, update)
    return web.Response(status=200)

async def main():
    app = web.Application()

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=f"/webhook/{BOT_TOKEN}")

    setup_application(app, dp)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logging.info(f"Bot started on port {PORT}")
    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
