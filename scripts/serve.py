"""生产用启动入口（容器 / 内网服务器）。

与 web_app.py 的区别只有一个：用 waitress 代替 Flask 自带的开发服务器。
开发服务器单进程、无并发防护，只适合本机点开看看；被人从别的机器访问时用这个。

    set XUANTI_HOST=0.0.0.0
    set XUANTI_PORT=8767
    python scripts/serve.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import web_app  # noqa: E402


def main() -> int:
    try:
        from waitress import serve
    except ImportError:
        print("缺少 waitress，请先执行：python -m pip install -r requirements.txt", file=sys.stderr)
        return 2

    host = (os.environ.get("XUANTI_HOST") or "127.0.0.1").strip()
    port = int(os.environ.get("XUANTI_PORT") or "8767")

    print(f"公众号爆款选题系统 · waitress  http://{host}:{port}/")
    if web_app.ACCESS_TOKEN:
        print("已启用访问口令：首次请在浏览器打开 /auth?token=你的口令")
    else:
        print("提示：对外提供服务时建议设置 XUANTI_ACCESS_TOKEN，否则任何人打开即可消耗你的 API 额度。")

    serve(web_app.app, host=host, port=port, threads=8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
