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

    def _plans(self) -> list[dict]:
        return self._load_plans()

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

    def _migrate_plan(self, plan: dict) -> dict:
        """迁移旧数据结构到新格式：target_hours/completed_hours → target/completed/unit"""
        if "target" not in plan:
            # 旧数据：target_hours, completed_hours
            plan["target"] = float(plan.get("target_hours", 0))
            plan["completed"] = float(plan.get("completed_hours", 0))
            plan["unit"] = "小时"
            # 清理旧字段（可选，保留兼容）
        if "unit" not in plan:
            plan["unit"] = "小时"
        return plan

    def _calc_progress(self, plan: dict) -> tuple[float, str]:
        """计算进度，返回 (progress_percent, display_str)"""
        target = float(plan.get("target", 0))
        completed = float(plan.get("completed", 0))
        unit = plan.get("unit", "小时")
        if target <= 0:
            return 0.0, f"{completed}/{unit}"
        progress = (completed / target) * 100
        return progress, f"{completed}/{target} {unit}"

    @tool(name="create_study_plan")
    async def create_study_plan(
        self,
        event: object,
        subject: str,
        target: float,
        unit: str = "小时",
        deadline: str = "",
    ) -> str:
        """创建学习计划

        Args:
            subject: 学习主题/科目，如"leetcode 热题HOT100"
            target: 目标数量，如10
            unit: 单位，如"题"、"章"、"小时"，默认"小时"
            deadline: 截止日期，如"2026-06-30"，可选
        """
        plans = self._plans()
        plan = {
            "id": len(plans) + 1,
            "subject": subject,
            "target": float(target),
            "completed": 0.0,
            "unit": unit,
            "deadline": deadline,
            "created_at": datetime.now().isoformat(),
        }
        plans.append(plan)
        self._save_plans(plans)
        return f"已创建学习计划: {subject}，目标: {target}{unit} (=^･ω･^=)"

    @tool(name="log_study")
    async def log_study(
        self,
        event: object,
        plan_id: int,
        count: float = 0,
        hours: float = 0,
    ) -> str:
        """记录学习打卡

        Args:
            plan_id: 计划ID
            count: 完成的数量（如3道题），优先使用
            hours: 学习小时数，count为0时使用hours
        """
        plans = self._plans()
        logs = self._logs()
        for plan in plans:
            if plan["id"] == plan_id:
                # 优先用 count，其次用 hours
                amount = float(count) if count > 0 else float(hours)
                if amount <= 0:
                    return "请输入有效的学习数量 (=^･ω･^)"
                
                # 兼容旧数据
                plan = self._migrate_plan(plan)
                plan["completed"] = float(plan.get("completed", 0)) + amount
                self._save_plans(plans)

                log = {
                    "plan_id": plan_id,
                    "count": count if count > 0 else 0,
                    "hours": hours,
                    "amount": amount,
                    "unit": plan.get("unit", "小时"),
                    "date": date.today().isoformat(),
                    "timestamp": datetime.now().isoformat(),
                }
                logs.append(log)
                self._save_logs(logs)

                progress, display = self._calc_progress(plan)
                status = "✅" if progress >= 100 else "⏳"
                return f"已记录: {plan['subject']} +{amount}{plan['unit']}，{status} {display} (≧∇≦)ﾉ"

        return f"未找到计划 ID: {plan_id} (=^･ω･^)"

    @tool(name="get_study_progress")
    async def get_study_progress(self, event: object, plan_id: int = 0) -> str:
        """获取学习进度

        Args:
            plan_id: 计划ID，不填则查看全部
        """
        plans = self._plans()

        if plan_id > 0:
            plan = next((p for p in plans if p["id"] == plan_id), None)
            if not plan:
                return f"未找到计划 ID: {plan_id} (=^･ω･^)"
            plan = self._migrate_plan(plan)
            progress, display = self._calc_progress(plan)
            status = "✅" if progress >= 100 else "⏳"
            return f"{status} {plan['subject']}: {display} ({progress:.1f}%) (=^･ω･^=)"

        if not plans:
            return "暂无学习计划 (=^･ω･^)"

        today = date.today().isoformat()

        # 显示所有计划
        result_lines = [f"📊 今日进度 ({today})", ""]

        completed_items = []
        in_progress_items = []

        for plan in plans:
            plan = self._migrate_plan(plan)
            target = float(plan.get("target", 0))
            completed = float(plan.get("completed", 0))
            unit = plan.get("unit", "小时")

            progress, _ = self._calc_progress(plan)
            item = f"{plan['subject']}: {completed}/{target} {unit}"

            if progress >= 100:
                completed_items.append(item)
            else:
                in_progress_items.append(item)

        # 已完成的放前面
        for item in completed_items:
            result_lines.append(f"✅ {item}")
        for item in in_progress_items:
            result_lines.append(f"⏳ {item}")

        if not completed_items and not in_progress_items:
            result_lines.append("  暂无记录 (=^･ω･^)")
        else:
            result_lines.append("")
            result_lines.append(f"🕐 累计：完成 {len(completed_items)} 项，进行中 {len(in_progress_items)} 项")

        return "\n".join(result_lines)

    @tool(name="get_daily_summary")
    async def get_daily_summary(self, event: object, date_str: str = "") -> str:
        """获取每日学习汇总

        Args:
            date_str: 日期，不填则默认今天
        """
        logs = self._logs()
        plans = self._plans()
        
        target_date = date_str or date.today().isoformat()
        
        # 筛选当日日志
        daily_logs = [l for l in logs if l.get("date") == target_date]
        
        if not daily_logs:
            return f"📊 {target_date} 暂无学习记录 (=^･ω･^)"
        
        # 按计划分组
        plan_map = {p["id"]: p for p in plans}
        daily_by_plan: dict[int, list[dict]] = {}
        for log in daily_logs:
            pid = log.get("plan_id")
            daily_by_plan.setdefault(pid, []).append(log)
        
        lines = [f"📊 {target_date} 学习汇总"]
        lines.append("")
        
        total_amount = 0.0
        unit_used = "项"
        
        for pid, plan_logs in daily_by_plan.items():
            plan = plan_map.get(pid, {"subject": f"计划#{pid}", "unit": "小时"})
            plan = self._migrate_plan(plan)
            unit = plan.get("unit", "小时")
            total = sum(log.get("amount", 0) for log in plan_logs)
            total_amount += total
            if unit_used == "项" or unit == unit_used:
                unit_used = unit
            else:
                unit_used = "混合"
            lines.append(f"  ✅ {plan['subject']}: +{total} {unit}")
        
        lines.append("")
        lines.append(f"🕐 累计完成: {total_amount} {unit_used} (≧∇≦)ﾉ")
        
        return "\n".join(lines)

    @tool(name="get_study_streak")
    async def get_study_streak(self, event: object) -> str:
        """获取连续学习天数"""
        logs = self._logs()
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
