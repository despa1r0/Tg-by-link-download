import json
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
