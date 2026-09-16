import html as html_module
import logging
import json
import re
import urllib.parse
import urllib.request

from bot.services.providers.common import hostname_matches
from bot.services.media_model import media_result

logger = logging.getLogger(__name__)

INSTAGRAM_DOMAINS = ("instagram.com", "instagr.am")
PROXY_DOMAINS = ("kkinstagram.com", "g.ddinstagram.com", "g.oginstagram.com")
EMBED_USER_AGENT = "Mozilla/5.0 (compatible; Discordbot/2.0)"


def is_instagram_url(url: str) -> bool:
    return hostname_matches(url, INSTAGRAM_DOMAINS)


def is_instagram_reel_url(url: str) -> bool:
    if not is_instagram_url(url):
        return False
    path = urllib.parse.unquote(urllib.parse.urlsplit(url).path).lower()
    return bool(re.match(r"^/(?:[^/]+/)?reels?(?:/|$)", path))


def extract_proxy_media(url: str) -> dict | None:
    """Resolve public Instagram media through embed proxies.

    This avoids Instagram's frequent anonymous-request blocks on VPS IP ranges.
    """
    parsed = urllib.parse.urlsplit(url)
    if not is_instagram_url(url):
        return None

    photo_fallback = None
    for proxy_domain in PROXY_DOMAINS:
        proxy_url = urllib.parse.urlunsplit(
            ("https", proxy_domain, parsed.path, parsed.query, "")
        )
        request = urllib.request.Request(proxy_url, headers={"User-Agent": EMBED_USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                final_url = response.url
                if not hostname_matches(final_url, PROXY_DOMAINS):
                    media_type = _response_media_type(response, final_url)
                    if media_type:
                        result = {
                            "type": media_type,
                            "url": final_url,
                            "title": "Instagram Media",
                        }
                        if media_type == "video":
                            return result
                        if not re.search(r"/(?:reel|reels|tv)/", parsed.path):
                            photo_fallback = photo_fallback or result
                    # A proxy can redirect back to the Instagram post when its
                    # scraper is rate-limited. That HTML page is not a photo.
                    continue
                page = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Instagram proxy %s failed: %s", proxy_domain, exc)
            continue

        structured = extract_structured_media(page)
        if structured:
            return structured

        video_url = next(
            (
                value
                for property_name in (
                    "og:video",
                    "og:video:url",
                    "og:video:secure_url",
                    "twitter:player:stream",
                )
                if (value := _meta_content(page, property_name))
            ),
            None,
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
        # An OG image alone is often a video poster, not a downloadable photo.
        if image_url and (_meta_content(page, "og:type") or "").lower() in {"image", "photo"} and not re.search(r"/(?:reel|reels|tv)/", parsed.path):
            photo_fallback = photo_fallback or {
                "type": "photo",
                "url": urllib.parse.urljoin(proxy_url, html_module.unescape(image_url)),
                "title": html_module.unescape(
                    _meta_content(page, "og:title") or "Instagram Media"
                ),
            }
    return photo_fallback


def extract_structured_media(page: str) -> dict | None:
    """Read post children from Instagram GraphQL/mobile JSON or JSON-LD."""
    def item(node):
        video = node.get("is_video") or node.get("media_type") == 2 or node.get("@type") == "VideoObject" or node.get("__typename") == "GraphVideo"
        if video:
            versions = node.get("video_versions") or []
            url = node.get("video_url") or node.get("contentUrl") or (versions[0].get("url") if versions else None)
            return {"type": "video", "url": url} if url else None
        candidates = (node.get("image_versions2") or {}).get("candidates") or []
        url = node.get("display_url") or (candidates[0].get("url") if candidates else None)
        if node.get("@type") == "ImageObject":
            url = node.get("contentUrl") or url
        return {"type": "photo", "url": url} if url else None

    def walk(node):
        if isinstance(node, dict):
            edges = (node.get("edge_sidecar_to_children") or {}).get("edges")
            children = [edge.get("node") for edge in edges] if edges else node.get("carousel_media")
            if children:
                items = [item(child) if isinstance(child, dict) else None for child in children]
                return media_result(items) if all(items) else None
            if any(key in node for key in ("is_video", "media_type")) or node.get("@type") in {"VideoObject", "ImageObject"} or node.get("__typename") in {"GraphVideo", "GraphImage"}:
                parsed = item(node)
                return media_result([parsed]) if parsed else None
            for value in node.values():
                if result := walk(value):
                    return result
        elif isinstance(node, list):
            for value in node:
                if result := walk(value):
                    return result
        return None

    payloads = [page] + re.findall(r"<script\b[^>]*>(.*?)</script>", page, re.I | re.S)
    for payload in payloads:
        payload = payload.strip()
        if payload.startswith("window._sharedData ="):
            payload = payload.split("=", 1)[1].strip().rstrip(";")
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if result := walk(data):
            return result
    return None


def _meta_content(page: str, property_name: str) -> str | None:
    patterns = (
        rf'<meta[^>]+(?:property|name)\s*=\s*["\']{re.escape(property_name)}["\'][^>]+content\s*=\s*["\']([^"\']+)',
        rf'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+(?:property|name)\s*=\s*["\']{re.escape(property_name)}["\']',
    )
    for pattern in patterns:
        if match := re.search(pattern, page, re.IGNORECASE):
            return match.group(1)
    return None


def _response_media_type(response, url: str) -> str | None:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if content_type.startswith("video/"):
        return "video"
    if content_type.startswith("image/"):
        return "photo"
    if content_type and content_type != "application/octet-stream":
        return None
    return _media_type(url)


def _media_type(url: str) -> str | None:
    path = urllib.parse.urlsplit(url).path.lower()
    if path.endswith((".mp4", ".mov", ".webm")):
        return "video"
    if path.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return "photo"
    return None
