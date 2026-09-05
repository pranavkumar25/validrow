"""The container's contract with whatever runs it.

None of this can be caught by running the app: it lives in the Dockerfile and
the compose files, and the failure is a deploy that builds cleanly and then
never answers. These assertions are cheap and each one stands for a deploy that
went wrong.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text()
COMPOSE = (ROOT / "docker-compose.prod.yml").read_text()
CADDYFILE = (ROOT / "Caddyfile").read_text()


def test_the_container_binds_the_port_it_is_given() -> None:
    """A hardcoded port fails every platform that assigns one.

    Render, Fly, Cloud Run and Heroku set PORT and health-check that port. An
    image listening on 8000 regardless reports as "no open ports detected",
    which looks like a crash and is not one.
    """
    cmd = re.search(r"^CMD (.+)$", DOCKERFILE, re.M)
    assert cmd, "Dockerfile has no CMD"
    assert "${PORT:-8000}" in cmd.group(1), f"CMD does not honour PORT: {cmd.group(1)}"


def test_the_container_listens_on_all_interfaces() -> None:
    """uvicorn defaults to 127.0.0.1, which is unreachable from outside."""
    assert "--host 0.0.0.0" in DOCKERFILE


def test_the_default_port_matches_what_caddy_proxies_to() -> None:
    """Caddy names the upstream port; the image's default has to agree."""
    upstream = re.search(r"reverse_proxy\s+api:(\d+)", CADDYFILE)
    assert upstream, "Caddyfile has no reverse_proxy to api"
    assert f"${{PORT:-{upstream.group(1)}}}" in DOCKERFILE
    assert f'expose: ["{upstream.group(1)}"]' in COMPOSE


def test_signals_reach_uvicorn() -> None:
    """Shutdown stops three background tasks, so SIGTERM must not be swallowed.

    `sh -c "uvicorn ..."` leaves the shell as PID 1 and uvicorn as its child;
    the shell does not forward the signal, so the lifespan's `finally` never
    runs. `exec` replaces the shell.
    """
    assert 'exec uvicorn' in DOCKERFILE


def test_the_image_does_not_run_as_root() -> None:
    assert re.search(r"^USER app$", DOCKERFILE, re.M), "no USER directive"
    # The one directory the app writes to has to belong to that user.
    assert "chown -R app:app /app" in DOCKERFILE


def test_the_build_context_excludes_the_virtualenv_and_git() -> None:
    """Without .dockerignore the daemon receives ~140 MB it never reads."""
    ignored = (ROOT / ".dockerignore").read_text().split()
    for path in (".venv/", ".git/", ".eve_storage/", "__pycache__/"):
        assert path in ignored, f"{path} is not in .dockerignore"


def test_secrets_cannot_be_baked_into_a_layer() -> None:
    ignored = (ROOT / ".dockerignore").read_text().split()
    assert ".env" in ignored and ".env.*" in ignored


def test_alembic_can_run_inside_the_image() -> None:
    """`alembic upgrade head` needs its ini file and a sync driver.

    env.py swaps "+asyncpg" for "+psycopg2" when Alembic opens its own
    connection, so the driver it swaps to has to be installed.
    """
    assert "COPY alembic.ini" in DOCKERFILE
    pyproject = (ROOT / "pyproject.toml").read_text()
    extra = re.search(r"^postgres = \[(.*?)\]", pyproject, re.M | re.S)
    assert extra and "psycopg2" in extra.group(1), "postgres extra has no sync driver"
    env_py = (ROOT / "src" / "eve" / "migrations" / "env.py").read_text()
    assert '"+psycopg2"' in env_py, "env.py no longer swaps to psycopg2; update the extra"


def test_the_declared_entrypoint_resolves_to_a_real_file() -> None:
    """`[tool.vercel] entrypoint` is resolved against files, not modules.

    "eve.api.main:app" is a valid import and an invalid entrypoint: the package
    is under src/, so there is no eve/api/main.py at the root for a path-based
    resolver to find. This asserts the declared module is a file that exists.
    """
    pyproject = (ROOT / "pyproject.toml").read_text()
    declared = re.search(r'^\[tool\.vercel\][^\[]*?entrypoint\s*=\s*"([^"]+)"',
                         pyproject, re.M | re.S)
    assert declared, "no [tool.vercel] entrypoint declared"
    module, _, obj = declared.group(1).partition(":")
    assert obj, f"entrypoint {declared.group(1)!r} is not in module:object form"
    path = ROOT.joinpath(*module.split(".")).with_suffix(".py")
    assert path.is_file(), f"entrypoint names {module}, but {path} does not exist"
    assert re.search(rf"^\s*(from .* import .*\b{obj}\b|{obj}\s*=)", path.read_text(), re.M), (
        f"{path.name} does not define or import {obj!r}"
    )


def test_the_root_entrypoint_serves_the_same_app() -> None:
    """The shim must re-export the app, not build a second one."""
    import main

    from eve.api.main import app as packaged

    assert main.app is packaged


def test_the_root_entrypoint_works_without_the_package_installed() -> None:
    """Its whole reason to exist: a runner that installs deps but not the project.

    The src fallback is what makes that case work, and it is a no-op when the
    package is installed, so nothing here proves itself at runtime.
    """
    shim = (ROOT / "main.py").read_text()
    assert 'parent / "src"' in shim, "the src fallback is gone"
    assert "sys.path.insert" in shim


def test_requirements_txt_names_only_real_extras() -> None:
    """It defers to pyproject's extras so the two cannot drift apart."""
    req = (ROOT / "requirements.txt").read_text()
    named = re.search(r"^\s*\.\[([^\]]+)\]", req, re.M)
    assert named, "requirements.txt does not install the project with extras"
    pyproject = (ROOT / "pyproject.toml").read_text()
    declared = set(re.findall(r"^(\w+) = \[", pyproject, re.M))
    for extra in (e.strip() for e in named.group(1).split(",")):
        assert extra in declared, f"requirements.txt asks for extra {extra!r}, which pyproject has no"


def test_the_production_backends_are_all_installable_from_requirements() -> None:
    """The three a shared deployment needs, none of which are base dependencies."""
    req = (ROOT / "requirements.txt").read_text()
    for extra in ("postgres", "s3", "redis"):
        assert extra in req, f"{extra} is missing; a deploy would crash on its import"
