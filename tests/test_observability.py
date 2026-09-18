import json
import logging
import os
import unittest
from unittest.mock import patch

from bot.observability import JsonFormatter, log_context, safe_url_metadata


class ObservabilityTests(unittest.TestCase):
    def test_safe_url_metadata_does_not_expose_path_query_or_credentials(self):
        url = "https://user:password@example.com/private/video/123?token=secret"

        metadata = safe_url_metadata(url)

        self.assertEqual(metadata["source_host"], "example.com")
        self.assertEqual(metadata["source_scheme"], "https")
        serialized = json.dumps(metadata)
        for secret in ("user", "password", "private", "token", "secret"):
            self.assertNotIn(secret, serialized)

    def test_query_tokens_do_not_change_url_fingerprint(self):
        first = safe_url_metadata("https://example.com/video/123?token=first")
        second = safe_url_metadata("https://example.com/video/123?token=second")

        self.assertEqual(first["source_url_hash"], second["source_url_hash"])

    def test_json_formatter_sanitizes_urls_and_includes_context(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "failed at https://example.com/private?id=secret",
            (),
            None,
        )
        record.error_message = "upstream rejected https://example.com/private?id=secret"

        with log_context(request_id="request-1", source_platform="youtube"):
            payload = json.loads(formatter.format(record))

        self.assertEqual(payload["request_id"], "request-1")
        self.assertEqual(payload["source_platform"], "youtube")
        self.assertNotIn("private", payload["message"])
        self.assertNotIn("secret", payload["message"])
        self.assertNotIn("private", payload["error_message"])
        self.assertNotIn("secret", payload["error_message"])

    def test_raw_user_id_is_only_logged_with_a_configured_salt(self):
        class User:
            id = 123456789

        class Chat:
            type = "private"

        class Message:
            from_user = User()
            chat = Chat()

        from bot.observability import request_context

        with patch.dict(os.environ, {}, clear=True):
            context = request_context(Message(), "https://youtu.be/example")
        self.assertNotIn("user_ref", context)

        with patch.dict(os.environ, {"LOG_CONTEXT_SALT": "test-only-salt"}):
            context = request_context(Message(), "https://youtu.be/example")
        self.assertIn("user_ref", context)
        self.assertNotIn("123456789", json.dumps(context))

    def test_json_formatter_redacts_cookie_values_and_auth_headers(self):
        formatter = JsonFormatter()
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "Cookie: sessionid=session-secret; csrftoken=csrf-secret",
            (),
            None,
        )
        record.error_message = (
            "sessionid=embedded-secret Authorization: Bearer bearer-secret"
        )

        serialized = formatter.format(record)

        for secret in (
            "session-secret",
            "csrf-secret",
            "embedded-secret",
            "bearer-secret",
        ):
            self.assertNotIn(secret, serialized)


if __name__ == "__main__":
    unittest.main()
