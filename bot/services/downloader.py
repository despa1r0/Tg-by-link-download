"""Public download API and platform-provider orchestration."""

import asyncio
import os
import urllib.parse
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
    if not info:
        return None
    if tiktok.is_tiktok_url(url):
        # Reuse the already resolved/normalised URL when the user presses a
        # download button instead of following the short link a second time.
        info["_download_url"] = url
    return _tiktok_thumbnail_fallback(url, info)


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
            if media.get("type") in {"photo", "image"}:
                return await _download_direct_files([media["url"]], "jpg")
            files = await ytdlp.download(media["url"], media_type, playlist_items)
            if files:
                return files

    download_url = (media_info or {}).get("_download_url")
    if not download_url:
        download_url = tiktok.normalize_url(url) if tiktok.is_tiktok_url(url) else url
    return await ytdlp.download(download_url, media_type, playlist_items)
