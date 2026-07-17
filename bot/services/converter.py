import asyncio
import os
import uuid
import ffmpeg
from bot.config import DOWNLOADS_DIR

async def convert_to_gif(input_path: str, start_time: str, end_time: str) -> str:
    """
    Converts a segment of a video to a GIF.
    start_time and end_time should be in format 'HH:MM:SS' or seconds.
    """
    unique_id = str(uuid.uuid4())
    output_path = os.path.join(DOWNLOADS_DIR, f"{unique_id}.gif")
    
    def _convert():
        try:
            (
                ffmpeg
                .input(input_path, ss=start_time, to=end_time)
                .filter('fps', fps=10)
                .filter('scale', '320:-1') # Scale width to 320, maintain aspect ratio
                .output(output_path)
                .overwrite_output()
                .run(quiet=True)
            )
            return output_path
        except ffmpeg.Error as e:
            print(f"FFmpeg error: {e.stderr}")
            return None

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _convert)
    return result
