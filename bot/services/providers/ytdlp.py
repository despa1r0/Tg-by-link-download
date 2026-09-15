import asyncio
import glob
import logging
import os
import uuid

import yt_dlp

from bot.config import (
    DOWNLOADS_DIR,
    MAX_DOWNLOAD_BYTES,
    YTDLP_CONCURRENCY,
    YTDLP_COOKIES_FILE,
)

logger = logging.getLogger(__name__)
VIDEO_SELECTION_MAX_BYTES = max(1, int(MAX_DOWNLOAD_BYTES * 0.98))
_YTDLP_SEMAPHORE = asyncio.Semaphore(YTDLP_CONCURRENCY)


def _format_size(format_info: dict) -> int | None:
    """Return yt-dlp's best known estimate for a format's download size."""
    size = format_info.get("filesize") or format_info.get("filesize_approx")
    return int(size) if isinstance(size, (int, float)) and size > 0 else None


def _merged_format(video: dict, audio: dict) -> dict:
    """Build a yt-dlp format-selector result for a video/audio pair."""
    video_size = _format_size(video)
    audio_size = _format_size(audio)
    result = {
        "format": f'{video.get("format", video["format_id"])}+'
                  f'{audio.get("format", audio["format_id"])}',
        "format_id": f'{video["format_id"]}+{audio["format_id"]}',
        "ext": video.get("ext") or "mp4",
        "protocol": "+".join(
            value for value in (video.get("protocol"), audio.get("protocol")) if value
        ),
        "requested_formats": [video, audio],
        "vcodec": video.get("vcodec"),
        "acodec": audio.get("acodec"),
    }
    if video_size is not None and audio_size is not None:
        result["filesize_approx"] = video_size + audio_size
    return result


def _select_video_format(context: dict, max_bytes: int = VIDEO_SELECTION_MAX_BYTES):
    """Choose the best video whose estimated final size fits the configured limit.

    yt-dlp sorts ``context['formats']`` from worst to best.  The former selector
    always chose the absolute best streams and only deleted the merged file after
    download when it exceeded the limit.  This is especially visible with short
    YouTube videos that offer a large 4K stream alongside several valid smaller
    variants.

    Formats without any size metadata remain a last-resort fallback for providers
    where yt-dlp cannot estimate the size.  The exact post-download check is still
    kept below.
    """
    formats = context.get("formats") or []
    audio_formats = [
        item for item in reversed(formats)
        if item.get("acodec") not in {None, "none"}
        and item.get("vcodec") in {None, "none"}
    ]
    unknown_size_fallback = None

    for video in reversed(formats):
        if video.get("vcodec") in {None, "none"}:
            continue

        video_size = _format_size(video)
        has_audio = video.get("acodec") not in {None, "none"}
        if has_audio:
            if video_size is not None and video_size <= max_bytes:
                yield video
                return
            if video_size is None and unknown_size_fallback is None:
                unknown_size_fallback = video
            continue

        if not audio_formats:
            if video_size is not None and video_size <= max_bytes:
                yield video
                return
            if video_size is None and unknown_size_fallback is None:
                unknown_size_fallback = video
            continue

        for audio in audio_formats:
            audio_size = _format_size(audio)
            candidate = _merged_format(video, audio)
            if video_size is not None and audio_size is not None:
                if video_size + audio_size <= max_bytes:
                    yield candidate
                    return
            elif unknown_size_fallback is None:
                unknown_size_fallback = candidate

    if unknown_size_fallback is not None:
        yield unknown_size_fallback


def _video_options() -> dict:
    return {
        "format": _select_video_format,
        "max_filesize": MAX_DOWNLOAD_BYTES,
        "merge_output_format": "mp4",
    }


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

    async with _YTDLP_SEMAPHORE:
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
        options.update(_video_options())
        # Stop a single HTTP transfer early when the server reports a size over
        # the cap. The selector handles the combined video+audio size and keeps
        # a small margin for MP4 container overhead during merging.

    if playlist_items:
        options["playlist_items"] = playlist_items
    elif media_type != "gallery":
        options["noplaylist"] = True

    def _download():
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(url, download=True)

    try:
        async with _YTDLP_SEMAPHORE:
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
