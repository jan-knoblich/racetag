"""pyinstaller_source_imports: hidden imports collected from the bundled
backend and reader-service sources (they are loaded by path at runtime, so
PyInstaller's static analysis never follows their imports)."""
import importlib.util
import sys
from pathlib import Path

import pyinstaller_source_imports as psi

_REPO = Path(__file__).resolve().parents[3]
_BACKEND = _REPO / "apps" / "backend" / "racetag-backend"
_READER = _REPO / "apps" / "reader-service" / "src"


def test_backend_middleware_import_is_collected():
    names = psi.external_imports([_BACKEND, _READER])
    # The module the 0.2.0 Windows bundle was missing.
    assert "fastapi.middleware.cors" in names
    assert "sqlite3" in names
    # Third-party modules are only collected when installed in the build
    # environment; the Linux desktop CI job does not install `requests`.
    if importlib.util.find_spec("requests") is not None:
        assert "requests" in names
    else:
        assert "requests" not in names


def test_local_modules_and_non_modules_are_excluded():
    names = psi.external_imports([_BACKEND, _READER])
    local = psi.local_top_level_names(_BACKEND) | psi.local_top_level_names(_READER)
    assert not [n for n in names if n.split(".", 1)[0] in local]
    # `from fastapi.middleware.cors import CORSMiddleware` must not add the class.
    assert "fastapi.middleware.cors.CORSMiddleware" not in names


def test_unresolvable_and_platform_specific_imports_are_skipped(tmp_path):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "__init__.py").write_text("")
    (src / "tests").mkdir()
    (src / "tests" / "test_x.py").write_text("import pytest_only_dependency_xyz\n")
    (src / "mod.py").write_text(
        "import json\n"
        "import pkg\n"
        "import definitely_not_installed_module_xyz\n"
        "from os.path import join\n"
        "if __import__('sys').platform == 'win32':\n"
        "    import msvcrt\n"
        "from . import sibling\n"
    )
    names = psi.external_imports([src])
    assert "json" in names
    assert "os.path" in names
    assert "pkg" not in names
    assert "definitely_not_installed_module_xyz" not in names
    assert "pytest_only_dependency_xyz" not in names
    assert "os.path.join" not in names
    assert ("msvcrt" in names) == (sys.platform == "win32")
