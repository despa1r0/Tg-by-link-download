import asyncio
import os
import re
import json
import uuid
import glob
import urllib.request
import yt_dlp
from bot.config import DOWNLOADS_DIR


# ──────────────────────────────────────────────────────────
# TikTok photo gallery extraction (yt-dlp can't do this)
# ──────────────────────────────────────────────────────────

def _is_tiktok_url(url: str) -> bool:
    return 'tiktok.com' in url


def _is_tiktok_short_url(url: str) -> bool:
    """Check if this is a shortened TikTok URL that needs redirect resolution."""
    return any(domain in url for domain in ('vt.tiktok.com', 'vm.tiktok.com'))


def _resolve_tiktok_url(url: str) -> str:
    """
    Follow redirects on short TikTok URLs (vt.tiktok.com, vm.tiktok.com)
    to get the real tiktok.com/@user/video|photo/ID URL.
    Returns the resolved URL, or the original if resolution fails.
    """
    if not _is_tiktok_short_url(url):
        return url
    req = urllib.request.Request(url, headers={
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
    })
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.url  # final URL after redirects
    except Exception:
        return url


def _normalise_tiktok_url(url: str) -> str:
    """Resolve short URLs and convert /photo/ to /video/ so TikTok serves page data."""
    url = _resolve_tiktok_url(url)
    return url.replace('/photo/', '/video/')


def _extract_tiktok_photos(url: str) -> list[str] | None:
    """
    Scrapes TikTok page HTML to find photo URLs from a photo/slideshow post.
    Returns a list of image URLs, or None if it's not a photo post.
    """
    url = _normalise_tiktok_url(url)
    req = urllib.request.Request(url, headers={
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f"TikTok page fetch error: {e}")
        return None

    # Try multiple embedded JSON script tags that TikTok uses
    script_ids = [
        '__UNIVERSAL_DATA_FOR_REHYDRATION__',
        'SIGI_STATE',
        '__NEXT_DATA__',
    ]
    data = None
    for sid in script_ids:
        pattern = rf'<script[^>]*id="{sid}"[^>]*>(.*?)</script>'
        match = re.search(pattern, html, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                break
            except json.JSONDecodeError:
                continue

    # Also try to find any large JSON blob in script tags
    if data is None:
        for match in re.finditer(r'<script[^>]*>((\{.{500,}?\}))</script>', html, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                break
            except json.JSONDecodeError:
                continue

    if data is None:
        return None

    # Recursively search the entire JSON tree for 'imagePost' containing 'images'
    photos = _find_image_post(data)
    return photos if photos else None


def _find_image_post(obj, depth=0) -> list[str] | None:
    """
    Recursively walks a JSON structure looking for an 'imagePost' key
    that contains an 'images' list with image URLs.
    """
    if depth > 15:  # prevent infinite recursion
        return None

    if isinstance(obj, dict):
        # Check if THIS dict has 'imagePost'
        image_post = obj.get('imagePost')
        if isinstance(image_post, dict):
            images = image_post.get('images', [])
            if isinstance(images, list) and images:
                urls = []
                for img in images:
                    url_list = (
                        img.get('imageURL', {}).get('urlList', [])
                        or img.get('displayImage', {}).get('urlList', [])
                        or img.get('ownerWatermarkImage', {}).get('urlList', [])
                    )
                    if url_list:
                        urls.append(url_list[0])
                if urls:
                    return urls

        # Recurse into all values
        for v in obj.values():
            result = _find_image_post(v, depth + 1)
            if result:
                return result

    elif isinstance(obj, list):
        for item in obj:
            result = _find_image_post(item, depth + 1)
            if result:
                return result

    return None


def _download_image(image_url: str, dest_path: str) -> bool:
    """Download a single image URL to disk."""
    req = urllib.request.Request(image_url, headers={
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
    })
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        with open(dest_path, 'wb') as f:
            f.write(resp.read())
        return True
    except Exception as e:
        print(f"Image download error: {e}")
        return False


# ──────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────

async def extract_info(url: str) -> dict | None:
    """
    Extracts metadata about a URL.
    For TikTok photo posts: returns a dict with '_tiktok_photos' key.
    For everything else: delegates to yt-dlp.
    """
    loop = asyncio.get_running_loop()

    # Check TikTok photos first
    if _is_tiktok_url(url):
        photo_urls = await loop.run_in_executor(None, _extract_tiktok_photos, url)
        if photo_urls:
            return {
                '_tiktok_photos': photo_urls,
                'extractor_key': 'TikTok',
            }

    # Fall back to yt-dlp
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    def _extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                return ydl.extract_info(_normalise_tiktok_url(url) if _is_tiktok_url(url) else url, download=False)
            except Exception as e:
                print(f"Info extract error: {e}")
                return None

    info = await loop.run_in_executor(None, _extract)

    # Fallback: if yt-dlp returned audio-only for a TikTok URL, it's a photo post.
    # The HTML scraper may have missed it, but we can still detect it here.
    if info and _is_tiktok_url(url):
        vcodec = info.get('vcodec', '')
        has_video = vcodec and vcodec != 'none'
        if not has_video:
            # It's a photo post that our scraper missed.
            # Use thumbnail URLs as a fallback (at least gives the cover images).
            thumb_urls = []
            for t in info.get('thumbnails', []):
                t_url = t.get('url', '')
                if t_url and t_url not in thumb_urls:
                    thumb_urls.append(t_url)
            if thumb_urls:
                return {
                    '_tiktok_photos': thumb_urls,
                    'extractor_key': 'TikTok',
                }

    return info


def is_gallery(info: dict) -> bool:
    """Returns True if the info dict represents a photo gallery."""
    if not info:
        return False
    # Our custom TikTok photo detection
    if '_tiktok_photos' in info:
        return True
    # yt-dlp playlist with multiple entries
    if info.get('_type') == 'playlist' or 'entries' in info:
        entries = info.get('entries', [])
        if len(list(entries)) > 1:
            return True
    return False


def get_gallery_count(info: dict) -> int:
    """Returns the number of items in a gallery."""
    if not info:
        return 0
    if '_tiktok_photos' in info:
        return len(info['_tiktok_photos'])
    if 'entries' in info:
        return len(list(info['entries']))
    return 1


async def download_tiktok_photos(
    photo_urls: list[str], indices: list[int] | None = None
) -> list[str]:
    """
    Downloads TikTok photos by their direct URLs.
    indices: 1-based list of which photos to download. None = all.
    Returns list of local file paths.
    """
    if indices is not None:
        selected = []
        for i in indices:
            if 1 <= i <= len(photo_urls):
                selected.append(photo_urls[i - 1])
    else:
        selected = photo_urls

    loop = asyncio.get_running_loop()
    paths = []
    for img_url in selected:
        unique_id = str(uuid.uuid4())
        ext = 'jpeg'
        dest = os.path.join(DOWNLOADS_DIR, f"{unique_id}.{ext}")
        ok = await loop.run_in_executor(None, _download_image, img_url, dest)
        if ok:
            paths.append(dest)
    return paths


async def download_media(
    url: str, media_type: str, playlist_items: str = None
) -> list[str]:
    """
    Downloads media from URL. media_type can be 'video', 'audio', or 'gallery'.
    Returns a list of file paths to the downloaded media.
    """
    unique_id = str(uuid.uuid4())

    if media_type == 'gallery':
        output_template = os.path.join(
            DOWNLOADS_DIR, f"{unique_id}_%(autonumber)s.%(ext)s"
        )
    else:
        output_template = os.path.join(DOWNLOADS_DIR, f"{unique_id}.%(ext)s")

    # Normalise TikTok photo URLs → video URLs for yt-dlp
    dl_url = _normalise_tiktok_url(url) if _is_tiktok_url(url) else url

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
        pass
    else:
        ydl_opts['format'] = 'best[filesize<50M]/bestvideo[filesize<50M]+bestaudio/best'
        ydl_opts['merge_output_format'] = 'mp4'

    if playlist_items:
        ydl_opts['playlist_items'] = playlist_items
    elif media_type != 'gallery':
        ydl_opts['noplaylist'] = True

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(dl_url, download=True)

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _download)

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
