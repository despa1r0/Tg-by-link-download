import asyncio
import os
import uuid
import logging
import ffmpeg
from bot.config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)


async def convert_to_gif(input_path: str, start_time: str, end_time: str) -> str | None:
    """
    Converts a segment of a video to a high-quality GIF.
    Uses custom palette generation (palettegen/paletteuse) to preserve color quality.
    start_time and end_time can be plain seconds ('1', '6') or 'MM:SS' / 'HH:MM:SS'.
    Returns the output path on success, or None on failure.
    """
    if not os.path.exists(input_path):
        logger.error("convert_to_gif: input file does not exist: %s", input_path)
        return None

    try:
        start_seconds = _timestamp_to_seconds(start_time)
        end_seconds = _timestamp_to_seconds(end_time)
    except ValueError as exc:
        logger.warning("Invalid GIF timestamps: %s", exc)
        return None
    duration = end_seconds - start_seconds
    if start_seconds < 0 or duration <= 0 or duration > 10:
        logger.warning("GIF segment must be between 1 and 10 seconds")
        return None

    unique_id = str(uuid.uuid4())
    output_path = os.path.join(DOWNLOADS_DIR, f"{unique_id}.gif")

    def _convert():
        try:
            input_video = (
                ffmpeg
                .input(input_path, ss=start_seconds, t=duration)
                .filter('fps', fps=12)
                # Keep portrait/landscape proportions, avoid upscaling, and use an
                # even height so Telegram's transcoder does not distort the result.
                .filter('scale', 'min(480,iw)', -2)
                .filter('setsar', 1)
            )
            split = input_video.filter_multi_output('split')
            palette = split[0].filter('palettegen', stats_mode='diff')
            (
                ffmpeg
                .filter([split[1], palette], 'paletteuse', dither='sierra2_4a')
                .output(output_path, loop=0)
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

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _convert)
    return result


def _timestamp_to_seconds(value: str) -> int:
    parts = value.split(':')
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"invalid timestamp: {value}")
    numbers = [int(part) for part in parts]
    if len(numbers) > 1 and any(part >= 60 for part in numbers[1:]):
        raise ValueError(f"timestamp component is out of range: {value}")
    return sum(part * 60 ** index for index, part in enumerate(reversed(numbers)))
