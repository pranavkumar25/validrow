"""ASGI entrypoint at the repo root, for platforms that import by file path.

The app lives at ``src/eve/api/main.py`` and is packaged as ``eve``. Vercel
resolves ``[tool.vercel] entrypoint`` against files in the repo, so it cannot
find ``eve.api.main`` (there is no ``eve/`` at the root) and its own suggestion,
``src.eve.api.main``, imports the module under a name its absolute imports do
not match. This file is the thing to point at instead.

Nothing here is Vercel-specific: any runner that wants ``module:object`` from
the repo root can use ``main:app``. Docker and the compose stack keep importing
``eve.api.main:app`` directly and never load this file.

The ``sys.path`` line is a fallback, not the normal route. When the project is
pip-installed, as in the Dockerfile, ``eve`` is already importable and the
insert is a no-op. It matters on a runner that installs the declared
dependencies but not the project itself, where ``src`` would otherwise be
invisible and the import below would fail.
"""
from __future__ import annotations

import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from eve.api.main import app  # noqa: E402  (must follow the path fallback)

__all__ = ["app"]
