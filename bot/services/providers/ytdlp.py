import asyncio
import glob
import logging
import os
import uuid

import yt_dlp

from bot.config import DOWNLOADS_DIR, MAX_DOWNLOAD_BYTES, YTDLP_COOKIES_FILE

logger = logging.getLogger(__name__)


def _base_options() -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }
    if YTDLP_COOKIES_FILE:
        options["cookiefile"] = YTDLP_COOKIES_FILE
    return options


async def extract_info(url: str) -> dict | None:
    options = {**_base_options(), "skip_download": True}

    def _extract():
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as exc:
            logger.warning("yt-dlp metadata extraction failed for %s: %s", url, exc)
            return None

    return await asyncio.get_running_loop().run_in_executor(None, _extract)


async def download(url: str, media_type: str, playlist_items: str | None = None) -> list[str]:
    unique_id = str(uuid.uuid4())
    suffix = "_%(autonumber)s.%(ext)s" if media_type == "gallery" else ".%(ext)s"
    output_template = os.path.join(DOWNLOADS_DIR, f"{unique_id}{suffix}")
    options = {**_base_options(), "outtmpl": output_template}

    if media_type == "audio":
        options["format"] = "bestaudio/best"
        options["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    elif media_type != "gallery":
        # Filesize metadata is frequently absent for Reddit/Instagram streams;
        # filtering on it makes otherwise valid formats disappear.
        options["format"] = "bv*+ba/b"
        options["merge_output_format"] = "mp4"

    if playlist_items:
        options["playlist_items"] = playlist_items
    elif media_type != "gallery":
        options["noplaylist"] = True

    def _download():
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)

    try:
        await asyncio.get_running_loop().run_in_executor(None, _download)
    except Exception as exc:
        logger.warning("yt-dlp download failed for %s: %s", url, exc)
        _cleanup(unique_id)
        return []

    files = sorted(
        path for path in glob.glob(os.path.join(DOWNLOADS_DIR, f"{unique_id}*"))
        if not path.endswith((".part", ".ytdl"))
    )
    accepted = []
    for path in files:
        if os.path.getsize(path) <= MAX_DOWNLOAD_BYTES:
            accepted.append(path)
        else:
            logger.warning("Downloaded file exceeds configured size limit: %s", path)
            os.remove(path)
    return accepted


def _cleanup(unique_id: str) -> None:
    for path in glob.glob(os.path.join(DOWNLOADS_DIR, f"{unique_id}*")):
        try:
            os.remove(path)
        except OSError:
            logger.warning("Could not remove incomplete download %s", path)
