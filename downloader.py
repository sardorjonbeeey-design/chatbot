"""
Link downloader for Qadam — TikTok & Instagram, no watermark, via a self-hosted Cobalt instance.

This file is fully self-contained and isolated from bot.py's DeepSeek/memory/limit logic.
It registers its own message handler on the shared Dispatcher, matching only messages that
contain a supported link — everything else falls through untouched to bot.py's own handlers.

Integration (only 2 lines needed in bot.py):
    from downloader import register_downloader
    register_downloader(dp)   # call this right after `dp = Dispatcher()`, before other handlers
"""

import os
import re
import logging
import asyncio
import aiohttp
from aiogram import Dispatcher
from aiogram.types import Message

log = logging.getLogger("qadam.downloader")

COBALT_API_URL = os.getenv("COBALT_API_URL")  # e.g. https://qadam-cobalt.onrender.com

LINK_PATTERN = re.compile(
    r"(youtube\.com|youtu\.be|m\.youtube\.com|music\.youtube\.com|"
    r"tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com|"
    r"instagram\.com|instagr\.am|"
    r"x\.com|twitter\.com|"
    r"reddit\.com|"
    r"soundcloud\.com|"
    r"vimeo\.com|"
    r"pinterest\.com)",
    re.IGNORECASE,
)


def contains_supported_link(message: Message) -> bool:
    return bool(message.text) and bool(LINK_PATTERN.search(message.text))


async def resolve_link(url: str) -> dict | None:
    """Calls the self-hosted Cobalt instance to resolve a media URL. Returns the parsed
    JSON response, or None if the request fails or Cobalt isn't configured."""
    if not COBALT_API_URL:
        log.warning("COBALT_API_URL not set — downloader is inactive")
        return None

    endpoint = COBALT_API_URL.rstrip("/") + "/"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    body = {
    "url": url,
    "videoQuality": "1080",
    "downloadMode": "auto",
    "youtubeVideoCodec": "h264",
    "youtubeDubLang": "en",
}

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(endpoint, headers=headers, json=body) as resp:
                data = await resp.json()
                if resp.status != 200:
                    log.error(f"Cobalt error {resp.status}: {data}")
                    return None
                return data
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.error(f"Cobalt request failed: {e}")
        return None
    except (ValueError, TypeError) as e:
        log.error(f"Cobalt returned unexpected response: {e}")
        return None


def extract_link_and_note(data: dict) -> tuple[str | None, str]:
    """Pulls a usable direct link out of Cobalt's response, whatever shape it came back in."""
    status = data.get("status")

    if status in ("redirect", "tunnel"):
        return data.get("url"), ""

    if status == "picker":
        items = data.get("picker") or []
        if items:
            return items[0].get("url"), (
                f" (bir nechta fayl topildi, birinchisi yuborildi — jami {len(items)} ta)"
                if len(items) > 1 else ""
            )
        return None, ""

    if status == "error":
        code = (data.get("error") or {}).get("code", "unknown")
        log.warning(f"Cobalt returned error status: {code}")
        return None, ""

    log.warning(f"Unhandled Cobalt status: {status}")
    return None, ""


def register_downloader(dp: Dispatcher):
    @dp.message(contains_supported_link)
    async def handle_download(message: Message):
        url_match = re.search(r"https?://\S+", message.text)
        if not url_match:
            return
        source_url = url_match.group(0)

        status_msg = await message.answer("⬇️ Havolani qayta ishlayapman...")

        if not COBALT_API_URL:
            await status_msg.edit_text(
                "🚧 Yuklab olish xizmati hozircha sozlanmagan. Birozdan keyin urinib ko'r."
            )
            return

        data = await resolve_link(source_url)

        try:
            await status_msg.delete()
        except Exception:
            pass

        if not data:
            await message.answer("Havolani ochib bo'lmadi — qayta urinib ko'r yoki boshqa link yubor 🙏")
            return

        link, note = extract_link_and_note(data)
        if not link:
            await message.answer("Bu havoladan faylni topa olmadim 😕 Ochiq/ommaviy post ekanligini tekshir.")
            return

        await message.answer(f"✅ Tayyor!{note}\n{link}")
