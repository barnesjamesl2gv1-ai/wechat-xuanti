"""账号定位.md

设计文件 §6：
  - 每个自有账号独立一份 `账号定位.md`，只分析自己的内容与表现，不参考对标账号；
  - 首次必须等两个完整三天周期结束，之后每两个完整周期检查/更新一次；
    两个周期内全无新文章则跳过；
  - 依据是「已有全部历史统计报告 + 最近两个周期文章数据」，不是把全文重新喂给模型；
  - 记录风格、优势方向、劣势方向与发展建议；劣势方向只降权，不是黑名单；
  - 每次覆盖同一个 .md，不留历史版本；失败时用旧定位，不阻断正常分析。
"""
from __future__ import annotations

import json
from pathlib import Path

import wechat_index_ai as ai
from common import folder_name, now_text

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "公众号分析输出"
PROFILE_DIR = OUTPUT / "账号定位"
STATE_FILE = PROFILE_DIR / "_state.json"
REPORT_ROOT = OUTPUT / "报告"
AI_ROOT = OUTPUT / "AI选题报告"

PROFILE_SYSTEM = """你是微信公众号账号运营分析助手。

任务：根据提供的数据，写一份该账号的「账号定位」文档，供后续选题分析参考。
只分析这个账号自己，不要提及或参考任何对标账号。

内容要求（用 Markdown，总长度不超过 600 字）：
1. 账号风格：题材偏好、写法特点
2. 优势方向：从实际阅读表现看，哪类题材数据更好
3. 劣势方向：表现偏弱的方向（只作为降权参考，不是禁止选题）
4. 发展建议：接下来可尝试的调整

硬规则：
- 只依据给定数据，不编造文章、数字或外部信息。
- 阅读量缺失的文章不要当作 0。
- 直接输出 Markdown 正文，不要输出 JSON，不要写「以下是」之类的开场白。"""




def profile_path(account: str) -> Path:
    return PROFILE_DIR / f"{folder_name(account)}.md"


def read_profile(account: str) -> str:
    path = profile_path(account)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")[:4000]
    except Exception:
        return ""


def _load_state() -> dict:
    if not STATE_FILE.is_file():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def completed_periods(account: str) -> list[dict]:
    """该账号已完成的三天周期（按结束时间升序）。"""
    base = REPORT_ROOT / "三天周期" / folder_name(account)
    if not base.is_dir():
        return []
    rows = []
    for item in base.iterdir():
        if not item.is_dir():
            continue
        try:
            summary = json.loads((item / "summary.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append({
            "dir": item.name,
            "path": item,
            "period_start": summary.get("period_start") or "",
            "period_end": summary.get("period_end") or "",
            "article_count": summary.get("article_count") or 0,
            "boom_count": summary.get("boom_count") or 0,
            "metrics": summary.get("metrics") or {},
        })
    rows.sort(key=lambda r: r["period_end"])
    return rows


def should_update(account: str) -> tuple[bool, str]:
    periods = completed_periods(account)
    count = len(periods)
    if count < 2:
        return False, f"只完成 {count} 个三天周期，不足两个"

    state = _load_state()
    last_count = int((state.get(account) or {}).get("period_count") or 0)
    if count - last_count < 2:
        return False, f"距上次更新只过了 {count - last_count} 个周期"

    recent = periods[-2:]
    if all(int(p["article_count"]) == 0 for p in recent):
        return False, "最近两个周期没有新文章，跳过更新"
    return True, f"已完成 {count} 个周期，触发更新"


def _history_digest(account: str, limit: int = 2600) -> str:
    base = REPORT_ROOT / "历史总报告"
    if not base.is_dir():
        return ""
    files = sorted(base.glob("*.md"), reverse=True)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        if account and account not in text:
            continue
        return text[:limit]
    return ""


def _period_digest(period: dict, limit: int = 12) -> dict:
    titles = []
    detail = period["path"] / "文章明细.json"
    if detail.is_file():
        try:
            items = json.loads(detail.read_text(encoding="utf-8"))
        except Exception:
            items = []
        for item in items[:limit]:
            titles.append({
                "title": (item.get("title") or "")[:60],
                "read": item.get("read_count"),
                "like": item.get("like_count"),
                "comment": item.get("comment_count"),
                "share": item.get("share_count"),
                "recommend": item.get("recommend_count"),
            })
    return {
        "period": f"{period['period_start']} ~ {period['period_end']}",
        "article_count": period["article_count"],
        "boom_count": period["boom_count"],
        "metrics": period.get("metrics") or {},
        "articles": titles,
    }


def build_context(account: str) -> dict:
    periods = completed_periods(account)
    return {
        "account": account,
        "history_report_digest": _history_digest(account),
        "recent_periods": [_period_digest(p) for p in periods[-2:]],
        "total_completed_periods": len(periods),
        "note": "阅读量为 null 表示当时未取到，不代表 0。",
    }


def update_profile(account: str, *, progress=None) -> str:
    if progress:
        progress("profile", f"更新「{account}」账号定位")
    context = build_context(account)
    text = ai.call_text(PROFILE_SYSTEM, context, max_tokens=1600)
    text = text.strip()
    if len(text) < 60:
        raise ValueError("模型返回的账号定位内容过短，未覆盖旧文件")

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    path = profile_path(account)
    header = f"# {account}｜账号定位\n\n> 更新时间：{now_text()}　依据：历史统计报告 + 最近两个三天周期\n\n"
    path.write_text(header + text + "\n", encoding="utf-8")

    state = _load_state()
    state[account] = {
        "period_count": len(completed_periods(account)),
        "updated_at": now_text(),
    }
    _save_state(state)
    return text


def maybe_update(account: str, *, progress=None) -> dict:
    """轮到更新才更新；任何失败都不抛出，避免阻断原有 AI 分析。"""
    try:
        needed, reason = should_update(account)
        if not needed:
            return {"updated": False, "reason": reason}
        update_profile(account, progress=progress)
        return {"updated": True, "reason": reason}
    except Exception as exc:  # noqa: BLE001
        return {"updated": False, "reason": f"更新失败，沿用旧定位：{exc}"}
