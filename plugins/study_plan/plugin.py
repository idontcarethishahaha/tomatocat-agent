from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from agent.plugins import Plugin
from agent.plugins.decorators import tool


class StudyPlanPlugin(Plugin):
    name = "study_plan"
    desc = "番茄猫学习计划助手"

    def __init__(self) -> None:
        super().__init__()
        self._data_dir: Path | None = None

    def _ensure_data_dir(self) -> Path:
        if self._data_dir is not None:
            return self._data_dir
        base = self.context.workspace if self.context and self.context.workspace else Path.cwd()
        self._data_dir = base / "study"
        self._data_dir.mkdir(exist_ok=True)
        return self._data_dir

    @property
    def _plans_file(self) -> Path:
        return self._ensure_data_dir() / "study_plans.json"

    @property
    def _logs_file(self) -> Path:
        return self._ensure_data_dir() / "study_logs.json"

    @property
    def _plans(self) -> list[dict]:
        return self._load_plans()

    @property
    def _logs(self) -> list[dict]:
        return self._load_logs()

    def _load_plans(self) -> list[dict]:
        if self._plans_file.exists():
            try:
                with open(self._plans_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _load_logs(self) -> list[dict]:
        if self._logs_file.exists():
            try:
                with open(self._logs_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save_plans(self, plans: list[dict]) -> None:
        with open(self._plans_file, "w", encoding="utf-8") as f:
            json.dump(plans, f, ensure_ascii=False, indent=2)

    def _save_logs(self, logs: list[dict]) -> None:
        with open(self._logs_file, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)

    @tool(name="create_study_plan")
    async def create_study_plan(self, event: object, subject: str, target_hours: float, deadline: str = "") -> str:
        """创建学习计划"""
        plans = self._plans
        plan = {
            "id": len(plans) + 1,
            "subject": subject,
            "target_hours": target_hours,
            "deadline": deadline,
            "created_at": datetime.now().isoformat(),
            "completed_hours": 0.0,
        }
        plans.append(plan)
        self._save_plans(plans)
        return f"已创建学习计划: {subject}，目标: {target_hours}小时 (=^･ω･^=)"

    @tool(name="log_study")
    async def log_study(self, event: object, plan_id: int, hours: float) -> str:
        """记录学习时长"""
        plans = self._plans
        logs = self._logs
        for plan in plans:
            if plan["id"] == plan_id:
                plan["completed_hours"] += hours
                self._save_plans(plans)

                log = {
                    "plan_id": plan_id,
                    "hours": hours,
                    "date": date.today().isoformat(),
                    "timestamp": datetime.now().isoformat(),
                }
                logs.append(log)
                self._save_logs(logs)

                progress = (plan["completed_hours"] / plan["target_hours"]) * 100
                return f"已记录学习: {plan['subject']} +{hours}小时，进度: {progress:.1f}% (≧∇≦)ﾉ"

        return f"未找到计划 ID: {plan_id}"

    @tool(name="get_study_progress")
    async def get_study_progress(self, event: object, plan_id: int = 0) -> str:
        """获取学习进度"""
        plans = self._plans
        if plan_id > 0:
            plan = next((p for p in plans if p["id"] == plan_id), None)
            if not plan:
                return f"未找到计划 ID: {plan_id}"
            progress = (plan["completed_hours"] / plan["target_hours"]) * 100
            return f"{plan['subject']}: {plan['completed_hours']}/{plan['target_hours']}小时 ({progress:.1f}%) (=^･ω･^=)"

        result = "所有学习计划:\n"
        for plan in plans:
            progress = (plan["completed_hours"] / plan["target_hours"]) * 100
            result += f"  - {plan['id']}. {plan['subject']}: {plan['completed_hours']}/{plan['target_hours']}小时 ({progress:.1f}%)\n"
        return result.strip()

    @tool(name="get_study_streak")
    async def get_study_streak(self, event: object) -> str:
        """获取连续学习天数"""
        logs = self._logs
        if not logs:
            return "暂无学习记录 (=^･ω･^=)"

        dates = sorted(set(log["date"] for log in logs))
        if not dates:
            return "暂无学习记录"

        streak = 0
        today = date.today()
        for i, log_date in enumerate(reversed(dates)):
            days_diff = (today - date.fromisoformat(log_date)).days
            if days_diff == i:
                streak += 1
            else:
                break

        return f"连续学习天数: {streak}天 (≧∇≦)ﾉ"
