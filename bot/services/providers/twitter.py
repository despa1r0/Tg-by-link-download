import json
import logging
import urllib.parse
import urllib.request

from bot.services.media_model import MediaItem, MediaResult, media_result
from bot.services.providers.common import hostname_matches, resolve_url

logger = logging.getLogger(__name__)

TWITTER_DOMAINS = (
    "twitter.com",
    "x.com",
    "fxtwitter.com",
    "vxtwitter.com",
    "fixupx.com",
)
TWITTER_SHORT_DOMAINS = ("t.co",)


def is_twitter_url(url: str) -> bool:
    return hostname_matches(url, TWITTER_DOMAINS)


def extract_media(url: str) -> MediaResult | None:
    resolved = resolve_url(url, TWITTER_SHORT_DOMAINS)
    parsed = urllib.parse.urlsplit(resolved)
    if not hostname_matches(resolved, TWITTER_DOMAINS):
        return None

    api_url = urllib.parse.urlunsplit(
        ("https", "api.fxtwitter.com", parsed.path, "", "")
    )
    request = urllib.request.Request(api_url, headers={"User-Agent": "TelegramBot/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.warning("FxTwitter API request failed: %s", exc)
        return None

    tweet = data.get("tweet") or data.get("status")
    if not tweet:
        return None
    media = tweet.get("media") or {}
    all_media = media.get("all") or (media.get("photos") or []) + (media.get("videos") or [])
    if not all_media:
        return None

    title = (tweet.get("text") or "Twitter Media")[:120]
    items = []
    for source_position, entry in enumerate(all_media, 1):
        raw_type = entry.get("type")
        if raw_type in {"photo", "image"}:
            kind = "photo"
            media_url = entry.get("url")
        elif raw_type in {"gif", "animated_gif"}:
            kind = "gif"
            media_url = _best_video_url(entry)
        elif raw_type == "video":
            kind = "video"
            media_url = _best_video_url(entry)
        else:
            return None
        if not media_url:
            return None
        items.append(
            MediaItem(
                media_type=kind,
                source_url=url,
                direct_url=media_url,
                source_index=source_position,
                title=title,
                duration=entry.get("duration"),
                metadata={"provider_type": raw_type},
            )
        )
    return media_result(items, title, source_url=url, provider="twitter")


def _best_video_url(media: dict) -> str:
    formats = [item for item in media.get("formats", []) if item.get("url")]
    mp4_formats = [item for item in formats if item.get("container") == "mp4"]
    candidates = mp4_formats or formats
    if candidates:
        return max(candidates, key=lambda item: item.get("bitrate") or item.get("size") or 0)["url"]
    return media.get("url") or media.get("transcode_url") or ""
