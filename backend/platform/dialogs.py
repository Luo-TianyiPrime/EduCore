"""Native file/folder picker helper.

Runs in a separate process because Tk must own the main thread, and because a
sandboxed browser cannot hand a real local path to the backend.  The API spawns
this module (or the packaged executable with ``--pick-path``) and returns the
chosen absolute path.
"""

from __future__ import annotations

import contextlib
import sys


def pick(mode: str) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None

    root = tk.Tk()
    root.withdraw()
    with contextlib.suppress(Exception):
        root.attributes("-topmost", True)
    try:
        if mode == "file":
            path = filedialog.askopenfilename(
                title="选择资料文件",
                filetypes=[
                    ("支持的资料", "*.pdf *.md *.markdown *.json *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"),
                    ("所有文件", "*.*"),
                ],
            )
        else:
            path = filedialog.askdirectory(title="选择资料文件夹", mustexist=True)
    finally:
        with contextlib.suppress(Exception):
            root.destroy()
    return path or None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args else "directory"
    if mode not in {"directory", "file"}:
        mode = "directory"
    path = pick(mode)
    if path:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())