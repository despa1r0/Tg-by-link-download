import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bot.services import downloader
from bot.services.media_model import MediaItem, media_result
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
        self.assertEqual(
            [item["url"] for item in media["items"]],
            ["https://video.twimg.com/animation.mp4"],
        )

    @patch("bot.services.providers.twitter.urllib.request.urlopen")
    def test_twitter_single_photo_uses_unified_result(self, urlopen):
        urlopen.return_value = FakeResponse(json.dumps({
            "tweet": {
                "text": "photo",
                "media": {"all": [{
                    "type": "photo",
                    "url": "https://pbs.twimg.com/media/photo.jpg",
                }]},
            }
        }))

        media = twitter.extract_media("https://x.com/example/status/1")

        self.assertEqual(media["type"], "photo")
        self.assertEqual(media["items"][0]["source_url"], "https://x.com/example/status/1")
        self.assertEqual(media["items"][0]["url"], "https://pbs.twimg.com/media/photo.jpg")

    @patch("bot.services.providers.twitter.urllib.request.urlopen")
    def test_twitter_single_video_keeps_best_direct_url_and_duration(self, urlopen):
        urlopen.return_value = FakeResponse(json.dumps({
            "tweet": {
                "text": "video",
                "media": {"all": [{
                    "type": "video",
                    "duration": 8.5,
                    "formats": [
                        {"container": "mp4", "bitrate": 100, "url": "https://video.twimg.com/low.mp4"},
                        {"container": "mp4", "bitrate": 500, "url": "https://video.twimg.com/high.mp4"},
                    ],
                }]},
            }
        }))

        media = twitter.extract_media("https://x.com/example/status/2")

        self.assertEqual(media["type"], "video")
        self.assertEqual(media["duration"], 8.5)
        self.assertEqual(media["items"][0]["url"], "https://video.twimg.com/high.mp4")


class DownloaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_direct_download_waits_then_cleans_operation_files(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_download(_function, _url, destination):
            started.set()
            await release.wait()
            Path(destination).write_bytes(b"media")
            return True

        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(downloader, "DOWNLOADS_DIR", directory),
                patch.object(downloader, "_run_sync", side_effect=delayed_download),
            ):
                operation = asyncio.create_task(
                    downloader._download_direct_files(["https://cdn.test/a.jpg"], "jpg")
                )
                await started.wait()
                operation.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await operation

            self.assertEqual(list(Path(directory).iterdir()), [])

    @patch("bot.services.downloader._download_direct_files", new_callable=AsyncMock)
    async def test_twitter_reuses_cached_provider_result(
        self, direct_download
    ):
        direct_url = "https://video.twimg.com/media/cached.mp4"
        direct_download.return_value = ["cached.mp4"]
        result = media_result([
            MediaItem(
                "video",
                "https://x.com/example/status/123",
                direct_url=direct_url,
            )
        ], provider="twitter")

        files = await downloader.download_media(
            "https://x.com/example/status/123",
            "video",
            media_info={"_media": result},
        )

        self.assertEqual(files, ["cached.mp4"])
        direct_download.assert_awaited_once_with([direct_url], "mp4")

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

    @patch("bot.services.downloader._download_direct_files", new_callable=AsyncMock)
    @patch("bot.services.downloader._run_sync", new_callable=AsyncMock)
    async def test_twitter_video_uses_resolved_cdn_url(
        self, run_sync, direct_download
    ):
        direct_url = "https://video.twimg.com/media/video.mp4"
        run_sync.return_value = media_result([
            MediaItem("video", "https://x.com/example/status/123", direct_url=direct_url)
        ], provider="twitter")
        direct_download.return_value = ["downloaded.mp4"]

        files = await downloader.download_media(
            "https://x.com/example/status/123", "video"
        )

        self.assertEqual(files, ["downloaded.mp4"])
        direct_download.assert_awaited_once_with([direct_url], "mp4")

    @patch("bot.services.downloader.ytdlp.download", new_callable=AsyncMock)
    @patch("bot.services.downloader._run_sync", new_callable=AsyncMock)
    async def test_twitter_audio_uses_resolved_cdn_url(
        self, run_sync, ytdlp_download
    ):
        direct_url = "https://video.twimg.com/media/video.mp4"
        run_sync.return_value = media_result([
            MediaItem("video", "https://x.com/example/status/123", direct_url=direct_url)
        ], provider="twitter")
        ytdlp_download.return_value = ["downloaded.mp3"]

        files = await downloader.download_media(
            "https://x.com/example/status/123", "audio"
        )

        self.assertEqual(files, ["downloaded.mp3"])
        ytdlp_download.assert_awaited_once_with(direct_url, "audio", None)

    @patch("bot.services.downloader._download_direct_files", new_callable=AsyncMock)
    @patch("bot.services.downloader._run_sync", new_callable=AsyncMock)
    async def test_twitter_gif_source_is_downloaded_as_video(
        self, run_sync, direct_download
    ):
        direct_url = "https://video.twimg.com/media/animation.mp4"
        run_sync.return_value = media_result([
            MediaItem("gif", "https://x.com/example/status/456", direct_url=direct_url)
        ], provider="twitter")
        direct_download.return_value = ["animation.mp4"]

        files = await downloader.download_media(
            "https://x.com/example/status/456", "video"
        )

        self.assertEqual(files, ["animation.mp4"])
        direct_download.assert_awaited_once_with([direct_url], "mp4")

    @patch("bot.services.downloader._download_direct_files", new_callable=AsyncMock)
    async def test_detected_photo_preserves_supported_extension(self, direct_download):
        direct_download.return_value = ["downloaded.png"]
        url = "https://preview.redd.it/photo.png?width=1080"
        result = media_result([MediaItem("photo", url, direct_url=url)])

        files = await downloader.download_media(
            "https://reddit.com/r/test/comments/123",
            "photo",
            media_info={"_media": result},
        )

        self.assertEqual(files, ["downloaded.png"])
        direct_download.assert_awaited_once_with(
            [url], "png"
        )


if __name__ == "__main__":
    unittest.main()
