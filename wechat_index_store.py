"""微信指数两轮验证 · 任务数据层

一个自有账号的每个三天周期，对应一套「两轮验证」任务：
  第一轮：基础 AI 给出的 3 个关键词 → 用户手绘 7 天 / 30 天曲线 → AI 深挖 3 个新关键词
  第二轮：新 3 个关键词 → 同样两张图 → AI 联网找事件 → 最多 3 个选题标题

落盘位置：公众号分析输出/微信指数验证/<账号>/<周期目录>/task.json
只做读写与结构校验，不调用 AI、不发网络请求。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "公众号分析输出"
STORE = OUTPUT / "微信指数验证"
SANDBOX = "_AI联调沙盒"          # 与 AI 选题报告同一个约定：沙盒数据单独一个目录，不混正式

SCHEMA_VERSION = 1
CHARTS = ("d7", "d30")
CHART_DAYS = {"d7": 7, "d30": 30}
CHART_LABELS = {"d7": "7 天", "d30": "30 天"}
# 每个关键词固定一种颜色，按位置分配，不随轮次变化
KEYWORD_COLORS = ("#2b4f9e", "#96690f", "#2f7d5b")
ROUND_KEYS = ("round1", "round2")
MAX_KEYWORDS = 3


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def folder_name(account: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", str(account)).strip(" .")
    return name[:80] or "未知公众号"


def base_dir(sandbox: bool = False) -> Path:
    return STORE / SANDBOX if sandbox else STORE


def task_dir(account: str, period_dir: str, sandbox: bool = False) -> Path:
    return base_dir(sandbox) / folder_name(account) / period_dir


def task_file(account: str, period_dir: str, sandbox: bool = False) -> Path:
    return task_dir(account, period_dir, sandbox) / "task.json"


# ---------------------------------------------------------------- 读写

def load(account: str, period_dir: str, sandbox: bool | None = None) -> dict | None:
    """读任务。sandbox=None 表示「不知道是哪个作用域」，先正式后沙盒找一遍。

    前端只有「选周期」时才带 sandbox；草稿、提交、重新生成这些接口只传账号和周期，
    所以这里按需回退查找，写回时再由任务自己的 sandbox 字段决定落哪个目录。
    """
    scopes = (bool(sandbox),) if sandbox is not None else (False, True)
    for scope in scopes:
        path = task_file(account, period_dir, scope)
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        data["sandbox"] = bool(data.get("sandbox", scope))
        return data
    return None


def save(task: dict) -> Path:
    account = task.get("account") or ""
    period_dir = (task.get("period") or {}).get("dir") or ""
    if not account or not period_dir:
        raise ValueError("任务缺少账号或周期，无法保存")
    task["schema_version"] = SCHEMA_VERSION
    task["sandbox"] = bool(task.get("sandbox"))
    task["updated_at"] = now_text()

    target = task_file(account, period_dir, task["sandbox"])
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(task, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass
    return target


# ---------------------------------------------------------------- 结构

def empty_series() -> dict:
    return {"strokes": [], "points": [], "done": False, "no_data": False}


def empty_chart() -> dict:
    return {"y_max": None, "series": [empty_series() for _ in range(MAX_KEYWORDS)]}


def empty_round(keywords: list[str]) -> dict:
    return {
        "keywords": list(keywords)[:MAX_KEYWORDS],
        "charts": {name: empty_chart() for name in CHARTS},
        "submitted": False,
        "analysis": None,
    }


def new_task(account: str, benchmark: str, period: dict, keywords: list[str],
             sandbox: bool = False) -> dict:
    clean = [str(k).strip() for k in keywords if str(k).strip()][:MAX_KEYWORDS]
    stamp = now_text()
    return {
        "schema_version": SCHEMA_VERSION,
        "account": account,
        "benchmark": benchmark,
        "sandbox": bool(sandbox),
        "period": {
            "start": period.get("period_start") or period.get("start") or "",
            "end": period.get("period_end") or period.get("end") or "",
            "dir": period.get("dir") or "",
        },
        "created_at": stamp,
        "updated_at": stamp,
        "round1": empty_round(clean),
        "round2": empty_round([]),
        "final": {"titles": [], "records": [], "generated_at": None, "last_error": ""},
    }


def chart_state(chart: dict) -> dict:
    series = chart.get("series") or []
    done = sum(1 for s in series if s.get("done"))
    no_data = sum(1 for s in series if s.get("no_data"))
    total = len(series) or MAX_KEYWORDS
    complete = total > 0 and (done + no_data) >= total
    return {
        "done": done,
        "no_data": no_data,
        "total": total,
        "complete": complete,
    }


def round_state(rnd: dict) -> dict:
    charts = rnd.get("charts") or {}
    per = {name: chart_state(charts.get(name) or empty_chart()) for name in CHARTS}
    return {
        "keywords": rnd.get("keywords") or [],
        "per_chart": per,
        "complete": all(per[name]["complete"] for name in CHARTS) and bool(rnd.get("keywords")),
        "submitted": bool(rnd.get("submitted")),
    }


def public_view(task: dict) -> dict:
    """给前端的视图：附上颜色、完成度、当前时间范围。"""
    charts = {}
    for rnd_key in ROUND_KEYS:
        rnd = task.get(rnd_key) or empty_round([])
        rnd_charts = {}
        for name in CHARTS:
            chart = (rnd.get("charts") or {}).get(name) or empty_chart()
            rnd_charts[name] = {
                "y_max": chart.get("y_max"),
                "series": chart.get("series") or [],
                "state": chart_state(chart),
                "days": CHART_DAYS[name],
                "label": CHART_LABELS[name],
            }
        charts[rnd_key] = {
            "keywords": rnd.get("keywords") or [],
            "charts": rnd_charts,
            "state": round_state(rnd),
            "submitted": bool(rnd.get("submitted")),
            "analysis": rnd.get("analysis"),
        }

    return {
        "account": task.get("account"),
        "benchmark": task.get("benchmark"),
        "sandbox": bool(task.get("sandbox")),
        "period": task.get("period"),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "colors": list(KEYWORD_COLORS),
        "charts": charts,
        "final": task.get("final") or {"titles": [], "records": [], "generated_at": None},
    }


def list_tasks(account: str, sandbox: bool = False) -> list[dict]:
    """该账号下指定作用域（正式 / 沙盒）所有周期的任务概要，供「选择任意一期继续」。"""
    base = base_dir(sandbox) / folder_name(account)
    if not base.is_dir():
        return []
    rows = []
    for item in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
        task = load(account, item.name, sandbox)
        if not task:
            continue
        r1 = round_state(task.get("round1") or empty_round([]))
        r2 = round_state(task.get("round2") or empty_round([]))
        final = task.get("final") or {}
        if final.get("titles"):
            stage = "已出标题"
        elif r2["submitted"]:
            stage = "第二轮已提交"
        elif r2["keywords"]:
            stage = "第二轮进行中"
        elif r1["submitted"]:
            stage = "第一轮已提交"
        elif r1["complete"]:
            stage = "曲线已画完"
        else:
            stage = "待绘图"
        rows.append({
            "period_dir": item.name,
            "period": task.get("period") or {},
            "stage": stage,
            "updated_at": task.get("updated_at"),
            "title_count": len(final.get("titles") or []),
        })
    return rows
