"""Every runtime file the package needs is declared for the wheel.

An editable install reads the source tree, so a missing `package-data` glob is
invisible to every local run and every test. It surfaces the first time someone
builds a wheel, as an exception at import:

    RuntimeError: Directory '.../site-packages/eve/web/static' does not exist

which is `mount_web` failing before the app object exists. That is what shipped:
`package-data` listed the classification lists and the Alembic revisions, and had
never learned about `web/templates` or `web/static`, so a deploy installed a
package with no web app in it.

Matching the globs is checked rather than building a wheel, so this stays fast
and needs no network.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "eve"

#: Suffixes Python itself installs, so they need no glob.
CODE = {".py"}
#: Never shipped, never wanted.
IGNORED_DIRS = {"__pycache__", ".pytest_cache"}


def _declared_globs() -> list[str]:
    """The `package-data` patterns, read out of pyproject.toml.

    Parsed rather than imported: tomllib is 3.11+, and this package supports 3.9.
    """
    text = (ROOT / "pyproject.toml").read_text()
    block = re.search(
        r"\[tool\.setuptools\.package-data\]\s*\neve\s*=\s*\[(.*?)\]", text, re.S
    )
    assert block, "package-data section not found in pyproject.toml"
    return re.findall(r'"([^"]+)"', block.group(1))


def _runtime_files() -> list[str]:
    """Every non-code file under the package, relative to it."""
    out = []
    for p in sorted(PKG.rglob("*")):
        if not p.is_file() or p.suffix in CODE:
            continue
        if any(part in IGNORED_DIRS for part in p.parts):
            continue
        out.append(p.relative_to(PKG).as_posix())
    return out


@pytest.mark.parametrize("relative_path", _runtime_files())
def test_every_runtime_file_is_declared_for_the_wheel(relative_path: str) -> None:
    globs = _declared_globs()
    assert any(fnmatch.fnmatch(relative_path, g) for g in globs), (
        f"{relative_path} ships in the source tree but no package-data glob "
        f"matches it, so a wheel would not contain it. Add one to "
        f"[tool.setuptools.package-data] in pyproject.toml. Declared: {globs}"
    )


def test_the_web_app_files_are_declared() -> None:
    """The specific gap that broke a deploy, named so it cannot come back."""
    globs = _declared_globs()
    for path in ("web/templates/base.html", "web/templates/landing.html",
                 "web/static/app.js", "web/static/InterVariable.woff2"):
        assert any(fnmatch.fnmatch(path, g) for g in globs), f"{path} is undeclared"


def test_a_read_only_disk_names_the_setting_that_fixes_it(tmp_path) -> None:
    """The crash a serverless deploy actually hits, and what it should say.

    With no database configured the engine keeps its workspace in a SQLite file,
    so a read-only filesystem fails inside `os.makedirs` with a bare
    PermissionError that names no setting. Falling back to a temp directory
    would be worse: the database would vanish between invocations and the app
    would look like it was losing data rather than misconfigured.
    """
    import os

    import pytest

    from eve.addresses import default_db_url
    from eve.config import Settings, set_settings
    from eve.storage import LocalObjectStore

    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    try:
        set_settings(Settings(local_storage_dir=str(locked / "workspace")))
        with pytest.raises(RuntimeError, match="EVE_WORKSPACE_DB_URL"):
            default_db_url()
        with pytest.raises(RuntimeError, match="EVE_S3_BUCKET"):
            LocalObjectStore(locked / "objects")
    finally:
        os.chmod(locked, 0o700)
        set_settings(None)
