"""Console entry point for the local API server."""

from __future__ import annotations

import argparse
import socket
import sys
import webbrowser

import uvicorn

from backend import __version__


def find_free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("无法找到可用端口")


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in and args_in[0] == "--pick-path":
        from backend.platform.dialogs import main as picker_main

        return picker_main(args_in[1:])

    parser = argparse.ArgumentParser(description="EduCore 本地服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 表示自动选择端口")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    port = args.port or find_free_port()
    url = f"http://{args.host}:{port}"
    print(f"EduCore {__version__} 启动于 {url}")
    if args.open_browser:
        webbrowser.open(url)

    if args.reload:
        # Reload mode needs an import string and cannot pass runtime state.
        import os

        os.environ["EDUCORE_DATA_DIR"] = args.data_dir or ""
        uvicorn.run(
            "backend.api.app:create_app",
            factory=True,
            host=args.host,
            port=port,
            reload=True,
            log_level="info",
        )
        return 0

    if args.data_dir:
        import os

        os.environ["EDUCORE_DATA_DIR"] = args.data_dir

    uvicorn.run(
        "backend.api.app:create_app",
        factory=True,
        host=args.host,
        port=port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())