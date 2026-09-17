import asyncio
from pathlib import Path

from tomatocat.agent.background.subagent_manager import SubAgentToolWrapper, SubagentManager
from tomatocat.task_store import TaskStore


class ToolInfo:
    name = "read_file"
    description = "read"


class PluginManager:
    async def execute_tool(self, _name, kwargs):
        return kwargs["path"]


def test_file_tool_blocks_relative_path_traversal(tmp_path):
    task_dir = tmp_path / "subagent-runs" / "job"
    task_dir.mkdir(parents=True)
    wrapper = SubAgentToolWrapper(ToolInfo(), PluginManager(), task_dir)
    rewritten = wrapper._rewrite_file_paths({"path": "../../config.toml"})
    assert Path(rewritten["path"]).parent == task_dir.resolve()


def test_completion_tracks_event_and_delivery(tmp_path):
    events = []
    sent = []

    class Bus:
        def enqueue(self, event):
            events.append(event)

    async def send(*args):
        sent.append(args)

    store = TaskStore(tmp_path / "tasks.db")
    store.create("job", "subagent", status="running")
    manager = SubagentManager(
        provider=object(), workspace=tmp_path, event_bus=Bus(), model="test",
        max_tokens=10, send_fn=send, task_store=store,
    )
    asyncio.run(manager._announce_result(
        job_id="job", label="label", task="task", origin_channel="telegram",
        origin_chat_id="123", status="completed", exit_reason="completed",
        result="done", profile="research", retry_count=0,
    ))
    record = store.get("job")
    assert record.status == "completed"
    assert record.delivery_status == "delivered"
    assert len(events) == 1
    assert len(sent) == 1


def test_pending_delivery_is_resumed_but_uncertain_send_is_not(tmp_path):
    sent = []

    async def send(*args):
        sent.append(args)

    store = TaskStore(tmp_path / "tasks.db")
    store.create(
        "pending", "subagent", status="running",
        origin_channel="telegram", origin_chat_id="123",
    )
    store.finish_and_queue_delivery(
        "pending", "completed", result="done", delivery_message="pending message",
    )
    store.create(
        "uncertain", "subagent", status="running",
        origin_channel="telegram", origin_chat_id="123",
    )
    store.finish_and_queue_delivery(
        "uncertain", "completed", result="done", delivery_message="uncertain message",
    )
    assert store.claim_delivery("uncertain").delivery_status == "sending"

    manager = SubagentManager(
        provider=object(), workspace=tmp_path, event_bus=None, model="test",
        max_tokens=10, send_fn=send, task_store=store,
    )
    assert asyncio.run(manager.resume_pending_deliveries()) == 1
    assert [args[2] for args in sent] == ["pending message"]
    assert store.get("pending").delivery_status == "delivered"
    assert store.get("uncertain").delivery_status == "sending"


def test_tool_wrapper_times_out(monkeypatch, tmp_path):
    import tomatocat.agent.background.subagent_manager as module

    class SlowPluginManager:
        async def execute_tool(self, _name, _kwargs):
            await asyncio.sleep(0.05)
            return "done"

    monkeypatch.setattr(module, "_DEFAULT_TOOL_TIMEOUT_SECONDS", 0.01)
    wrapper = SubAgentToolWrapper(ToolInfo(), SlowPluginManager(), tmp_path)
    result = asyncio.run(wrapper.execute(path="file.txt"))
    assert "工具执行超时" in result


def test_retry_accepts_legacy_plain_text_payload(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    store.create("legacy", "subagent", payload="old plain task", status="retry_wait", retry_policy="safe")
    manager = SubagentManager(
        provider=object(), workspace=tmp_path, event_bus=None, model="test",
        max_tokens=10, task_store=store,
    )
    received = {}

    async def fake_run_subagent(**kwargs):
        received.update(kwargs)

    manager._run_subagent = fake_run_subagent

    async def run_retry():
        task = await manager.retry_job("legacy")
        assert task is not None
        await task

    asyncio.run(run_retry())
    assert received["task"] == "old plain task"


def test_retry_registers_and_cleans_up_running_job(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    store.create("retry-job", "subagent", payload='{"task":"retry me"}', status="retry_wait", retry_policy="safe")
    manager = SubagentManager(
        provider=object(), workspace=tmp_path, event_bus=None, model="test",
        max_tokens=10, task_store=store,
    )

    async def run_retry():
        release = asyncio.Event()

        async def fake_run_subagent(**_kwargs):
            await release.wait()

        manager._run_subagent = fake_run_subagent
        task = await manager.retry_job("retry-job")
        assert task is not None
        assert manager.get_running_count() == 1
        assert manager.list_running_jobs()[0]["job_id"] == "retry-job"
        release.set()
        await task
        await asyncio.sleep(0)
        assert manager.get_running_count() == 0
        assert manager.list_running_jobs() == []

    asyncio.run(run_retry())


def test_stop_cancels_jobs_and_leaves_them_retryable(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    store.create("running-job", "subagent", status="running")
    manager = SubagentManager(
        provider=object(), workspace=tmp_path, event_bus=None, model="test",
        max_tokens=10, task_store=store,
    )

    class BlockingSubagent:
        async def run(self, _task):
            await asyncio.Event().wait()

    manager._build_subagent = lambda **_kwargs: BlockingSubagent()

    async def scenario():
        task = asyncio.create_task(manager._run_subagent(
            job_id="running-job",
            task="work",
            label="work",
            task_dir=tmp_path,
            origin_channel="",
            origin_chat_id="",
            profile="research",
            retry_count=0,
        ))
        manager._register_running_job(
            job_id="running-job",
            bg_task=task,
            label="work",
            task="work",
            task_dir=tmp_path,
            profile="research",
            origin_channel="",
            origin_chat_id="",
            retry_count=0,
        )
        await asyncio.sleep(0)
        await manager.stop()
        await asyncio.sleep(0)
        assert task.cancelled()
        assert manager.get_running_count() == 0

    asyncio.run(scenario())
    assert store.get("running-job").status == "retry_wait"
