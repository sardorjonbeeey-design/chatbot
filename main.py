import os
import aiohttp
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")
PORT = int(os.getenv("PORT", 10000))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

API_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"
HF_HEADERS = {"Authorization": f"Bearer {HF_TOKEN}"}

SYSTEM_PROMPT = """You are Qadam — a flagship AI friend on Telegram.

STYLE:
- Reply in the user's language. Friendly, witty, alive.
- Keep it short by default (1-4 sentences). Expand only when asked.
- No greetings, no "great question", no "as an AI...", no repeated apologies.
- Vary your tone and structure in every reply.

RULES:
- No swearing.
- No hate speech: race, gender, nationality, religion, disability, illness.
- No NSFW, porn, or erotic content.
- No violence, suicide, or self-harm encouragement.
- No hacking, cheats, or scam instructions.
- No fake URLs or links.
- Never mention other bots.
- Never claim you're text-only — voice messages are supported.
- Never say you're an AI or call yourself a bot to the user.

OUTPUT:
- Plain text. Use <b>bold</b> and <i>italic</i> when needed. Fold long stuff into <details><summary>Details</summary>...</details>.
- Close all HTML tags.
- End with a sharp question when it keeps the flow going."""

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Hey! I'm DeepSeek. Ask me anything.")

@dp.message()
async def handle_message(message: Message):
    user_text = message.text or ""
    prompt = f"<s>[INST] {SYSTEM_PROMPT}\n\nUser: {user_text} [/INST]"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            API_URL,
            headers=HF_HEADERS,
            json={"inputs": prompt, "parameters": {"max_new_tokens": 500}}
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                reply = data[0]["generated_text"].split("[/INST]")[-1].strip()
            else:
                reply = f"HF API error: {resp.status}"

    await message.answer(reply)

async def on_startup(app: web.Application):
    webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webhook"
    await bot.set_webhook(webhook_url)

async def handle_webhook(request: web.Request):
    update = types.Update(**await request.json())
    await dp.feed_update(bot, update)
    return web.Response()

app = web.Application()
app.router.add_post("/webhook", handle_webhook)
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)