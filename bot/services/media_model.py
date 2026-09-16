"""Compact, ordered media metadata shared by providers and handlers."""

import urllib.parse


def media_result(items: list[dict], title: str = "") -> dict | None:
    if not items or any(not item.get("url") for item in items):
        return None
    kinds = {item["type"] for item in items}
    kind = items[0]["type"] if len(items) == 1 else (
        "mixed" if len(kinds) > 1 else "gallery" if kinds == {"photo"} else "carousel"
    )
    return {"type": kind, "items": items, "title": title, "url": items[0]["url"]}


def from_ytdlp(info: dict, source_url: str) -> dict | None:
    entries = info.get("entries")
    if entries is not None:
        entries = list(entries)
        info["entries"] = entries
        # A social post can be a carousel; a YouTube playlist is not a post.
        extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
        if not any(name in extractor for name in ("instagram", "twitter", "reddit", "tiktok")):
            return None
    else:
        entries = [info]
    items = []
    for entry in entries:
        if not entry:
            return None  # Do not silently omit unavailable carousel children.
        formats = entry.get("formats") or []
        has_video = any(value.get("vcodec") not in (None, "none") for value in [entry, *formats])
        url = entry.get("url") or entry.get("webpage_url")
        ext = (entry.get("ext") or urllib.parse.urlsplit(url or "").path.rsplit(".", 1)[-1]).lower()
        photo = not has_video and ext in {"jpg", "jpeg", "png", "webp"}
        if photo and url:
            items.append({"type": "photo", "url": url})
        elif has_video or ext in {"mp4", "webm", "mov", "m3u8"}:
            # Keep the post URL for separate audio/video streams and signed headers.
            items.append({"type": "video", "url": source_url, "download": "ytdlp",
                          "index": len(items) + 1 if info.get("entries") is not None else None})
        else:
            return None
    return media_result(items, info.get("title") or "")
