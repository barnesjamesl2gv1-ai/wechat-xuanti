"""启动冒烟测试：应用必须真的能监听端口并回页面，而不是"import 得动"。

为什么需要它（2026-11-19 的教训）：采集端在 Python 3.13 上启动即崩
（pywebview 依赖的 pythonnet 加载 .NET 失败），而当时所有单元测试都是绿的——
因为测试只 import 模块、从不真正启动服务。于是故障只能等用户双击时才发现。

所以这里用**子进程 + 真实端口**起一次服务，只断言两件最小的事：

    GET /            返回 200 且是 HTML（模板能渲染、前端构建物在）
    GET /api/status  返回 200 且 ok=true（业务接口可用）

不联网、不调模型。文章数据目录指向临时目录，不碰真实数据。
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOOT_TIMEOUT_SECONDS = 40.0

sys.path.insert(0, str(ROOT))

# 设了访问口令的环境里，子进程也需要同一个口令；没设就是空串。
try:
    import web_app  # noqa: E402

    ACCESS_TOKEN = str(getattr(web_app, "ACCESS_TOKEN", "") or "")
except Exception:  # pragma: no cover - 导入失败由子进程的日志给结论
    ACCESS_TOKEN = ""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


def get(opener: urllib.request.OpenerDirector, url: str, timeout: float = 3.0):
    """返回 (状态码, 正文)；HTTP 错误不抛，交给断言判断。"""
    try:
        with opener.open(url, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


def log_tail(path: Path, lines: int = 15) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return "<没有日志>"
    return "\n".join(text.splitlines()[-lines:]) or "<日志是空的>"


def remove_file(path: Path) -> None:
    """Windows 上子进程刚退出时文件句柄可能还没释放，重试几次再放弃。"""
    for _ in range(25):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.1)


class BootSmokeTests(unittest.TestCase):
    def test_service_starts_and_serves(self) -> None:
        port = free_port()
        stem = uuid.uuid4().hex[:8]
        tmp = ROOT / ".tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        log_path = tmp / f"boot-{stem}.log"
        storage = tmp / f"boot-storage-{stem}"
        storage.mkdir()

        env = dict(os.environ)
        env.update({
            "XUANTI_HOST": "127.0.0.1",
            "XUANTI_PORT": str(port),
            "XUANTI_STORAGE_ROOT": str(storage),
            "XUANTI_OPEN_BROWSER": "0",
            "PYTHONIOENCODING": "utf-8",
        })
        if ACCESS_TOKEN:
            env["XUANTI_ACCESS_TOKEN"] = ACCESS_TOKEN

        base = f"http://127.0.0.1:{port}"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "web_app.py")],
                cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,   # 便于 POSIX 整组结束；Windows 用 taskkill /T
            )
        self.addCleanup(shutil.rmtree, storage, True)
        self.addCleanup(remove_file, log_path)
        self.addCleanup(self._stop, process)

        opener = build_opener()
        deadline = time.monotonic() + BOOT_TIMEOUT_SECONDS
        status_code, body = 0, b""
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(
                    f"web_app 启动即退出（退出码 {process.returncode}）：\n"
                    f"{log_tail(log_path)}"
                )
            try:
                status_code, body = get(opener, f"{base}/api/status", timeout=2.0)
                if status_code in (200, 401):   # 401 说明口令中间件已在工作，服务是活的
                    break
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.25)

        self.assertIn(
            status_code, (200, 401),
            f"{BOOT_TIMEOUT_SECONDS:g} 秒内服务没有响应：\n{log_tail(log_path)}",
        )

        if ACCESS_TOKEN:
            auth_url = f"{base}/auth?token={urllib.parse.quote(ACCESS_TOKEN)}"
            self.assertEqual(get(opener, auth_url)[0], 200, "口令换 cookie 失败")

        status_code, body = get(opener, f"{base}/api/status")
        self.assertEqual(status_code, 200, f"/api/status 不是 200：\n{body[:400]!r}")
        payload = json.loads(body.decode("utf-8", "replace"))
        self.assertTrue(payload.get("ok"), f"/api/status 返回 ok=false：{payload}")

        status_code, body = get(opener, f"{base}/")
        self.assertEqual(status_code, 200, "首页不是 200（模板或静态资源有问题）")
        html = body.decode("utf-8", "replace")
        self.assertIn("<html", html.lower(), "首页内容不是 HTML")
        self.assertIn("公众号", html, "首页里没有预期文案，模板可能没渲染")

    def _stop(self, process: subprocess.Popen) -> None:
        """连同子进程一起结束。

        Windows 上项目内 `.venv\\Scripts\\python.exe` 是转发壳，真正跑 Flask 的是它的
        子进程；只 terminate 壳进程会留下孤儿继续占端口和日志文件。
        """
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(process.pid), 15)
            except (ProcessLookupError, PermissionError):
                process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
