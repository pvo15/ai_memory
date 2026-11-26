import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import dashboard  # noqa: E402

ACCESS = 'INFO:     127.0.0.1:41648 - "%s" %s'


class LevelOf(unittest.TestCase):
    def test_ok_request_with_error_in_url_is_info(self):
        line = ACCESS % ("GET /context?branch=fix%2Fx&project=my-error-tracker HTTP/1.1", "200 OK")
        self.assertEqual(dashboard.level_of(line), "info")

    def test_ok_request_with_retry_in_url_is_info(self):
        line = ACCESS % ("GET /context?branch=fix-retry-logic HTTP/1.1", "200 OK")
        self.assertEqual(dashboard.level_of(line), "info")

    def test_server_error_status_is_error(self):
        line = ACCESS % ("POST /mcp HTTP/1.1", "500 Internal Server Error")
        self.assertEqual(dashboard.level_of(line), "error")

    def test_client_error_status_is_info(self):
        line = ACCESS % ("POST /mcp HTTP/1.1", "400 Bad Request")
        self.assertEqual(dashboard.level_of(line), "info")

    def test_error_lines_still_error(self):
        for line in (
            "ERROR:    Exception in ASGI application",
            "Traceback (most recent call last):",
            "sync failed: timeout",
        ):
            self.assertEqual(dashboard.level_of(line), "error", line)

    def test_warning_lines_still_warn(self):
        self.assertEqual(dashboard.level_of("WARNING:  retry in 5s"), "warn")

    def test_quoted_request_in_app_line_is_ignored(self):
        self.assertEqual(dashboard.level_of('proxy saw "GET /my-error-tracker HTTP/1.1"'), "info")

    def test_quoted_app_message_keeps_keyword_match(self):
        self.assertEqual(dashboard.level_of('sync: "push failed"'), "error")

    def test_plain_line_is_info(self):
        self.assertEqual(dashboard.level_of("INFO:     Application startup complete."), "info")


if __name__ == "__main__":
    unittest.main()
