from __future__ import annotations
import argparse, hashlib, json, re, statistics, sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
except ImportError:
    print("缺少 openpyxl，请运行：py -m pip install -r requirements.txt", file=sys.stderr)
    raise SystemExit(2)

VERSION = "0.2.0"
PERIOD = timedelta(days=3)
BOOM = 5000
TRANSIENT = {"key","pass_ticket","appmsg_token","scene","subscene","clicktime","enterid","from","isappinstalled","nwr_flag"}
COLS = [
 ("article_key","文章唯一标识"),("account_name","公众号名称"),("article_title","文章标题"),
 ("published_article_time","发布时间"),("collect_time","采集时间"),
 ("read_count","阅读量"),("like_count","点赞量"),("comment_count","评论量"),
 ("share_count","转发量"),("recommend_count","推荐量"),("article_link","文章链接"),
 ("article_short_link","文章短链接"),("ip_location","IP属地"),("audience_count","受众数"),
 ("html_source","HTML来源"),("duration_seconds","采集耗时（秒）"),
 ("record_status","记录状态"),("source_path","本地归档文件"),
 ("first_seen_at","首次纳入分析时间"),("last_seen_at","最近读取时间")
]
METRICS = [("read_count","阅读"),("like_count","点赞"),("comment_count","评论"),("share_count","转发"),("recommend_count","推荐")]
NUMERIC = {"read_count","like_count","comment_count","share_count","recommend_count","audience_count","duration_seconds"}
FIELDS = [k for k,_ in COLS]

def parse_dt(v):
    if not v: return None
    if isinstance(v, datetime): return v.replace(tzinfo=None, microsecond=0)
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M","%Y-%m-%d","%Y/%m/%d %H:%M:%S","%Y/%m/%d %H:%M","%Y/%m/%d"):
        try: return datetime.strptime(s,fmt)
        except ValueError: pass
    try: return datetime.fromisoformat(s.replace("Z","+00:00")).replace(tzinfo=None,microsecond=0)
    except ValueError: return None

def dtstr(v): return v.strftime("%Y-%m-%d %H:%M:%S") if v else ""
def now_local(): return datetime.now().replace(microsecond=0)

def number(v, floating=False):
    if v is None or isinstance(v, bool): return None
    try:
        if floating:
            n = float(v)
        elif isinstance(v, int):
            n = v
        elif isinstance(v, float):
            if not v.is_integer(): return None
            n = int(v)
        else:
            raw = str(v).replace(",", "").strip()
            try:
                n = int(raw)
            except ValueError:
                as_float = float(raw)
                if not as_float.is_integer(): return None
                n = int(as_float)
        if n < 0: return None
        return n if floating else int(n)
    except (ValueError, TypeError):
        return None

def canonical_link(link):
    p=urlsplit(str(link).strip())
    if p.scheme.lower() not in {"http","https"} or not p.hostname: return str(link).strip()
    host=p.hostname.lower() + (f":{p.port}" if p.port else "")
    q=sorted((k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.lower() not in TRANSIENT)
    return urlunsplit((p.scheme.lower(),host,p.path or "/",urlencode(q),""))

def article_key(d):
    acct=str(d.get("account_name") or "").strip()
    link=str(d.get("article_link") or d.get("article_short_link") or "").strip()
    identity=canonical_link(link) if link else f"{d.get('published_article_time','')}|{d.get('article_title','')}"
    return hashlib.sha256(f"{acct}|{identity}".encode()).hexdigest()[:20]

def normalize(d, path, root, now):
    acct=str(d.get("account_name") or "").strip()
    title=str(d.get("article_title") or "").strip()
    if not acct or not title: raise ValueError("缺少 account_name 或 article_title")
    r={k:None for k in FIELDS}
    r.update(article_key=article_key(d),account_name=acct,article_title=title,
             source_path=path.relative_to(root).as_posix(),first_seen_at=dtstr(now),last_seen_at=dtstr(now))
    for k in ("published_article_time","collect_time","article_link","article_short_link","ip_location","html_source","record_status"):
        if k in d: r[k]=None if d[k] is None or str(d[k]).strip()=="" else str(d[k]).strip()
    for k in ("read_count","like_count","comment_count","share_count","recommend_count","audience_count"):
        if k in d: r[k]=number(d[k])
    if "duration_seconds" in d: r["duration_seconds"]=number(d["duration_seconds"],True)
    present=set(d)|{"article_key","source_path","first_seen_at","last_seen_at"}
    return r,present

def scan(root, now):
    found, errors = {}, []
    for path in sorted(root.rglob("article_detail.json")):
        try:
            d=json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(d,dict): raise ValueError("JSON 根节点不是对象")
            r,present=normalize(d,path,root,now)
            k=r["article_key"]
            if k in found:
                old=found[k]["record"]; old_dt=parse_dt(old.get("collect_time")); new_dt=parse_dt(r.get("collect_time"))
                if new_dt and (not old_dt or new_dt>=old_dt): found[k]={"record":r,"present":present}
                errors.append(f"重复文章记录：{path}")
            else: found[k]={"record":r,"present":present}
        except Exception as e: errors.append(f"{path}: {e}")
    return found,errors

def read_book(path):
    out={}
    if not path.exists(): return out
    wb=load_workbook(path,read_only=True,data_only=True)
    labels={label:key for key,label in COLS}
    try:
        for ws in wb.worksheets:
            rows=ws.iter_rows(values_only=True); header=next(rows,None)
            if not header: continue
            idx={str(v):i for i,v in enumerate(header) if v is not None}
            if "文章唯一标识" not in idx: continue
            for row in rows:
                i=idx["文章唯一标识"]
                if i>=len(row) or not row[i]: continue
                rec={k:None for k in FIELDS}
                for label,j in idx.items():
                    k=labels.get(label)
                    if k and j<len(row):
                        v=row[j]
                        rec[k]=dtstr(v) if isinstance(v,datetime) else v
                if not rec.get("account_name"): rec["account_name"]=ws.title
                for k in NUMERIC:
                    if rec.get(k) is not None: rec[k]=number(rec[k],k=="duration_seconds")
                out[str(rec["article_key"])]=rec
    finally: wb.close()
    return out

def merge(old, scanned, now):
    data={k:dict(v) for k,v in old.items()}; added=changed=0
    for key,item in scanned.items():
        new,present=item["record"],item["present"]
        if key not in data: data[key]=new; added+=1; continue
        before={k:data[key].get(k) for k in FIELDS if k!="last_seen_at"}
        rec=dict(data[key])
        for field in FIELDS:
            if field in present: rec[field]=new.get(field)
        rec["first_seen_at"]=data[key].get("first_seen_at") or dtstr(now)
        rec["last_seen_at"]=dtstr(now)
        after={k:rec.get(k) for k in FIELDS if k!="last_seen_at"}
        if before!=after: changed+=1
        data[key]=rec
    return data,added,changed

def sheet_name(name, used):
    base=re.sub(r"[\[\]:*?/\\\\]","_",name).strip("'")[:31] or "公众号"
    candidate=base; i=2
    while candidate.casefold() in used:
        suffix=f"_{i}"; candidate=base[:31-len(suffix)]+suffix; i+=1
    used.add(candidate.casefold()); return candidate

def write_book(path, records):
    wb=Workbook(); wb.remove(wb.active)
    groups=defaultdict(list)
    for r in records.values(): groups[str(r.get("account_name") or "未知公众号")].append(r)
    if not groups: groups["暂无数据"]=[]
    used=set(); headers=[label for _,label in COLS]
    for acct,items in sorted(groups.items()):
        ws=wb.create_sheet(sheet_name(acct,used)); ws.sheet_view.showGridLines=False; ws.append(headers)
        items.sort(key=lambda x:str(x.get("published_article_time") or ""),reverse=True)
        for r in items: ws.append([r.get(k) for k,_ in COLS])
        # 将来源文本强制作为文本单元格，避免标题以“=”开头时被 Excel 当作公式。
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
        for c in ws[1]:
            c.font=Font(bold=True,color="FFFFFF"); c.fill=PatternFill("solid",fgColor="244062")
            c.alignment=Alignment(horizontal="center",vertical="center")
        ws.freeze_panes="A2"; ws.auto_filter.ref=ws.dimensions; ws.row_dimensions[1].height=24
        for i,(key,label) in enumerate(COLS,1):
            letter=get_column_letter(i)
            ws.column_dimensions[letter].width=48 if key=="article_title" else 42 if key in {"article_link","article_short_link"} else 52 if key=="source_path" else 14 if key in NUMERIC else max(14,min(26,len(label)*2+4))
            if key in NUMERIC:
                for row in ws.iter_rows(min_row=2,min_col=i,max_col=i):
                    row[0].number_format="0.##" if key=="duration_seconds" else "#,##0"
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp"); wb.save(tmp); wb.close(); tmp.replace(path)

def values(items, field): return [a[field] for a in items if isinstance(a.get(field),(int,float))]
def fmt(v):
    if v is None: return "未提供"
    return f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v,float) else f"{v:,}"

def metric_md(items):
    s=stats(items); lines=["| 指标 | 中位数 | 平均数 | 最高值 | 有效数据篇数 |","|---|---:|---:|---:|---:|"]
    for field,label in METRICS:
        m=s["metrics"][field]
        lines.append(f"| {label} | {fmt(m['median'])} | {fmt(m['average'])} | {fmt(m['max'])} | {m['count']} |")
    return "\n".join(lines)

def grouped(items):
    g=defaultdict(list)
    for a in items: g[str(a.get("account_name") or "未知公众号")].append(a)
    return g

def write_text(path,text):
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(text,encoding="utf-8"); tmp.replace(path)

def write_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str),encoding="utf-8"); tmp.replace(path)


def history_sections(items):
    sections=[]
    for acct,group in sorted(grouped(items).items()):
        reads=values(group,"read_count")
        lines=[f"## {acct}","",f"文章：{len(group)} 篇；阅读量中位数：{fmt(statistics.median(reads) if reads else None)}。"]
        if len(group)<20: lines.append("注意：样本不足 20 篇，只能做初步观察，不足以证明哪类题材更容易爆。")
        lines += ["","### 五项指标整体表现","",metric_md(group),"","### 各项指标领先文章",""]
        for field,label in METRICS:
            candidates=[a for a in group if isinstance(a.get(field),(int,float))]
            best=max(candidates,key=lambda x:x[field]) if candidates else None
            lines.append(f"{label}最多：{best['article_title']}（{fmt(best[field])}）" if best else f"{label}最多：暂无有效数据")
        top=sorted([a for a in group if isinstance(a.get("read_count"),(int,float))],key=lambda x:x["read_count"],reverse=True)[:7]
        lines += ["","### 文章明细（阅读前七）","","| 文章 | 发布时间 | 阅读 | 点赞 | 评论 | 转发 | 推荐 |","|---|---|---:|---:|---:|---:|---:|"]
        for a in top:
            title=str(a.get("article_title") or "").replace("|","／")
            lines.append(f"| {title} | {a.get('published_article_time') or '未提供'} | {fmt(a.get('read_count'))} | {fmt(a.get('like_count'))} | {fmt(a.get('comment_count'))} | {fmt(a.get('share_count'))} | {fmt(a.get('recommend_count'))} |")
        recent=sorted(group,key=lambda x:str(x.get("published_article_time") or ""),reverse=True)[:10]
        lines += ["","### 最近发布的文章（用于避免重复选题）",""]
        lines += [f"- {a.get('published_article_time') or '发布时间缺失'}：{a.get('article_title')}" for a in recent]
        sections.append("\n".join(lines))
    return "\n\n".join(sections)

def history_report(items, now, added, changed):
    collects=[parse_dt(a.get("collect_time")) for a in items]
    collects=[x for x in collects if x]
    latest=max(collects) if collects else None
    lines=["# 微信公众号历史选题分析（自动生成）","",f"报告生成：{dtstr(now)}",
           f"当前已读取文章：{len(items)} 篇","","说明：阅读、点赞、评论、转发、推荐均来自文章保存时的数据，不是实时值。",
           f"本轮数据变化：新增 {added} 篇；更新 {changed} 篇。",
           "本报告为首次全量历史基线，不参与后续三天周期比较。",""]
    if latest:
        age=max(0,(now-latest).total_seconds()/86400)
        lines.append(f"最新采集时间：{dtstr(latest)}；距报告生成约 {age:.1f} 天。")
        if age>3: lines.append("注意：最新采集数据已超过 3 天，不能当作今日流量。")
    else: lines.append("注意：没有可识别的采集时间，无法判断数据新鲜度。")
    lines += ["",history_sections(items),"","## 供 AI 选题的说明","",
              "请结合账号历史文章和当天真实热点提出候选关键词。不能把阅读量高直接解释为标题或选题导致；区分采集时间与发布时间；不得臆测缺失指标。微信指数关键词的三轮验证由人工完成，本脚本不执行。",""]
    return "\n".join(lines)

def period_items(items,start,end):
    result=[]
    for a in items:
        published=parse_dt(a.get("published_article_time"))
        if published and start<=published<end: result.append(a)
    return result

def account_summary(items):
    return {acct:stats(group) for acct,group in grouped(items).items()}


def metric_stats(items):
    result = {}
    for field, label in METRICS:
        vals = values(items, field)
        result[field] = {
            "count": len(vals),
            "total": sum(vals) if vals else None,
            "average": round(sum(vals) / len(vals), 2) if vals else None,
            "median": statistics.median(vals) if vals else None,
            "max": max(vals) if vals else None,
        }
    return result


def stats(items):
    boom = [
        a for a in items
        if isinstance(a.get("read_count"), (int, float)) and a["read_count"] > BOOM
    ]
    unknown = sum(1 for a in items if a.get("read_count") is None)
    return {
        "article_count": len(items),
        "boom_count": len(boom),
        "unknown_read_count": unknown,
        "metrics": metric_stats(items),
        "valid_for_no_boom_streak": bool(items) and unknown == 0,
        "status": (
            "成功" if boom else
            "无文章" if not items else
            "待判断" if unknown else
            "失败"
        ),
    }


def metric_md_from_stats(summary):
    lines = [
        "| 指标 | 总量 | 单篇平均 | 中位数 | 最高值 | 有效数据篇数 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for field, label in METRICS:
        item = summary["metrics"][field]
        lines.append(
            f"| {label} | {fmt(item['total'])} | {fmt(item['average'])} | "
            f"{fmt(item['median'])} | {fmt(item['max'])} | {item['count']} |"
        )
    return "\n".join(lines)


def account_folder_name(account):
    name = re.sub(r'[<>:"/\\|?*]', "_", str(account)).strip(" .")
    return name[:80] or "未知公众号"


def period_summary(account, start, end, items, supplement):
    s = stats(items)
    return {
        "account_name": account,
        "period_start": dtstr(start),
        "period_end": dtstr(end),
        "article_count": s["article_count"],
        "boom_count": s["boom_count"],
        "unknown_read_count": s["unknown_read_count"],
        "valid_for_no_boom_streak": s["valid_for_no_boom_streak"],
        "status": s["status"],
        "supplement_count": supplement,
        "metrics": s["metrics"],
    }


def percent_change(previous, current):
    if previous is None or current is None:
        return "无法比较"
    if previous == 0:
        return "从0开始" if current > 0 else "0%"
    change = (current - previous) / abs(previous) * 100
    return f"{change:+.1f}%"


def compare_md(current, previous):
    if not previous:
        return "这是该账号的第一个三天周期，没有上一周期可比较。"

    lines = [
        f"上一周期文章数：{previous.get('article_count', 0)} 篇 → 本周期：{current.get('article_count', 0)} 篇",
        f"上一周期爆款数：{previous.get('boom_count', 0)} 篇 → 本周期：{current.get('boom_count', 0)} 篇",
        "",
        "| 指标 | 上周期单篇平均 | 本周期单篇平均 | 变化 | 上周期总量 | 本周期总量 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for field, label in METRICS:
        old = previous.get("metrics", {}).get(field, {})
        new = current.get("metrics", {}).get(field, {})
        old_avg, new_avg = old.get("average"), new.get("average")
        lines.append(
            f"| {label} | {fmt(old_avg)} | {fmt(new_avg)} | "
            f"{percent_change(old_avg, new_avg)} | "
            f"{fmt(old.get('total'))} | {fmt(new.get('total'))} |"
        )
    return "\n".join(lines)


def period_report(account, start, end, now, items, summary, previous, supplement):
    status = summary["status"]
    if status == "成功":
        judgement = f"**本周期成功：** 出现 {summary['boom_count']} 篇爆款。"
    elif status == "失败":
        judgement = "**本周期失败：** 本周期有文章，但没有文章阅读量超过5000。"
    elif status == "待判断":
        judgement = (
            f"**暂不判定：** 有 {summary['unknown_read_count']} 篇文章缺少阅读量，"
            "不能据此认定本周期失败。"
        )
    else:
        judgement = "**无文章：** 本周期没有新文章，不计入成功或失败周期。"

    lines = [
        f"# {account}｜三天周期分析报告",
        "",
        f"报告生成：{dtstr(now)}",
        f"分析周期：[{dtstr(start)}，{dtstr(end)})",
        f"本周期文章：{len(items)} 篇",
        f"补采文章：{supplement} 篇（文章发布时间在本周期，采集时间晚于周期结束）",
        "",
        "## 周期结果",
        "",
        f"爆款标准：阅读量严格大于 {BOOM:,}。",
        f"本周期爆款：{summary['boom_count']} 篇。",
        judgement,
        "",
        "## 五项数据汇总",
        "",
        metric_md_from_stats(summary),
        "",
        "说明：指标取自目前归档中最新保存的数据，不代表历史某一时刻的实时数值。",
        "",
        "## 与上一周期比较",
        "",
        compare_md(summary, previous),
        "",
    ]

    viral = sorted(
        [a for a in items if isinstance(a.get("read_count"), (int, float)) and a["read_count"] > BOOM],
        key=lambda a: a["read_count"],
        reverse=True,
    )
    lines += [
        "## 本周期爆款文章",
        "",
        "| 文章 | 发布时间 | 阅读 | 点赞 | 评论 | 转发 | 推荐 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    if viral:
        for a in viral:
            title = str(a.get("article_title") or "").replace("|", "／")
            lines.append(
                f"| {title} | {a.get('published_article_time') or '未提供'} | "
                f"{fmt(a.get('read_count'))} | {fmt(a.get('like_count'))} | "
                f"{fmt(a.get('comment_count'))} | {fmt(a.get('share_count'))} | "
                f"{fmt(a.get('recommend_count'))} |"
            )
    else:
        lines.append("| 本周期没有符合标准的爆款 | — | — | — | — | — | — |")

    lines += [
        "",
        "## 本周期全部文章明细",
        "",
        "| 文章 | 发布时间 | 采集时间 | 阅读 | 点赞 | 评论 | 转发 | 推荐 | 数据标记 |",
        "|---|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for a in sorted(items, key=lambda x: str(x.get("published_article_time") or "")):
        collect = parse_dt(a.get("collect_time"))
        marker = "采集时间缺失" if not collect else ("补采数据" if collect > end else "周期内采集")
        title = str(a.get("article_title") or "").replace("|", "／")
        lines.append(
            f"| {title} | {a.get('published_article_time') or '未提供'} | "
            f"{a.get('collect_time') or '未提供'} | {fmt(a.get('read_count'))} | "
            f"{fmt(a.get('like_count'))} | {fmt(a.get('comment_count'))} | "
            f"{fmt(a.get('share_count'))} | {fmt(a.get('recommend_count'))} | {marker} |"
        )

    lines += [
        "",
        "## 数据说明",
        "",
        "本报告负责整理、统计和比较数据，不自动认定标题、选题、封面或发布时间与阅读表现之间存在因果关系；相关判断交由后续 AI 分析。",
        "",
    ]
    return "\n".join(lines)


def fried_report(account, end, now, all_items, out):
    start = end - timedelta(days=15)
    account_items = [
        a for a in all_items
        if str(a.get("account_name") or "") == account
    ]
    recent = [
        a for a in account_items
        if (published := parse_dt(a.get("published_article_time")))
        and start <= published < end
    ]
    recent_viral = [
        a for a in recent
        if isinstance(a.get("read_count"), (int, float)) and a["read_count"] > BOOM
    ]

    if recent_viral:
        selected = recent_viral
        source_label = "近15天爆款"
        period_label = f"[{dtstr(start)}，{dtstr(end)})"
    else:
        selected = [
            a for a in account_items
            if (published := parse_dt(a.get("published_article_time")))
            and published < start
            and isinstance(a.get("read_count"), (int, float))
            and a["read_count"] > BOOM
        ]
        source_label = "历史更早爆款（近15天没有爆款，已回看首次历史数据）"
        period_label = f"早于 {dtstr(start)} 的历史文章"

    if not selected:
        return None

    ordered = sorted(selected, key=lambda a: a["read_count"], reverse=True)[:10]
    lines = [
        f"# {account}｜炒冷饭报告",
        "",
        f"生成时间：{dtstr(now)}",
        f"本次检索范围：{period_label}",
        f"采用数据：{source_label}",
        f"符合条件的文章：{len(selected)} 篇；本报告展示阅读量最高的 {len(ordered)} 篇。",
        "",
        "筛选标准：阅读量严格大于5000；最多收录10篇，按阅读量从高到低排序。",
        "说明：本报告只提供历史爆款数据，重新尝试哪些方向由 AI 结合文章内容判断。",
        "",
        "| 文章 | 发布时间 | 阅读 | 点赞 | 评论 | 转发 | 推荐 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for a in ordered:
        title = str(a.get("article_title") or "").replace("|", "／")
        lines.append(
            f"| {title} | {a.get('published_article_time') or '未提供'} | "
            f"{fmt(a.get('read_count'))} | {fmt(a.get('like_count'))} | "
            f"{fmt(a.get('comment_count'))} | {fmt(a.get('share_count'))} | "
            f"{fmt(a.get('recommend_count'))} |"
        )

    folder = out / "报告" / account_folder_name(account) / "特别报告"
    stamp = end.strftime("%Y%m%d_%H%M%S")
    write_text(folder / f"炒冷饭报告_{stamp}.md", "\n".join(lines))
    write_json(folder / f"炒冷饭报告_{stamp}_文章数据.json", ordered)
    return str(folder / f"炒冷饭报告_{stamp}.md")


def new_account_state(anchor):
    return {
        "last_period_end": dtstr(anchor),
        "no_boom_streak": 0,
        "fried_reported_in_streak": False,
        "last_period_summary": None,
    }


def migrate_state(state, accounts, now):
    # 兼容第一版已经生成的 analysis_state.json，保留首次运行时间和周期起点。
    if "accounts" in state:
        anchor = parse_dt(state.get("initialized_at")) or now
        for account in accounts:
            state["accounts"].setdefault(account, new_account_state(anchor))
        state["version"] = 2
        return state, False

    anchor = parse_dt(state.get("last_period_end")) or parse_dt(state.get("initialized_at")) or now
    migrated = {
        "version": 2,
        "initialized_at": state.get("initialized_at") or dtstr(anchor),
        "accounts": {},
    }
    for account in accounts:
        migrated["accounts"][account] = new_account_state(anchor)
    return migrated, True


def run(storage, out, now):
    storage = storage.resolve()
    out = out.resolve()
    if not storage.is_dir():
        raise FileNotFoundError(f"找不到文章归档目录：{storage}")

    book = out / "公众号数据总表.xlsx"
    state_file = out / "analysis_state.json"
    old = read_book(book)
    scanned, errors = scan(storage, now)
    if not scanned and not old:
        raise RuntimeError(f"没有找到可用的 article_detail.json：{storage}")

    records, added, changed = merge(old, scanned, now)
    write_book(book, records)
    all_items = list(records.values())
    accounts = sorted(grouped(all_items).keys())
    reports = out / "报告"

    if not state_file.exists():
        stamp = now.strftime("%Y%m%d_%H%M%S")
        write_text(
            reports / "历史总报告" / f"历史总报告_{stamp}.md",
            history_report(all_items, now, added, changed),
        )
        state = {
            "version": 2,
            "initialized_at": dtstr(now),
            "accounts": {account: new_account_state(now) for account in accounts},
        }
        write_json(state_file, state)
        return {
            "mode": "首次全量",
            "articles": len(all_items),
            "accounts": len(accounts),
            "new": added,
            "updated": changed,
            "period_reports": 0,
            "history_report": True,
            "scan_errors": errors,
            "workbook": str(book),
            "output": str(out),
        }

    state = json.loads(state_file.read_text(encoding="utf-8"))
    state, migrated = migrate_state(state, accounts, now)
    generated = 0
    per_account = {}
    notices = []

    for account in accounts:
        account_state = state["accounts"][account]
        cursor = parse_dt(account_state.get("last_period_end"))
        if not cursor:
            raise RuntimeError(f"{account} 的周期起点无法读取，请检查 analysis_state.json。")

        account_count = 0
        account_items = [a for a in all_items if str(a.get("account_name") or "") == account]

        while now - cursor >= PERIOD:
            start, end = cursor, cursor + PERIOD
            selected = period_items(account_items, start, end)
            supplement = sum(
                1 for a in selected
                if (collect := parse_dt(a.get("collect_time"))) and collect > end
            )
            folder = f"{start.strftime('%Y%m%d_%H%M%S')}__{end.strftime('%Y%m%d_%H%M%S')}"
            dest = reports / "三天周期" / account_folder_name(account) / folder
            summary_file = dest / "summary.json"

            if summary_file.exists():
                summary = json.loads(summary_file.read_text(encoding="utf-8"))
            else:
                summary = period_summary(account, start, end, selected, supplement)
                write_text(
                    dest / "周期报告.md",
                    period_report(
                        account, start, end, now, selected, summary,
                        account_state.get("last_period_summary"), supplement
                    ),
                )
                write_json(dest / "文章明细.json", selected)
                write_json(summary_file, summary)

            if summary.get("boom_count", 0) > 0:
                account_state["no_boom_streak"] = 0
                account_state["fried_reported_in_streak"] = False
            elif summary.get("valid_for_no_boom_streak"):
                account_state["no_boom_streak"] = int(account_state.get("no_boom_streak", 0)) + 1
                if (
                    account_state["no_boom_streak"] >= 2
                    and not account_state.get("fried_reported_in_streak", False)
                ):
                    special = fried_report(account, end, now, all_items, out)
                    account_state["fried_reported_in_streak"] = True
                    if special:
                        notices.append(f"{account}：已生成炒冷饭报告")
                    else:
                        notices.append(f"{account}：历史上没有阅读量超过5000的文章，暂不生成炒冷饭报告")

            account_state["last_period_summary"] = summary
            cursor = end
            account_state["last_period_end"] = dtstr(cursor)
            account_count += 1
            generated += 1

        per_account[account] = {
            "period_reports": account_count,
            "no_boom_streak": account_state.get("no_boom_streak", 0),
            "next_period_due": dtstr(cursor + PERIOD),
        }

    write_json(state_file, state)
    next_due = min(
        (parse_dt(v["next_period_due"]) for v in per_account.values() if v.get("next_period_due")),
        default=None,
    )
    return {
        "mode": "增量更新",
        "articles": len(all_items),
        "accounts": len(accounts),
        "new": added,
        "updated": changed,
        "period_reports": generated,
        "per_account": per_account,
        "special_report_notices": notices,
        "state_migrated_from_v1": migrated,
        "scan_errors": errors,
        "workbook": str(book),
        "output": str(out),
        "next_period_due": dtstr(next_due),
        "now": dtstr(now),
    }


def main():
    base=Path(__file__).resolve().parent
    p=argparse.ArgumentParser(description="读取 Access WeChat Article 的 article_detail.json 并生成公众号分析报告")
    p.add_argument("--storage-root",type=Path,default=base/"storages")
    p.add_argument("--output-dir",type=Path,default=base/"公众号分析输出")
    p.add_argument("--now",type=str,default=None,help="测试用时间，例如 2026-11-12 20:00:00")
    p.add_argument("--version",action="version",version=VERSION)
    args=p.parse_args(); now=parse_dt(args.now) if args.now else now_local()
    if not now: p.error("--now 时间格式无法识别")
    try: result=run(args.storage_root,args.output_dir,now)
    except PermissionError as e:
        print(f"写入失败：{e}\n如果 Excel 正在打开，请先关闭 公众号数据总表.xlsx。",file=sys.stderr); return 2
    except Exception as e: print(f"运行失败：{e}",file=sys.stderr); return 1
    print("="*48+"\n微信公众号本地分析完成\n"+"="*48)
    for k,v in result.items():
        if k=="scan_errors":
            print(f"扫描异常：{len(v)} 项")
            for err in v[:10]: print(" - "+err)
            if len(v)>10: print(f" ...另有 {len(v)-10} 项")
        else: print(f"{k}: {v}")
    return 0

if __name__=="__main__": raise SystemExit(main())
