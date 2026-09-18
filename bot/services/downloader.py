"""Public download API and platform-provider orchestration."""

import asyncio
import contextvars
import os
import urllib.parse
import uuid
from collections.abc import Mapping, Sequence

from bot.config import DOWNLOADS_DIR
from bot.services.media_model import MediaItem, MediaResult, from_ytdlp, media_result
from bot.services.providers import instagram, reddit, tiktok, twitter, ytdlp
from bot.services.providers.common import download_file


async def _run_sync(function, *args):
    """Run blocking provider work without losing the request logging context."""
    context = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(
        None, context.run, function, *args
    )


async def extract_info(url: str) -> dict | None:
    """Extract metadata, using specialized providers before/after yt-dlp as needed."""
    if tiktok.is_tiktok_url(url):
        photo_urls = await _run_sync(tiktok.extract_photos, url)
        if photo_urls:
            result = media_result(
                [MediaItem("photo", url, direct_url=value) for value in photo_urls],
                source_url=url,
                provider="tiktok",
            )
            return {"_media": result, "extractor_key": "TikTok"}
        url = tiktok.normalize_url(url)

    if twitter.is_twitter_url(url):
        media = await _run_sync(twitter.extract_media, url)
        if media:
            return {
                "_media": media,
                "extractor_key": "Twitter",
                "title": media.get("title"),
                "duration": media.get("duration"),
            }

    # Resolve the complete post before accepting a proxy's single preview.
    if instagram.is_instagram_url(url) or reddit.is_reddit_url(url):
        info = await ytdlp.extract_info(url)
        if info and (result := from_ytdlp(info, url)):
            return {**info, "_media": result}
        provider = instagram if instagram.is_instagram_url(url) else reddit
        media = await _run_sync(provider.extract_proxy_media, url)
        if media:
            return {
                "_media": media,
                "extractor_key": provider.__name__.rsplit(".", 1)[-1],
                "title": media.get("title"),
                "duration": media.get("duration"),
            }
        return None

    info = await ytdlp.extract_info(url)
    if not info:
        return None
    info["_download_url"] = url
    if result := from_ytdlp(info, url):
        info["_media"] = result
    return info


async def download_items(
    items: Sequence[MediaItem | Mapping], indices: list[int] | None = None
) -> list[str]:
    """Download every selected child in source order, without silent partial albums."""
    files = []
    for index, item in enumerate(items, 1):
        if indices is not None and index not in indices:
            continue
        if item.get("download") == "ytdlp":
            child = item.get("index")
            paths = await ytdlp.download(item["url"], "video", str(child) if child else None)
        else:
            extension = "mp4"
            if item["type"] == "photo":
                path = urllib.parse.urlsplit(item["url"]).path
                candidate = path.rsplit(".", 1)[-1].lower() if "." in path else "jpg"
                extension = candidate if candidate in {"jpg", "jpeg", "png", "webp"} else "jpg"
            paths = await _download_direct_files(
                [item["url"]], extension
            )
        if not paths:
            cleanup_files(files)
            return []
        files.extend(paths)
    return files


async def _download_direct_files(urls: list[str], extension: str) -> list[str]:
    semaphore = asyncio.Semaphore(4)
    destinations = [
        os.path.join(DOWNLOADS_DIR, f"{uuid.uuid4()}.{extension}") for _ in urls
    ]

    async def _download_one(url: str, destination: str) -> str | None:
        async with semaphore:
            downloaded = await _run_sync(download_file, url, destination)
        return destination if downloaded else None

    batch = asyncio.gather(*(
        _download_one(url, destination)
        for url, destination in zip(urls, destinations, strict=True)
    ))
    try:
        # Shield blocking worker threads so cancellation can wait for them and
        # reliably remove their operation-owned destinations afterward.
        paths = await asyncio.shield(batch)
    except asyncio.CancelledError:
        try:
            await batch
        finally:
            cleanup_files(destinations)
        raise
    if any(path is None for path in paths):
        cleanup_files(destinations)
        return []
    return [path for path in paths if path]


def cleanup_files(paths) -> None:
    """Best-effort cleanup shared by platform adapters and failed operations."""
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


async def download_media(
    url: str,
    media_type: str,
    playlist_items: str | None = None,
    media_info: dict | None = None,
) -> list[str]:
    """Download video, audio, or gallery media from a supported URL."""
    detected: MediaResult | Mapping | None = (media_info or {}).get("_media")
    if not detected and not media_info and any(
        predicate(url)
        for predicate in (
            twitter.is_twitter_url,
            instagram.is_instagram_url,
            reddit.is_reddit_url,
            tiktok.is_tiktok_url,
        )
    ):
        refreshed = await extract_info(url)
        detected = (refreshed or {}).get("_media")
    if detected:
        items = detected["items"]
        if media_type == "audio":
            return await ytdlp.download(items[0]["url"], "audio", playlist_items)
        indices = [int(value) for value in playlist_items.split(",")] if playlist_items else None
        return await download_items(items, indices)

    download_url = (media_info or {}).get("_download_url")
    if not download_url:
        download_url = tiktok.normalize_url(url) if tiktok.is_tiktok_url(url) else url
    return await ytdlp.download(download_url, media_type, playlist_items)
