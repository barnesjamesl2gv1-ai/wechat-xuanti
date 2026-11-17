#!/usr/bin/env python3
"""提交前自检（leakguard）：把个人信息、密钥、本机路径挡在本地。

用法：
    python scripts/check_leaks.py            # 扫描仓库里已跟踪的全部文件（CI 用）
    python scripts/check_leaks.py --staged   # 只扫已暂存的内容（pre-commit 用）

为什么需要它：开源仓库最容易泄露的不是代码，而是「顺手写下的」东西——
.gitignore 里的真实账号名、README 里的本机路径、截图、提交信息。

真实账号名这类词没法硬编码进仓库（写进来又是一次泄露），把它放在本地 git 配置里：
    git config --local leakguard.words "某个账号名,另一个账号名"
这个配置只存在你本机的 .git/config，不会被提交。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# 只提示「语义明确」的特征，宁少不多：规则一旦误报，大家就会习惯性跳过
PATTERNS: list[tuple[str, str]] = [
    ("疑似 API 密钥", r"(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}"
                     r"|github_pat_[A-Za-z0-9_]{22,}|xox[baprs]-[A-Za-z0-9-]{10,}"
                     r"|AKIA[0-9A-Z]{16})"),
    ("私钥内容", r"-----BEGIN (RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY"),
    ("邮箱地址", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("本机用户目录", r"[A-Za-z]:\\Users\\"),
    ("本机绝对路径", r"[A-Za-z]:\\[^\s\\\"'<>|]{1,24}\\"),
    ("手机号", r"\b1[3-9]\d{9}\b"),
]

# 这些上下文里的命中不报（示例值、本机回环地址、GitHub 匿名邮箱）
ALLOW = ("example.com", "example.org", "127.0.0.1", "localhost",
         "users.noreply.github.com", "your-key", "<你的", "请填写")

# 本行带这个标记就不检查（文档里要举反例时用）
ALLOW_MARK = "leakguard:allow"

SKIP_FILES = ("scripts/check_leaks.py", ".githooks/pre-commit")


def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], capture_output=True)
    return proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else ""


def tracked_files() -> list[str]:
    return [line for line in git("ls-files").splitlines() if line]


def staged_files() -> list[str]:
    out = git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    return [line for line in out.splitlines() if line]


def read_staged(path: str) -> str | None:
    proc = subprocess.run(["git", "show", f":{path}"], capture_output=True)
    if proc.returncode != 0:
        return None
    return decode(proc.stdout)


def read_worktree(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            return decode(handle.read())
    except OSError:
        return None


def decode(raw: bytes) -> str | None:
    if b"\x00" in raw[:8192]:      # 二进制（图片等）直接跳过
        return None
    return raw.decode("utf-8", "replace")


def local_words() -> list[str]:
    raw = git("config", "--local", "--get", "leakguard.words").strip()
    return [word for word in re.split(r"[,，;；\s]+", raw) if word]


def mask(text: str) -> str:
    text = text.strip()
    if len(text) <= 8:
        return text[:2] + "***"
    return text[:6] + "***"


def scan(files: list[str], loader) -> list[tuple[str, int, str, str]]:
    words = local_words()
    patterns = list(PATTERNS) + [(f"本地自定义敏感词（{mask(w)}）", re.escape(w)) for w in words]
    findings: list[tuple[str, int, str, str]] = []

    for path in files:
        if path in SKIP_FILES:
            continue
        text = loader(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if ALLOW_MARK in line:
                continue
            if any(token in line for token in ALLOW):
                continue
            for label, pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    findings.append((path, lineno, label, mask(match.group(0))))
                    break
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="提交前自检：扫描个人信息与密钥")
    parser.add_argument("--staged", action="store_true", help="只扫描已暂存的内容")
    args = parser.parse_args()

    if args.staged:
        files, loader = staged_files(), read_staged
    else:
        files, loader = tracked_files(), read_worktree

    findings = scan(files, loader)
    if not findings:
        return 0

    print(f"leakguard：发现 {len(findings)} 处需要确认的内容\n", file=sys.stderr)
    for path, lineno, label, preview in findings:
        print(f"  {path}:{lineno}  [{label}]  {preview}", file=sys.stderr)
    words = local_words()
    print("""
怎么处理：
  - 是误报 / 示例值：在该行加上注释标记  leakguard:allow
  - 是真实信息：删掉它，改用占位符（如 <你的账号>）
  - 只想提醒不改：确认无误后  git commit --no-verify
""", file=sys.stderr)
    if not words:
        print("提示：真实账号名这类词可以用本地配置加入检查（不会进仓库）：\n"
              "  git config --local leakguard.words \"账号名1,账号名2\"\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
