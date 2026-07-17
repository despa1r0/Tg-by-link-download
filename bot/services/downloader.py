import asyncio
import os
import uuid
import glob
import yt_dlp
from bot.config import DOWNLOADS_DIR

async def download_media(url: str, media_type: str) -> str:
    """
    Downloads media from URL. media_type can be 'video' or 'audio'.
    Returns the file path to the downloaded media.
    """
    unique_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOADS_DIR, f"{unique_id}.%(ext)s")
    
    ydl_opts = {
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
    }
    
    if media_type == 'audio':
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        # Prioritize video under 50MB
        ydl_opts['format'] = 'best[filesize<50M]/bestvideo[filesize<50M]+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mp4'

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
            
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _download)
        
        # Find the downloaded file
        files = glob.glob(os.path.join(DOWNLOADS_DIR, f"{unique_id}.*"))
        if files:
            return files[0]
        return None
    except Exception as e:
        print(f"Download error: {e}")
        return None
