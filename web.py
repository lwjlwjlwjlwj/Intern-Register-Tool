"""Web UI 一键启动器。

用法：
  python web.py                    # 启动 Web 控制台，自动打开浏览器
  python web.py --port 9000        # 指定端口
  python web.py --no-browser       # 不自动打开浏览器
  python web.py --data-dir D:/cfg   # 指定配置/数据目录

说明：
  - 配置持久化在 ~/.intern-register-tool/webui-data/config.json（可用
    --data-dir 或环境变量 APP_DATA_DIR 覆盖），打包成单文件 exe 也能直接读写；
  - 端口默认 8000，被占用时自动顺延到下一个空闲端口；
  - 顶层的 `from src.yyds_client import YydsMailClient` 不是死代码 ——
    它让 PyInstaller 静态分析能把 src 包与 curl_cffi 一起打进单文件，
    否则 webui/clients.py 里的延迟导入在打包后会被漏掉（yyds 模式不可用）。
"""

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from src.yyds_client import YydsMailClient  # noqa: F401  （见模块 docstring）


def _free_port(start: int, tries: int = 20) -> int:
    """从 start 起找第一个可绑定的本地端口。"""
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(f"端口 {start}~{start + tries - 1} 都被占用，请用 --port 指定其它端口")


def main() -> int:
    ap = argparse.ArgumentParser(description="Intern-Register-Tool Web UI 一键启动")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址（默认 127.0.0.1，仅本机可访问）")
    ap.add_argument("--port", type=int, default=8000,
                    help="监听端口（默认 8000；被占用时自动顺延）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--data-dir", default=None,
                    help="配置/数据目录（默认 ~/.intern-register-tool/webui-data）")
    args = ap.parse_args()

    if args.data_dir:
        os.environ["APP_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())

    # 端口探测要在 import webui（连带 uvicorn）之前做，报错快、不浪费启动时间。
    port = _free_port(args.port)
    if port != args.port:
        print(f"⚠ 端口 {args.port} 被占用，已顺延到 {port}")

    url_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{url_host}:{port}"

    print("=" * 60)
    print("  Intern-Register-Tool  Web 控制台")
    print("=" * 60)
    print(f"  访问地址： {url}")
    print(f"  配置目录： {os.environ.get('APP_DATA_DIR', '') or Path.home() / '.intern-register-tool' / 'webui-data'}")
    print("  Ctrl+C 停止服务")
    print("=" * 60, flush=True)

    if not args.no_browser:
        threading.Thread(
            target=lambda: (time.sleep(1.2), webbrowser.open(url)),
            daemon=True,
        ).start()

    import uvicorn

    from webui.main import app

    uvicorn.run(app, host=args.host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
