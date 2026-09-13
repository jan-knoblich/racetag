"""Collect the imports of the backend and reader-service sources for PyInstaller.

The desktop shell loads the backend's ``app.py`` by file path at runtime and
runs the reader-service from the bundled ``reader_src`` directory. Neither is
imported by name from the frozen entry point, so PyInstaller's static analysis
never sees their ``import`` statements. Every third-party or stdlib module they
use must therefore be listed as a hidden import, and a hand-maintained list
drifts: the 0.2.0 release self-test failed on a Windows bundle without
``fastapi.middleware.cors``, which only the macOS spec listed.

This helper parses the sources with ``ast`` and returns every imported module
that is importable in the build environment and is not one of the sources' own
modules. Candidates that cannot be resolved (platform-specific modules such as
``msvcrt`` on macOS, or ``from x import name`` where ``name`` is not a module)
are skipped, so the result never produces PyInstaller "Hidden import not found"
errors, which release.yml treats as a failed build.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Iterable, List, Set

_SKIP_DIRS = {"tests", "__pycache__"}


def _source_files(src_dir: Path) -> Iterable[Path]:
    for path in sorted(src_dir.rglob("*.py")):
        if _SKIP_DIRS.intersection(path.relative_to(src_dir).parts):
            continue
        yield path


def local_top_level_names(src_dir: Path) -> Set[str]:
    """Top-level module and package names that live inside *src_dir*."""
    names = {p.stem for p in src_dir.glob("*.py")}
    names.update(
        p.name for p in src_dir.iterdir()
        if p.is_dir() and p.name not in _SKIP_DIRS and any(p.glob("*.py"))
    )
    return names


def imported_names(src_dir: Path) -> Set[str]:
    """Every absolute module name imported anywhere in *src_dir*.

    ``from a.b import c`` contributes both ``a.b`` and ``a.b.c``; the second
    is only a candidate and is dropped later if ``c`` is not a module.
    """
    names: Set[str] = set()
    for path in _source_files(src_dir):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module)
                names.update(f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*")
    return names


def _is_importable_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:  # noqa: BLE001 - parent is not a package, import error, etc.
        return False


def external_imports(src_dirs: Iterable[Path]) -> List[str]:
    """Sorted importable modules used by *src_dirs*, excluding their own modules."""
    src_dirs = [Path(d) for d in src_dirs]
    local: Set[str] = set()
    candidates: Set[str] = set()
    for src_dir in src_dirs:
        local |= local_top_level_names(src_dir)
        candidates |= imported_names(src_dir)
    return sorted(
        name for name in candidates
        if name.split(".", 1)[0] not in local and _is_importable_module(name)
    )
