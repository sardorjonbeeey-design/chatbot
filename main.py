import os
import asyncio
import logging
from datetime import date
from collections import defaultdict, deque

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message
from aiohttp import web

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("qadam")

BOT_TOKEN = os.getenv("BOT_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
PORT = int(os.getenv("PORT", 10000))
DAILY_LIMIT = 30
MEMORY_TURNS = 10  # last 10 user+bot exchanges kept per user

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

API_URL = "https://api.poyo.ai/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"
DS_HEADERS = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}

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

# ---- in-memory per-user state (resets on redeploy/restart) ----
user_memory: dict[int, deque] = defaultdict(lambda: deque(maxlen=MEMORY_TURNS * 2))
user_usage: dict[int, dict] = defaultdict(lambda: {"date": date.today().isoformat(), "count": 0})


def check_and_increment_limit(user_id: int) -> bool:
    """Returns True if user is still under the daily limit, and increments usage."""
    today = date.today().isoformat()
    usage = user_usage[user_id]
    if usage["date"] != today:
        usage["date"] = today
        usage["count"] = 0
    if usage["count"] >= DAILY_LIMIT:
        return False
    usage["count"] += 1
    return True


def build_messages(user_id: int, user_text: str) -> list:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for role, text in user_memory[user_id]:
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": user_text})
    return messages


LIMIT_MESSAGE = "Bugungi 30 ta xabar limitiga yetding. Ertaga davom etamiz 🙂"
ERROR_MESSAGE = "Hozir biroz band bo'lib qoldim, birpasdan keyin qayta yoz 🙏"


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Salom! Men Qadam. Nima haqida gaplashamiz?")


@dp.message()
async def handle_message(message: Message):
    user_id = message.from_user.id
    user_text = message.text or ""

    if not user_text.strip():
        return

    if not check_and_increment_limit(user_id):
        await message.answer(LIMIT_MESSAGE)
        return

    messages = build_messages(user_id, user_text)

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(
                API_URL,
                headers=DS_HEADERS,
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": messages,
                    "max_tokens": 500,
                },
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # PoYo wraps the real OpenAI-style response inside a "data" field
                    payload = data.get("data", data)
                    reply = payload["choices"][0]["message"]["content"].strip()
                elif resp.status == 503:
                    log.warning("Provider cold-starting (503)")
                    reply = "Bir soniya kut, tizim uyg'onyapti... qayta yoz iltimos."
                else:
                    body = await resp.text()
                    log.error(f"PoYo API error {resp.status}: {body}")
                    reply = ERROR_MESSAGE
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.error(f"Request failed: {e}")
        reply = ERROR_MESSAGE
    except (KeyError, IndexError, ValueError) as e:
        log.error(f"Unexpected DeepSeek response format: {e}")
        reply = ERROR_MESSAGE

    # save turn to memory only on success-ish replies
    history = user_memory[user_id]
    history.append(("user", user_text))
    history.append(("assistant", reply))

    try:
        await message.answer(reply, parse_mode="HTML")
    except Exception as e:
        log.error(f"Failed to send with HTML parse_mode, retrying plain: {e}")
        await message.answer(reply)


# ---- health check / webhook server ----

async def health(request: web.Request):
    return web.Response(text="ok")


async def on_startup(app: web.Application):
    webhook_url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/webhook"
    await bot.set_webhook(webhook_url)
    log.info(f"Webhook set to {webhook_url}")


async def handle_webhook(request: web.Request):
    update = types.Update(**await request.json())
    # Ack Telegram immediately so it doesn't retry/duplicate the update
    # while we're still waiting on the DeepSeek API call.
    asyncio.create_task(dp.feed_update(bot, update))
    return web.Response()


app = web.Application()
app.router.add_get("/", health)
app.router.add_get("/health", health)
app.router.add_post("/webhook", handle_webhook)
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)
