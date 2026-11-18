"""配置接口的离线测试：写入 .env 的合并语义、校验、以及不回传密钥。"""
from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import web_app

TMP_ROOT = Path(__file__).resolve().parent / ".tmp"


def make_temp_dir() -> Path:
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    for _ in range(10):
        candidate = TMP_ROOT / f"fx-{uuid.uuid4().hex[:12]}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError("无法创建测试夹具目录")


class SettingsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_file = web_app.ENV_FILE
        self._env = {k: os.environ.pop(k, None) for k in
                     ("XUANTI_STORAGE_ROOT", "DEEPSEEK_API_KEY", "SEARCH_PROVIDER",
                      "BOCHA_API_KEY", "TAVILY_API_KEY")}
        self.dir = make_temp_dir()
        web_app.ENV_FILE = self.dir / ".env"
        web_app.ENV_FILE.write_text(
            "# 我的配置\nSEARCH_PROVIDER=bocha\nBOCHA_API_KEY=keep-me\n",
            encoding="utf-8")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        web_app.ENV_FILE = self._env_file
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # ------------------------------------------------------------ 写入语义
    def test_write_env_merges_and_preserves_comments(self):
        web_app.write_env({"DEEPSEEK_API_KEY": "sk-test-123"})
        text = web_app.ENV_FILE.read_text(encoding="utf-8")
        self.assertIn("# 我的配置", text)
        self.assertIn("BOCHA_API_KEY=keep-me", text)
        self.assertIn("DEEPSEEK_API_KEY=sk-test-123", text)

    def test_write_env_replaces_existing_value(self):
        web_app.write_env({"SEARCH_PROVIDER": "tavily"})
        text = web_app.ENV_FILE.read_text(encoding="utf-8")
        self.assertIn("SEARCH_PROVIDER=tavily", text)
        self.assertNotIn("SEARCH_PROVIDER=bocha", text)

    def test_write_env_rejects_newline(self):
        with self.assertRaises(ValueError):
            web_app.write_env({"DEEPSEEK_API_KEY": "sk-a\nsk-b"})

    def test_write_env_requires_content(self):
        with self.assertRaises(ValueError):
            web_app.write_env({"DEEPSEEK_API_KEY": "   "})

    # ------------------------------------------------------------ 接口
    def test_get_returns_status_without_key_text(self):
        web_app.write_env({"DEEPSEEK_API_KEY": "sk-secret-value"})
        client = web_app.app.test_client()
        body = client.get("/api/settings").get_json()
        self.assertTrue(body["key_ready"])
        self.assertNotIn("sk-secret-value", str(body))

    def test_post_saves_key_and_storage_root(self):
        client = web_app.app.test_client()
        resp = client.post("/api/settings", json={
            "deepseek_api_key": "sk-new",
            "storage_root": "collector/storages",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["key_ready"])
        self.assertTrue(body["storage_root"].endswith("collector\\storages")
                        or body["storage_root"].endswith("collector/storages"))
        self.assertNotIn("sk-new", str(body))

    def test_post_rejects_unknown_provider(self):
        client = web_app.app.test_client()
        resp = client.post("/api/settings", json={"search_provider": "bing"})
        self.assertEqual(resp.status_code, 400)

    def test_post_with_empty_payload_is_rejected(self):
        client = web_app.app.test_client()
        resp = client.post("/api/settings", json={"deepseek_api_key": ""})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
