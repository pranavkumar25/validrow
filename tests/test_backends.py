"""Backend selection.

Each of these subsystems has a single-process default and a shared production
backend. The bug this file guards against is the one the codebase already had:
the production class existing, and the selection that would reach it never
being written — so everything silently ran on the local default.
"""
from __future__ import annotations

import pytest

from eve.config import Settings, set_settings
from eve.jobs.queue import resolve_backend
from eve.kv import InMemoryKV, get_kv, set_kv
from eve.storage import LocalObjectStore, S3ObjectStore, get_object_store, set_object_store


def _reset_singletons():
    set_object_store(None)
    set_kv(None)


@pytest.fixture(autouse=True)
def clear(tmp_path):
    yield
    _reset_singletons()


def test_object_store_is_local_without_s3_settings(tmp_path):
    set_settings(Settings(local_storage_dir=str(tmp_path)))
    _reset_singletons()
    assert isinstance(get_object_store(), LocalObjectStore)


def test_object_store_switches_to_s3_when_configured(tmp_path):
    pytest.importorskip("aioboto3")
    set_settings(
        Settings(
            local_storage_dir=str(tmp_path),
            s3_bucket="eve-uploads",
            s3_access_key="key",
            s3_secret_key="secret",
        )
    )
    _reset_singletons()
    store = get_object_store()
    assert isinstance(store, S3ObjectStore)
    assert store.bucket == "eve-uploads"


def test_partial_s3_settings_do_not_count_as_configured(tmp_path):
    # A bucket with no credentials is a half-finished deploy, not an S3 setup.
    set_settings(Settings(local_storage_dir=str(tmp_path), s3_bucket="eve-uploads"))
    _reset_singletons()
    assert isinstance(get_object_store(), LocalObjectStore)


def test_kv_is_in_process_without_redis():
    set_settings(Settings())
    _reset_singletons()
    assert isinstance(get_kv(), InMemoryKV)


def test_kv_demands_the_extra_when_redis_is_configured(monkeypatch):
    set_settings(Settings(redis_url="redis://localhost:6379/0"))
    _reset_singletons()
    monkeypatch.setitem(__import__("sys").modules, "redis", None)
    # Silently falling back to in-process here would give every worker its own
    # rate-limit budget against the same provider.
    with pytest.raises(RuntimeError, match="redis"):
        get_kv()


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        (Settings(), "inline"),
        (Settings(queue_backend="inline", redis_url="redis://x"), "inline"),
        (Settings(queue_backend="arq"), "arq"),
        # auto + no Redis = nothing to queue onto.
        (Settings(queue_backend="auto"), "inline"),
    ],
)
def test_queue_backend_resolution(settings, expected):
    assert resolve_backend(settings) == expected


def test_settings_treat_blank_as_unconfigured():
    s = Settings()
    assert not s.s3_configured
    assert not s.redis_configured
    assert s.cors_origin_list == []

    s = Settings(cors_origins="https://a.com, https://b.com ,")
    assert s.cors_origin_list == ["https://a.com", "https://b.com"]
