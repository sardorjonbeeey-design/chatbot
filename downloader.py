"""
Link downloader for Qadam — YouTube, TikTok, Instagram, X, Reddit, SoundCloud, Vimeo,
Pinterest — via a self-hosted Cobalt instance.

This file is fully self-contained and isolated from main.py's DeepSeek/memory/limit logic.
It registers its own message handler on the shared Dispatcher, matching only messages that
contain a supported link — everything else falls through untouched to main.py's own handlers.

Integration (only 2 lines needed in main.py):
    from downloader import register_downloader
    register_downloader(dp)   # call this right after `dp = Dispatcher()`, before other handlers
"""

import os
import re
import time
import logging
import asyncio
from typing import Optional, Tuple

import aiohttp
from aiogram import Dispatcher
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

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

# Platforms where the user almost certainly wants audio, not video
AUDIO_ONLY_DOMAINS = ("soundcloud.com", "music.youtube.com")

# Known Cobalt error codes -> friendly Uzbek messages. Falls back to a generic
# message for anything not in this table (Cobalt's error list can grow over time).
ERROR_MESSAGES = {
    "error.api.link.invalid": "❌ Havola noto'g'ri ko'rinishda.",
    "error.api.link.unsupported": "❌ Bu platforma yoki havola turi hozircha qo'llab-quvvatlanmaydi.",
    "error.api.fetch.empty": "🔒 Topilmadi — post yopiq (private) yoki o'chirilgan bo'lishi mumkin.",
    "error.api.fetch.fail": "⚠️ Manbadan yuklab bo'lmadi. Birozdan keyin qayta urinib ko'ring.",
    "error.api.fetch.critical": "⚠️ Manbada texnik xatolik yuz berdi. Birozdan keyin qayta urinib ko'ring.",
    "error.api.fetch.rate": "⏳ Juda ko'p so'rov yuborildi. Birozdan keyin qayta urinib ko'ring.",
    "error.api.rate_exceeded": "⏳ Juda ko'p so'rov yuborildi. Birozdan keyin qayta urinib ko'ring.",
    "error.api.content.too_long": "📏 Fayl juda uzun — yuklab bo'lmaydi.",
    "error.api.youtube.login": "🔒 Bu video login talab qiladi (yosh cheklovi yoki xususiy bo'lishi mumkin).",
}
DEFAULT_ERROR_MESSAGE = "😕 Havolani qayta ishlab bo'lmadi. Boshqa havola bilan urinib ko'ring."
NOT_CONFIGURED_MESSAGE = "🚧 Yuklab olish xizmati hozircha sozlanmagan. Birozdan keyin urinib ko'r."
TIMEOUT_MESSAGE = "⏳ Server javob berishga ancha vaqt oldi. Birozdan keyin qayta urinib ko'ring."
OFFLINE_MESSAGE = "🚫 Yuklab olish xizmati hozircha ishlamayapti. Birozdan keyin qayta urinib ko'ring."
INVALID_RESPONSE_MESSAGE = "😕 Kutilmagan javob keldi. Qayta urinib ko'ring."

# Same blockquote-styled action vocabulary used across the whole bot (see main.py) —
# duplicated here in miniature since this file is intentionally self-contained and
# doesn't import from main.py. Telegram has no true "colored text"; <blockquote> is the
# closest thing to a distinct "system status" look available in its HTML parse mode.
ACTION_LABELS = {
    "checking": "🔎 Tekshiryapman",
    "downloading": "⬇️ Yuklab olyapman",
    "uploading": "⬆️ Yuklayapman",
}
DOWNLOAD_STAGES = [(0, "checking"), (2, "downloading")]


def render_status(action_key: str, dots: str) -> str:
    label = ACTION_LABELS.get(action_key, action_key)
    return f"<blockquote>{label}{dots}</blockquote>"


def contains_supported_link(message: Message) -> bool:
    return bool(message.text) and bool(LINK_PATTERN.search(message.text))


def error_message_for_code(code: Optional[str]) -> str:
    if not code:
        return DEFAULT_ERROR_MESSAGE
    return ERROR_MESSAGES.get(code, DEFAULT_ERROR_MESSAGE)


async def resolve_link(url: str) -> Tuple[Optional[dict], Optional[str]]:
    """Calls the self-hosted Cobalt instance to resolve a media URL.

    Returns (data, reason):
      - (data, None)            -> got a parseable JSON response (may itself be a
                                    Cobalt-level error like private/unsupported —
                                    caller reads data["error"]["code"] for that)
      - (None, "not_configured") -> COBALT_API_URL isn't set
      - (None, "timeout")        -> request timed out
      - (None, "offline")        -> couldn't reach Cobalt, or it 5xx'd
      - (None, "invalid_response") -> got a response but couldn't parse it as JSON
    """
    if not COBALT_API_URL:
        log.warning("COBALT_API_URL not set — downloader is inactive")
        return None, "not_configured"

    endpoint = COBALT_API_URL.rstrip("/") + "/"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    download_mode = "audio" if any(d in url.lower() for d in AUDIO_ONLY_DOMAINS) else "auto"
    body = {"url": url, "videoQuality": "1080", "downloadMode": download_mode}

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.post(endpoint, headers=headers, json=body) as resp:
                try:
                    data = await resp.json()
                except (aiohttp.ContentTypeError, ValueError) as e:
                    log.error(f"Cobalt returned non-JSON response for {url}: {e}")
                    return None, "invalid_response"

                if resp.status >= 500:
                    log.error(f"Cobalt server error {resp.status} for {url}: {data}")
                    return None, "offline"

                # Cobalt returns structured JSON error bodies even on 4xx (e.g. private
                # posts come back as HTTP 400 with {"status":"error","error":{"code":...}})
                # so we hand the body to the caller regardless of status code here.
                log.info(f"Cobalt resolved {url} -> status={data.get('status')}")
                return data, None
    except asyncio.TimeoutError:
        log.error(f"Cobalt request timed out for {url}")
        return None, "timeout"
    except aiohttp.ClientConnectorError as e:
        log.error(f"Cannot reach Cobalt instance for {url}: {e}")
        return None, "offline"
    except aiohttp.ClientError as e:
        log.error(f"Cobalt request failed for {url}: {e}")
        return None, "offline"


def extract_link_and_note(data: dict) -> Tuple[Optional[str], str]:
    """Pulls a usable direct link out of Cobalt's response, whatever shape it came back in."""
    status = data.get("status")

    if status in ("redirect", "tunnel"):
        return data.get("url"), ""

    if status == "picker":
        items = data.get("picker") or []
        if items:
            note = f" (jami {len(items)} ta fayl topildi, birinchisi yuborildi)" if len(items) > 1 else ""
            return items[0].get("url"), note
        return None, ""

    # anything else (including "error") has no usable link — caller reports the error
    return None, ""


async def safe_delete(msg: Message):
    try:
        await msg.delete()
    except Exception:
        pass


async def safe_edit(msg: Message, text: str, parse_mode: Optional[str] = None):
    try:
        await msg.edit_text(text, parse_mode=parse_mode)
    except Exception:
        pass


async def send_result(message: Message, link: str, note: str, source_url: str) -> bool:
    """Tries to deliver the media directly into the chat (video/audio/document, in that
    order of preference). Returns True if any direct send succeeded. If every attempt
    fails (usually because the file is too large for Telegram's URL-fetch limits), the
    caller falls back to an inline button instead — the raw link is never shown as text."""
    caption = f"✅ Tayyor!{note}" if note else "✅ Tayyor!"
    is_audio = any(d in source_url.lower() for d in AUDIO_ONLY_DOMAINS)

    attempts = (
        [("audio", message.answer_audio)]
        if is_audio
        else [("video", message.answer_video), ("document", message.answer_document)]
    )

    for kind, send_func in attempts:
        try:
            await send_func(**{kind: link}, caption=caption)
            return True
        except Exception as e:
            log.warning(f"Direct {kind} send failed for {source_url}: {e}")

    return False


def register_downloader(dp: Dispatcher):
    @dp.message(contains_supported_link)
    async def handle_download(message: Message):
        url_match = re.search(r"https?://\S+", message.text)
        if not url_match:
            return
        source_url = url_match.group(0)

        status_msg = await message.answer(render_status(DOWNLOAD_STAGES[0][1], "."), parse_mode="HTML")
        stop_event = asyncio.Event()

        def stage_for(elapsed: int) -> str:
            current = DOWNLOAD_STAGES[0][1]
            for threshold, key in DOWNLOAD_STAGES:
                if elapsed >= threshold:
                    current = key
            return current

        async def animate_status():
            start = time.monotonic()
            dot_count = 0
            while not stop_event.is_set():
                elapsed = int(time.monotonic() - start)
                stage = stage_for(elapsed)
                dots = "." * ((dot_count % 3) + 1)
                await safe_edit(status_msg, render_status(stage, dots), parse_mode="HTML")
                dot_count += 1
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=1.2)
                except asyncio.TimeoutError:
                    pass

        animate_task = asyncio.create_task(animate_status())

        data, reason = await resolve_link(source_url)

        stop_event.set()
        await animate_task

        if reason == "not_configured":
            await safe_edit(status_msg, NOT_CONFIGURED_MESSAGE)
            return
        if reason == "timeout":
            await safe_edit(status_msg, TIMEOUT_MESSAGE)
            return
        if reason == "offline":
            await safe_edit(status_msg, OFFLINE_MESSAGE)
            return
        if reason == "invalid_response":
            await safe_edit(status_msg, INVALID_RESPONSE_MESSAGE)
            return
        if not data:
            await safe_edit(status_msg, DEFAULT_ERROR_MESSAGE)
            return

        link, note = extract_link_and_note(data)

        if not link:
            code = (data.get("error") or {}).get("code")
            if code:
                log.warning(f"Cobalt error for {source_url}: {code}")
            await safe_edit(status_msg, error_message_for_code(code))
            return

        await safe_edit(status_msg, render_status("uploading", "..."), parse_mode="HTML")

        sent = await send_result(message, link, note, source_url)
        await safe_delete(status_msg)

        if not sent:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔗 Havola", url=link)]]
            )
            text = (
                f"✅ Tayyor!{note}\n\n"
                "⏳ Fayl juda katta yoki formati mos emas — tugma orqali yuklab oling. "
                "Havola vaqtinchalik amal qiladi."
            )
            await message.answer(text, reply_markup=keyboard)
