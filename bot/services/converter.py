import asyncio
import contextvars
import logging
import math
import os
import re
import uuid

import ffmpeg

from bot.config import DOWNLOADS_DIR, FFMPEG_CONCURRENCY

logger = logging.getLogger(__name__)
_FFMPEG_SEMAPHORE = asyncio.Semaphore(FFMPEG_CONCURRENCY)


class GifUploadLimitExceeded(Exception):
    """The converted GIF cannot fit in the destination's upload limit."""


def parse_gif_range(value: str) -> tuple[str, str, int | float, int | float]:
    """Parse the same START-END timestamp format accepted by the Telegram bot."""
    match = re.fullmatch(r"(\d+(?::\d+){0,2})\s*-\s*(\d+(?::\d+){0,2})", value.strip())
    if not match:
        raise ValueError("Invalid format. Use START-END (for example 00:15-00:25 or 1-6).")
    start_time, end_time = match.groups()
    start_seconds, end_seconds = parse_gif_times(start_time, end_time)
    return start_time, end_time, start_seconds, end_seconds


def parse_gif_times(start_time: str, end_time: str) -> tuple[int | float, int | float]:
    """Validate separate Discord modal fields before starting a download."""
    pattern = r"(?:[0-9]+(?:\.[0-9]+)?|[0-9]+:[0-9]+(?::[0-9]+)?)"
    for label, value in (("Start", start_time), ("End", end_time)):
        if not re.fullmatch(pattern, value.strip()):
            raise ValueError(
                f"Invalid {label.lower()} time. Use numbers in seconds, MM:SS, or HH:MM:SS."
            )
    try:
        start_seconds = _timestamp_to_seconds(start_time.strip())
        end_seconds = _timestamp_to_seconds(end_time.strip())
    except ValueError as exc:
        raise ValueError("Invalid timestamp. Minutes and seconds must be below 60.") from exc
    if end_seconds <= start_seconds:
        raise ValueError("End time must be after start time.")
    return start_seconds, end_seconds


async def convert_to_gif(input_path: str, start_time: str, end_time: str) -> str | None:
    """
    Converts a segment to a Telegram-native silent MP4 animation.

    Telegram converts uploaded GIF files to this representation itself. Producing
    it directly avoids broken/black previews and dramatically reduces file size.
    start_time and end_time can be plain seconds ('1', '6') or 'MM:SS' / 'HH:MM:SS'.
    Returns the output path on success, or None on failure.
    """
    if not os.path.exists(input_path):
        logger.error(
            "GIF conversion input file does not exist",
            extra={
                "event": "media_operation_failed",
                "download_stage": "convert_to_gif",
                "error_type": "FileNotFoundError",
                "error_message": "Input media file does not exist",
            },
        )
        return None

    try:
        start_seconds = _timestamp_to_seconds(start_time)
        end_seconds = _timestamp_to_seconds(end_time)
    except ValueError as exc:
        logger.warning("Invalid GIF timestamps: %s", exc)
        return None
    duration = end_seconds - start_seconds
    if start_seconds < 0 or duration <= 0:
        logger.warning("GIF segment duration must be positive")
        return None

    unique_id = str(uuid.uuid4())
    output_path = os.path.join(DOWNLOADS_DIR, f"{unique_id}.mp4")

    def _convert():
        try:
            input_video = (
                ffmpeg
                .input(input_path, ss=start_seconds, t=duration)
                .filter('fps', fps=15)
                # Fit the longest side into 720 px without upscaling. Both output
                # dimensions remain even, as required by H.264/yuv420p.
                .filter(
                    'scale',
                    'if(gt(iw,ih),min(720,iw),-2)',
                    'if(gt(iw,ih),-2,min(720,ih))',
                )
                .filter('setsar', 1)
            )
            (
                input_video
                .output(
                    output_path,
                    vcodec='libx264',
                    pix_fmt='yuv420p',
                    # `fast` noticeably reduces CPU time for interactive bot
                    # jobs while CRF keeps the visual quality target stable.
                    preset='fast',
                    crf=26,
                    movflags='+faststart',
                    an=None,
                )
                .overwrite_output()
                .run(quiet=True)
            )
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return output_path
            return None
        except ffmpeg.Error as e:
            stderr = e.stderr.decode('utf-8', errors='replace') if e.stderr else 'unknown'
            logger.error("FFmpeg error: %s", stderr)
            return None
        except Exception as e:
            logger.error("Unexpected converter error: %s", e)
            return None
        finally:
            if os.path.exists(output_path) and os.path.getsize(output_path) == 0:
                os.remove(output_path)

    async with _FFMPEG_SEMAPHORE:
        context = contextvars.copy_context()
        operation = asyncio.get_running_loop().run_in_executor(
            None, context.run, _convert
        )
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            # FFmpeg runs in a worker thread. Wait for the thread before cleanup
            # so it cannot recreate an operation-owned file after cancellation.
            try:
                await operation
            finally:
                if os.path.exists(output_path):
                    os.remove(output_path)
            raise


async def convert_to_discord_gif(
    input_path: str, start_time: str, end_time: str, *, max_output_bytes: int | None = None
) -> str | None:
    """Create a looping GIF, stopping when its destination upload limit is reached."""
    if not os.path.exists(input_path):
        return None

    try:
        start_seconds = _timestamp_to_seconds(start_time)
        end_seconds = _timestamp_to_seconds(end_time)
    except ValueError:
        return None
    duration = end_seconds - start_seconds
    if duration <= 0 or start_seconds < 0:
        return None

    output_path = os.path.join(DOWNLOADS_DIR, f"{uuid.uuid4()}.gif")

    def _convert() -> str | None:
        result = None
        try:
            frames = (
                ffmpeg.input(input_path, ss=start_seconds, t=duration)
                .video.filter("fps", fps=10)
                .filter(
                    "scale",
                    "if(gt(iw,ih),min(480,iw),-1)",
                    "if(gt(iw,ih),-1,min(480,ih))",
                    flags="lanczos",
                )
            )
            split = frames.split()
            palette = split[0].filter("palettegen")
            gif = ffmpeg.filter([split[1], palette], "paletteuse")
            output_options = {"loop": 0}
            if max_output_bytes is not None:
                output_options["fs"] = max_output_bytes
            ffmpeg.output(gif, output_path, **output_options).overwrite_output().run(quiet=True)
            if os.path.exists(output_path):
                output_size = os.path.getsize(output_path)
                if max_output_bytes is not None and output_size >= max_output_bytes:
                    raise GifUploadLimitExceeded
                if output_size > 0:
                    result = output_path
        except GifUploadLimitExceeded:
            raise
        except ffmpeg.Error as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else "unknown"
            logger.error("FFmpeg GIF conversion failed: %s", stderr)
        except Exception as exc:
            logger.error("Unexpected Discord GIF conversion error: %s", exc)
        finally:
            if result is None and os.path.exists(output_path):
                os.remove(output_path)
        return result

    async with _FFMPEG_SEMAPHORE:
        context = contextvars.copy_context()
        operation = asyncio.get_running_loop().run_in_executor(None, context.run, _convert)
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            try:
                await operation
            finally:
                if os.path.exists(output_path):
                    os.remove(output_path)
            raise


def _timestamp_to_seconds(value: str) -> int | float:
    # Metadata extractors commonly report duration with millisecond precision
    # (for example, FxTwitter may return 7.533). User-entered timestamps are
    # still validated by the handler, but the full-video conversion path must
    # accept these provider-generated values.
    if ':' not in value:
        try:
            seconds = float(value)
        except ValueError as exc:
            raise ValueError(f"invalid timestamp: {value}") from exc
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError(f"invalid timestamp: {value}")
        return int(seconds) if seconds.is_integer() else seconds

    parts = value.split(':')
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"invalid timestamp: {value}")
    numbers = [int(part) for part in parts]
    if len(numbers) > 1 and any(part >= 60 for part in numbers[1:]):
        raise ValueError(f"timestamp component is out of range: {value}")
    return sum(part * 60 ** index for index, part in enumerate(reversed(numbers)))
