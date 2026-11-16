"""联网搜索适配层（微信指数模块用）

为什么要单独一层：DeepSeek 的 API 只有对话补全，**不包含联网搜索**。
所以「自动联网找新闻 / 打开原文核查」必须由我们自己接一个搜索服务，
再把结果作为资料交给 DeepSeek 做判断，而不是让模型假装搜过。

支持两种服务，用 .env 里的 SEARCH_PROVIDER 选择：
  SEARCH_PROVIDER=bocha   → 博查 AI 搜索（国内，中文资讯覆盖好）
  SEARCH_PROVIDER=tavily  → Tavily（英文为主，返回正文摘要，海外服务）

对应密钥也放 .env，不要提交仓库、不要贴到聊天里：
  BOCHA_API_KEY=...
  TAVILY_API_KEY=...

用法：
  python search_provider.py --selftest            # 检查配置是否可用
  python search_provider.py "关键词" [条数]        # 命令行试搜索
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"
TAVILY_ENDPOINT = "https://api.tavily.com/search"

TIMEOUT = 30


# ---------------------------------------------------------------- 配置

def load_env(path: Path = ENV_FILE) -> dict:
    data: dict[str, str] = {}
    if not path.is_file():
        return data
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def resolve_config() -> dict:
    """返回 {provider, key, ready, hint}。只读配置，不发网络请求。"""
    env = load_env()
    provider = (env.get("SEARCH_PROVIDER") or "").strip().lower()
    bocha = env.get("BOCHA_API_KEY") or ""
    tavily = env.get("TAVILY_API_KEY") or ""

    if not provider:
        if bocha:
            provider = "bocha"
        elif tavily:
            provider = "tavily"

    key = {"bocha": bocha, "tavily": tavily}.get(provider, "")
    hints = {
        "bocha": "在 .env 里写 SEARCH_PROVIDER=bocha 和 BOCHA_API_KEY=你的key",
        "tavily": "在 .env 里写 SEARCH_PROVIDER=tavily 和 TAVILY_API_KEY=你的key",
    }
    return {
        "provider": provider,
        "key": key,
        "ready": bool(provider and key),
        "hint": hints.get(provider, "在 .env 里设置 SEARCH_PROVIDER（bocha 或 tavily）和对应密钥"),
    }


# ---------------------------------------------------------------- HTTP

def _post(url: str, body: dict, headers: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        raise RuntimeError(f"搜索服务返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("搜索服务网络不可达，请检查网络或代理") from exc
    except TimeoutError as exc:
        raise RuntimeError("搜索服务超时") from exc
    except OSError as exc:
        raise RuntimeError(f"搜索服务连接被中断：{exc}") from exc


# ---------------------------------------------------------------- 搜索实现

def _search_bocha(query: str, count: int) -> list[dict]:
    cfg = resolve_config()
    data = _post(
        BOCHA_ENDPOINT,
        {"query": query, "count": max(1, min(count, 20)), "summary": True, "freshness": "oneMonth"},
        {"Authorization": f"Bearer {cfg['key']}"},
    )
    pages = (((data.get("data") or {}).get("webPages") or {}).get("value")) or []
    results = []
    for item in pages:
        results.append({
            "title": (item.get("name") or "").strip(),
            "url": (item.get("url") or "").strip(),
            "snippet": (item.get("snippet") or "").strip(),
            "content": (item.get("summary") or item.get("snippet") or "").strip(),
            "published": (item.get("datePublished") or item.get("dateLastCrawled") or "").strip(),
            "site": (item.get("siteName") or "").strip(),
        })
    return [r for r in results if r["url"]]


def _search_tavily(query: str, count: int) -> list[dict]:
    cfg = resolve_config()
    data = _post(
        TAVILY_ENDPOINT,
        {
            "api_key": cfg["key"],
            "query": query,
            "search_depth": "basic",
            "max_results": max(1, min(count, 20)),
            "topic": "news",
            "days": 30,
            "include_answer": False,
        },
        {},
    )
    results = []
    for item in data.get("results") or []:
        results.append({
            "title": (item.get("title") or "").strip(),
            "url": (item.get("url") or "").strip(),
            "snippet": (item.get("content") or "")[:400].strip(),
            "content": (item.get("content") or "").strip(),
            "published": (item.get("published_date") or "").strip(),
            "site": urllib.parse.urlsplit(item.get("url") or "").netloc,
        })
    return [r for r in results if r["url"]]


def search(query: str, count: int = 8) -> list[dict]:
    """统一入口：返回 [{title,url,snippet,content,published,site}]。"""
    cfg = resolve_config()
    if not cfg["ready"]:
        raise RuntimeError(f"没有可用的搜索服务配置。{cfg['hint']}")
    if cfg["provider"] == "bocha":
        return _search_bocha(query, count)
    if cfg["provider"] == "tavily":
        return _search_tavily(query, count)
    raise RuntimeError(f"未知的 SEARCH_PROVIDER：{cfg['provider']}")


# ---------------------------------------------------------------- 打开原文

def fetch_text(url: str, limit: int = 6000) -> str:
    """抓取网页正文纯文本，用于核实事件事实。失败返回空串，由调用方判定不可核实。"""
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
        html = raw.decode(charset, errors="replace")
    except Exception:
        return ""

    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    text = re.sub(r"(?s)<[^>]+>", "\n", html)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&[a-zA-Z#0-9]{1,6};", " ", text)
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text[:limit]


# ---------------------------------------------------------------- 自检

def selftest() -> int:
    cfg = resolve_config()
    print(f"搜索服务：{cfg['provider'] or '(未配置)'}")
    print(f"密钥：{'已配置' if cfg['key'] else '未配置'}")
    print(f"状态：{'可用' if cfg['ready'] else '不可用'}")
    if not cfg["ready"]:
        print(f"提示：{cfg['hint']}")
        return 2
    try:
        rows = search("最近一周 动物 新闻", 3)
    except Exception as exc:  # noqa: BLE001
        print(f"试搜索失败：{exc}")
        return 1
    print(f"试搜索返回 {len(rows)} 条：")
    for row in rows:
        print(f"  - {row['title']}  ({row['site']})  {row['url']}")
        if row["published"]:
            print(f"    发布时间：{row['published']}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] == "--selftest":
        return selftest()
    query = args[0]
    count = int(args[1]) if len(args) > 1 and args[1].isdigit() else 8
    for row in search(query, count):
        print(f"{row['title']}\n  {row['url']}\n  {row['snippet'][:160]}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
