"""护栏测试：本仓库只跑在浏览器里，禁止引入 GUI / .NET 运行时依赖。

背景（2026-11-19 的真实故障）：上游采集端的桌面外壳依赖 pywebview → pythonnet → .NET。
在 Python 3.13 上 pythonnet 3.1.0 加载 .NET 失败：

    Failed to resolve Python.Runtime.Loader.Initialize from
    ...\\pythonnet\\runtime\\Python.Runtime.dll

窗口起不来，整个程序直接退出。本仓库刻意不含桌面外壳，靠的是
「标准库 + Flask + openpyxl + waitress」，任何环境下都能 headless 启动。

这条测试就是那道闸门：谁把 pywebview / pythonnet / tkinter / Qt 之类的依赖
加回来，CI 立刻红，而不是等某个用户的 Python 版本上双击失败。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 只要出现，就意味着这个仓库不再是「纯浏览器 + 纯 Python」项目
FORBIDDEN_MODULES = {
    "pywebview": "桌面窗口外壳；Windows 上依赖 pythonnet/.NET，Python 3.13 会加载失败",
    "webview": "pywebview 的导入名，同上",
    "pythonnet": "CLR 桥，需要 .NET 运行时",
    "clr": "pythonnet 的导入名，需要 .NET 运行时",
    "clr_loader": "pythonnet 的加载器，需要 .NET 运行时",
    "tkinter": "本地 GUI；本项目用浏览器做界面",
    "PyQt5": "本地 GUI；本项目用浏览器做界面",
    "PyQt6": "本地 GUI；本项目用浏览器做界面",
    "PySide2": "本地 GUI；本项目用浏览器做界面",
    "PySide6": "本地 GUI；本项目用浏览器做界面",
    "wx": "本地 GUI；本项目用浏览器做界面",
}

IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def production_files() -> list[Path]:
    """只看会被用户执行的代码；测试文件自己会提到这些名字，排除在外。"""
    files = [p for p in sorted(ROOT.glob("*.py")) if not p.name.startswith("test_")]
    files += sorted((ROOT / "scripts").glob("*.py"))
    return files


def requirement_names() -> list[tuple[str, str]]:
    """requirements.txt 里的 (包名, 原始行)。"""
    path = ROOT / "requirements.txt"
    if not path.is_file():
        return []
    items: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[<>=!~\[;,\s]", line, maxsplit=1)[0].strip()
        if name:
            items.append((name, line))
    return items


class NoGuiDependencyTests(unittest.TestCase):
    def test_requirements_have_no_gui_or_dotnet(self) -> None:
        lowercase = {key.lower(): value for key, value in FORBIDDEN_MODULES.items()}
        hits = [
            f"requirements.txt: {line} —— {lowercase[name.lower()]}"
            for name, line in requirement_names()
            if name.lower() in lowercase
        ]
        self.assertEqual(hits, [], "requirements.txt 引入了 GUI/.NET 依赖：\n" + "\n".join(hits))

    def test_production_code_imports_no_gui_or_dotnet(self) -> None:
        lowercase = {key.lower(): value for key, value in FORBIDDEN_MODULES.items()}
        hits: list[str] = []
        for path in production_files():
            # utf-8-sig：Windows 编辑器可能写出 BOM，带 BOM 时 `^\s*import` 会漏匹配
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            for module in IMPORT_RE.findall(text):
                reason = lowercase.get(module.lower())
                if reason:
                    hits.append(f"{path.relative_to(ROOT).as_posix()}: import {module} —— {reason}")
        self.assertEqual(hits, [], "生产代码引入了 GUI/.NET 依赖：\n" + "\n".join(hits))

    def test_requirements_still_cover_what_the_app_needs(self) -> None:
        """反向兜底：把必备依赖顺手删掉，也应该被发现。"""
        names = {name.lower() for name, _ in requirement_names()}
        for required in ("flask", "openpyxl"):
            self.assertIn(required, names, f"requirements.txt 少了 {required}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
