import logging
import os
import urllib.parse
import urllib.request

from bot.config import MAX_DOWNLOAD_BYTES

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
        logger.warning("Could not resolve short URL %s: %s", url, exc)
        return url


def download_file(url: str, destination: str) -> bool:
    """Stream a remote media file to disk while enforcing a size limit."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
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
        return True
    except Exception as exc:
        logger.warning("Media download failed for %s: %s", url, exc)
        if os.path.exists(destination):
            os.remove(destination)
        return False
