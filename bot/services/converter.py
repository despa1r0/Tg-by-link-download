import asyncio
import os
import uuid
import logging
import ffmpeg
from bot.config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)


async def convert_to_gif(input_path: str, start_time: str, end_time: str) -> str | None:
    """
    Converts a segment to a Telegram-native silent MP4 animation.

    Telegram converts uploaded GIF files to this representation itself. Producing
    it directly avoids broken/black previews and dramatically reduces file size.
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
                    preset='medium',
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
