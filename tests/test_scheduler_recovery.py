import asyncio
from datetime import datetime, timedelta, timezone

from tomatocat.scheduler import ScheduledJob, SchedulerService
from tomatocat.task_store import TaskStore


def test_failed_periodic_job_can_run_again(tmp_path):
    attempts = 0

    async def send(*_args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("first run failed")

    store = TaskStore(tmp_path / "tasks.db")
    scheduler = SchedulerService(tmp_path / "jobs.json", send, task_store=store)
    job = ScheduledJob(
        trigger="every", mode="instant",
        fire_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        channel="telegram", chat_id="1", interval_seconds=60, message="hello",
    )
    scheduler._jobs[job.id] = job

    async def run_twice():
        for _ in range(2):
            scheduler._in_flight.add(job.id)
            await scheduler._execute_and_reschedule(job)

    asyncio.run(run_twice())
    records = [record for record in store.list_all() if record.task_type == "scheduler"]
    assert attempts == 2
    assert job.run_count == 1
    assert job.id not in scheduler._in_flight
    assert len({record.job_id for record in records}) == 2
    assert {record.status for record in records} == {"failed", "completed"}


def test_stop_cancels_and_waits_for_in_flight_jobs(tmp_path):
    started = asyncio.Event()

    async def send(*_args):
        started.set()
        await asyncio.Event().wait()

    async def scenario():
        store = TaskStore(tmp_path / "tasks.db")
        scheduler = SchedulerService(tmp_path / "jobs.json", send, task_store=store)
        job = ScheduledJob(
            trigger="at", mode="instant",
            fire_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            channel="telegram", chat_id="1", message="hello",
        )
        scheduler._jobs[job.id] = job
        await scheduler._tick()
        await started.wait()
        assert scheduler._execution_tasks
        await scheduler.stop()
        assert not scheduler._execution_tasks
        assert job.id not in scheduler._in_flight
        assert store.list_all()[0].status == "failed"

    asyncio.run(scenario())
