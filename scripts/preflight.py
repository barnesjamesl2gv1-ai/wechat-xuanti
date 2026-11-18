"""发布前自检（preflight）：一条命令跑完所有「上次是在发布后才发现」的检查。

用法：
    python scripts/preflight.py            # 本地自检，问题只提示
    python scripts/preflight.py --strict   # CI/推送前，任何错误都失败

覆盖过去三天反复踩到的坑：
  1. 语法/导入错误        —— 提交前编译一遍
  2. 测试失败              —— 跑全部离线用例
  3. 个人信息/密钥泄露     —— 复用 leakguard
  4. .bat 编码             —— 必须纯 ASCII + CRLF + 无 BOM（cmd 解析中文行会崩）
  5. 文档图片引用          —— README 指向的图片必须存在（否则 GitHub 上显示裂图）
  6. 占位符                —— <owner>、YOUR_KEY、TODO 之类不能进发布文档
  7. 数据目录被跟踪        —— 文章数据、密钥、采集端目录绝不能进 git
  8. 超大文件              —— 单个跟踪文件超过 1 MB 给出警告
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ERRORS: list[str] = []
WARNINGS: list[str] = []


def git(*args: str) -> list[str]:
    proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line.strip()]


def rel(path: pathlib.Path) -> str:
    return path.relative_to(ROOT).as_posix()


# ---------------------------------------------------------------- 1. 编译
def check_compile() -> None:
    targets = sorted(ROOT.glob("*.py")) + sorted((ROOT / "scripts").glob("*.py"))
    for path in targets:
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            ERRORS.append(f"语法错误 {rel(path)}:{exc.lineno} {exc.msg}")


# ---------------------------------------------------------------- 2. 测试
def check_tests() -> None:
    module_root = ROOT
    tests = sorted(p.name for p in module_root.glob("test_*.py"))
    if not tests:
        WARNINGS.append("没有找到 test_*.py")
        return
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", *[t[:-3] for t in tests]],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        ERRORS.append("测试失败: " + " / ".join(tail))


# ---------------------------------------------------------------- 3. 泄露检查
def check_leaks() -> None:
    script = ROOT / "scripts" / "check_leaks.py"
    if not script.is_file():
        return  # 该仓库没装 leakguard 时静默跳过
    proc = subprocess.run([sys.executable, str(script)], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        detail = " / ".join((proc.stderr or "").strip().splitlines()[1:4])
        ERRORS.append(f"leakguard 命中: {detail}")


# ---------------------------------------------------------------- 4. bat 编码
def check_bat_files() -> None:
    for path in sorted(ROOT.glob("*.bat")):
        raw = path.read_bytes()
        name = rel(path)
        if raw.startswith(b"\xef\xbb\xbf"):
            ERRORS.append(f"{name}: 含 UTF-8 BOM，cmd 会把它当命令")
        non_ascii = sum(1 for byte in raw if byte > 127)
        declares_utf8 = b"chcp 65001" in raw
        if non_ascii and not declares_utf8:
            ERRORS.append(f"{name}: 含 {non_ascii} 个非 ASCII 字节，中文会导致 cmd 解析失败"
                          f"（如确需中文，请在脚本里加 chcp 65001 并把文件存为 UTF-8）")
        elif non_ascii:
            WARNINGS.append(f"{name}: 含中文，已用 chcp 65001 声明 UTF-8")
        crlf = raw.count(b"\r\n")
        lf = raw.count(b"\n")
        if crlf == 0:
            ERRORS.append(f"{name}: 没有 CRLF 换行，双击会闪退或语法错误")
        elif lf != crlf:
            ERRORS.append(f"{name}: 混用 CRLF 与 LF（{crlf} vs {lf}）")


# ---------------------------------------------------------------- 5. 文档图片
IMAGE_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def check_doc_images() -> None:
    for doc in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
        if not doc.is_file():
            continue
        for target in IMAGE_RE.findall(doc.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#")):
                continue
            if not (doc.parent / target).exists():
                ERRORS.append(f"{rel(doc)}: 引用的图片不存在 -> {target}")


# ---------------------------------------------------------------- 6. 占位符
PLACEHOLDERS = ["<owner>", "<your-", "YOUR_KEY", "TODO", "FIXME", "TBD"]


def check_placeholders() -> None:
    docs = [ROOT / "README.md", ROOT / "SECURITY.md",
            *sorted((ROOT / "docs").glob("*.md"))]
    for doc in docs:
        if not doc.is_file():
            continue
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for token in PLACEHOLDERS:
                if token in line:
                    ERRORS.append(f"{rel(doc)}:{lineno} 残留占位符 {token}")


# ---------------------------------------------------------------- 7/8. 数据与体积
# 目录用前缀匹配；文件用精确匹配（.env.example 是模板，不能误伤）
FORBIDDEN_DIRS = ("storages/", "公众号分析输出/", "collector/")
FORBIDDEN_FILES = (".env", "ai_pairs.json")


def check_tracked_files() -> None:
    tracked = git("ls-files")
    for path in tracked:
        if path in FORBIDDEN_FILES or any(path.startswith(item) for item in FORBIDDEN_DIRS):
            ERRORS.append(f"数据/密钥文件被跟踪: {path}")
    for path in tracked:
        full = ROOT / path
        if full.is_file() and full.stat().st_size > 1024 * 1024:
            WARNINGS.append(f"大文件 {path} ({full.stat().st_size / 1e6:.1f} MB)")


def main() -> int:
    parser = argparse.ArgumentParser(description="发布前自检")
    parser.add_argument("--strict", action="store_true", help="警告也视为失败")
    parser.add_argument("--profile", choices=("open", "private"), default="open",
                        help="open=公开仓库（含文档与泄露检查）；private=私有仓库（只查代码与数据）")
    args = parser.parse_args()

    checks = [check_compile, check_tests, check_leaks, check_bat_files,
              check_tracked_files]
    if args.profile == "open":
        checks += [check_doc_images, check_placeholders]
    for check in checks:
        check()

    for line in ERRORS:
        print(f"[错误] {line}", file=sys.stderr)
    for line in WARNINGS:
        print(f"[提示] {line}", file=sys.stderr)

    if ERRORS:
        print(f"\npreflight: {len(ERRORS)} 个错误，{len(WARNINGS)} 个提示", file=sys.stderr)
        return 1
    if WARNINGS and args.strict:
        print(f"\npreflight: 严格模式，{len(WARNINGS)} 个提示视为失败", file=sys.stderr)
        return 1
    print("preflight: 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
