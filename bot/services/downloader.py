"""Public download API and platform-provider orchestration."""

import asyncio
import os
import urllib.parse
import uuid

from bot.config import DOWNLOADS_DIR
from bot.services.providers import instagram, reddit, tiktok, twitter, ytdlp
from bot.services.providers.common import download_file
from bot.services.media_model import from_ytdlp, media_result


async def extract_info(url: str) -> dict | None:
    """Extract metadata, using specialized providers before/after yt-dlp as needed."""
    loop = asyncio.get_running_loop()

    if tiktok.is_tiktok_url(url):
        photo_urls = await loop.run_in_executor(None, tiktok.extract_photos, url)
        if photo_urls:
            return {"_media": media_result([{ "type": "photo", "url": value} for value in photo_urls]), "extractor_key": "TikTok"}
        url = tiktok.normalize_url(url)

    if twitter.is_twitter_url(url):
        media = await loop.run_in_executor(None, twitter.extract_media, url)
        if media:
            return {
                "_twitter_media": media,
                **({"_media": media} if media.get("items") else {}),
                "extractor_key": "Twitter",
                "title": media.get("title"),
            }

    # Resolve the complete post before accepting a proxy's single preview.
    if instagram.is_instagram_url(url) or reddit.is_reddit_url(url):
        info = await ytdlp.extract_info(url)
        if info and (result := from_ytdlp(info, url)):
            return {**info, "_media": result}
        provider = instagram if instagram.is_instagram_url(url) else reddit
        media = await loop.run_in_executor(None, provider.extract_proxy_media, url)
        if media:
            return {"_media": _provider_result(media), "extractor_key": provider.__name__.rsplit(".", 1)[-1]}
        return None

    info = await ytdlp.extract_info(url)
    if not info:
        return None
    info["_download_url"] = url
    if result := from_ytdlp(info, url):
        info["_media"] = result
    return info


def _provider_result(media: dict) -> dict:
    if media.get("items"):
        return media
    kind = "photo" if media["type"] in {"photo", "image", "photos"} else "video"
    urls = media.get("urls") or [media["url"]]
    return media_result([{"type": kind, "url": url} for url in urls], media.get("title", ""))


def _tiktok_thumbnail_fallback(url: str, info: dict) -> dict:
    # Thumbnails cannot prove that a post contains photos (audio/video previews).
    return info


def is_gallery(info: dict) -> bool:
    if not info:
        return False
    if "_media" in info:
        return len(info["_media"]["items"]) > 1
    return bool(info.get("_tiktok_photos"))


def get_gallery_count(info: dict) -> int:
    if not info:
        return 0
    if "_media" in info:
        return len(info["_media"]["items"])
    return len(info.get("_tiktok_photos") or [])


async def download_items(items: list[dict], indices: list[int] | None = None) -> list[str]:
    """Download every selected child in source order, without silent partial albums."""
    files = []
    for index, item in enumerate(items, 1):
        if indices is not None and index not in indices:
            continue
        if item.get("download") == "ytdlp":
            child = item.get("index")
            paths = await ytdlp.download(item["url"], "video", str(child) if child else None)
        else:
            paths = await _download_direct_files([item["url"]], "jpg" if item["type"] == "photo" else "mp4")
        if not paths:
            for path in files:
                if os.path.exists(path):
                    os.remove(path)
            return []
        files.extend(paths)
    return files


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
    semaphore = asyncio.Semaphore(4)

    async def _download_one(url: str) -> str | None:
        destination = os.path.join(DOWNLOADS_DIR, f"{uuid.uuid4()}.{extension}")
        async with semaphore:
            downloaded = await loop.run_in_executor(
                None, download_file, url, destination
            )
        return destination if downloaded else None

    paths = await asyncio.gather(*(_download_one(url) for url in urls))
    return [path for path in paths if path]


async def download_detected_photo(media_url: str) -> list[str]:
    """Download a previously detected Instagram or Reddit photo URL."""
    if not media_url:
        return []

    path = urllib.parse.urlsplit(media_url).path
    extension = path.rsplit(".", 1)[-1].lower() if "." in path else "jpg"
    if extension not in {"jpg", "jpeg", "png", "webp"}:
        extension = "jpg"

    return await _download_direct_files([media_url], extension)


async def download_media(
    url: str,
    media_type: str,
    playlist_items: str | None = None,
    media_info: dict | None = None,
) -> list[str]:
    """Download video, audio, or gallery media from a supported URL."""
    loop = asyncio.get_running_loop()

    detected = (media_info or {}).get("_media")
    if detected:
        items = detected["items"]
        if media_type == "audio":
            return await ytdlp.download(items[0]["url"], "audio", playlist_items)
        indices = [int(value) for value in playlist_items.split(",")] if playlist_items else None
        return await download_items(items, indices)

    if twitter.is_twitter_url(url):
        media = (media_info or {}).get("_twitter_media")
        if not media:
            media = await loop.run_in_executor(None, twitter.extract_media, url)
        if media and media.get("type") in {"video", "gif"}:
            media_urls = media.get("urls") or []
            if media_urls:
                # FxTwitter already resolved the post to a public CDN URL. Going
                # back through the yt-dlp Twitter extractor here can require
                # authentication even though the media itself is downloadable.
                if media_type == "video":
                    files = await twitter.download_media(media_urls[:1])
                else:
                    files = await ytdlp.download(
                        media_urls[0], media_type, playlist_items
                    )
                if files:
                    return files

    if instagram.is_instagram_url(url):
        media = (media_info or {}).get("_instagram_media")
        if not media:
            media = await loop.run_in_executor(None, instagram.extract_proxy_media, url)
        if media:
            if media.get("items"):
                indices = [int(value) for value in playlist_items.split(",")] if playlist_items else None
                return await download_items(media["items"], indices)
            if media.get("type") in {"photo", "image"}:
                return await _download_direct_files([media["url"]], "jpg")
            files = await ytdlp.download(media["url"], media_type, playlist_items)
            if files:
                return files

    if reddit.is_reddit_url(url):
        media = (media_info or {}).get("_reddit_media")
        if not media:
            media = await loop.run_in_executor(None, reddit.extract_proxy_media, url)
        if media:
            if media.get("items"):
                indices = [int(value) for value in playlist_items.split(",")] if playlist_items else None
                return await download_items(media["items"], indices)
            if media.get("type") in {"photo", "image"}:
                return await _download_direct_files([media["url"]], "jpg")
            files = await ytdlp.download(media["url"], media_type, playlist_items)
            if files:
                return files

    download_url = (media_info or {}).get("_download_url")
    if not download_url:
        download_url = tiktok.normalize_url(url) if tiktok.is_tiktok_url(url) else url
    return await ytdlp.download(download_url, media_type, playlist_items)
