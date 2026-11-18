"""公众号爆款选题系统 · 本地 Web 薄封装（第4阶段）

只做三件事：调度已有脚本、读取已有文件、格式化展示。
不重写抓取、统计、AI 判断逻辑；不新建第二套业务状态。
"""
from __future__ import annotations

import hmac
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request

from common import folder_name, now_text
import wechat_index_store as index_store
import wechat_index_ai as index_ai

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "公众号分析输出"
REPORT_ROOT = OUTPUT / "报告"
AI_ROOT = OUTPUT / "AI选题报告"
PAIRS_FILE = ROOT / "ai_pairs.json"
ENV_FILE = ROOT / ".env"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
SANDBOX = "_AI联调沙盒"

HOST = (os.environ.get("XUANTI_HOST") or "127.0.0.1").strip()
DEFAULT_PORT = 8765
MAX_PAIRS = 3
# 远程/容器部署时设一个口令，避免任何人打开页面就能花你的 API 额度。
# 不设（默认）＝ 本机直接用，不打扰。
ACCESS_TOKEN = (os.environ.get("XUANTI_ACCESS_TOKEN") or "").strip()


def probe_host() -> str:
    """监听 0.0.0.0 / :: 时，本机探活要走回环地址。"""
    return "127.0.0.1" if HOST in {"", "0.0.0.0", "::", "[::]"} else HOST


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        return probe.connect_ex((probe_host(), port)) == 0


def pick_port() -> int:
    """8765 被占用时向后找一个空闲端口，仍然只监听本机。"""
    requested = os.environ.get("XUANTI_PORT")
    if requested:
        return int(requested)
    for candidate in range(DEFAULT_PORT, DEFAULT_PORT + 10):
        if not port_in_use(candidate):
            return candidate
    raise SystemExit("8765 起的 10 个端口都被占用，请关闭占用程序后重试。")


PORT = int(os.environ.get("XUANTI_PORT") or DEFAULT_PORT)

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

TASK_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()
LAST: dict = {"action": None, "ok": None, "at": None, "duration": 0.0,
              "lines": [], "summary": None, "preview": {}}


# ---------------------------------------------------------------- 访问口令（可选）

COOKIE_NAME = "xuanti_token"


def token_ok(value: str) -> bool:
    return bool(ACCESS_TOKEN) and hmac.compare_digest((value or "").strip(), ACCESS_TOKEN)


@app.before_request
def require_access_token():
    """设了 XUANTI_ACCESS_TOKEN 才启用；本机默认不设，不影响日常使用。"""
    if not ACCESS_TOKEN:
        return None
    if request.path == "/auth" or request.path.startswith("/static/") \
            or request.path == "/favicon.ico":
        return None
    if token_ok(request.cookies.get(COOKIE_NAME, "")):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"ok": False,
                        "error": "需要访问口令：先在浏览器打开 /auth?token=口令 一次"}), 401
    return ("需要访问口令", 401,
            {"Content-Type": "text/plain; charset=utf-8"})


@app.get("/auth")
def auth():
    """用 ?token=口令 换一个 cookie，之后正常访问即可。"""
    if not ACCESS_TOKEN:
        return redirect("/")
    if not token_ok(request.args.get("token", "")):
        return ("口令不正确。地址形如 /auth?token=你的口令", 401,
                {"Content-Type": "text/plain; charset=utf-8"})
    resp = redirect("/")
    resp.set_cookie(COOKIE_NAME, ACCESS_TOKEN, httponly=True, samesite="Lax",
                    max_age=30 * 24 * 3600)
    return resp


# ---------------------------------------------------------------- 基础工具



def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def has_api_key() -> bool:
    # 容器/CI 场景用 -e 传密钥，不必挂载 .env
    if (os.environ.get("DEEPSEEK_API_KEY") or "").strip():
        return True
    if not ENV_FILE.is_file():
        return False
    try:
        for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip().upper() in {"DEEPSEEK_API_KEY", "DEEPSEEK_KEY"} and value.strip().strip('"\''):
                return True
    except Exception:
        return False
    return False


def is_sandbox(path: Path) -> bool:
    return SANDBOX in path.parts


# ---------------------------------------------------------------- 账号与周期

def load_pairs() -> list[dict]:
    data = read_json(PAIRS_FILE, {}) or {}
    pairs = []
    for item in data.get("pairs", []) or []:
        own = str(item.get("own_account") or "").strip()
        bench = str(item.get("benchmark_account") or "").strip()
        if not own:
            continue
        pairs.append({
            "own_account": own,
            "benchmark_account": bench,
            "account_type": "new" if str(item.get("account_type")) == "new" else "old",
            "enabled": bool(item.get("enabled", True)),
        })
    return pairs


def latest_period(own: str) -> dict | None:
    """读取该账号最近一个已完成的三天周期（以 summary.json 为存在依据）。"""
    base = REPORT_ROOT / "三天周期" / folder_name(own)
    if not base.is_dir():
        return None
    best = None
    for item in base.iterdir():
        if not item.is_dir():
            continue
        summary = read_json(item / "summary.json")
        if not isinstance(summary, dict):
            continue
        end = str(summary.get("period_end") or "")
        if not end:
            continue
        if best is None or end > best["period_end"]:
            best = {
                "dir": item.name,
                "period_start": str(summary.get("period_start") or ""),
                "period_end": end,
                "article_count": summary.get("article_count"),
                "boom_count": summary.get("boom_count"),
                "unknown_read_count": summary.get("unknown_read_count"),
                "path": item,
            }
    return best


def ai_result_for(own: str, period_dir: str) -> Path | None:
    path = AI_ROOT / folder_name(own) / period_dir / "分析结果.json"
    return path if path.is_file() else None


def latest_ai_result() -> dict | None:
    if not AI_ROOT.is_dir():
        return None
    best = None
    for path in AI_ROOT.rglob("分析结果.json"):
        if is_sandbox(path):
            continue
        data = read_json(path)
        if not isinstance(data, dict):
            continue
        created = str(data.get("created_at") or "")
        if best is None or created > best["created_at"]:
            best = {
                # 注意：这里不能塞 Path 对象，build_status() 的结果要直接 jsonify
                "id": rel_id(path),
                "created_at": created,
                "own_account": str((data.get("pair") or {}).get("own_account") or ""),
                "benchmark_account": str((data.get("pair") or {}).get("benchmark_account") or ""),
                "period": data.get("period") or {},
                "data": data,
            }
    return best


def rel_id(path: Path) -> str:
    try:
        return path.resolve().relative_to(OUTPUT.resolve()).as_posix()
    except Exception:
        return ""


# ---------------------------------------------------------------- 报告索引

def list_reports(include_sandbox: bool = False) -> dict:
    groups = {"历史总报告": [], "三天周期": [], "炒冷饭": [], "AI报告": []}

    hist = REPORT_ROOT / "历史总报告"
    if hist.is_dir():
        for path in sorted(hist.glob("*.md"), reverse=True):
            groups["历史总报告"].append({
                "id": rel_id(path), "title": path.stem, "kind": "md",
                "account": "", "period": "",
            })

    period_root = REPORT_ROOT / "三天周期"
    if period_root.is_dir():
        for account_dir in sorted(p for p in period_root.iterdir() if p.is_dir()):
            for item in sorted((p for p in account_dir.iterdir() if p.is_dir()), reverse=True):
                report = item / "周期报告.md"
                summary = read_json(item / "summary.json", {}) or {}
                if not report.is_file():
                    continue
                start = str(summary.get("period_start") or "")[:10]
                end = str(summary.get("period_end") or "")[:10]
                groups["三天周期"].append({
                    "id": rel_id(report),
                    "title": f"{start} ~ {end}" if start and end else item.name,
                    "kind": "md",
                    "account": account_dir.name,
                    "period": "",
                })

    for path in sorted(REPORT_ROOT.rglob("炒冷饭报告_*.md"), reverse=True):
        if is_sandbox(path) and not include_sandbox:
            continue
        groups["炒冷饭"].append({
            "id": rel_id(path), "title": path.stem, "kind": "md",
            "account": "", "period": "",
        })

    if AI_ROOT.is_dir():
        for path in sorted(AI_ROOT.rglob("分析报告.md"), reverse=True):
            if is_sandbox(path) and not include_sandbox:
                continue
            data = read_json(path.parent / "分析结果.json", {}) or {}
            period = data.get("period") or {}
            start = str(period.get("start") or "")[:10]
            end = str(period.get("end") or "")[:10]
            groups["AI报告"].append({
                "id": rel_id(path),
                "title": f"选题报告 {start} ~ {end}" if start and end else path.parent.name,
                "kind": "md",
                "account": str((data.get("pair") or {}).get("own_account") or path.parent.parent.name),
                "period": "",
            })
    return groups


ALLOWED_ROOTS = [REPORT_ROOT, AI_ROOT]


def safe_report_path(report_id: str) -> Path:
    if not report_id or ".." in report_id:
        abort(400, "报告标识无效")
    candidate = (OUTPUT / report_id).resolve()
    if candidate.suffix.lower() not in {".md", ".json"}:
        abort(400, "只允许查看 Markdown 或 JSON 报告")
    for root in ALLOWED_ROOTS:
        try:
            candidate.relative_to(root.resolve())
            break
        except ValueError:
            continue
    else:
        abort(403, "该路径不在报告白名单目录内")
    if not candidate.is_file():
        abort(404, "报告文件不存在")
    return candidate


# ---------------------------------------------------------------- 脚本调度

def python_exe() -> str:
    return str(VENV_PY) if VENV_PY.is_file() else sys.executable


def run_script(args: list[str], timeout: int = 1800) -> dict:
    """固定参数列表调用现有脚本，不拼接用户输入。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    started = time.time()
    try:
        proc = subprocess.run(
            [python_exe(), *args],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
        )
        code, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "stdout": "",
                "stderr": "执行超时，已停止等待。", "duration": round(time.time() - started, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": -1, "stdout": "",
                "stderr": f"无法启动脚本：{exc}", "duration": round(time.time() - started, 1)}
    return {"ok": code == 0, "code": code, "stdout": out, "stderr": err,
            "duration": round(time.time() - started, 1)}


def parse_preview(stdout: str) -> tuple[list[dict], dict]:
    """解析 ai_topic_analyzer.py --dry-run 的输出，只读取，不重算规则。"""
    rows: list[dict] = []
    summary = {"done": 0, "skip": 0, "bad": 0}
    for raw in stdout.splitlines():
        line = raw.strip()
        match = re.match(r"^\[(可分析|跳过|成功|未执行|异常)\]\s*(\S+)\s*(.*)$", line)
        if match:
            rows.append({"status": match.group(1), "account": match.group(2),
                         "detail": match.group(3).strip(), "period": ""})
            continue
        if line.startswith("周期：") and rows:
            rows[-1]["period"] = line.split("：", 1)[-1].strip()
            continue
        tail = re.match(r"^本轮：完成\s*(\d+)，跳过/预览\s*(\d+)，未执行/异常\s*(\d+)", line)
        if tail:
            summary = {"done": int(tail.group(1)), "skip": int(tail.group(2)), "bad": int(tail.group(3))}
    return rows, summary


UPDATE_LABELS = {
    "mode": "模式",
    "articles": "文章",
    "accounts": "账号",
    "new": "新增",
    "updated": "更新",
    "period_reports": "周期报告",
}


def parse_update_summary(stdout: str) -> list[str]:
    """从 wechat_analyzer.py 的输出里解析统计字段，转成中文摘要。"""
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key in UPDATE_LABELS:
            values[key] = value.strip()
    labels: list[str] = []
    if values.get("mode"):
        labels.append(values["mode"])
    for key in ("articles", "accounts", "new", "updated", "period_reports"):
        if key in values:
            labels.append(f"{UPDATE_LABELS[key]} {values[key]}")
    return labels


def begin_task(action: str) -> bool:
    if not TASK_LOCK.acquire(blocking=False):
        return False
    with STATE_LOCK:
        LAST.update({"action": action, "ok": None, "at": now_text(),
                     "duration": 0.0, "lines": [], "summary": None})
    return True


def end_task(result: dict, *, preview_rows=None) -> None:
    with STATE_LOCK:
        LAST.update({
            "ok": result.get("ok"),
            "duration": result.get("duration", 0.0),
            "lines": tail_lines(result),
            "summary": result.get("summary"),
            "stats": result.get("stats") or [],
        })
        if preview_rows is not None:
            LAST["preview"] = {row["account"]: row for row in preview_rows}
    TASK_LOCK.release()


def tail_lines(result: dict, limit: int = 40) -> list[str]:
    text = (result.get("stdout") or "").strip()
    if not text:
        text = (result.get("stderr") or "").strip()
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-limit:] if len(lines) > limit else lines


# ---------------------------------------------------------------- 状态组装

STATE_LABELS = {
    "未到期": ("未到期", "该账号还没有完成的三天周期"),
    "已分析": ("已分析", "本周期已存在分析结果，不会再次扣费"),
    "待检查": ("待检查", "点击“预览 AI 分析”确认是否可以分析"),
    "已停用": ("已停用", "该组合当前未启用"),
}


def build_status() -> dict:
    with STATE_LOCK:
        preview = dict(LAST.get("preview") or {})
        last = dict(LAST)
    pairs = []
    for pair in load_pairs():
        own = pair["own_account"]
        period = latest_period(own)
        entry = {**pair, "state": "待检查", "state_label": "待检查",
                 "state_hint": STATE_LABELS["待检查"][1], "period": None,
                 "own_articles": None, "benchmark_viral": None, "ai": None}
        if period:
            entry["period"] = {k: period[k] for k in
                               ("period_start", "period_end", "article_count", "boom_count")}
            entry["own_articles"] = period.get("article_count")
            result_path = ai_result_for(own, period["dir"])
            if result_path:
                data = read_json(result_path, {}) or {}
                entry["ai"] = {
                    "id": rel_id(result_path),
                    "created_at": data.get("created_at"),
                    "counts": data.get("data_counts"),
                    "usage": data.get("usage"),
                }
                entry["benchmark_viral"] = (data.get("data_counts") or {}).get("benchmark_viral")
        if not pair["enabled"]:
            entry["state"] = "已停用"
        elif period and entry["ai"]:
            entry["state"] = "已分析"
        elif not period:
            entry["state"] = "未到期"
        row = preview.get(own)
        if row and pair["enabled"]:
            status = row["status"]
            if status == "可分析":
                mapped = "可分析"
            elif status == "成功":
                mapped = "已分析"
            elif status == "异常":
                mapped = "异常"
            elif status == "未执行":
                mapped = "未配置 Key"
            elif status == "跳过":
                reason = row.get("detail") or ""
                if "完成的三天周期" in reason:
                    mapped = "未到期"
                elif "已分析" in reason:
                    mapped = "已分析"
                else:
                    mapped = "无材料/已跳过"
            else:
                mapped = None
            if mapped:
                entry["state"] = mapped
                entry["state_label"] = mapped
                entry["state_hint"] = row["detail"] or STATE_LABELS.get(mapped, ("", ""))[1]
                if row.get("period"):
                    entry["preview_period"] = row["period"]
        if entry["state"] in STATE_LABELS:
            entry["state_label"], entry["state_hint"] = STATE_LABELS[entry["state"]]
        pairs.append(entry)

    return {
        "ok": True,
        "pairs": pairs,
        "env": {
            "has_key": has_api_key(),
            "python": python_exe(),
            "port": PORT,
            "root": str(ROOT),
            "venv_ready": VENV_PY.is_file(),
        },
        "last": {
            "action": last.get("action"), "ok": last.get("ok"),
            "at": last.get("at"), "duration": last.get("duration"),
            "lines": last.get("lines") or [], "summary": last.get("summary"),
        },
        "busy": TASK_LOCK.locked(),
        "latest_ai": latest_ai_result(),
    }


# ---------------------------------------------------------------- 路由

@app.get("/")
def index():
    return render_template("index.html", port=PORT)


# 浏览器默认会请求 /favicon.ico，没有就给一个内联 SVG，省掉控制台那条 404
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="14" fill="#2b4f9e"/>'
    '<path d="M14 44 L26 30 L36 38 L50 18" fill="none" stroke="#f6f2e9" '
    'stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
    '<circle cx="50" cy="18" r="4" fill="#e0b25a"/>'
    '</svg>'
)


@app.get("/favicon.ico")
def favicon():
    return app.response_class(FAVICON_SVG, mimetype="image/svg+xml")


@app.get("/api/status")
def api_status():
    return jsonify(build_status())


@app.get("/api/pairs")
def api_pairs_get():
    return jsonify({"ok": True, "pairs": load_pairs()})


@app.post("/api/pairs")
def api_pairs_post():
    payload = request.get_json(silent=True) or {}
    raw = payload.get("pairs")
    if not isinstance(raw, list):
        return jsonify({"ok": False, "error": "缺少 pairs 数组"}), 400

    cleaned, seen_enabled, errors = [], set(), []
    for index, item in enumerate(raw[:MAX_PAIRS], start=1):
        if not isinstance(item, dict):
            continue
        own = str(item.get("own_account") or "").strip()
        bench = str(item.get("benchmark_account") or "").strip()
        kind = "new" if str(item.get("account_type")) == "new" else "old"
        enabled = bool(item.get("enabled", True))
        if not own and not bench:
            continue
        if not own or not bench:
            errors.append(f"第 {index} 组：自有账号和对标账号都必须填写")
            continue
        if own == bench:
            errors.append(f"第 {index} 组：自有账号和对标账号不能相同")
            continue
        key = (own, bench)
        if enabled and key in seen_enabled:
            errors.append(f"第 {index} 组：启用了重复的账号组合")
            continue
        if enabled:
            seen_enabled.add(key)
        cleaned.append({"own_account": own, "benchmark_account": bench,
                        "account_type": kind, "enabled": enabled})

    if errors:
        return jsonify({"ok": False, "error": "；".join(errors)}), 400
    if len(cleaned) > MAX_PAIRS:
        return jsonify({"ok": False, "error": f"最多配置 {MAX_PAIRS} 组"}), 400
    if sum(1 for item in cleaned if item["enabled"]) > MAX_PAIRS:
        return jsonify({"ok": False, "error": f"最多启用 {MAX_PAIRS} 组"}), 400

    backup = PAIRS_FILE.with_suffix(".json.bak") if PAIRS_FILE.exists() else None
    if backup is not None:
        try:
            backup.write_text(PAIRS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    PAIRS_FILE.write_text(json.dumps({"pairs": cleaned}, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    return jsonify({"ok": True, "pairs": cleaned, "note": "已保存；历史报告与既有输出不受影响。"})


@app.post("/api/update-data")
def api_update_data():
    if not begin_task("update-data"):
        return jsonify({"ok": False, "error": "已有任务正在执行，请等待完成"}), 409
    result = run_script(["wechat_analyzer.py"], timeout=900)
    result["stats"] = parse_update_summary(result.get("stdout") or "")
    end_task(result)
    return jsonify({"ok": result["ok"], "code": result["code"],
                    "duration": result["duration"], "stats": result["stats"],
                    "lines": tail_lines(result),
                    "error": "" if result["ok"] else (result.get("stderr") or "执行失败").strip()[-400:]})


@app.post("/api/ai-preview")
def api_ai_preview():
    if not begin_task("ai-preview"):
        return jsonify({"ok": False, "error": "已有任务正在执行，请等待完成"}), 409
    result = run_script(["ai_topic_analyzer.py", "--dry-run"], timeout=300)
    rows, summary = parse_preview(result.get("stdout") or "")
    result["summary"] = summary
    end_task(result, preview_rows=rows)
    return jsonify({"ok": result["ok"], "code": result["code"],
                    "duration": result["duration"], "rows": rows,
                    "summary": summary, "lines": tail_lines(result),
                    "error": "" if result["ok"] else (result.get("stderr") or "执行失败").strip()[-400:]})


@app.post("/api/run-ai")
def api_run_ai():
    if not begin_task("run-ai"):
        return jsonify({"ok": False, "error": "已有任务正在执行，请等待完成"}), 409
    result = run_script(["ai_topic_analyzer.py"], timeout=1800)
    rows, summary = parse_preview(result.get("stdout") or "")
    result["summary"] = {"done": sum(1 for r in rows if r["status"] == "成功"),
                         "skip": sum(1 for r in rows if r["status"] == "跳过"),
                         "bad": sum(1 for r in rows if r["status"] in {"异常", "未执行"})}
    end_task(result, preview_rows=rows)
    return jsonify({"ok": result["ok"], "code": result["code"],
                    "duration": result["duration"], "rows": rows,
                    "summary": result["summary"], "lines": tail_lines(result),
                    "error": "" if result["ok"] else (result.get("stderr") or "执行失败").strip()[-400:]})


@app.get("/api/reports")
def api_reports():
    include = request.args.get("sandbox") == "1"
    return jsonify({"ok": True, "groups": list_reports(include_sandbox=include),
                    "sandbox": include})


# ---------------------------------------------------------------- 微信指数两轮验证

def ai_result_path(account: str, period_dir: str, sandbox: bool = False) -> Path:
    base = AI_ROOT / "_AI联调沙盒" / "AI选题报告" if sandbox else AI_ROOT
    return base / folder_name(account) / period_dir / "分析结果.json"


def seed_keywords(account: str, period_dir: str, sandbox: bool = False) -> tuple[list[str], str]:
    """从已有 AI 结果里取第一轮的 3 个基础关键词。"""
    data = read_json(ai_result_path(account, period_dir, sandbox), {}) or {}
    candidates = ((data.get("analysis") or {}).get("candidates")) or []
    keywords = [str(c.get("keyword") or "").strip() for c in candidates if isinstance(c, dict)]
    return [k for k in keywords if k][:3], str(data.get("created_at") or "")


@app.get("/api/index/tasks")
def api_index_tasks():
    account = (request.args.get("account") or "").strip()
    sandbox = request.args.get("sandbox") == "1"
    if not account:
        return jsonify({"ok": False, "error": "缺少账号"}), 400

    tasks = index_store.list_tasks(account, sandbox)
    have = {row["period_dir"] for row in tasks}

    # 有 AI 结果但还没开始验证的周期，也要出现在列表里
    base = AI_ROOT / "_AI联调沙盒" / "AI选题报告" if sandbox else AI_ROOT
    account_dir = base / folder_name(account)
    if account_dir.is_dir():
        for item in sorted((p for p in account_dir.iterdir() if p.is_dir()), reverse=True):
            if item.name in have:
                continue
            keywords, _ = seed_keywords(account, item.name, sandbox)
            if not keywords:
                continue
            data = read_json(item / "分析结果.json", {}) or {}
            tasks.append({
                "period_dir": item.name,
                "period": data.get("period") or {},
                "stage": "未开始",
                "updated_at": "",
                "title_count": 0,
            })

    return jsonify({"ok": True, "tasks": tasks})


@app.get("/api/index/task")
def api_index_task():
    account = (request.args.get("account") or "").strip()
    period_dir = (request.args.get("period") or "").strip()
    sandbox = request.args.get("sandbox") == "1"
    if not account or not period_dir:
        return jsonify({"ok": False, "error": "缺少账号或周期"}), 400

    task = index_store.load(account, period_dir, sandbox)
    if task is None:
        keywords, ai_created = seed_keywords(account, period_dir, sandbox)
        if not keywords:
            return jsonify({
                "ok": False,
                "error": "这一期还没有可用的基础 AI 关键词，请先完成该周期的 AI 选题分析",
            }), 404
        benchmark = ""
        for pair in load_pairs():
            if pair["own_account"] == account:
                benchmark = pair["benchmark_account"]
                break
        period = {"dir": period_dir, "start": "", "end": ""}
        ai_data = read_json(ai_result_path(account, period_dir, sandbox), {}) or {}
        period.update(ai_data.get("period") or {})
        task = index_store.new_task(account, benchmark, period, keywords, sandbox=sandbox)
        index_store.save(task)

    return jsonify({
        "ok": True,
        "task": index_store.public_view(task),
        "now_ms": int(time.time() * 1000),
        "server_time": now_text(),
    })


def _clean_series(raw: dict) -> dict:
    strokes = []
    for stroke in (raw.get("strokes") or [])[:200]:
        if not isinstance(stroke, list):
            continue
        points = []
        for point in stroke[:4000]:
            if isinstance(point, (list, tuple)) and len(point) == 2:
                try:
                    ts, value = int(point[0]), float(point[1])
                except (TypeError, ValueError):
                    continue
                if ts <= 0 or value < 0:
                    continue
                points.append([ts, round(min(value, 1e9), 2)])
        if points:
            strokes.append(points)
    flat = [p for stroke in strokes for p in stroke]
    return {
        "strokes": strokes,
        "points": flat,
        "done": bool(raw.get("done")) and len(flat) >= 2,
        "no_data": bool(raw.get("no_data")),
    }


@app.post("/api/index/draft")
def api_index_draft():
    payload = request.get_json(silent=True) or {}
    account = str(payload.get("account") or "").strip()
    period_dir = str(payload.get("period") or "").strip()
    round_key = str(payload.get("round") or "").strip()
    chart_name = str(payload.get("chart") or "").strip()

    if round_key not in index_store.ROUND_KEYS or chart_name not in index_store.CHARTS:
        return jsonify({"ok": False, "error": "轮次或图表名称无效"}), 400
    task = index_store.load(account, period_dir)
    if task is None:
        return jsonify({"ok": False, "error": "任务不存在，请先打开该周期"}), 404

    raw_chart = payload.get("chart_data") or {}
    chart = task[round_key]["charts"][chart_name]
    y_max = raw_chart.get("y_max")
    try:
        chart["y_max"] = float(y_max) if y_max not in (None, "", 0) else None
    except (TypeError, ValueError):
        chart["y_max"] = None

    incoming = raw_chart.get("series") or []
    for index, series in enumerate(chart["series"]):
        if index >= len(incoming) or not isinstance(incoming[index], dict):
            continue
        series.update(_clean_series(incoming[index]))

    index_store.save(task)
    return jsonify({
        "ok": True,
        "task": index_store.public_view(task),
        "saved_at": now_text(),
    })


# ---------------------------------------------------------------- 后台任务（关闭标签页也继续）

INDEX_JOBS: dict[str, dict] = {}
INDEX_JOB_LOCK = threading.Lock()


def index_job_key(account: str, period_dir: str, stage: str) -> str:
    return f"{account}|{period_dir}|{stage}"


def job_snapshot(key: str) -> dict:
    with INDEX_JOB_LOCK:
        return dict(INDEX_JOBS.get(key) or {})


def job_patch(key: str, **fields) -> None:
    with INDEX_JOB_LOCK:
        current = INDEX_JOBS.setdefault(key, {})
        current.update(fields)
        current["updated_at"] = now_text()


def index_job_active(key: str) -> bool:
    with INDEX_JOB_LOCK:
        return (INDEX_JOBS.get(key) or {}).get("status") in {"queued", "running"}


def start_index_job(key: str, runner) -> None:
    def _run() -> None:
        try:
            job_patch(key, status="running", started_at=now_text(), error="",
                      step="start", detail="准备中")
            runner(lambda step, detail="": job_patch(key, step=step, detail=detail))
            job_patch(key, status="done", finished_at=now_text(), step="done", detail="已完成")
        except Exception as exc:  # noqa: BLE001
            job_patch(key, status="error", error=str(exc)[:500], finished_at=now_text(),
                      step="error", detail="失败")

    threading.Thread(target=_run, daemon=True).start()


@app.get("/api/index/job")
def api_index_job():
    account = (request.args.get("account") or "").strip()
    period_dir = (request.args.get("period") or "").strip()
    stage = (request.args.get("stage") or "round1").strip()
    if not account or not period_dir:
        return jsonify({"ok": False, "error": "缺少账号或周期"}), 400
    snap = job_snapshot(index_job_key(account, period_dir, stage))
    return jsonify({"ok": True, "job": snap})


@app.post("/api/index/submit-round1")
def api_index_submit_round1():
    payload = request.get_json(silent=True) or {}
    account = str(payload.get("account") or "").strip()
    period_dir = str(payload.get("period") or "").strip()
    task = index_store.load(account, period_dir)
    if task is None:
        return jsonify({"ok": False, "error": "任务不存在，请先打开该周期"}), 404

    state = index_store.round_state(task.get("round1") or {})
    if not state["complete"]:
        return jsonify({
            "ok": False,
            "error": "第一轮还有曲线没完成：每条曲线都要点「完成当前曲线」或标记「无指数数据」",
        }), 400

    if not index_ai.read_api_key():
        return jsonify({"ok": False, "error": "未配置 DEEPSEEK_API_KEY，无法提交分析"}), 400

    key = index_job_key(account, period_dir, "round1")
    if index_job_active(key):
        return jsonify({"ok": False, "error": "这一轮正在分析中，请稍候"}), 409

    job_patch(key, status="queued", step="queued", detail="已排队，等待开始",
              error="", created_at=now_text(), result=None)

    def runner(progress) -> None:
        fresh = index_store.load(account, period_dir)
        if fresh is None:
            raise RuntimeError("任务在分析过程中丢失")
        updated = index_ai.run_round1(fresh, progress=progress)
        index_store.save(updated)
        job_patch(key, result=index_store.public_view(updated))

    start_index_job(key, runner)
    return jsonify({"ok": True, "job_key": key})


def _start_round2(account: str, period_dir: str, stage: str):
    task = index_store.load(account, period_dir)
    if task is None:
        return jsonify({"ok": False, "error": "任务不存在，请先打开该周期"}), 404

    state = index_store.round_state(task.get("round2") or {})
    if not state["complete"]:
        return jsonify({
            "ok": False,
            "error": "第二轮还有曲线没完成：每条曲线都要点「完成当前曲线」或标记「无指数数据」",
        }), 400

    if not index_ai.read_api_key():
        return jsonify({"ok": False, "error": "未配置 DEEPSEEK_API_KEY，无法提交分析"}), 400

    key = index_job_key(account, period_dir, stage)
    if index_job_active(key) or index_job_active(index_job_key(account, period_dir, "round2")):
        return jsonify({"ok": False, "error": "第二轮正在执行中，请稍候"}), 409

    job_patch(key, status="queued", step="queued", detail="已排队，等待开始",
              error="", created_at=now_text(), result=None)

    def runner(progress) -> None:
        fresh = index_store.load(account, period_dir)
        if fresh is None:
            raise RuntimeError("任务在分析过程中丢失")
        updated = index_ai.run_round2(fresh, progress=progress)
        index_store.save(updated)
        job_patch(key, result=index_store.public_view(updated))

    start_index_job(key, runner)
    return jsonify({"ok": True, "job_key": key})


@app.post("/api/index/submit-round2")
def api_index_submit_round2():
    payload = request.get_json(silent=True) or {}
    account = str(payload.get("account") or "").strip()
    period_dir = str(payload.get("period") or "").strip()
    return _start_round2(account, period_dir, "round2")


@app.post("/api/index/regenerate")
def api_index_regenerate():
    """复用已有 4 张图，重新联网搜索、去重并生成标题（不重做绘图）。"""
    payload = request.get_json(silent=True) or {}
    account = str(payload.get("account") or "").strip()
    period_dir = str(payload.get("period") or "").strip()
    return _start_round2(account, period_dir, "regen")


@app.get("/api/report/<path:report_id>")
def api_report(report_id: str):
    path = safe_report_path(report_id)
    text = path.read_text(encoding="utf-8", errors="replace")
    return jsonify({"ok": True, "id": report_id, "name": path.name,
                    "kind": "markdown" if path.suffix.lower() == ".md" else "json",
                    "content": text, "truncated": len(text) > 200_000})


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(500)
def handle_error(exc):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": getattr(exc, "description", "请求失败")}), getattr(exc, "code", 500)
    return getattr(exc, "description", "请求失败"), getattr(exc, "code", 500)


def main() -> int:
    global PORT
    if not (ROOT / "wechat_analyzer.py").is_file():
        print("未找到 wechat_analyzer.py，请确认启动位置。")
        return 2
    wanted = PORT
    PORT = pick_port()
    print("=" * 52)
    print("公众号爆款选题系统 · 本地 Web MVP")
    print(f"项目目录：{ROOT}")
    if PORT != wanted:
        print(f"注意：默认端口 {wanted} 已被其他程序占用，本次使用 {PORT}。")
    print(f"访问地址：http://{HOST if HOST not in ('0.0.0.0', '') else '127.0.0.1'}:{PORT}/")
    if ACCESS_TOKEN:
        print(f"已启用访问口令，首次请打开：http://127.0.0.1:{PORT}/auth?token=你的口令")
    print("关闭本窗口即停止服务。")
    print("=" * 52)
    if os.environ.get("XUANTI_OPEN_BROWSER") == "1":
        # 默认不自动打开浏览器：webbrowser.open 会抢焦点，已开着的同地址标签还会重载，
        # 打断用户当前正在看的内容。需要自动打开时显式设置 XUANTI_OPEN_BROWSER=1。
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}/")).start()
    try:
        app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)
    except OSError as exc:
        print(f"端口 {PORT} 可能已被占用：{exc}")
        print("请关闭占用该端口的程序，或设置环境变量 XUANTI_PORT 换一个端口。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
