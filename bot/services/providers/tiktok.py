import json
import logging
import re
import urllib.request

from bot.services.providers.common import USER_AGENT, hostname_matches, resolve_url

logger = logging.getLogger(__name__)

TIKTOK_DOMAINS = ("tiktok.com",)
TIKTOK_SHORT_DOMAINS = ("vt.tiktok.com", "vm.tiktok.com")


def is_tiktok_url(url: str) -> bool:
    return hostname_matches(url, TIKTOK_DOMAINS)


def normalize_url(url: str) -> str:
    resolved = resolve_url(url, TIKTOK_SHORT_DOMAINS)
    return resolved.replace("/photo/", "/video/")


def extract_photos(url: str) -> list[str] | None:
    request = urllib.request.Request(normalize_url(url), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            html = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("TikTok page fetch failed: %s", exc)
        return None

    data = None
    for script_id in ("__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE", "__NEXT_DATA__"):
        match = re.search(
            rf'<script[^>]*id="{script_id}"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if match:
            try:
                data = json.loads(match.group(1))
                break
            except json.JSONDecodeError:
                continue

    if data is None:
        for match in re.finditer(r"<script[^>]*>((\{.{500,}?\}))</script>", html, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                break
            except json.JSONDecodeError:
                continue

    return _find_image_post(data) if data is not None else None


def _find_image_post(obj, depth: int = 0) -> list[str] | None:
    if depth > 15:
        return None
    if isinstance(obj, dict):
        image_post = obj.get("imagePost")
        if isinstance(image_post, dict):
            urls = []
            for image in image_post.get("images", []):
                url_list = (
                    image.get("imageURL", {}).get("urlList", [])
                    or image.get("displayImage", {}).get("urlList", [])
                    or image.get("ownerWatermarkImage", {}).get("urlList", [])
                )
                if url_list:
                    urls.append(url_list[0])
            if urls:
                return urls
        for value in obj.values():
            if result := _find_image_post(value, depth + 1):
                return result
    elif isinstance(obj, list):
        for item in obj:
            if result := _find_image_post(item, depth + 1):
                return result
    return None
