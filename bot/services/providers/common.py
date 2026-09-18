import json
import logging
import os
import subprocess
import urllib.parse
import urllib.request

from bot.config import MAX_DOWNLOAD_BYTES
from bot.observability import log_media_failure, safe_url_metadata

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def hostname_matches(url: str, domains: tuple[str, ...]) -> bool:
    """Return whether *url* belongs to one of the domains, including subdomains."""
    try:
        hostname = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains)


def resolve_url(url: str, short_domains: tuple[str, ...]) -> str:
    """Resolve redirects only for explicitly known URL-shortener domains."""
    if not hostname_matches(url, short_domains):
        return url

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.url
    except Exception as exc:
        logger.warning(
            "Could not resolve short URL",
            extra={
                "event": "url_resolution_failed",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
                **safe_url_metadata(url),
            },
        )
        return url


def download_file(url: str, destination: str) -> bool:
    """Stream a remote media file to disk while enforcing a size limit."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if content_type in {"text/html", "application/json", "text/plain"}:
                raise ValueError("server returned a page instead of media")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
                raise ValueError("remote file exceeds configured size limit")

            downloaded = 0
            with open(destination, "wb") as output:
                while chunk := response.read(256 * 1024):
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise ValueError("remote file exceeds configured size limit")
                    output.write(chunk)
        if file_media_type(destination) is None:
            raise ValueError("downloaded file is not recognized media")
        return True
    except Exception as exc:
        log_media_failure(logger, stage="write_file", error=exc, url=url)
        if os.path.exists(destination):
            os.remove(destination)
        return False


def file_media_type(path: str) -> str | None:
    """Identify bytes/streams, never trust a CDN URL's filename extension."""
    with open(path, "rb") as source:
        header = source.read(32)
    if header.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")) or (
        header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    ):
        return "photo"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "animation"
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", path],
            capture_output=True, timeout=15, check=True,
        )
        streams = json.loads(result.stdout).get("streams", [])
        if any(stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic") for stream in streams):
            return "video"
        if any(stream.get("codec_type") == "audio" for stream in streams):
            return "audio"
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None
