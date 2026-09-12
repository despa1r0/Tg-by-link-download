import html as html_module
import logging
import re
import urllib.parse
import urllib.request

from bot.services.providers.common import hostname_matches, resolve_url

logger = logging.getLogger(__name__)

REDDIT_DOMAINS = ("reddit.com", "redd.it")
REDDIT_SHORT_DOMAINS = ("redd.it", "v.redd.it")


def is_reddit_url(url: str) -> bool:
    return hostname_matches(url, REDDIT_DOMAINS)


def extract_proxy_media(url: str) -> dict | None:
    """Fallback extraction through vxreddit when yt-dlp cannot read a post."""
    resolved = resolve_url(url, REDDIT_SHORT_DOMAINS)
    parsed = urllib.parse.urlsplit(resolved)
    if not hostname_matches(resolved, REDDIT_DOMAINS):
        return None

    proxy_url = urllib.parse.urlunsplit(
        ("https", "vxreddit.com", parsed.path, parsed.query, "")
    )
    # Embed services expose media metadata to crawler user agents and redirect
    # ordinary browsers back to Reddit.
    request = urllib.request.Request(
        proxy_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Discordbot/2.0)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            page = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("vxreddit fallback failed: %s", exc)
        return None

    if "Failed to get data from Reddit" in page:
        return None

    title_match = re.search(r'<meta property="og:title" content="([^"]+)"', page)
    title = html_module.unescape(title_match.group(1)) if title_match else "Reddit Media"

    video_match = re.search(r'<meta property="og:video" content="([^"]+)"', page)
    if video_match:
        return {"type": "video", "url": html_module.unescape(video_match.group(1)), "title": title}

    image_match = re.search(r'<meta property="og:image" content="([^"]+)"', page)
    if not image_match:
        return None
    image_url = html_module.unescape(image_match.group(1))
    nested_url = urllib.parse.parse_qs(urllib.parse.urlsplit(image_url).query).get("url")
    if nested_url:
        image_url = nested_url[0]
    return {"type": "photo", "url": image_url, "title": title}
