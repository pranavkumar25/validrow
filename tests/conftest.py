"""Shared fixtures. Resets process-wide singletons between tests so state from
one test never leaks into the next."""
from __future__ import annotations

import csv
import io

import pytest

from eve.config import Settings, set_settings
from eve.jobs.pipeline import NullAsyncProber
from eve.jobs.store import InMemoryJobStore, set_job_store
from eve.kv import InMemoryKV, set_kv
from eve.layers import dns_mx
from eve.smtp_infra import set_async_prober
from eve.storage import LocalObjectStore, set_object_store
from eve.tenancy import set_current_workspace_id


@pytest.fixture(autouse=True)
def reset_state(tmp_path):
    set_settings(Settings())
    dns_mx.clear_cache()
    set_object_store(LocalObjectStore(tmp_path / "store"))
    set_job_store(InMemoryJobStore())
    set_kv(InMemoryKV())
    set_async_prober(NullAsyncProber())
    # The workspace is a ContextVar, so it would otherwise leak into the next
    # test and make an isolation failure look like a passing test.
    set_current_workspace_id(None)
    yield


def parse_csv(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
