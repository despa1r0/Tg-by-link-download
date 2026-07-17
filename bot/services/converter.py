import asyncio
import os
import uuid
import logging
import ffmpeg
from bot.config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)


async def convert_to_gif(input_path: str, start_time: str, end_time: str) -> str | None:
    """
    Converts a segment of a video to a GIF.
    start_time and end_time can be plain seconds ('1', '6') or 'MM:SS' / 'HH:MM:SS'.
    Returns the output path on success, or None on failure.
    """
    if not os.path.exists(input_path):
        logger.error("convert_to_gif: input file does not exist: %s", input_path)
        return None

    unique_id = str(uuid.uuid4())
    output_path = os.path.join(DOWNLOADS_DIR, f"{unique_id}.gif")

    def _convert():
        try:
            (
                ffmpeg
                .input(input_path, ss=start_time, to=end_time)
                .filter('fps', fps=10)
                .filter('scale', 480, -1)  # 480px wide, keep aspect ratio
                .output(output_path, f='gif')
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

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _convert)
    return result
