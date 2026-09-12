import json
import unittest
from unittest.mock import AsyncMock, patch

from bot.services import downloader
from bot.services.providers import instagram, reddit, twitter


class FakeResponse:
    def __init__(self, body: str, url: str = "https://example.test"):
        self._body = body.encode()
        self.url = url
        self.headers = {}

    def read(self, _size=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ProviderTests(unittest.TestCase):
    def test_domain_checks_do_not_accept_suffix_attacks(self):
        self.assertTrue(reddit.is_reddit_url("https://www.reddit.com/r/test"))
        self.assertFalse(reddit.is_reddit_url("https://reddit.com.attacker.test/r/test"))
        self.assertTrue(instagram.is_instagram_url("https://www.instagram.com/reel/abc"))
        self.assertFalse(instagram.is_instagram_url("https://instagram.com.attacker.test/reel/abc"))

    @patch("bot.services.providers.reddit.urllib.request.urlopen")
    def test_reddit_proxy_hostname_is_not_duplicated(self, urlopen):
        urlopen.return_value = FakeResponse(
            '<meta property="og:video" content="https://v.redd.it/video.mp4">'
        )
        media = reddit.extract_proxy_media("https://www.reddit.com/r/test/comments/abc/post")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://vxreddit.com/r/test/comments/abc/post")
        self.assertEqual(media["type"], "video")

    @patch("bot.services.providers.reddit.urllib.request.urlopen")
    def test_reddit_proxy_classifies_photo(self, urlopen):
        urlopen.return_value = FakeResponse(
            '<meta property="og:image" content="https://preview.redd.it/photo.jpg">'
        )
        media = reddit.extract_proxy_media("https://www.reddit.com/r/test/comments/abc/post")
        self.assertEqual(media["type"], "photo")
        self.assertEqual(media["url"], "https://preview.redd.it/photo.jpg")

    @patch("bot.services.providers.instagram.urllib.request.urlopen")
    def test_instagram_proxy_classifies_photo(self, urlopen):
        urlopen.return_value = FakeResponse(
            "",
            url="https://scontent.example-cdn.test/media/photo.jpg?token=abc",
        )
        media = instagram.extract_proxy_media("https://www.instagram.com/p/abc/")
        self.assertEqual(media["type"], "photo")
        self.assertIn("photo.jpg", media["url"])

    @patch("bot.services.providers.instagram.urllib.request.urlopen")
    def test_instagram_proxy_resolves_direct_video(self, urlopen):
        urlopen.return_value = FakeResponse(
            "",
            url="https://scontent.example-cdn.test/media/reel.mp4?token=abc",
        )
        media = instagram.extract_proxy_media("https://www.instagram.com/reel/abc/")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.host, "kkinstagram.com")
        self.assertEqual(media["type"], "video")
        self.assertIn("reel.mp4", media["url"])

    @patch("bot.services.providers.twitter.urllib.request.urlopen")
    def test_twitter_animated_gif_is_classified_as_gif(self, urlopen):
        payload = {
            "tweet": {
                "text": "animation",
                "media": {
                    "all": [{
                        "type": "animated_gif",
                        "url": "https://video.twimg.com/animation.mp4",
                    }]
                },
            }
        }
        urlopen.return_value = FakeResponse(json.dumps(payload))
        media = twitter.extract_media("https://x.com/example/status/123")
        self.assertEqual(media["type"], "gif")
        self.assertEqual(media["urls"], ["https://video.twimg.com/animation.mp4"])


class DownloaderTests(unittest.IsolatedAsyncioTestCase):
    @patch("bot.services.downloader._download_direct_files", new_callable=AsyncMock)
    async def test_detected_photo_preserves_supported_extension(self, direct_download):
        direct_download.return_value = ["downloaded.png"]

        files = await downloader.download_detected_photo(
            "https://preview.redd.it/photo.png?width=1080"
        )

        self.assertEqual(files, ["downloaded.png"])
        direct_download.assert_awaited_once_with(
            ["https://preview.redd.it/photo.png?width=1080"], "png"
        )


if __name__ == "__main__":
    unittest.main()
