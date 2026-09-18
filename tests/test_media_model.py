import unittest

from bot.services.media_model import MediaItem, MediaResult, from_ytdlp


class MediaModelTests(unittest.TestCase):
    def test_source_position_is_distinct_from_ui_order(self):
        result = from_ytdlp(
            {
                "extractor_key": "Instagram",
                "entries": [
                    {"url": "https://cdn.test/photo.jpg", "ext": "jpg"},
                    {"url": "ignored", "vcodec": "h264", "duration": 5.5},
                    {"url": "https://cdn.test/last.jpg", "ext": "jpg"},
                ],
            },
            "https://instagram.com/p/source/",
        )

        self.assertIsInstance(result, MediaResult)
        self.assertEqual(result["type"], "mixed")
        self.assertEqual([item.source_index for item in result.items], [1, 2, 3])
        self.assertEqual(result.items[1].download_strategy, "ytdlp")
        self.assertEqual(
            result.items[1].source_url, "https://instagram.com/p/source/"
        )

    def test_model_preserves_direct_url_duration_and_metadata(self):
        item = MediaItem(
            "video",
            "https://x.com/user/status/1",
            direct_url="https://video.twimg.com/media/one.mp4",
            source_index=1,
            duration=7.25,
            metadata={"provider_type": "video"},
        )
        result = MediaResult((item,), provider="twitter")

        self.assertEqual(result["type"], "video")
        self.assertEqual(result["duration"], 7.25)
        self.assertEqual(item["url"], "https://video.twimg.com/media/one.mp4")
        self.assertEqual(item["metadata"]["provider_type"], "video")


if __name__ == "__main__":
    unittest.main()
