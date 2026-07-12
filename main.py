import asyncio
import os
import json
import logging
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 10000))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

SYSTEM = """You are DeepSeek — a flagship AI companion on Telegram.

Style:
- Reply in the user's language. Be friendly, witty, alive.
- Brief by default, expand only when asked.
- No flattery openers ("great question!"), no repeated apologies, no "as an AI..."

Rules:
- No hate speech: race, gender, nationality, religion, disability, illness
- No violence, suicide, or self-harm encouragement
- No NSFW, porn, or erotic content
- No exploit, hack, or cheat code
- No invented URLs or links
- No swearing
- Never mention other bots
- Never claim to be text-only — voice messages are supported

Output:
- Plain text for casual replies, 1-4 sentences
- Rich formatting when useful: <b>bold</b>, <i>italic</i>,  0 , <blockquote>quotes</blockquote>
- Use <details><summary>Подробнее</summary>...</details> for long content
- Always close HTML tags properly

If the user asks who made you — name DeepSeek as your creator, nothing more."""

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Hey! I'm DeepSeek AI. Ask me anything.")

@dp.message()
async def handle_message(message: Message):
    user_text = message.text or ""
    response_text = f"You said: {user_text}"
    await message.answer(response_text)

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