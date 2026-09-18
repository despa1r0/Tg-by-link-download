"""Structured, privacy-conscious logging helpers for media operations."""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlsplit

_LOG_CONTEXT: contextvars.ContextVar[dict[str, object] | None] = contextvars.ContextVar(
    "log_context", default=None
)
_URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
_STANDARD_LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__)


def safe_url_metadata(url: str | None) -> dict[str, str]:
    """Return useful URL identifiers without query strings, fragments, or paths."""
    if not url:
        return {}
    try:
        parsed = urlsplit(url)
    except ValueError:
        return {"source_url_hash": _fingerprint(url)}

    canonical_url = f"{parsed.scheme.lower()}://{parsed.hostname or ''}{parsed.path}"
    metadata = {"source_url_hash": _fingerprint(canonical_url)}
    if parsed.scheme:
        metadata["source_scheme"] = parsed.scheme.lower()
    if parsed.hostname:
        metadata["source_host"] = parsed.hostname.lower().rstrip(".")
    return metadata


def source_platform(url: str | None) -> str:
    """Classify a media URL into a stable, low-cardinality platform name."""
    host = safe_url_metadata(url).get("source_host", "")
    domains = (
        (("youtube.com", "youtu.be"), "youtube"),
        (("tiktok.com",), "tiktok"),
        (("instagram.com", "instagr.am"), "instagram"),
        (("twitter.com", "x.com", "fxtwitter.com", "vxtwitter.com"), "twitter"),
        (("reddit.com", "redd.it"), "reddit"),
    )
    for candidates, platform in domains:
        if any(host == domain or host.endswith(f".{domain}") for domain in candidates):
            return platform
    return "other"


def request_context(message, url: str | None = None) -> dict[str, object]:
    """Build non-sensitive context for correlating one Telegram operation."""
    context: dict[str, object] = {
        "request_id": uuid.uuid4().hex,
        "source_platform": source_platform(url),
        **safe_url_metadata(url),
    }
    chat = getattr(message, "chat", None)
    if chat is None:
        chat = getattr(getattr(message, "message", None), "chat", None)
    if chat is not None and getattr(chat, "type", None):
        context["chat_type"] = str(chat.type)

    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)
    salt = os.getenv("LOG_CONTEXT_SALT", "")
    if user_id is not None and salt:
        context["user_ref"] = hmac.new(
            salt.encode(), str(user_id).encode(), hashlib.sha256
        ).hexdigest()[:16]
    return context


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    """Attach fields to every log record emitted in the current async context."""
    merged = {
        **(_LOG_CONTEXT.get() or {}),
        **{k: v for k, v in fields.items() if v is not None},
    }
    token = _LOG_CONTEXT.set(merged)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


def log_media_failure(
    logger: logging.Logger,
    *,
    stage: str,
    error: Exception | str,
    url: str | None = None,
    media_type: str | None = None,
) -> None:
    """Emit the common structured event used to investigate media failures."""
    error_type = type(error).__name__ if isinstance(error, Exception) else "OperationFailed"
    error_message = str(error)
    logger.error(
        "Media operation failed",
        extra={
            "event": "media_operation_failed",
            "download_stage": stage,
            "media_type": media_type,
            "error_type": error_type,
            "error_message": error_message,
            "source_platform": source_platform(url) if url else None,
            **safe_url_metadata(url),
        },
    )


class JsonFormatter(logging.Formatter):
    """Serialize logs as one JSON object per line for Docker and Loki."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": _sanitize_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_LOG_RECORD_FIELDS and key not in {"message", "asctime"}:
                payload[key] = _sanitize_text(value) if isinstance(value, str) else value
        payload.update(_LOG_CONTEXT.get() or {})
        if record.exc_info:
            payload["exception"] = _sanitize_text(self.formatException(record.exc_info))
        return json.dumps(
            {key: value for key, value in payload.items() if value is not None},
            ensure_ascii=False,
            default=str,
        )


def configure_logging() -> None:
    """Configure stdout logging for collection by the container runtime."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _sanitize_text(value: str) -> str:
    """Remove URL paths and credentials from free-form dependency errors."""
    def replace(match: re.Match[str]) -> str:
        metadata = safe_url_metadata(match.group(0).rstrip(".,);]"))
        host = metadata.get("source_host", "unknown-host")
        fingerprint = metadata["source_url_hash"]
        return f"[url:{host}#{fingerprint}]"

    return _URL_PATTERN.sub(replace, value).replace("\x00", "")[:4000]
