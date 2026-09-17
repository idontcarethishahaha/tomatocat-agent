import asyncio

import pytest

from main import _make_channel_sender
from tomatocat.instance_lock import WorkspaceInstanceLock


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
