"""访问口令（XUANTI_ACCESS_TOKEN）的离线测试。

没有口令时行为必须和以前完全一样；设了口令则只有 /auth?token= 能换到 cookie。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import web_app


class AccessTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = web_app.ACCESS_TOKEN
        self.addCleanup(setattr, web_app, "ACCESS_TOKEN", self._original)
        web_app.ACCESS_TOKEN = ""
        self.client = web_app.app.test_client()

    def test_without_token_everything_is_open(self):
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_api_is_blocked_without_cookie(self):
        web_app.ACCESS_TOKEN = "s3cret"
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(resp.get_json()["ok"])

    def test_page_is_blocked_without_cookie(self):
        web_app.ACCESS_TOKEN = "s3cret"
        self.assertEqual(self.client.get("/").status_code, 401)

    def test_static_and_favicon_stay_open(self):
        web_app.ACCESS_TOKEN = "s3cret"
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/favicon.ico").status_code, 200)

    def test_wrong_token_is_rejected(self):
        web_app.ACCESS_TOKEN = "s3cret"
        self.assertEqual(self.client.get("/auth?token=nope").status_code, 401)

    def test_right_token_sets_cookie_and_unlocks(self):
        web_app.ACCESS_TOKEN = "s3cret"
        resp = self.client.get("/auth?token=s3cret")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/")
        self.assertEqual(self.client.get("/api/status").status_code, 200)

    def test_auth_without_configured_token_goes_home(self):
        self.assertEqual(self.client.get("/auth?token=whatever").status_code, 302)


if __name__ == "__main__":
    unittest.main(verbosity=2)
