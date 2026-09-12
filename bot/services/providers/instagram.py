import html as html_module
import logging
import re
import urllib.parse
import urllib.request

from bot.services.providers.common import hostname_matches

logger = logging.getLogger(__name__)

INSTAGRAM_DOMAINS = ("instagram.com", "instagr.am")
PROXY_DOMAINS = ("kkinstagram.com", "g.ddinstagram.com", "g.oginstagram.com")
EMBED_USER_AGENT = "Mozilla/5.0 (compatible; Discordbot/2.0)"


def is_instagram_url(url: str) -> bool:
    return hostname_matches(url, INSTAGRAM_DOMAINS)


def extract_proxy_media(url: str) -> dict | None:
    """Resolve public Instagram media through embed proxies.

    This avoids Instagram's frequent anonymous-request blocks on VPS IP ranges.
    """
    parsed = urllib.parse.urlsplit(url)
    if not is_instagram_url(url):
        return None

    for proxy_domain in PROXY_DOMAINS:
        proxy_url = urllib.parse.urlunsplit(
            ("https", proxy_domain, parsed.path, parsed.query, "")
        )
        request = urllib.request.Request(proxy_url, headers={"User-Agent": EMBED_USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                final_url = response.url
                if not hostname_matches(final_url, PROXY_DOMAINS):
                    return {
                        "type": _media_type(final_url),
                        "url": final_url,
                        "title": "Instagram Media",
                    }
                page = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Instagram proxy %s failed: %s", proxy_domain, exc)
            continue

        video_url = _meta_content(page, "og:video") or _meta_content(
            page, "twitter:player:stream"
        )
        if video_url:
            return {
                "type": "video",
                "url": urllib.parse.urljoin(proxy_url, html_module.unescape(video_url)),
                "title": html_module.unescape(
                    _meta_content(page, "og:title") or "Instagram Media"
                ),
            }
        image_url = _meta_content(page, "og:image")
        if image_url:
            return {
                "type": "photo",
                "url": urllib.parse.urljoin(proxy_url, html_module.unescape(image_url)),
                "title": html_module.unescape(
                    _meta_content(page, "og:title") or "Instagram Media"
                ),
            }
    return None


def _meta_content(page: str, property_name: str) -> str | None:
    patterns = (
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{re.escape(property_name)}["\']',
    )
    for pattern in patterns:
        if match := re.search(pattern, page, re.IGNORECASE):
            return match.group(1)
    return None


def _media_type(url: str) -> str:
    path = urllib.parse.urlsplit(url).path.lower()
    return "video" if path.endswith((".mp4", ".mov", ".webm")) else "photo"
