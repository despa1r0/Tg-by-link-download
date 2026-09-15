import unittest
from unittest.mock import patch

from bot.handlers import media


class MediaCacheTests(unittest.TestCase):
    def tearDown(self):
        for cache in (
            media.url_cache,
            media.gallery_cache,
            media.gallery_source_cache,
            media.photo_url_cache,
            media.media_info_cache,
            media.cache_expiry,
        ):
            cache.clear()

    def test_cache_keeps_only_reusable_provider_data(self):
        cache_key = (1, 2)
        provider_media = {
            "type": "video",
            "urls": ["https://cdn.example/video.mp4"],
        }
        with patch("bot.handlers.media.time.monotonic", return_value=100):
            media._store_cache(
                cache_key,
                "https://x.com/example/status/123",
                {
                    "_twitter_media": provider_media,
                    "formats": [{"large": "metadata is not cached"}],
                },
            )

        self.assertEqual(
            media.media_info_cache[cache_key],
            {"_twitter_media": provider_media},
        )

    def test_expired_entry_is_removed_from_all_related_caches(self):
        cache_key = (1, 2)
        with patch("bot.handlers.media.time.monotonic", return_value=100):
            media._store_cache(cache_key, "https://example.com/video")
        media.gallery_cache[cache_key] = ["https://cdn.example/photo.jpg"]
        media.photo_url_cache[cache_key] = "https://cdn.example/photo.jpg"

        with patch(
            "bot.handlers.media.time.monotonic",
            return_value=100 + media.CACHE_TTL_SECONDS + 1,
        ):
            media._purge_cache()

        self.assertNotIn(cache_key, media.url_cache)
        self.assertNotIn(cache_key, media.gallery_cache)
        self.assertNotIn(cache_key, media.photo_url_cache)
        self.assertNotIn(cache_key, media.cache_expiry)


if __name__ == "__main__":
    unittest.main()
