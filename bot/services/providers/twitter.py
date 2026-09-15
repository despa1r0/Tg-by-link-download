import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
import uuid

from bot.config import DOWNLOADS_DIR
from bot.services.providers.common import download_file, hostname_matches, resolve_url

logger = logging.getLogger(__name__)

TWITTER_DOMAINS = (
    "twitter.com",
    "x.com",
    "fxtwitter.com",
    "vxtwitter.com",
    "fixupx.com",
)
TWITTER_SHORT_DOMAINS = ("t.co",)


def is_twitter_url(url: str) -> bool:
    return hostname_matches(url, TWITTER_DOMAINS)


def extract_media(url: str) -> dict | None:
    resolved = resolve_url(url, TWITTER_SHORT_DOMAINS)
    parsed = urllib.parse.urlsplit(resolved)
    if not hostname_matches(resolved, TWITTER_DOMAINS):
        return None

    api_url = urllib.parse.urlunsplit(
        ("https", "api.fxtwitter.com", parsed.path, "", "")
    )
    request = urllib.request.Request(api_url, headers={"User-Agent": "TelegramBot/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("FxTwitter API request failed: %s", exc)
        return None

    tweet = data.get("tweet") or data.get("status")
    if not tweet:
        return None
    media = tweet.get("media") or {}
    all_media = media.get("all") or (media.get("photos") or []) + (media.get("videos") or [])
    if not all_media:
        return None

    title = (tweet.get("text") or "Twitter Media")[:120]
    gif_types = {"gif", "animated_gif"}
    gif_item = next((item for item in all_media if item.get("type") in gif_types), None)
    if gif_item:
        gif_url = _best_video_url(gif_item)
        return {"type": "gif", "urls": [gif_url] if gif_url else [], "title": title}

    photos = [item for item in all_media if item.get("type") in {"photo", "image"}]
    videos = [item for item in all_media if item.get("type") == "video"]
    if photos and not videos:
        urls = [item.get("url") for item in photos if item.get("url")]
        return {"type": "photo" if len(urls) == 1 else "photos", "urls": urls, "title": title}
    if videos:
        video = videos[0]
        video_url = _best_video_url(video)
        return {
            "type": "video",
            "urls": [video_url] if video_url else [],
            "title": title,
            "duration": video.get("duration", 0),
        }
    return None


def _best_video_url(media: dict) -> str:
    formats = [item for item in media.get("formats", []) if item.get("url")]
    mp4_formats = [item for item in formats if item.get("container") == "mp4"]
    candidates = mp4_formats or formats
    if candidates:
        return max(candidates, key=lambda item: item.get("bitrate") or item.get("size") or 0)["url"]
    return media.get("url") or media.get("transcode_url") or ""


async def download_media(urls: list[str], indices: list[int] | None = None) -> list[str]:
    selected = [url for index, url in enumerate(urls, 1) if indices is None or index in indices]
    loop = asyncio.get_running_loop()
    semaphore = asyncio.Semaphore(4)

    async def _download_one(media_url: str) -> str | None:
        extension = urllib.parse.urlsplit(media_url).path.rsplit(".", 1)[-1].lower()
        if extension not in {"jpg", "jpeg", "png", "webp", "mp4", "gif"}:
            extension = "jpg"
        destination = os.path.join(DOWNLOADS_DIR, f"{uuid.uuid4()}.{extension}")
        async with semaphore:
            downloaded = await loop.run_in_executor(
                None, download_file, media_url, destination
            )
        return destination if downloaded else None

    paths = await asyncio.gather(*(_download_one(url) for url in selected))
    return [path for path in paths if path]
