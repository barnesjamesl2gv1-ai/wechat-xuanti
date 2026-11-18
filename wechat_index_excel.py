"""微信指数模块 · Excel 选题去重库

设计文件 §5 的要求：
  - 每个自有账号独立一份 Excel；
  - 从功能启用当天开始记录，不导入历史文章；
  - 每次最终生成的合格标题全部写入（不管用户用不用）；
  - 按「事件」去重，不是只比标题文字；
  - 同一次生成允许同一事件多个角度，后续生成视为整体已推荐。

字段：生成时间 / 周期 / 轮次 / 关键词 / 标题 / 事件标识 / 事件要点 / 来源链接
"""
from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "公众号分析输出"
DEDUP_DIR = OUTPUT / "选题去重库"
SANDBOX = "_AI联调沙盒"   # 联调沙盒的记录单独一份，绝不挡正式选题

HEADERS = ["生成时间", "周期", "轮次", "关键词", "标题", "事件标识", "事件要点", "来源链接"]
COLUMN_WIDTHS = [19, 24, 8, 14, 42, 28, 46, 52]
SHEET_TITLE = "已推荐事件"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def folder_name(account: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", str(account)).strip(" .")
    return name[:80] or "未知公众号"


def book_path(account: str, sandbox: bool = False) -> Path:
    folder = DEDUP_DIR / SANDBOX if sandbox else DEDUP_DIR
    return folder / f"{folder_name(account)}.xlsx"


def _ensure_book(path: Path) -> Workbook:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            book = load_workbook(path)
        except Exception:
            book = Workbook()
        sheet = book.active
        if sheet.max_row == 1 and sheet.cell(row=1, column=1).value in (None, ""):
            _write_header(sheet)
        return book

    book = Workbook()
    sheet = book.active
    sheet.title = SHEET_TITLE
    _write_header(sheet)
    book.save(path)
    return book


def _write_header(sheet) -> None:
    sheet.title = SHEET_TITLE
    for index, header in enumerate(HEADERS, start=1):
        cell = sheet.cell(row=1, column=index, value=header)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="F0EDE6")
        sheet.column_dimensions[get_column_letter(index)].width = COLUMN_WIDTHS[index - 1]
    sheet.freeze_panes = "A2"


def load_events(account: str, limit: int = 200, sandbox: bool = False) -> list[dict]:
    """读取该账号已推荐过的事件（最近的 N 条，按写入顺序）。"""
    path = book_path(account, sandbox)
    if not path.is_file():
        return []
    try:
        book = load_workbook(path, read_only=True)
    except Exception:
        return []
    sheet = book.active
    rows = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if not row or not any(row):
            continue
        rows.append({
            "generated_at": row[0] or "",
            "period": row[1] or "",
            "round": row[2] or "",
            "keyword": row[3] or "",
            "title": row[4] or "",
            "event_id": row[5] or "",
            "event_summary": row[6] or "",
            "sources": row[7] or "",
        })
    book.close()
    return rows[-limit:]


def append_records(account: str, records: list[dict], *, period: str, round_label: str,
                   sandbox: bool = False) -> int:
    """把本次生成的全部标题写入去重库，返回写入条数。"""
    if not records:
        return 0
    path = book_path(account, sandbox)
    book = _ensure_book(path)
    sheet = book.active
    stamp = now_text()
    added = 0
    for record in records:
        sources = record.get("sources") or []
        if isinstance(sources, list):
            source_text = " | ".join(str((s or {}).get("url") if isinstance(s, dict) else s) for s in sources)[:900]
        else:
            source_text = str(sources)[:900]
        sheet.append([
            stamp,
            period,
            round_label,
            str(record.get("keyword") or "")[:120],
            str(record.get("title") or "")[:200],
            str(record.get("event_id") or "")[:120],
            str(record.get("event_summary") or "")[:600],
            source_text,
        ])
        for column in range(1, len(HEADERS) + 1):
            sheet.cell(row=sheet.max_row, column=column).alignment = Alignment(
                vertical="top", wrap_text=True)
        added += 1
    book.save(path)
    return added


# ---------------------------------------------------------------- 事件相似度

_PUNCT = re.compile(r"[\s，。、；：！？,.;:!?\"'“”‘’（）()《》<>【】\[\]—\-…·]+")

# 判定「同一条事件」的口径（两路任一命中即判重）：
#   1) 整句字符序列相似度 —— 换说法、加减地点前缀都还能保住连续片段；
#   2) 剥离通报类套话后再比 —— 「启动/发布/通报/典型案例」这类词会把
#      「重庆启动植树造林」和「重庆启动保护月」拉得很像，先剥掉再比才分得开。
# 实测基准（库里 3 条真实事件；12 条同事件换说法 + 12 条同领域不同事件）：
#   旧口径 bigram-Jaccard 0.72 → 只召回 3/12（近乎逐字重复才命中），去重实际失效；
#   现口径 → 召回 10/12，12 条负样本零误伤（最高 seq 0.439 / core 0.343）。
# 取舍：宁可少给一个选题，也不要重复推荐同一事件，所以偏向召回。
SEQ_DUP_THRESHOLD = 0.50
CORE_DUP_THRESHOLD = 0.45

# 不携带事件身份的通报类套话，仅用于 core 比对
BOILERPLATE = ("正式", "启动", "发布", "通报", "举行", "召开", "公布", "迎来", "出现",
               "开展", "相关", "典型", "案例", "活动", "表示", "目前", "已经", "即将")


def normalize(text: str) -> str:
    return _PUNCT.sub("", str(text or "")).lower()


def core_terms(text: str) -> str:
    clean = normalize(text)
    for word in BOILERPLATE:
        clean = clean.replace(word, "")
    return clean


def sequence_ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def core_ratio(left: str, right: str) -> float:
    a, b = core_terms(left), core_terms(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def is_duplicate_event(event_id: str, event_summary: str, existing: list[dict],
                       *, seq_threshold: float = SEQ_DUP_THRESHOLD,
                       core_threshold: float = CORE_DUP_THRESHOLD) -> dict | None:
    """在已有记录里找同一个事件：先比标识，再比要点相似度。"""
    clean_id = normalize(event_id)
    for row in existing:
        if clean_id and normalize(row.get("event_id") or "") == clean_id:
            return row
    for row in existing:
        other = row.get("event_summary") or ""
        if (sequence_ratio(event_summary, other) >= seq_threshold
                or core_ratio(event_summary, other) >= core_threshold):
            return row
    return None
