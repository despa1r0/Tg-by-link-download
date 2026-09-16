"""Preserve explicit Instagram photos which yt-dlp's video extractor omits."""

from yt_dlp.extractor.instagram import InstagramIE as BaseInstagramIE


class InstagramIE(BaseInstagramIE):
    def _extract_product_media(self, product_media):
        result = super()._extract_product_media(product_media)
        if product_media.get("media_type") == 1:
            candidates = (product_media.get("image_versions2") or {}).get("candidates") or []
            candidates = [value for value in candidates if value.get("url")]
            if candidates:
                photo = max(candidates, key=lambda value: (value.get("width") or 0) * (value.get("height") or 0))
                result["formats"] = [{"url": photo["url"], "ext": "jpg", "vcodec": "none", "acodec": "none"}]
                result["_media_type"] = "photo"
        return result
