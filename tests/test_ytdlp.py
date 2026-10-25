import unittest

from bot.config import MAX_DOWNLOAD_BYTES
from bot.services.providers.ytdlp import _select_video_format, _video_options


def _format(
    format_id, *, video="none", audio="none", size=None, approx=None, width=None, height=None
):
    return {
        "width": width,
        "height": height,
        "format_id": format_id,
        "format": format_id,
        "ext": "mp4" if video != "none" else "m4a",
        "protocol": "https",
        "vcodec": video,
        "acodec": audio,
        "filesize": size,
        "filesize_approx": approx,
    }


class VideoFormatSelectorTests(unittest.TestCase):
    def test_video_download_options_enforce_configured_hard_limit(self):
        options = _video_options()

        self.assertIs(options["format"], _select_video_format)
        self.assertEqual(options["max_filesize"], MAX_DOWNLOAD_BYTES)
        self.assertEqual(options["merge_output_format"], "mp4")

    def test_chooses_smaller_separate_streams_when_best_video_is_too_large(self):
        formats = [
            _format("audio", audio="aac", size=4),
            _format("medium-video", video="h264", size=40),
            _format("oversized-video", video="h264", size=60),
        ]

        selected = list(_select_video_format({"formats": formats}, max_bytes=50))

        self.assertEqual(selected[0]["format_id"], "medium-video+audio")
        self.assertEqual(selected[0]["filesize_approx"], 44)

    def test_uses_approximate_size_for_youtube_formats(self):
        formats = [
            _format("compact", video="h264", audio="aac", approx=45),
            _format("best", video="h264", audio="aac", approx=70),
        ]

        selected = list(_select_video_format({"formats": formats}, max_bytes=50))

        self.assertEqual(selected[0]["format_id"], "compact")

    def test_prefers_known_safe_format_over_higher_unknown_size_format(self):
        formats = [
            _format("known-safe", video="h264", audio="aac", size=45),
            _format("unknown-best", video="h264", audio="aac"),
        ]

        selected = list(_select_video_format({"formats": formats}, max_bytes=50))

        self.assertEqual(selected[0]["format_id"], "known-safe")

    def test_keeps_unknown_size_as_fallback_for_other_extractors(self):
        formats = [_format("unknown", video="h264", audio="aac")]

        selected = list(_select_video_format({"formats": formats}, max_bytes=50))

        self.assertEqual(selected[0]["format_id"], "unknown")

    def test_rejects_download_when_all_known_formats_are_oversized(self):
        formats = [_format("oversized", video="h264", audio="aac", size=51)]

        self.assertEqual(
            list(_select_video_format({"formats": formats}, max_bytes=50)),
            [],
        )

    def test_prefers_h264_aac_up_to_1080p_over_larger_av1(self):
        # Mirrors YouTube Shorts, where AV1/VP9 offer 1440p and 4K streams.
        formats = [
            _format("aac", audio="mp4a.40.2", size=1),
            _format("h264-720", video="avc1.4D401F", size=5, width=720, height=1280),
            _format("h264-1080", video="avc1.640028", size=15, width=1080, height=1920),
            _format("opus", audio="opus", size=1),
            _format("vp9-1080", video="vp9", size=9, width=1080, height=1920),
            _format("av1-2160", video="av01.0.12M.08", size=40, width=2160, height=3840),
        ]

        selected = list(_select_video_format({"formats": formats}, max_bytes=50))

        self.assertEqual(selected[0]["format_id"], "h264-1080+aac")

    def test_smaller_limit_selects_lower_compatible_resolution(self):
        formats = [
            _format("aac", audio="mp4a.40.2", size=1),
            _format("h264-720", video="avc1.4D401F", size=5, width=720, height=1280),
            _format("h264-1080", video="avc1.640028", size=15, width=1080, height=1920),
        ]

        selected = list(_select_video_format({"formats": formats}, max_bytes=10))

        self.assertEqual(selected[0]["format_id"], "h264-720+aac")

    def test_falls_back_to_other_codecs_with_audio_instead_of_silent_h264(self):
        formats = [
            _format("opus", audio="opus", size=1),
            _format("h264-720", video="avc1.4D401F", size=5, width=720, height=1280),
            _format("vp9-1080", video="vp9", size=9, width=1080, height=1920),
        ]

        selected = list(_select_video_format({"formats": formats}, max_bytes=50))

        self.assertEqual(selected[0]["format_id"], "vp9-1080+opus")

    def test_destination_limit_lowers_hard_limit(self):
        options = _video_options(10)

        self.assertEqual(options["max_filesize"], 10)
        formats = [
            _format("small", video="avc1", audio="mp4a", size=9),
            _format("large", video="avc1", audio="mp4a", size=12),
        ]
        selected = list(options["format"]({"formats": formats}))
        self.assertEqual(selected[0]["format_id"], "small")


if __name__ == "__main__":
    unittest.main()
