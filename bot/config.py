import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

PROJECT_DIR = Path(__file__).resolve().parent.parent
DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR", str(PROJECT_DIR / "downloads"))
YTDLP_COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE") or None
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_MB", "50")) * 1024 * 1024

os.makedirs(DOWNLOADS_DIR, exist_ok=True)
