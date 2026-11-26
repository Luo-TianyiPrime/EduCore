# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for EduCore (Windows / macOS).

Playwright is intentionally excluded from the bundle: PDF export falls back to
the installed Edge/Chrome via ``--print-to-pdf``.  Development builds keep using
Playwright when available.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "prompts"), "prompts"),
    (str(ROOT / "templates"), "templates"),
]
binaries = []
for distribution in (
    "genai-prices",
    "pydantic-ai-slim",
    "pydantic-graph",
    "openai",
    "tiktoken",
    "lancedb",
    "pyarrow",
    "jieba",
    "pypdfium2",
    "pdfplumber",
    "python-docx",
    "docxtpl",
    "markdown-it-py",
    "fastapi",
    "uvicorn",
    "pydantic",
):
    try:
        datas += copy_metadata(distribution)
    except Exception as exc:  # pragma: no cover - metadata optional
        print(f"[spec] skip metadata {distribution}: {exc}")

hiddenimports = [
    "backend.api.app",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.null",
]

for package in (
    "jieba",
    "lancedb",
    "pyarrow",
    "pypdfium2",
    "pdfplumber",
    "docx",
    "docxtpl",
    "markdown_it",
    "mdit_py_plugins",
    "pydantic_ai",
    "pydantic_graph",
    "openai",
    "tiktoken",
):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
    except Exception as exc:  # pragma: no cover - optional package
        print(f"[spec] skip {package}: {exc}")
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

a = Analysis(
    [str(ROOT / "launcher" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "playwright",
        "torch",
        "matplotlib",
        "notebook",
        "IPython",
        "pytest",
        "tkinter.test",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EduCore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EduCore",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="EduCore.app",
        icon=None,
        bundle_identifier="local.educore.app",
        info_plist={
            "CFBundleName": "EduCore",
            "CFBundleDisplayName": "EduCore",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )