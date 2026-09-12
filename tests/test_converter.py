import unittest

from bot.services.converter import _timestamp_to_seconds


class TimestampTests(unittest.TestCase):
    def test_supported_timestamp_formats(self):
        self.assertEqual(_timestamp_to_seconds("5"), 5)
        self.assertEqual(_timestamp_to_seconds("01:05"), 65)
        self.assertEqual(_timestamp_to_seconds("1:01:05"), 3665)

    def test_invalid_timestamp_components(self):
        for value in ("", ":", "1::2", "01:60", "a:10"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _timestamp_to_seconds(value)


if __name__ == "__main__":
    unittest.main()
