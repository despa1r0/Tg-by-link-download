import html as html_module
import json
import logging
import re
import urllib.parse
import urllib.request

from bot.services.media_model import media_result
from bot.services.providers.common import hostname_matches, resolve_url
from bot.services.providers.instagram import _meta_content

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

    if "/gallery/" in parsed.path or "/comments/" in parsed.path:
        # Native metadata preserves gallery order, unlike an embed's OG preview.
        json_url = urllib.parse.urlunsplit(("https", "www.reddit.com", parsed.path.rstrip("/") + ".json", "raw_json=1", ""))
        try:
            request = urllib.request.Request(json_url, headers={"User-Agent": "TelegramMediaBot/1.0"})
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read(2 * 1024 * 1024))
            post = payload[0]["data"]["children"][0]["data"]
            if post.get("gallery_data"):
                items = []
                for child in post["gallery_data"]["items"]:
                    metadata = post["media_metadata"][child["media_id"]]
                    source = metadata.get("s") or {}
                    video_url = source.get("mp4")
                    media_url = video_url or source.get("u")
                    if not media_url:
                        return None
                    items.append({"type": "video" if video_url else "photo", "url": html_module.unescape(media_url)})
                return media_result(items, post.get("title", "Reddit Media"))
        except (ValueError, KeyError, IndexError, TypeError, OSError) as exc:
            logger.debug("Reddit structured metadata unavailable: %s", exc)
        if "/gallery/" in parsed.path:
            return None  # Never present the gallery's cover as the whole post.

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

    if (_meta_content(page, "og:type") or "").startswith("video"):
        return None
    image_match = re.search(r'<meta property="og:image" content="([^"]+)"', page)
    if not image_match:
        return None
    image_url = html_module.unescape(image_match.group(1))
    nested_url = urllib.parse.parse_qs(urllib.parse.urlsplit(image_url).query).get("url")
    if nested_url:
        image_url = nested_url[0]
    return {"type": "photo", "url": image_url, "title": title}
