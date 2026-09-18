import json
import unittest
from unittest.mock import AsyncMock, patch

from bot.services import downloader
from bot.services.providers import instagram, reddit, twitter


class FakeResponse:
    def __init__(
        self,
        body: str,
        url: str = "https://example.test",
        content_type: str | None = None,
    ):
        self._body = body.encode()
        self.url = url
        self.headers = {"Content-Type": content_type} if content_type else {}

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
        media = instagram.extract_proxy_media(
            "https://www.instagram.com/reel/abc/?stkn=secret&img_index=2"
        )
        request = urlopen.call_args.args[0]
        self.assertEqual(request.host, "instagram7.com")
        self.assertEqual(request.full_url, "https://instagram7.com/reel/abc/?img_index=2")
        self.assertEqual(media["type"], "video")
        self.assertIn("reel.mp4", media["url"])

    @patch("bot.services.providers.instagram.urllib.request.urlopen")
    def test_instagram_uses_content_type_for_extensionless_video(self, urlopen):
        urlopen.return_value = FakeResponse(
            "",
            url="https://scontent.example-cdn.test/media/opaque-resource",
            content_type="video/mp4; charset=binary",
        )

        media = instagram.extract_proxy_media("https://www.instagram.com/p/abc/")

        self.assertEqual(media["type"], "video")

    @patch("bot.services.providers.instagram.urllib.request.urlopen")
    def test_instagram_does_not_treat_post_redirect_as_photo(self, urlopen):
        urlopen.side_effect = [
            FakeResponse(
                "<html>Instagram post</html>",
                url="https://www.instagram.com/p/abc/",
                content_type="text/html",
            ),
            FakeResponse(
                '<meta property="og:video:secure_url" content="/videos/abc/1">',
                url="https://eeinstagram.com/p/abc/",
            ),
        ]

        media = instagram.extract_proxy_media("https://www.instagram.com/p/abc/")

        self.assertEqual(media["type"], "video")
        self.assertEqual(media["url"], "https://eeinstagram.com/videos/abc/1")

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
    @patch("bot.services.downloader.ytdlp.download", new_callable=AsyncMock)
    @patch("bot.services.downloader.twitter.download_media", new_callable=AsyncMock)
    @patch("bot.services.downloader.asyncio.get_running_loop")
    async def test_twitter_reuses_cached_provider_result(
        self, get_running_loop, twitter_download, ytdlp_download
    ):
        direct_url = "https://video.twimg.com/media/cached.mp4"
        get_running_loop.return_value.run_in_executor = AsyncMock()
        twitter_download.return_value = ["cached.mp4"]

        files = await downloader.download_media(
            "https://x.com/example/status/123",
            "video",
            media_info={
                "_twitter_media": {"type": "video", "urls": [direct_url]}
            },
        )

        self.assertEqual(files, ["cached.mp4"])
        get_running_loop.return_value.run_in_executor.assert_not_awaited()
        twitter_download.assert_awaited_once_with([direct_url])
        ytdlp_download.assert_not_awaited()

    @patch("bot.services.downloader.tiktok.normalize_url")
    @patch("bot.services.downloader.ytdlp.download", new_callable=AsyncMock)
    async def test_tiktok_reuses_cached_normalized_url(
        self, ytdlp_download, normalize_url
    ):
        normalized_url = "https://www.tiktok.com/@example/video/123"
        ytdlp_download.return_value = ["video.mp4"]

        files = await downloader.download_media(
            "https://vm.tiktok.com/short/",
            "video",
            media_info={"_download_url": normalized_url},
        )

        self.assertEqual(files, ["video.mp4"])
        normalize_url.assert_not_called()
        ytdlp_download.assert_awaited_once_with(normalized_url, "video", None)

    @patch("bot.services.downloader.ytdlp.download", new_callable=AsyncMock)
    @patch("bot.services.downloader.twitter.download_media", new_callable=AsyncMock)
    @patch("bot.services.downloader.asyncio.get_running_loop")
    async def test_twitter_video_uses_resolved_cdn_url(
        self, get_running_loop, twitter_download, ytdlp_download
    ):
        get_running_loop.return_value.run_in_executor = AsyncMock(return_value={
            "type": "video", "urls": ["https://video.twimg.com/media/video.mp4"]
        })
        twitter_download.return_value = ["downloaded.mp4"]

        files = await downloader.download_media(
            "https://x.com/example/status/123", "video"
        )

        self.assertEqual(files, ["downloaded.mp4"])
        twitter_download.assert_awaited_once_with(
            ["https://video.twimg.com/media/video.mp4"]
        )
        ytdlp_download.assert_not_awaited()

    @patch("bot.services.downloader.ytdlp.download", new_callable=AsyncMock)
    @patch("bot.services.downloader.twitter.download_media", new_callable=AsyncMock)
    @patch("bot.services.downloader.asyncio.get_running_loop")
    async def test_twitter_audio_uses_resolved_cdn_url(
        self, get_running_loop, twitter_download, ytdlp_download
    ):
        direct_url = "https://video.twimg.com/media/video.mp4"
        get_running_loop.return_value.run_in_executor = AsyncMock(return_value={
            "type": "video", "urls": [direct_url]
        })
        ytdlp_download.return_value = ["downloaded.mp3"]

        files = await downloader.download_media(
            "https://x.com/example/status/123", "audio"
        )

        self.assertEqual(files, ["downloaded.mp3"])
        ytdlp_download.assert_awaited_once_with(direct_url, "audio", None)
        twitter_download.assert_not_awaited()

    @patch("bot.services.downloader.ytdlp.download", new_callable=AsyncMock)
    @patch("bot.services.downloader.twitter.download_media", new_callable=AsyncMock)
    @patch("bot.services.downloader.asyncio.get_running_loop")
    async def test_twitter_gif_source_is_downloaded_as_video(
        self, get_running_loop, twitter_download, ytdlp_download
    ):
        direct_url = "https://video.twimg.com/media/animation.mp4"
        get_running_loop.return_value.run_in_executor = AsyncMock(return_value={
            "type": "gif", "urls": [direct_url]
        })
        twitter_download.return_value = ["animation.mp4"]

        files = await downloader.download_media(
            "https://x.com/example/status/456", "video"
        )

        self.assertEqual(files, ["animation.mp4"])
        twitter_download.assert_awaited_once_with([direct_url])
        ytdlp_download.assert_not_awaited()

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
