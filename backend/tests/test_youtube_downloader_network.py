import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from youtube_downloader import _get_ydl_opts  # noqa: E402


class DownloaderNetworkPolicyTests(unittest.TestCase):
    def test_rutube_uses_slow_api_timeout_and_retry_policy(self):
        try:
            options = _get_ydl_opts(
                url="https://rutube.ru/video/98496e99d44465a58d59f2427eba8b46/"
            )
        except TypeError:
            self.fail("network options are not URL-aware for slow Rutube API calls")

        self.assertGreaterEqual(options["socket_timeout"], 60)
        self.assertGreaterEqual(options["retries"], 10)
        self.assertGreaterEqual(options["extractor_retries"], 5)
        self.assertGreaterEqual(options["fragment_retries"], 10)


if __name__ == "__main__":
    unittest.main()
