import asyncio
import os
import uuid
import glob
import yt_dlp
from bot.config import DOWNLOADS_DIR

async def extract_info(url: str) -> dict:
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
    }
    def _extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                return ydl.extract_info(url, download=False)
            except Exception as e:
                print(f"Info extract error: {e}")
                return None
                
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _extract)

async def download_media(url: str, media_type: str, playlist_items: str = None) -> list[str]:
    """
    Downloads media from URL. media_type can be 'video' or 'audio' or 'gallery'.
    Returns a list of file paths to the downloaded media.
    """
    unique_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOADS_DIR, f"{unique_id}_%(autonumber)s.%(ext)s") if media_type == 'gallery' else os.path.join(DOWNLOADS_DIR, f"{unique_id}.%(ext)s")
    
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
    elif media_type == 'gallery':
        # Download images/videos from gallery
        pass
    else:
        # Prioritize video under 50MB
        ydl_opts['format'] = 'best[filesize<50M]/bestvideo[filesize<50M]+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mp4'

    if playlist_items:
        ydl_opts['playlist_items'] = playlist_items
    elif media_type != 'gallery':
        ydl_opts['noplaylist'] = True

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
            
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _download)
        
        # Find the downloaded files
        if media_type == 'gallery':
            files = glob.glob(os.path.join(DOWNLOADS_DIR, f"{unique_id}_*"))
            return sorted(files)
        else:
            files = glob.glob(os.path.join(DOWNLOADS_DIR, f"{unique_id}.*"))
            if files:
                return [files[0]]
        return []
    except Exception as e:
        print(f"Download error: {e}")
        return []
