"""Environment-backed Discord adapter settings."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _channel_ids(value: str) -> frozenset[int]:
    if not value.strip():
        return frozenset()
    try:
        ids = frozenset(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError("DISCORD_ALLOWED_CHANNEL_IDS must contain comma-separated IDs") from exc
    if any(channel_id <= 0 for channel_id in ids):
        raise ValueError("Discord channel IDs must be positive integers")
    return ids


@dataclass(frozen=True, slots=True)
class DiscordSettings:
    token: str | None
    allowed_channel_ids: frozenset[int]
    allow_dms: bool
    fallback_upload_bytes: int
    attachments_per_message: int

    @classmethod
    def from_env(cls) -> "DiscordSettings":
        upload_mb = max(1, int(os.getenv("DISCORD_FALLBACK_UPLOAD_MB", "20")))
        attachments = int(os.getenv("DISCORD_ATTACHMENTS_PER_MESSAGE", "10"))
        if not 1 <= attachments <= 10:
            raise ValueError("DISCORD_ATTACHMENTS_PER_MESSAGE must be between 1 and 10")
        return cls(
            token=os.getenv("DISCORD_BOT_TOKEN") or None,
            allowed_channel_ids=_channel_ids(
                os.getenv("DISCORD_ALLOWED_CHANNEL_IDS", "")
            ),
            allow_dms=_boolean("DISCORD_ALLOW_DMS", True),
            fallback_upload_bytes=upload_mb * 1024 * 1024,
            attachments_per_message=attachments,
        )

    def channel_allowed(self, channel_id: int, *, is_dm: bool) -> bool:
        return self.allow_dms if is_dm else channel_id in self.allowed_channel_ids
