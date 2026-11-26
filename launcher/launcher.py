"""EduCore desktop launcher.

The launcher runs the FastAPI app in-process, picks an available local port,
opens the browser and provides a graceful exit path through a sentinel file.
It also implements the native path picker mode used by the settings UI when the
application is packaged (``EduCore.exe --pick-path directory``).
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path


def find_free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("没有可用的本地端口")


def _log_crash(traceback_text: str) -> None:
    try:
        from backend.platform.paths import resolve_data_dir

        directory = resolve_data_dir() / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "launcher-error.log").write_text(traceback_text, encoding="utf-8")
    except Exception:
        import tempfile

        Path(tempfile.gettempdir(), "educore-launcher-error.log").write_text(
            traceback_text, encoding="utf-8"
        )


def _show_error(message: str) -> None:
    _log_crash(message)
    if os.environ.get("EDUCORE_NO_DIALOG") == "1":
        print(message, file=sys.stderr)
        return
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("EduCore 启动失败", message)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def run_server() -> int:
    import uvicorn

    from backend.api.app import create_app
    from backend.platform.paths import resolve_data_dir

    os.environ["EDUCORE_LAUNCHER"] = "1"
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    sentinel = data_dir / "runtime" / "shutdown.request"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    if sentinel.exists():
        sentinel.unlink()

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    config = uvicorn.Config(
        create_app(),
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="educore-server", daemon=True)
    thread.start()

    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    else:
        _show_error("本地服务启动超时，请查看数据目录下的 logs/educore.log。")
        return 1

    try:
        if os.environ.get("EDUCORE_NO_BROWSER") != "1":
            webbrowser.open(url)
    except Exception:
        pass

    try:
        while not server.should_exit:
            if sentinel.exists():
                sentinel.unlink(missing_ok=True)
                server.should_exit = True
                break
            time.sleep(0.4)
    except KeyboardInterrupt:
        server.should_exit = True
    finally:
        thread.join(timeout=15)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--pick-path":
        from backend.platform.dialogs import main as picker_main

        return picker_main(args[1:])
    try:
        return run_server()
    except Exception:
        _show_error(traceback.format_exc()[-1500:])
        return 1


if __name__ == "__main__":
    sys.exit(main())