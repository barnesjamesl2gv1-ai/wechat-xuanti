"""微信指数模块 · AI 分析（两轮）

分工：
  search_provider.py  负责真的去联网搜；
  本模块负责把「手绘换算出来的趋势数据 + 联网检索到的公开信息」交给 DeepSeek，
  产出结构化结果，并做严格校验。

两条硬规则（来自设计文件）：
  1. 手绘数值是用户估算，绝不能描述成「微信指数官方数据」；
  2. 联网结果必须真的来自搜索，模型不得凭空编造事件。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import search_provider as sp
import wechat_index_excel

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

MODEL = "deepseek-flash"
EFFORT = "low"
API_ENDPOINT = "https://api.deepseek.com/chat/completions"
OUTPUT_MAX_TOKENS = 2400

CURVE_SAMPLE_POINTS = 14
ROUND1_SEARCH_PER_KEYWORD = 1
ROUND1_FETCH_PER_KEYWORD = 2


# ---------------------------------------------------------------- 基础

def read_api_key() -> str:
    env = sp.load_env(ENV_FILE)
    return (env.get("DEEPSEEK_API_KEY") or "").strip()


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def call_json(system_prompt: str, user_payload: dict, *, max_tokens: int = OUTPUT_MAX_TOKENS) -> tuple[dict, dict]:
    key = read_api_key()
    if not key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，无法调用模型")
    body = {
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        API_ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )

    result = None
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                result = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            # HTTP 错误不重试，避免重复计费
            raise RuntimeError(f"DeepSeek 请求失败：HTTP {exc.code}（未自动重试，避免重复费用）") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt == 1:
                time.sleep(2)  # 连接被重置这类临时网络问题，按设计允许自动重试一次
                continue
            raise RuntimeError(f"DeepSeek 网络连接被中断（已重试一次）：{exc}") from exc

    if result is None:
        raise RuntimeError(f"DeepSeek 调用失败：{last_error}")

    try:
        choice = result["choices"][0]
        usage_info = result.get("usage") or {}
        if choice.get("finish_reason") == "length":
            raise ValueError(
                "模型输出被截断：completion_tokens="
                f"{usage_info.get('completion_tokens')}，reasoning_tokens="
                f"{usage_info.get('completion_tokens_details', {}).get('reasoning_tokens') if isinstance(usage_info.get('completion_tokens_details'), dict) else 'n/a'}"
            )
        content = choice["message"]["content"]
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("模型返回的不是 JSON 对象")
        usage = {k: result.get("usage", {}).get(k) for k in
                 ("prompt_tokens", "completion_tokens", "total_tokens")
                 if isinstance(result.get("usage", {}).get(k), int)}
        return data, usage
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("模型返回无法解析为规定的 JSON") from exc


# ---------------------------------------------------------------- 曲线换算

def _series_points(series: dict) -> list[list[float]]:
    points = series.get("points") or []
    clean = []
    for item in points:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                clean.append([int(item[0]), float(item[1])])
            except (TypeError, ValueError):
                continue
    clean.sort(key=lambda p: p[0])
    return clean


def summarize_series(series: dict, *, now_ms: int | None = None) -> dict:
    """把一条手绘曲线整理成：采样点 + 最近 1～2 天变化 + 峰值。"""
    points = _series_points(series)
    if series.get("no_data"):
        return {"points": [], "no_data": True, "recent_change": None, "peak": None, "first": None, "last": None}
    if not points:
        return {"points": [], "no_data": False, "recent_change": None, "peak": None, "first": None, "last": None}

    now_ms = now_ms or int(datetime.now().timestamp() * 1000)
    span = max(1, len(points) // CURVE_SAMPLE_POINTS)
    sampled = points[::span]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])

    def label(ts: int) -> str:
        return datetime.fromtimestamp(ts / 1000).strftime("%m-%d %H:%M")

    recent = [p for p in points if p[0] >= now_ms - 2 * 86400000]
    earlier = [p for p in points if now_ms - 4 * 86400000 <= p[0] < now_ms - 2 * 86400000]
    recent_change = None
    if recent and earlier:
        r = max(p[1] for p in recent)
        e = max(p[1] for p in earlier)
        if e > 0:
            recent_change = round((r - e) / e * 100, 1)

    return {
        "points": [[label(ts), round(value, 1)] for ts, value in sampled],
        "no_data": False,
        "recent_change": recent_change,
        "peak": round(max(p[1] for p in points), 1),
        "first": round(points[0][1], 1),
        "last": round(points[-1][1], 1),
    }


def build_keyword_block(task: dict, round_key: str, now_ms: int) -> list[dict]:
    rnd = task.get(round_key) or {}
    keywords = rnd.get("keywords") or []
    charts = rnd.get("charts") or {}
    block = []
    for index, keyword in enumerate(keywords):
        item = {"keyword": keyword}
        for name, label in (("d7", "近7天"), ("d30", "近30天")):
            series = ((charts.get(name) or {}).get("series") or [])[index] if index < len(((charts.get(name) or {}).get("series") or [])) else {}
            item[label] = summarize_series(series or {}, now_ms=now_ms)
        block.append(item)
    return block


# ---------------------------------------------------------------- 联网检索

def search_keyword(keyword: str, *, count: int = 6, progress=None) -> list[dict]:
    if progress:
        progress("search", f"联网搜索「{keyword}」")
    try:
        rows = sp.search(f"{keyword} 新闻 近一周", count)
    except Exception:
        rows = []
    picked = rows[:count]
    for row in picked[:ROUND1_FETCH_PER_KEYWORD]:
        if not row.get("content") or len(row["content"]) < 200:
            text = sp.fetch_text(row["url"], limit=1800)
            if text:
                row["content"] = text
    return picked


def collect_search(keywords: list[str], *, progress=None) -> dict:
    out = {}
    for keyword in keywords:
        for _ in range(ROUND1_SEARCH_PER_KEYWORD):
            out[keyword] = search_keyword(keyword, progress=progress)
            break
    return out


def _trim_rows(rows: list[dict], *, limit: int = 6, size: int = 400) -> list[dict]:
    trimmed = []
    for row in rows[:limit]:
        trimmed.append({
            "title": (row.get("title") or "")[:120],
            "url": row.get("url") or "",
            "published": (row.get("published") or "")[:32],
            "summary": (row.get("content") or row.get("snippet") or "")[:size],
        })
    return trimmed


# ---------------------------------------------------------------- 第一轮

ROUND1_SYSTEM = """你是微信公众号选题分析助手，负责「微信指数两轮验证」的第一轮。

输入包含：
- 三个关键词，以及用户手工在微信指数页面上描出的近似趋势（近 7 天与近 30 天）。
  这些数值是用户目测描线的估算值，不是微信指数官方采集数据，不得表述为官方数值。
- 每个关键词的近期公开资讯检索结果（标题、链接、摘要）。

任务：
1. 结合趋势与资讯，判断每个关键词当前的热度走势；优先看最近 1～2 天的上涨速度，总热度量为次要参考；30 天曲线用来看是否可持续。
2. 围绕更有潜力的方向，给出 3 个全新的、比原来更具体的关键词，用于第二轮验证。
   - 必须与第一轮的 3 个关键词完全不同（不得重复或仅加标点）。
   - 可以集中在同一优势方向，不强制平均分配。
   - 必须能在微信指数里查到，避免过长的整句。

只输出 JSON，结构如下：
{
  "assessment": "对这一轮整体走势的简短判断（120 字以内）",
  "per_keyword": [
    {"keyword": "原关键词", "trend": "上升|平稳|下滑", "reason": "判断依据，引用曲线或资讯要点（80 字以内）"}
  ],
  "next_keywords": [
    {"keyword": "新关键词", "why": "为什么选它（50 字以内）"}
  ],
  "sources": [{"title": "资讯标题", "url": "链接"}]
}
next_keywords 必须恰好 3 条。不要输出分析过程以外的内容。"""


def call_text(system_prompt: str, user_payload: dict, *, max_tokens: int = 1600) -> str:
    """纯文本输出（用于账号定位这类 Markdown 结果）。"""
    key = read_api_key()
    if not key:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，无法调用模型")
    body = {
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        API_ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    result = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                result = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"DeepSeek 请求失败：HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if attempt == 1:
                time.sleep(2)
                continue
            raise RuntimeError(f"DeepSeek 网络连接被中断（已重试一次）：{exc}") from exc
    if result is None:
        raise RuntimeError("DeepSeek 调用失败")
    try:
        choice = result["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("模型输出被截断，未覆盖旧定位")
        return str(choice["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("模型返回结构异常") from exc


def validate_round1(data: dict, round1_keywords: list[str]) -> dict:
    next_rows = data.get("next_keywords")
    if not isinstance(next_rows, list):
        raise ValueError("模型未按要求返回 next_keywords")

    cleaned, seen = [], set()
    for row in next_rows:
        word = str((row or {}).get("keyword") if isinstance(row, dict) else row or "").strip()
        if not word:
            continue
        if word in round1_keywords or word in seen:
            continue
        seen.add(word)
        cleaned.append({"keyword": word, "why": str((row or {}).get("why") or "")[:120] if isinstance(row, dict) else ""})
        if len(cleaned) == 3:
            break

    if len(cleaned) < 3:
        raise ValueError(f"模型给出的新关键词不足 3 个且不重复（实际 {len(cleaned)} 个），请重新生成")

    sources = []
    for row in (data.get("sources") or [])[:12]:
        if isinstance(row, dict) and row.get("url"):
            sources.append({"title": str(row.get("title") or "")[:160], "url": str(row["url"])[:600]})

    return {
        "assessment": str(data.get("assessment") or "")[:600],
        "per_keyword": [
            {
                "keyword": str(row.get("keyword") or "")[:80],
                "trend": str(row.get("trend") or "")[:12],
                "reason": str(row.get("reason") or "")[:300],
            }
            for row in (data.get("per_keyword") or [])[:6] if isinstance(row, dict)
        ],
        "next_keywords": cleaned,
        "sources": sources,
        "generated_at": now_text(),
        "model": {"name": MODEL, "reasoning_effort": EFFORT},
    }


def run_round1(task: dict, *, progress=None, history_hint: str = "") -> dict:
    """执行第一轮：联网检索 → 模型判断 → 3 个新关键词。返回更新后的任务。"""
    def step(name: str, detail: str = "") -> None:
        if progress:
            progress(name, detail)

    now_ms = int(datetime.now().timestamp() * 1000)
    round1 = task.get("round1") or {}
    keywords = round1.get("keywords") or []
    if len(keywords) < 3:
        raise RuntimeError("第一轮关键词不足 3 个，无法提交")

    step("prepare", "整理手绘趋势数据")
    curve_block = build_keyword_block(task, "round1", now_ms)
    if not any(block.get("近7天", {}).get("points") for block in curve_block):
        raise RuntimeError("没有可用的曲线数据，请先完成手绘")

    step("search", "联网检索近期资讯")
    search_map = collect_search(keywords, progress=progress)

    payload = {
        "account": task.get("account"),
        "period": task.get("period"),
        "notice": "以下热度数值为用户手绘估算，不是微信指数官方数据。",
        "keywords": curve_block,
        "search_results": {k: _trim_rows(v) for k, v in search_map.items()},
        "account_history_hint": history_hint,
    }

    step("model", "调用 DeepSeek 分析走势并推导新关键词")
    data, usage = call_json(ROUND1_SYSTEM, payload)
    step("validate", "校验关键词")
    analysis = validate_round1(data, keywords)
    analysis["usage"] = usage

    task["round1"]["submitted"] = True
    task["round1"]["analysis"] = analysis
    task["round1"]["submitted_at"] = now_text()

    # 新的第二轮关键词 → 重置第二轮画布（旧草稿按设计清空）
    from wechat_index_store import empty_round  # 局部导入避免循环

    task["round2"] = empty_round([row["keyword"] for row in analysis["next_keywords"]])
    step("done", "第一轮完成，已生成 3 个新关键词")
    return task


# ---------------------------------------------------------------- 第二轮

ROUND2_SYSTEM = """你是微信公众号选题编辑，负责把「验证过的趋势 + 核实过的新闻」变成可以直接写的标题。

输入包含：
- 第二轮三个关键词，以及用户手绘的 7 天 / 30 天近似趋势（人工估算，不是官方数据）。
- 第一轮结论摘要。
- 每个关键词的近期新闻检索结果（标题、链接、摘要、部分正文节选）。
- 该账号**已经推荐过的事件**列表（这些事件不得再次使用）。

硬规则：
1. 只能基于检索结果里能核实的事件写标题，不得编造事件、数字、地点或当事人。
2. 优先最近 7 天；不足再放宽到 30 天；绝不使用 30 天以外的事件凑数。
3. 已推荐事件列表中出现过的事件一律不得再用（按事件主体+地点+时间判断，不只看标题文字）。
4. 同一次生成中，同一个事件允许从明显不同的角度写多个标题。
5. 趋势更强、最近 1～2 天上涨更快的方向可以多给标题；宁缺毋滥，允许只给 0～2 个。
6. 标题真实、可点击、有悬念但不误导；不要出现「震惊」「必看」这类空话。
7. 事件若只有单一来源且无法核实核心事实，不要收录。

只输出 JSON：
{
  "titles": [
    {
      "title": "公众号标题（不超过 40 字）",
      "keyword": "对应关键词",
      "event_id": "稳定的事件标识，如 地点+主体+日期",
      "event_summary": "事件核心事实，40 字以内",
      "angle": "从什么角度写，20 字以内",
      "sources": ["来源链接1", "来源链接2"]
    }
  ],
  "skipped": [{"keyword": "关键词", "reason": "为什么没有可用选题（20 字内）"}]
}"""


def collect_news(keywords: list[str], *, progress=None, per_keyword: int = 6) -> dict:
    """第二轮：按关键词搜新闻，并抓取正文用于核实。"""
    out: dict[str, list[dict]] = {}
    for keyword in keywords:
        if progress:
            progress("search", f"联网检索「{keyword}」相关新闻")
        try:
            rows = sp.search(f"{keyword} 新闻", per_keyword)
        except Exception:
            rows = []
        enriched = []
        for row in rows[:per_keyword]:
            text = sp.fetch_text(row.get("url") or "", limit=2400)
            if text:
                row["content"] = text
            enriched.append(row)
        out[keyword] = enriched
    return out


def _model_event_id(row: dict, index: int) -> str:
    raw = str(row.get("event_id") or "").strip()
    if raw:
        return raw[:120]
    summary = str(row.get("event_summary") or "").strip()
    return f"auto-{index}-{abs(hash(summary)) % 10**8}" if summary else f"auto-{index}"


def validate_round2(data: dict, keywords: list[str], existing: list[dict]) -> dict:
    raw_titles = data.get("titles")
    if not isinstance(raw_titles, list):
        raise ValueError("模型未按要求返回 titles 数组")

    accepted: list[dict] = []
    dropped: list[dict] = []

    for index, row in enumerate(raw_titles):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        summary = str(row.get("event_summary") or "").strip()
        sources = []
        for item in (row.get("sources") or [])[:5]:
            if isinstance(item, dict) and item.get("url"):
                sources.append({"title": str(item.get("title") or "")[:160],
                                "url": str(item["url"])[:600]})
            elif isinstance(item, str) and item.startswith("http"):
                sources.append({"title": "", "url": item[:600]})
        if not title or not summary or not sources:
            dropped.append({"title": title, "reason": "缺少标题、事件要点或来源链接"})
            continue
        if len(title) > 60:
            title = title[:60]

        event_id = _model_event_id(row, index)
        duplicate = wechat_index_excel.is_duplicate_event(event_id, summary, existing)
        if duplicate:
            dropped.append({"title": title, "reason": f"事件已推荐过：{str(duplicate.get('title') or '')[:30]}"})
            continue

        keyword = str(row.get("keyword") or "").strip()
        if keyword not in keywords:
            keyword = keywords[0] if keywords else keyword

        accepted.append({
            "title": title,
            "keyword": keyword,
            "event_id": event_id,
            "event_summary": summary[:600],
            "angle": str(row.get("angle") or "")[:200],
            "sources": sources,
        })
        if len(accepted) == 3:
            break

    skipped = []
    for row in (data.get("skipped") or [])[:6]:
        if isinstance(row, dict):
            skipped.append({"keyword": str(row.get("keyword") or "")[:80],
                            "reason": str(row.get("reason") or "")[:300]})

    return {"titles": accepted, "dropped": dropped, "skipped": skipped,
            "generated_at": now_text(), "model": {"name": MODEL, "reasoning_effort": EFFORT}}


def run_round2(task: dict, *, progress=None, history_hint: str = "") -> dict:
    """执行第二轮：联网核实 → 事件去重 → 最多 3 个标题。返回更新后的任务。"""
    def step(name: str, detail: str = "") -> None:
        if progress:
            progress(name, detail)

    account = task.get("account") or ""
    round2 = task.get("round2") or {}
    keywords = round2.get("keywords") or []
    if not keywords:
        raise RuntimeError("第二轮还没有关键词，请先完成第一轮")

    now_ms = int(datetime.now().timestamp() * 1000)
    step("prepare", "整理第二轮曲线与第一轮结论")
    curve_block = build_keyword_block(task, "round2", now_ms)
    if not any(block.get("近7天", {}).get("points") for block in curve_block):
        raise RuntimeError("第二轮没有可用的曲线数据，请先完成手绘")

    step("search", "联网检索并抓取原文")
    news = collect_news(keywords, progress=progress)

    step("dedupe", "读取已推荐事件，准备去重")
    sandbox = bool(task.get("sandbox"))
    existing = wechat_index_excel.load_events(account, limit=200, sandbox=sandbox)
    existing_brief = [
        {"event_id": row.get("event_id"), "event_summary": row.get("event_summary"),
         "title": row.get("title"), "period": row.get("period")}
        for row in existing[-60:]
    ]

    payload = {
        "account": account,
        "period": task.get("period"),
        "notice": "热度数值为用户手绘估算，不是微信指数官方数据。",
        "keywords": curve_block,
        "round1_assessment": ((task.get("round1") or {}).get("analysis") or {}).get("assessment", ""),
        "news": {k: _trim_rows(v, limit=3, size=320) for k, v in news.items()},
        "already_recommended_events": existing_brief,
        "account_history_hint": history_hint,
    }

    step("model", "调用 DeepSeek 生成选题标题")
    data, usage = call_json(ROUND2_SYSTEM, payload, max_tokens=8000)
    step("validate", "校验标题与事件去重")
    result = validate_round2(data, keywords, existing)
    result["usage"] = usage
    result["searched"] = {k: len(v) for k, v in news.items()}

    period_label = f"{task.get('period', {}).get('start', '')} ~ {task.get('period', {}).get('end', '')}"
    appended = wechat_index_excel.append_records(
        account, result["titles"], period=period_label, round_label="第二轮", sandbox=sandbox)

    task["round2"]["submitted"] = True
    task["round2"]["analysis"] = {
        "assessment": "第二轮已完成联网核查与事件去重。",
        "skipped": result["skipped"],
        "dropped": result["dropped"],
        "generated_at": result["generated_at"],
        "usage": usage,
        "searched": result["searched"],
    }

    final = task.setdefault("final", {"titles": [], "records": [], "generated_at": None, "history": []})
    history = final.setdefault("history", [])
    history.append({
        "generated_at": result["generated_at"],
        "titles": result["titles"],
        "usage": usage,
        "dropped": result["dropped"],
    })
    final["titles"] = result["titles"]
    final["dropped"] = result["dropped"]
    final["generated_at"] = result["generated_at"]
    final["usage"] = usage
    final["excel_appended"] = appended

    step("done", f"第二轮完成，生成 {len(result['titles'])} 个标题")
    return task
