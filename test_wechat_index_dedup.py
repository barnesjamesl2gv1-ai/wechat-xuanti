r"""事件去重的离线回归测试（不发网络请求、不调模型、不碰 user 数据）。

运行：
    .\.venv\Scripts\python.exe -m unittest test_wechat_index_dedup -v

覆盖两件事：
1. 相似度阈值真的能认出「同一事件的换说法」——这是「重新生成不得重复」的兜底；
2. 阈值不会把「同地区、同领域的不同事件」误判成重复。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import wechat_index_ai
import wechat_index_excel as dedup


def row(event_id: str, summary: str, title: str = "示例标题") -> dict:
    return {"event_id": event_id, "event_summary": summary, "title": title,
            "period": "2026-10-19 00:00:00 ~ 2026-10-22 00:00:00"}


# 与真实去重库里的三条事件同构的样本
EXISTING = [
    row("山东东营+黄河三角洲+2026-11-15",
        "2026候鸟迁徙季，山东东营黄河三角洲出现“鸟浪”景观。",
        "黄河三角洲“鸟浪”凌空：候鸟迁徙季的生态答卷"),
    row("山西古交+张某某非法狩猎案+2026-11-03",
        "古交法院发布典型案例，张某某禁猎期布设捕兽夹捕猎狗獾、猪獾。",
        "古交法院发布非法狩猎典型案例：禁猎期布夹捕猎狗獾"),
    row("重庆+2026野生动物保护月+2026-11-14",
        "重庆启动2026野生动物保护月，救助猛禽放归大自然。",
        "重庆启动野生动物保护月，救助猛禽放归自然"),
]


class EventIdTests(unittest.TestCase):
    def test_same_normalized_event_id_is_duplicate(self):
        hit = dedup.is_duplicate_event(
            "山东东营 + 黄河三角洲 + 2026-11-15",
            "完全不同的要点文字。", EXISTING)
        self.assertIsNotNone(hit, "标识相同（忽略空格）就该判重")

    def test_different_event_id_can_still_match_by_summary(self):
        hit = dedup.is_duplicate_event("新标识-xxx", "2026候鸟迁徙季，山东东营黄河三角洲出现“鸟浪”景观。",
                                       EXISTING)
        self.assertIsNotNone(hit, "标识不同但要点几乎一样，也该判重")


class ParaphraseRecallTests(unittest.TestCase):
    """模型重新生成时会换说法，这些都必须判重，否则「重新生成」会重复推荐。"""

    SAME_EVENT = [
        ("候鸟迁徙季 东营黄河三角洲出现鸟浪奇观", 0),
        ("2026 年候鸟迁徙季，山东东营黄河三角洲出现鸟浪景观。", 0),
        ("山西古交法院通报非法狩猎案：禁猎期用捕兽夹猎捕狗獾猪獾", 1),
        ("重庆2026年野生动物保护月启动，猛禽救助后放归", 2),
        ("重庆市林业局启动野生动物保护宣传月", 2),
    ]

    def test_paraphrases_are_detected(self):
        for text, index in self.SAME_EVENT:
            with self.subTest(text=text):
                hit = dedup.is_duplicate_event("", text, EXISTING)
                self.assertIsNotNone(hit, f"同事件换说法没判重：{text}")
                self.assertEqual(hit["event_summary"], EXISTING[index]["event_summary"])

    def test_threshold_is_in_sane_range(self):
        self.assertGreater(dedup.SEQ_DUP_THRESHOLD, 0.3,
                           "序列相似度阈值过低会把不同事件全判成重复")
        self.assertLess(dedup.SEQ_DUP_THRESHOLD, 0.75,
                           "阈值过高会让换说法的同事件漏网（旧的 0.72 实操几乎不命中）")
        self.assertGreater(dedup.CORE_DUP_THRESHOLD, 0.3)
        self.assertLess(dedup.CORE_DUP_THRESHOLD, 0.75)


class FalsePositiveTests(unittest.TestCase):
    """同地区、同领域但确实是新事件，不能被挡掉。"""

    DISTINCT_EVENTS = [
        "重庆启动 2026 年春季植树造林活动。",
        "野生动物保护法修订草案提请审议。",
        "山东东营：黄河口生态旅游区门票价格调整。",
        "内蒙古警方破获特大非法狩猎案，查获猎夹 200 余个。",
        "云南西双版纳：亚洲象群再次进城觅食。",
    ]

    def test_distinct_events_not_blocked(self):
        for text in self.DISTINCT_EVENTS:
            with self.subTest(text=text):
                self.assertIsNone(dedup.is_duplicate_event("", text, EXISTING),
                                  f"把新事件误判成重复：{text}")


class ValidateRound2Tests(unittest.TestCase):
    """第二轮校验：跨批次去重，但同批次同事件多角度要保留。"""

    @staticmethod
    def _title(title: str, summary: str, event_id: str = "", sources=None) -> dict:
        return {
            "title": title,
            "keyword": "候鸟迁徙",
            "event_id": event_id,
            "event_summary": summary,
            "angle": "科普",
            "sources": sources if sources is not None else [{"title": "来源", "url": "https://example.com/a"}],
        }

    def test_same_event_multiple_angles_allowed_within_one_batch(self):
        data = {"titles": [
            self._title("东营鸟浪刷屏，先看清这事", "东营黄河三角洲候鸟迁徙季出现鸟浪。", "新-1"),
            self._title("鸟浪不是特效，是迁徙季的必然", "东营黄河三角洲候鸟迁徙季出现鸟浪。", "新-1"),
        ]}
        result = wechat_index_ai.validate_round2(data, ["候鸟迁徙"], [])
        self.assertEqual(len(result["titles"]), 2, "同一次生成里同事件的两个角度应都保留")
        self.assertEqual(result["dropped"], [])

    def test_existing_event_is_dropped(self):
        data = {"titles": [
            self._title("黄河三角洲鸟浪", "2026候鸟迁徙季，山东东营黄河三角洲出现“鸟浪”景观。"),
        ]}
        result = wechat_index_ai.validate_round2(data, ["候鸟迁徙"], EXISTING)
        self.assertEqual(result["titles"], [], "库里已有的事件必须挡掉")
        self.assertEqual(len(result["dropped"]), 1)
        self.assertIn("已推荐过", result["dropped"][0]["reason"])

    def test_missing_sources_is_dropped(self):
        data = {"titles": [
            self._title("没有来源的标题", "某个事件。", "x", sources=[]),
        ]}
        result = wechat_index_ai.validate_round2(data, ["候鸟迁徙"], [])
        self.assertEqual(result["titles"], [])
        self.assertIn("缺少标题、事件要点或来源链接", result["dropped"][0]["reason"])

    def test_titles_capped_at_three(self):
        data = {"titles": [
            self._title(f"标题{i}", f"完全不同的第 {i} 个事件。", f"e{i}") for i in range(5)
        ]}
        result = wechat_index_ai.validate_round2(data, ["候鸟迁徙"], [])
        self.assertEqual(len(result["titles"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
