"""接口层回归测试：/api/status 的结果必须能被 jsonify 直接序列化。

背景：latest_ai_result() 曾经把 Path 对象塞进返回值，正式作用域第一次出现
AI 结果时 /api/status 就会 500（沙盒数据被跳过，所以开发阶段一直没暴露）。
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

# 有些环境不允许写系统临时目录，统一在项目内的 .tmp（已 gitignore）里建夹具
TMP_ROOT = Path(__file__).resolve().parent / ".tmp"


def make_temp_dir() -> Path:
    """在项目内的 .tmp（已 gitignore）里建夹具目录。

    不用 tempfile.mkdtemp：某些受限环境下它创建的目录不允许再建子目录。
    """
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    for _ in range(10):
        candidate = TMP_ROOT / f"fx-{uuid.uuid4().hex[:12]}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError("无法创建测试夹具目录")


def write_ai_result(root: Path) -> None:
    target = root / "AI选题报告" / "示例公众号" / "20261028_000000__20261031_000000"
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "created_at": "2026-10-28 10:15:00",
        "pair": {"own_account": "示例公众号", "benchmark_account": "示例对标号",
                 "account_type": "old"},
        "period": {"start": "2026-10-28 00:00:00", "end": "2026-10-31 00:00:00"},
        "data_counts": {"own_articles": 4, "benchmark_viral": 9},
        "model": {"name": "deepseek-flash", "reasoning_effort": "low"},
        "analysis": {"candidates": []},
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    (target / "分析结果.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class StatusSerializationTests(unittest.TestCase):
    def test_latest_ai_result_is_json_serializable(self):
        root = make_temp_dir()
        self.addCleanup(shutil.rmtree, root, True)
        write_ai_result(root)

        original = web_app.AI_ROOT
        web_app.AI_ROOT = root
        self.addCleanup(setattr, web_app, "AI_ROOT", original)
        latest = web_app.latest_ai_result()
        self.assertIsNotNone(latest, "应该能扫到刚写入的 AI 结果")
        json.dumps(latest)          # 有 Path 就会在这里抛 TypeError
        self.assertIsInstance(latest.get("id"), str)

    def test_build_status_serializes_with_ai_result(self):
        root = make_temp_dir()
        self.addCleanup(shutil.rmtree, root, True)
        write_ai_result(root)

        original = web_app.AI_ROOT
        web_app.AI_ROOT = root
        self.addCleanup(setattr, web_app, "AI_ROOT", original)
        payload = web_app.build_status()
        json.dumps(payload, ensure_ascii=False)
        self.assertTrue(payload.get("ok"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
