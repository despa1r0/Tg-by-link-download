import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yt_dlp
from test_providers import FakeResponse

from bot.handlers import media
from bot.services import downloader
from bot.services.media_model import from_ytdlp, media_result
from bot.services.providers import instagram, reddit, twitter, ytdlp
from bot.services.providers.common import download_file, file_media_type
from bot.services.providers.instagram_ytdlp import InstagramIE


class MediaTypesTests(unittest.TestCase):
    def test_instagram_extractor_preserves_photos_through_ytdlp_processing(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures" / "instagram.json").read_text())
        product = next(case for case in fixtures if case["name"] == "mixed carousel")["payload"]["items"][0]
        product["pk"] = "123"
        for index, child in enumerate(product["carousel_media"]):
            child["pk"] = str(index + 1)
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "format": ytdlp._metadata_format}) as ydl:
            extractor = InstagramIE(ydl)
            info = extractor._extract_product(product, video_id="abc", get_comments=False)
            info.update(extractor="instagram", extractor_key="Instagram", webpage_url="https://instagram.com/p/abc/")
            processed = ydl.process_ie_result(info, download=False)
        result = from_ytdlp(processed, "https://instagram.com/p/abc/")
        self.assertEqual(result["type"], "mixed")
        self.assertEqual([item["type"] for item in result["items"]], ["photo", "video"])

    def test_instagram_structured_matrix(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures" / "instagram.json").read_text())
        for case in fixtures:
            with self.subTest(case=case["name"]):
                result = instagram.extract_structured_media('<script type="application/json">' + json.dumps(case["payload"]) + '</script>')
                self.assertEqual(result["type"], case["type"])
                self.assertEqual([item["url"] for item in result["items"]], case["urls"])

    def test_incomplete_carousel_is_not_reduced_to_cover(self):
        payload = {"edge_sidecar_to_children": {"edges": [
            {"node": {"is_video": False, "display_url": "https://cdn.test/photo.jpg"}},
            {"node": {"is_video": True, "display_url": "https://cdn.test/poster.jpg"}},
        ]}}
        self.assertIsNone(instagram.extract_structured_media(json.dumps(payload)))

    @patch("bot.services.providers.instagram.urllib.request.urlopen")
    def test_og_poster_is_not_a_photo(self, urlopen):
        urlopen.return_value = FakeResponse('<meta property="og:image" content="https://cdn.test/poster.jpg">', url="https://kkinstagram.com/p/abc/")
        self.assertIsNone(instagram.extract_proxy_media("https://instagram.com/p/abc/"))

    @patch("bot.services.providers.instagram.urllib.request.urlopen")
    def test_later_proxy_video_wins_over_photo_redirect(self, urlopen):
        urlopen.side_effect = [FakeResponse("", url="https://cdn.test/cover.jpg"), FakeResponse("", url="https://cdn.test/video.mp4")]
        self.assertEqual(instagram.extract_proxy_media("https://instagram.com/p/abc/")["type"], "video")

    @patch("bot.services.providers.twitter.urllib.request.urlopen")
    def test_twitter_mixed_and_multiple_videos_keep_order(self, urlopen):
        for types in (["photo", "video", "photo"], ["video", "video"], ["photo", "animated_gif"]):
            entries = [{"type": kind, "url": f"https://cdn.test/{index}"} for index, kind in enumerate(types)]
            urlopen.return_value = FakeResponse(json.dumps({"tweet": {"media": {"all": entries}}}))
            result = twitter.extract_media("https://x.com/user/status/1")
            self.assertEqual([item["url"] for item in result["items"]], [item["url"] for item in entries])
            self.assertEqual(len(result["items"]), len(types))

    @patch("bot.services.providers.reddit.urllib.request.urlopen")
    def test_reddit_gallery_order(self, urlopen):
        post = {"gallery_data": {"items": [{"media_id": "b"}, {"media_id": "a"}]}, "media_metadata": {
            "a": {"s": {"u": "https://cdn.test/a.jpg"}}, "b": {"s": {"mp4": "https://cdn.test/b.mp4"}}}}
        urlopen.return_value = FakeResponse(json.dumps([{"data": {"children": [{"data": post}]}}]))
        result = reddit.extract_proxy_media("https://reddit.com/gallery/abc")
        self.assertEqual(result["type"], "mixed")
        self.assertEqual([item["type"] for item in result["items"]], ["video", "photo"])

    def test_youtube_playlist_is_not_gallery(self):
        info = {"extractor_key": "YoutubeTab", "entries": [{"url": "https://cdn.test/a.mp4"}] * 2}
        self.assertIsNone(from_ytdlp(info, "https://youtube.com/playlist?list=abc"))
        self.assertNotIn("_media", info)

    def test_social_video_collection_is_not_photo_gallery(self):
        info = {"extractor_key": "Instagram", "entries": [{"vcodec": "h264"}, {"vcodec": "h264"}]}
        result = from_ytdlp(info, "https://instagram.com/p/abc/")
        self.assertEqual(result["type"], "carousel")
        self.assertEqual([item["index"] for item in result["items"]], [1, 2])

    def test_tiktok_thumbnails_do_not_prove_photo_post(self):
        info = {"thumbnails": [{"url": "https://cdn.test/cover.jpg"}]}
        self.assertIsNone(from_ytdlp(info, "https://tiktok.com/@a/video/1"))

    def test_bytes_override_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "video.mp4"
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(32))
            self.assertEqual(file_media_type(str(path)), "photo")

    @patch("bot.services.providers.common.subprocess.run")
    def test_ffprobe_distinguishes_video_and_audio_cover(self, probe):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "no-extension"
            path.write_bytes(b"media")
            for streams, expected in [
                ([{"codec_type": "video"}], "video"),
                ([{"codec_type": "video", "disposition": {"attached_pic": 1}}, {"codec_type": "audio"}], "audio"),
                ([], None),
            ]:
                probe.return_value.stdout = json.dumps({"streams": streams}).encode()
                self.assertEqual(file_media_type(str(path)), expected)

    @patch("bot.services.providers.common.urllib.request.urlopen")
    def test_html_download_is_rejected_and_removed(self, urlopen):
        urlopen.return_value = FakeResponse("<html>Access denied</html>", content_type="text/html")
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "photo.jpg")
            self.assertFalse(download_file("https://cdn.test/photo.jpg", path))
            self.assertFalse(os.path.exists(path))


class MediaFlowTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        for key in list(media.url_cache):
            media._drop_cache(key)

    @patch("bot.services.downloader.instagram.extract_proxy_media")
    @patch("bot.services.downloader.ytdlp.extract_info", new_callable=AsyncMock)
    async def test_instagram_complete_metadata_precedes_proxy(self, extract, proxy):
        extract.return_value = {"extractor_key": "Instagram", "entries": [
            {"url": "https://cdn.test/a.jpg", "ext": "jpg"}, {"url": "https://cdn.test/b.mp4", "vcodec": "h264"}]}
        result = await downloader.extract_info("https://instagram.com/p/abc/")
        self.assertEqual(result["_media"]["type"], "mixed")
        proxy.assert_not_called()

    @patch("bot.services.downloader.instagram.extract_proxy_media")
    @patch("bot.services.downloader.ytdlp.extract_info", new_callable=AsyncMock)
    async def test_instagram_structured_proxy_fallback(self, extract, proxy):
        extract.return_value = None
        proxy.return_value = media_result([{"type": "photo", "url": "https://cdn.test/a.jpg"}])
        self.assertEqual((await downloader.extract_info("https://instagram.com/p/abc/"))["_media"]["type"], "photo")

    @patch("bot.services.downloader._download_direct_files", new_callable=AsyncMock)
    async def test_selected_items_download_in_source_order(self, download):
        download.side_effect = [["a.jpg"], ["c.mp4"]]
        items = [{"type": kind, "url": str(index)} for index, kind in enumerate(["photo", "video", "video"], 1)]
        self.assertEqual(await downloader.download_items(items, [3, 1]), ["a.jpg", "c.mp4"])
        self.assertEqual([call.args[0] for call in download.await_args_list], [["1"], ["3"]])

    @patch("bot.services.downloader._download_direct_files", new_callable=AsyncMock)
    async def test_failed_child_cleans_up_album(self, download):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.jpg"
            path.write_bytes(b"photo")
            download.side_effect = [[str(path)], []]
            self.assertEqual(await downloader.download_items([{"type": "photo", "url": "a"}, {"type": "video", "url": "b"}]), [])
            self.assertFalse(path.exists())

    @patch("bot.handlers.media.file_media_type")
    async def test_album_ten_plus_one_uses_correct_types(self, identify):
        identify.side_effect = lambda path: "photo" if int(path) % 2 == 0 else "video"
        message = AsyncMock()
        await media._send_album(message, [str(i) for i in range(11)])
        group = message.answer_media_group.await_args.args[0]
        self.assertEqual([item.type for item in group], ["photo", "video"] * 5)
        message.answer_media_group.assert_awaited_once()
        message.answer_photo.assert_awaited_once()

    @patch("bot.handlers.media.extract_info", new_callable=AsyncMock)
    async def test_photo_video_and_mixed_buttons(self, extract):
        for kinds, expected in [(["photo"], "dl_photo"), (["video"], "dl_video"), (["photo", "video"], "dl_gallery_all")]:
            result = media_result([{"type": kind, "url": f"https://cdn.test/{i}"} for i, kind in enumerate(kinds)])
            extract.return_value = {"_media": result}
            message = AsyncMock()
            message.text = "https://instagram.com/p/abc/"
            reply = message.reply.return_value
            reply.chat.id, reply.message_id = 1, 2
            await media.handle_link(message, AsyncMock())
            keyboard = reply.edit_text.await_args.kwargs["reply_markup"]
            callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
            self.assertIn(expected, callbacks)
            if "video" in kinds:
                self.assertNotIn("dl_photo", callbacks)
            self.assertEqual(media.media_info_cache[(1, 2)]["_media"], result)

    @patch("bot.handlers.media.extract_info", new_callable=AsyncMock)
    async def test_instagram_reel_reaches_media_extractor(self, extract):
        url = "https://www.instagram.com/reel/abc/"
        extract.return_value = None
        message = AsyncMock()
        message.text = url

        await media.handle_link(message, AsyncMock())

        extract.assert_awaited_once_with(url)
        message.reply.assert_awaited_once_with("Analyzing link… ⏳")

    @patch("bot.handlers.media._send_album", new_callable=AsyncMock)
    @patch("bot.handlers.media.download_media", new_callable=AsyncMock)
    async def test_old_album_button_uses_its_own_cache(self, download, send):
        result = {"_media": media_result([{"type": "video", "url": "one"}, {"type": "photo", "url": "two"}])}
        media._store_cache((1, 10), "https://instagram.com/p/old/", result)
        media._store_cache((1, 20), "https://instagram.com/p/new/")
        callback = AsyncMock()
        callback.data = "dl_gallery_all"
        callback.message.chat.id, callback.message.message_id = 1, 10
        state = AsyncMock()
        state.get_data.return_value = {"gallery_cache_id": [1, 20]}
        download.return_value = ["missing-photo.jpg"]
        await media.handle_dl_callback(callback, state)
        download.assert_awaited_once_with("https://instagram.com/p/old/", "gallery", media_info=result)
        self.assertIn((1, 20), media.url_cache)

    @patch("bot.handlers.media.download_media", new_callable=AsyncMock)
    async def test_album_selection_rejects_out_of_bounds_and_invalid_tokens(self, download):
        media._store_cache((1, 10), "https://instagram.com/p/abc/")
        for text in ("0", "3", "1-999999999999", "1,invalid", "2-1"):
            message = AsyncMock()
            message.text = text
            state = AsyncMock()
            state.get_data.return_value = {"url": "https://instagram.com/p/abc/", "gallery_count": 2, "gallery_cache_id": [1, 10]}
            await media.process_gallery_selection(message, state)
            state.set_state.assert_awaited_once_with(media.BotStates.waiting_for_gallery_selection)
        download.assert_not_awaited()

    @patch("bot.services.downloader.tiktok.extract_photos", return_value=["https://cdn.test/1.jpg", "https://cdn.test/2.jpg"])
    async def test_tiktok_photo_post_uses_explicit_items(self, extract):
        result = await downloader.extract_info("https://tiktok.com/@user/photo/123")
        self.assertEqual(result["_media"]["type"], "gallery")
        self.assertEqual(len(result["_media"]["items"]), 2)
