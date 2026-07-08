"""Checkpoint mechanism for task state saving and resumption."""
import uuid
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

CHECKPOINT_SCHEMA_VERSION = "v1"
CHECKPOINT_NONE_STATUS = "no-checkpoint"
CHECKPOINT_VALID_STATUS = "valid"
CHECKPOINT_STALE_STATUS = "stale"


def now() -> str:
    return datetime.now().isoformat()


class CheckpointManager:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.checkpoints_dir = workspace / "checkpoints"
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = self.checkpoints_dir / "checkpoints.json"
        self._state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if self._state_file.exists():
            try:
                return json.loads(self._state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"items": {}, "current_id": ""}

    def _save_state(self) -> None:
        self._state_file.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def create(
        self,
        task_type: str,
        task_id: str,
        current_goal: str,
        completed: List[str] = None,
        next_step: str = "",
        blocker: str = "",
        metadata: Dict[str, Any] = None,
        trigger: str = "manual"
    ) -> Dict[str, Any]:
        checkpoint_id = f"ckpt_{uuid.uuid4().hex[:8]}"
        parent_id = self._state.get("current_id", "")

        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": parent_id,
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "created_at": now(),
            "task_type": task_type,
            "task_id": task_id,
            "current_goal": current_goal,
            "completed": completed or [],
            "next_step": next_step,
            "blocker": blocker,
            "metadata": metadata or {},
            "trigger": trigger,
            "status": "active"
        }

        self._state["items"][checkpoint_id] = checkpoint
        self._state["current_id"] = checkpoint_id
        self._save_state()
        return checkpoint

    def get(self, checkpoint_id: str) -> Optional[Dict[str, Any]]:
        return self._state.get("items", {}).get(checkpoint_id)

    def get_current(self) -> Optional[Dict[str, Any]]:
        current_id = self._state.get("current_id", "")
        if not current_id:
            return None
        return self.get(current_id)

    def get_by_task(self, task_type: str, task_id: str) -> List[Dict[str, Any]]:
        items = self._state.get("items", {}).values()
        return [
            item for item in items
            if item.get("task_type") == task_type
            and item.get("task_id") == task_id
        ]

    def evaluate_resume(self, task_type: str, task_id: str) -> Dict[str, Any]:
        checkpoints = self.get_by_task(task_type, task_id)
        if not checkpoints:
            return {"status": CHECKPOINT_NONE_STATUS, "checkpoint": None}

        latest = max(checkpoints, key=lambda x: x.get("created_at", ""))

        if latest.get("status") == "completed":
            return {"status": CHECKPOINT_NONE_STATUS, "checkpoint": None}

        age_hours = (datetime.now() - datetime.fromisoformat(latest["created_at"])).total_seconds() / 3600
        if age_hours > 24:
            return {"status": CHECKPOINT_STALE_STATUS, "checkpoint": latest}

        return {"status": CHECKPOINT_VALID_STATUS, "checkpoint": latest}

    def mark_completed(self, checkpoint_id: str, final_result: str = "") -> None:
        checkpoint = self.get(checkpoint_id)
        if checkpoint:
            checkpoint["status"] = "completed"
            checkpoint["final_result"] = final_result
            checkpoint["completed_at"] = now()
            self._save_state()

    def delete(self, checkpoint_id: str) -> None:
        if checkpoint_id in self._state.get("items", {}):
            del self._state["items"][checkpoint_id]
            if self._state.get("current_id") == checkpoint_id:
                self._state["current_id"] = ""
            self._save_state()

    def cleanup_old(self, max_age_hours: int = 72) -> int:
        deleted = 0
        cutoff = datetime.now() - datetime.timedelta(hours=max_age_hours)
        items = list(self._state.get("items", {}).items())

        for checkpoint_id, checkpoint in items:
            created_at = checkpoint.get("created_at", "")
            if created_at:
                try:
                    if datetime.fromisoformat(created_at) < cutoff:
                        del self._state["items"][checkpoint_id]
                        deleted += 1
                except ValueError:
                    pass

        if deleted > 0:
            self._save_state()
        return deleted

    def get_summary(self, task_type: str = None) -> Dict[str, Any]:
        items = self._state.get("items", {}).values()
        if task_type:
            items = [item for item in items if item.get("task_type") == task_type]

        active = [item for item in items if item.get("status") == "active"]
        completed = [item for item in items if item.get("status") == "completed"]

        return {
            "total": len(items),
            "active": len(active),
            "completed": len(completed),
            "active_checkpoints": [
                {
                    "checkpoint_id": item["checkpoint_id"],
                    "task_type": item["task_type"],
                    "task_id": item["task_id"],
                    "current_goal": item["current_goal"],
                    "created_at": item["created_at"],
                    "next_step": item["next_step"]
                }
                for item in active
            ]
        }