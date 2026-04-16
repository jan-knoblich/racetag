# -*- mode: python ; coding: utf-8 -*-
# pyinstaller.win.spec — Windows .exe bundle for Racetag (W-072)
#
# Build command (run from apps/desktop/ on a Windows machine):
#   pip install pyinstaller
#   python generate_win_version_info.py
#   pyinstaller pyinstaller.win.spec --clean
#
# Output:
#   One-directory (faster startup):  apps/desktop/dist/Racetag/Racetag.exe
#   One-file alternative:            add --onefile to the pyinstaller command.
#
# Windows builds happen via CI on windows-latest runners (W-075).
# Do NOT attempt to run this spec on macOS.

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve paths relative to this spec file (spec is in apps/desktop/).
# ---------------------------------------------------------------------------
SPEC_DIR = Path(SPECPATH)            # apps/desktop/
REPO_ROOT = SPEC_DIR.parent.parent   # monorepo root

# Read version from VERSION file.
VERSION = (SPEC_DIR / "VERSION").read_text().strip()

# ---------------------------------------------------------------------------
# Data files bundled into the exe
# ---------------------------------------------------------------------------
datas = [
    # Frontend static assets — served by FastAPI StaticFiles at runtime.
    (str(REPO_ROOT / "apps" / "frontend"), "frontend"),

    # Backend Python source — loaded by _build_combined_app() via importlib.
    # In frozen mode app.py resolves BACKEND_SRC to _MEIPASS/backend_src.
    (str(REPO_ROOT / "apps" / "backend" / "racetag-backend"), "backend_src"),

    # Reader-service Python source — used by the --reader-service dispatch.
    # In frozen mode app.py resolves the path to _MEIPASS/reader_src.
    (str(REPO_ROOT / "apps" / "reader-service" / "src"), "reader_src"),
]

# ---------------------------------------------------------------------------
# Hidden imports (same as mac spec — keep in sync)
# ---------------------------------------------------------------------------
hiddenimports = [
    # Reader-service modules.
    "racetag_reader_service",
    "sirit_client",
    "tag_tracker",
    "session_state",
    "utils",
    "backend_client",
    "backend_client.http",
    "backend_client.mock",
    "backend_client.base",
    "models",
    # Backend modules.
    "racetag_backend_app",
    "storage",
    "models_api",
    "domain",
    # FastAPI / Starlette.
    "fastapi",
    "fastapi.staticfiles",
    "fastapi.responses",
    "starlette.middleware.cors",
    "starlette.routing",
    "starlette.staticfiles",
    # Pydantic v2.
    "pydantic",
    "pydantic.deprecated.class_validators",
    "pydantic_core",
    # uvicorn.
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # pywebview Windows backend (WebView2).
    "webview",
    "webview.platforms.edgechromium",
    # multiprocessing freeze support — required on Windows.
    "multiprocessing",
    "multiprocessing.freeze_support",
]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    [str(SPEC_DIR / "app.py")],
    pathex=[
        str(SPEC_DIR),
        str(REPO_ROOT / "apps" / "backend" / "racetag-backend"),
        str(REPO_ROOT / "apps" / "reader-service" / "src"),
    ],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
        # macOS-specific webview backend.
        "webview.platforms.cocoa",
        "webview.platforms.gtk",
        "webview.platforms.qt",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# ---------------------------------------------------------------------------
# One-directory build (default — faster startup than --onefile).
# For one-file, replace the two EXE/COLLECT blocks below with a single EXE
# that has all binaries/datas embedded (splash=SplashScreen(...) recommended
# to mask the unpack delay).
# ---------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Racetag",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,              # windowed — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,       # Windows does not need argv_emulation
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SPEC_DIR / "icons" / "racetag.ico"),
    version=str(SPEC_DIR / "win_version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Racetag",
)
