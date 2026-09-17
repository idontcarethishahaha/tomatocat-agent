from tomatocat.task_store import TaskStore


def test_task_store_state_transition_and_recovery(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    store.create("job-1", "subagent", payload="research", status="queued")
    assert store.update("job-1", "running", expected_status="queued")
    assert not store.update("job-1", "completed", expected_status="queued")
    assert store.update("job-1", "completed", result="done", expected_status="running")
    assert store.get("job-1").result == "done"
    assert store.update_delivery("job-1", "pending")
    assert store.update_delivery("job-1", "failed", error="offline")
    assert store.get("job-1").delivery_error == "offline"
    store.create("job-2", "subagent", status="running")
    assert [j.job_id for j in store.list_unfinished()] == ["job-2"]
    assert store.mark_interrupted() == 1
    assert store.get("job-2").status == "retry_wait"
    store.create("sched-1", "scheduler", status="running")
    store.mark_interrupted()
    assert store.get("sched-1").status == "failed"


def test_claim_retry_is_atomic_and_respects_limit(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    store.create("job", "subagent", payload='{"task":"x"}', status="retry_wait", retry_count=0, max_retries=1, retry_policy="safe")
    claimed = store.claim_retry("job")
    assert claimed is not None and claimed.retry_count == 1 and claimed.status == "queued"
    assert store.claim_retry("job") is None


def test_retry_policy_requires_confirmation_for_side_effects(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    store.create("confirm", "subagent", status="retry_wait", retry_policy="confirm")
    assert store.claim_retry("confirm") is None
    claimed = store.claim_retry("confirm", allow_side_effects=True)
    assert claimed is not None
    assert claimed.retry_count == 1


def test_structured_subagent_metadata_is_stored(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    record = store.create(
        "job", "subagent", label="research", profile="research",
        origin_channel="telegram", origin_chat_id="123",
    )
    assert record.label == "research"
    assert record.profile == "research"
    assert record.origin_channel == "telegram"
    assert record.origin_chat_id == "123"


def test_finish_and_delivery_claim_are_atomic(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    store.create("job", "subagent", status="running")
    assert store.finish_and_queue_delivery(
        "job", "completed", result="done", delivery_message="notify",
    )
    record = store.get("job")
    assert record.status == "completed"
    assert record.delivery_status == "pending"
    assert record.delivery_message == "notify"
    claimed = store.claim_delivery("job")
    assert claimed is not None and claimed.delivery_status == "sending"
    assert store.claim_delivery("job") is None
    assert not store.finish_and_queue_delivery(
        "job", "completed", result="duplicate", delivery_message="duplicate notification",
    )
    assert store.get("job").delivery_message == "notify"
