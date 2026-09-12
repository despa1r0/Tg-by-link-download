"""Public download API and platform-provider orchestration."""

import asyncio
import os
import uuid

from bot.config import DOWNLOADS_DIR
from bot.services.providers import instagram, reddit, tiktok, twitter, ytdlp
from bot.services.providers.common import download_file


async def extract_info(url: str) -> dict | None:
    """Extract metadata, using specialized providers before/after yt-dlp as needed."""
    loop = asyncio.get_running_loop()

    if tiktok.is_tiktok_url(url):
        photo_urls = await loop.run_in_executor(None, tiktok.extract_photos, url)
        if photo_urls:
            return {"_tiktok_photos": photo_urls, "extractor_key": "TikTok"}
        url = tiktok.normalize_url(url)

    if twitter.is_twitter_url(url):
        media = await loop.run_in_executor(None, twitter.extract_media, url)
        if media:
            return {
                "_twitter_media": media,
                "extractor_key": "Twitter",
                "title": media.get("title"),
            }

    # Prefer embed proxies for sites that frequently block anonymous VPS traffic.
    if instagram.is_instagram_url(url):
        media = await loop.run_in_executor(None, instagram.extract_proxy_media, url)
        if media:
            return {
                "_instagram_media": media,
                "extractor_key": "Instagram",
                "title": media.get("title"),
            }

    if reddit.is_reddit_url(url):
        media = await loop.run_in_executor(None, reddit.extract_proxy_media, url)
        if media:
            return {
                "_reddit_media": media,
                "extractor_key": "Reddit",
                "title": media.get("title"),
            }

    info = await ytdlp.extract_info(url)
    return _tiktok_thumbnail_fallback(url, info) if info else None


def _tiktok_thumbnail_fallback(url: str, info: dict) -> dict:
    if not tiktok.is_tiktok_url(url):
        return info
    has_video = bool(info.get("vcodec") and info.get("vcodec") != "none")
    if has_video:
        return info
    thumbnails = []
    for thumbnail in info.get("thumbnails", []):
        thumbnail_url = thumbnail.get("url", "")
        if thumbnail_url and thumbnail_url not in thumbnails:
            thumbnails.append(thumbnail_url)
    return {"_tiktok_photos": thumbnails, "extractor_key": "TikTok"} if thumbnails else info


def is_gallery(info: dict) -> bool:
    if not info:
        return False
    if "_tiktok_photos" in info:
        return True
    entries = info.get("entries")
    return bool(entries and len(list(entries)) > 1)


def get_gallery_count(info: dict) -> int:
    if not info:
        return 0
    if "_tiktok_photos" in info:
        return len(info["_tiktok_photos"])
    entries = info.get("entries")
    return len(list(entries)) if entries else 1


async def download_tiktok_photos(
    photo_urls: list[str], indices: list[int] | None = None
) -> list[str]:
    selected = [
        url for index, url in enumerate(photo_urls, 1)
        if indices is None or index in indices
    ]
    return await _download_direct_files(selected, "jpeg")


download_twitter_media = twitter.download_media


async def _download_direct_files(urls: list[str], extension: str) -> list[str]:
    loop = asyncio.get_running_loop()
    paths = []
    for url in urls:
        destination = os.path.join(DOWNLOADS_DIR, f"{uuid.uuid4()}.{extension}")
        if await loop.run_in_executor(None, download_file, url, destination):
            paths.append(destination)
    return paths


async def download_media(
    url: str, media_type: str, playlist_items: str | None = None
) -> list[str]:
    """Download video, audio, or gallery media from a supported URL."""
    loop = asyncio.get_running_loop()

    if instagram.is_instagram_url(url):
        media = await loop.run_in_executor(None, instagram.extract_proxy_media, url)
        if media:
            files = await ytdlp.download(media["url"], media_type, playlist_items)
            if files:
                return files

    if reddit.is_reddit_url(url):
        media = await loop.run_in_executor(None, reddit.extract_proxy_media, url)
        if media:
            files = await ytdlp.download(media["url"], media_type, playlist_items)
            if files:
                return files

    download_url = tiktok.normalize_url(url) if tiktok.is_tiktok_url(url) else url
    return await ytdlp.download(download_url, media_type, playlist_items)
