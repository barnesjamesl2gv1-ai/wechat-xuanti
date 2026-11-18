"""跨模块共用的小工具。

`folder_name()` 与 `now_text()` 原来在 5 个文件里各有一份完全相同的实现
（account_profile / wechat_index_store / wechat_index_excel / web_app /
wechat_analyzer），改一处容易漏四处，统一到这里。
"""
from __future__ import annotations

import re
from datetime import datetime

_BAD_CHARS = re.compile(r'[<>:"/\\|?*]')


def now_text() -> str:
    """统一的本地时间字符串，用于任务时间戳。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def folder_name(account: str) -> str:
    """账号名 → 目录名：替换非法字符、去首尾空格与点、限长 80。"""
    name = _BAD_CHARS.sub("_", str(account)).strip(" .")
    return name[:80] or "未知公众号"
