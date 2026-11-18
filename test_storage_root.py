"""文章数据目录解析的离线测试。

XUANTI_STORAGE_ROOT 让本项目能读采集端的输出目录，不必把文章复制过来。
"""
from __future__ import annotations

import json
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


class StorageRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {k: __import__("os").environ.pop(k, None) for k in ("XUANTI_STORAGE_ROOT",)}
        self._env_file = web_app.ENV_FILE
        self.addCleanup(self._restore_env)
        # 默认指向一个不存在的 .env，避免读到本机真实配置
        web_app.ENV_FILE = make_temp_dir() / "absent.env"
        self.addCleanup(shutil.rmtree, web_app.ENV_FILE.parent, True)

    def _restore_env(self) -> None:
        import os
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        web_app.ENV_FILE = self._env_file

    def test_default_points_to_project_storages(self):
        self.assertEqual(web_app.resolve_storage_root(), web_app.ROOT / "storages")

    def test_env_var_relative_path(self):
        import os
        os.environ["XUANTI_STORAGE_ROOT"] = "collector/storages"
        self.assertEqual(web_app.resolve_storage_root(),
                         (web_app.ROOT / "collector" / "storages").resolve())

    def test_env_var_absolute_path(self):
        import os
        target = make_temp_dir()
        self.addCleanup(shutil.rmtree, target, True)
        os.environ["XUANTI_STORAGE_ROOT"] = str(target)
        self.assertEqual(web_app.resolve_storage_root(), target)

    def test_dot_env_file_is_read(self):
        env_file = make_temp_dir() / ".env"
        self.addCleanup(shutil.rmtree, env_file.parent, True)
        env_file.write_text("XUANTI_STORAGE_ROOT=collector/storages\n", encoding="utf-8")
        web_app.ENV_FILE = env_file
        self.assertEqual(web_app.resolve_storage_root(),
                         (web_app.ROOT / "collector" / "storages").resolve())

    def test_storage_ready_requires_article_files(self):
        import os
        root = make_temp_dir()
        self.addCleanup(shutil.rmtree, root, True)
        os.environ["XUANTI_STORAGE_ROOT"] = str(root)
        self.assertFalse(web_app.storage_ready())

        article = root / "示例账号" / "2026-01-01 00-00 标题__abc"
        article.mkdir(parents=True)
        (article / "article_detail.json").write_text(
            json.dumps({"title": "x"}, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(web_app.storage_ready())


if __name__ == "__main__":
    unittest.main(verbosity=2)
