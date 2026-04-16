# -*- mode: python ; coding: utf-8 -*-
# pyinstaller.mac.spec — macOS .app bundle for Racetag (W-071)
#
# Build command (run from apps/desktop/):
#   pip install pyinstaller
#   pyinstaller pyinstaller.mac.spec --clean
#
# Output: apps/desktop/dist/Racetag.app
#
# Requires PyInstaller >= 6.x.  Tested on macOS 14+ (arm64 and x86_64).

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve paths relative to this spec file (spec is in apps/desktop/).
# ---------------------------------------------------------------------------
SPEC_DIR = Path(SPECPATH)            # apps/desktop/
REPO_ROOT = SPEC_DIR.parent.parent   # monorepo root

# Read version from VERSION file (single source of truth).
VERSION = (SPEC_DIR / "VERSION").read_text().strip()
VERSION_TUPLE = tuple(int(x) for x in VERSION.split("."))

# ---------------------------------------------------------------------------
# Data files bundled into the .app
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
# Hidden imports
# ---------------------------------------------------------------------------
# PyInstaller's static analyser misses modules that are imported dynamically
# (e.g. via importlib) or that are loaded only in frozen mode.
hiddenimports = [
    # Reader-service modules (loaded via --reader-service dispatch path).
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
    # Backend modules (loaded via importlib.util in _build_combined_app).
    "racetag_backend_app",
    "storage",
    "models_api",
    "domain",
    # FastAPI / Starlette internals that are often missed.
    "fastapi",
    "fastapi.staticfiles",
    "fastapi.responses",
    "fastapi.middleware",
    "fastapi.middleware.cors",
    "fastapi.security",
    "starlette.middleware",
    "starlette.middleware.cors",
    "starlette.routing",
    "starlette.staticfiles",
    "starlette.responses",
    # Pydantic v2 internals.
    "pydantic",
    "pydantic.deprecated.class_validators",
    "pydantic_core",
    # uvicorn internals.
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
    # pywebview macOS backend.
    "webview",
    "webview.platforms.cocoa",
    # multiprocessing support (needed by PyInstaller on macOS).
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
        # Exclude heavy packages we definitely don't ship.
        "tkinter",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

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
    console=False,          # windowed app — no Terminal window
    disable_windowed_traceback=False,
    argv_emulation=True,    # macOS: handle Apple Event open-file on startup
    target_arch=None,       # None = native arch; set to 'universal2' for fat binary
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SPEC_DIR / "icons" / "racetag.icns"),
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

# ---------------------------------------------------------------------------
# macOS .app bundle (BUNDLE)
# ---------------------------------------------------------------------------
app = BUNDLE(
    coll,
    name="Racetag.app",
    icon=str(SPEC_DIR / "icons" / "racetag.icns"),
    bundle_identifier="com.racetag.app",
    version=VERSION,
    info_plist={
        # Human-readable product name.
        "CFBundleName": "Racetag",
        "CFBundleDisplayName": "Racetag",
        # Version strings — both must be set for Finder "Get Info".
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        # macOS requirement: UTI for the document types supported.
        "LSMinimumSystemVersion": "12.0",
        # Hide from Dock when running as a helper (set False for main app).
        "LSUIElement": False,
        # Suppress the Python runtime warning from Gatekeeper.
        "NSHighResolutionCapable": True,
        # Required for WKWebView local-network access (even loopback).
        "NSLocalNetworkUsageDescription": (
            "Racetag uses a local network connection to communicate with "
            "the RFID reader on the LAN."
        ),
        "com.apple.security.network.client": True,
    },
)
