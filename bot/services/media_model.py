"""Typed, ordered media metadata shared by providers and platform adapters."""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

MediaType = Literal["photo", "video", "gif", "audio"]
DownloadStrategy = Literal["direct", "ytdlp"]


@dataclass(frozen=True, slots=True)
class MediaItem(Mapping[str, Any]):
    """One child in source order.

    ``source_index`` is the provider/yt-dlp child position and is deliberately
    separate from the item's position in a UI selection.
    """

    media_type: MediaType
    source_url: str
    direct_url: str | None = None
    download_strategy: DownloadStrategy = "direct"
    source_index: int | None = None
    title: str = ""
    duration: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def download_url(self) -> str:
        return self.direct_url or self.source_url

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "type": self.media_type,
            "url": self.download_url,
            "source_url": self.source_url,
            "direct_url": self.direct_url,
            "download": self.download_strategy,
            "index": self.source_index,
            "title": self.title,
            "duration": self.duration,
            "metadata": dict(self.metadata),
        }
        return {key: item for key, item in value.items() if item not in (None, "")}

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __len__(self) -> int:
        return len(self.as_dict())


@dataclass(frozen=True, slots=True)
class MediaResult(Mapping[str, Any]):
    """Complete media extracted from one source post, in source order."""

    items: tuple[MediaItem, ...]
    source_url: str = ""
    title: str = ""
    provider: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("MediaResult requires at least one item")

    @property
    def media_type(self) -> str:
        kinds = {item.media_type for item in self.items}
        if len(self.items) == 1:
            return self.items[0].media_type
        if kinds == {"photo"}:
            return "gallery"
        if kinds <= {"video", "gif"}:
            return "carousel"
        return "mixed"

    @property
    def duration(self) -> float | None:
        return self.items[0].duration if len(self.items) == 1 else None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "type": self.media_type,
            "items": self.items,
            "title": self.title,
            "url": self.items[0].download_url,
            "source_url": self.source_url or self.items[0].source_url,
            "provider": self.provider,
            "duration": self.duration,
            "metadata": dict(self.metadata),
        }
        return {key: item for key, item in value.items() if item not in (None, "")}

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __len__(self) -> int:
        return len(self.as_dict())


def _coerce_item(item: MediaItem | Mapping[str, Any], source_url: str) -> MediaItem:
    if isinstance(item, MediaItem):
        return item
    strategy = item.get("download_strategy") or item.get("download") or "direct"
    item_url = str(item.get("url") or item.get("direct_url") or source_url)
    original_url = str(item.get("source_url") or (source_url if strategy == "ytdlp" else item_url))
    return MediaItem(
        media_type=item["type"],
        source_url=original_url,
        direct_url=item.get("direct_url") or (item_url if strategy == "direct" else None),
        download_strategy=strategy,
        source_index=item.get("source_index", item.get("index")),
        title=str(item.get("title") or ""),
        duration=item.get("duration"),
        metadata=item.get("metadata") or {},
    )


def media_result(
    items: Sequence[MediaItem | Mapping[str, Any]],
    title: str = "",
    *,
    source_url: str = "",
    provider: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> MediaResult | None:
    if not items:
        return None
    try:
        normalized = tuple(_coerce_item(item, source_url) for item in items)
    except (KeyError, TypeError, ValueError):
        return None
    if any(not item.download_url for item in normalized):
        return None
    return MediaResult(
        items=normalized,
        source_url=source_url,
        title=title,
        provider=provider,
        metadata=metadata or {},
    )


def from_ytdlp(info: dict, source_url: str) -> MediaResult | None:
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

    items: list[MediaItem] = []
    provider = str(info.get("extractor_key") or info.get("extractor") or "ytdlp")
    for source_position, entry in enumerate(entries, 1):
        if not entry:
            return None  # Do not silently omit unavailable carousel children.
        formats = entry.get("formats") or []
        has_video = any(value.get("vcodec") not in (None, "none") for value in [entry, *formats])
        direct_url = entry.get("url") or entry.get("webpage_url")
        ext = (
            entry.get("ext")
            or urllib.parse.urlsplit(direct_url or "").path.rsplit(".", 1)[-1]
        ).lower()
        photo = not has_video and ext in {"jpg", "jpeg", "png", "webp"}
        if photo and direct_url:
            items.append(
                MediaItem(
                    media_type="photo",
                    source_url=source_url,
                    direct_url=direct_url,
                    source_index=source_position if info.get("entries") is not None else None,
                )
            )
        elif has_video or ext in {"mp4", "webm", "mov", "m3u8"}:
            items.append(
                MediaItem(
                    media_type="video",
                    source_url=source_url,
                    download_strategy="ytdlp",
                    source_index=source_position if info.get("entries") is not None else None,
                    duration=entry.get("duration") or info.get("duration"),
                )
            )
        else:
            return None
    return media_result(
        items,
        info.get("title") or "",
        source_url=source_url,
        provider=provider,
    )
