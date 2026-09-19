import asyncio
import os

import pytest

from main import _make_channel_sender
from tomatocat.instance_lock import WorkspaceInstanceLock
from tomatocat.network import configure_tun_routing


def test_missing_channel_is_delivery_failure():
    sender = _make_channel_sender([])
    with pytest.raises(LookupError, match="telegram"):
        asyncio.run(sender("telegram", "123", "hello"))


def test_workspace_lock_rejects_second_instance_and_can_be_reused(tmp_path):
    first = WorkspaceInstanceLock(tmp_path / ".tomatocat.lock")
    second = WorkspaceInstanceLock(tmp_path / ".tomatocat.lock")
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="another TomatoCat instance"):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_tun_routing_removes_explicit_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7897")

    removed = configure_tun_routing()

    assert {"HTTP_PROXY", "https_proxy"}.issubset(removed)
    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "FTP_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "ftp_proxy",
    ):
        assert key not in os.environ
    assert os.environ["NO_PROXY"] == "*"
    assert os.environ["no_proxy"] == "*"
