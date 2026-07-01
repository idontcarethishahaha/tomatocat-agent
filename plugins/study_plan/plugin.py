"""学习计划插件 - 番茄猫学习助手

参考 tomatocat 项目的实现，采用 target/completed/unit 灵活单位设计。
支持：题、章、页、小时等多种单位；进度百分比计算；连续学习天数追踪。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from tomatocat.plugins import Plugin, tool


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
        return self._ensure_data_dir() / "plans.json"

    @property
    def _logs_file(self) -> Path:
        return self._ensure_data_dir() / "logs.json"

    # ── 数据读写 ─────────────────────────────────────────────────

    def _load_plans(self) -> list[dict]:
        if self._plans_file.exists():
            try:
                data = json.loads(self._plans_file.read_text(encoding="utf-8"))
                # 兼容旧的 {"plans": [...]} 格式和纯数组格式
                if isinstance(data, dict):
                    return data.get("plans", [])
                return data
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save_plans(self, plans: list[dict]) -> None:
        self._plans_file.write_text(
            json.dumps(plans, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_logs(self) -> list[dict]:
        if self._logs_file.exists():
            try:
                data = json.loads(self._logs_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data.get("logs", [])
                return data
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save_logs(self, logs: list[dict]) -> None:
        self._logs_file.write_text(
            json.dumps(logs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 数据兼容迁移 ─────────────────────────────────────────────

    def _migrate_plan(self, plan: dict) -> dict:
        """迁移旧数据结构（total_hours/completed_hours → target/completed/unit）"""
        if "target" not in plan:
            plan["target"] = float(plan.get("total_hours", 0))
            plan["completed"] = float(plan.get("completed_hours", 0))
            plan["unit"] = "小时"
        if "unit" not in plan:
            plan["unit"] = "小时"
        # 确保数值类型正确
        plan["target"] = float(plan.get("target", 0))
        plan["completed"] = float(plan.get("completed", 0))
        return plan

    def _calc_progress(self, plan: dict) -> tuple[float, str]:
        """计算进度，返回 (progress_percent, display_str)"""
        plan = self._migrate_plan(plan)
        target = float(plan.get("target", 0))
        completed = float(plan.get("completed", 0))
        unit = plan.get("unit", "小时")
        if target <= 0:
            return 0.0, f"{completed}/{unit}"
        progress = (completed / target) * 100
        return progress, f"{completed}/{target} {unit}"

    def _get_streak(self) -> int:
        logs = self._load_logs()
        if not logs:
            return 0
        dates = set(log.get("date", "") for log in logs)
        streak = 0
        today = date.today()
        for i in range(365):
            check_date = (today - timedelta(days=i)).isoformat()
            if check_date in dates:
                streak += 1
            else:
                if i == 0:
                    continue
                break
        return streak

    # ── 工具 ─────────────────────────────────────────────────────

    @tool(name="create_study_plan", description="创建一个学习计划")
    async def create_study_plan(
        self,
        event: object,
        subject: str,
        target: float,
        unit: str = "小时",
        deadline: str = "",
    ) -> str:
        """
        创建学习计划

        Args:
            subject: 学习主题/科目，如"leetcode 热题HOT100"
            target: 目标数量，如10
            unit: 单位，如"题"、"章"、"页"、"小时"，默认"小时"
            deadline: 截止日期，如"2026-06-30"，可选
        """
        plans = self._load_plans()

        # 去重检查：如果已有相同 subject + deadline 的 active 计划，不重复创建
        for existing in plans:
            if (
                existing.get("subject") == subject
                and existing.get("deadline", "") == deadline
                and existing.get("status", "active") == "active"
            ):
                return f"已存在相同的学习计划: {subject}（ID:{existing['id']}），不重复创建哦 (=^･ω･^)"

        target_val = float(target)
        plan = {
            "id": len(plans) + 1,
            "subject": subject,
            "target": target_val,
            "completed": 0.0,
            "unit": unit,
            "deadline": deadline,
            "created_at": datetime.now().isoformat(),
            "status": "active",
        }
        plans.append(plan)
        self._save_plans(plans)

        msg = f"已创建学习计划: {subject}，目标: {target_val}{unit} (=^･ω･^=)"
        if deadline:
            msg += f"\n截止日期: {deadline}"
        return msg

    @tool(name="log_study", description="记录学习打卡")
    async def log_study(
        self,
        event: object,
        plan_id: int,
        count: float = 0,
        hours: float = 0,
    ) -> str:
        """
        记录学习打卡

        Args:
            plan_id: 计划ID
            count: 完成的数量（如3道题），优先使用
            hours: 学习小时数，count为0时使用hours
        """
        plans = self._load_plans()
        logs = self._load_logs()

        for plan in plans:
            if plan["id"] == plan_id:
                plan = self._migrate_plan(plan)

                # 优先用 count，其次用 hours
                amount = float(count) if count and count > 0 else float(hours)
                if amount <= 0:
                    return "请输入有效的学习数量 (=｀ω´=)"

                plan["completed"] = float(plan.get("completed", 0)) + amount
                self._save_plans(plans)

                log = {
                    "plan_id": plan_id,
                    "count": count if (count and count > 0) else 0,
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
                streak = self._get_streak()
                msg = f"已记录: {plan['subject']} +{amount}{plan['unit']}，{status} {display} (≧∇≦)ﾉ"
                if streak > 0:
                    msg += f"\n连续学习: {streak}天"
                return msg

        return f"未找到计划 ID: {plan_id} (=｀ω´=)"

    @tool(name="get_study_progress", description="查看学习进度")
    async def get_study_progress(self, event: object, plan_id: int = 0) -> str:
        """
        查看学习进度

        Args:
            plan_id: 计划ID，不填则查看全部
        """
        plans = self._load_plans()

        if plan_id > 0:
            plan = next((p for p in plans if p["id"] == plan_id), None)
            if not plan:
                return f"未找到计划 ID: {plan_id} (=｀ω´=)"
            plan = self._migrate_plan(plan)
            progress, display = self._calc_progress(plan)
            status = "✅" if progress >= 100 else "⏳"
            return f"{status} {plan['subject']}: {display} ({progress:.1f}%) (=^･ω･^=)"

        # 过滤 active 计划
        active_plans = [p for p in plans if p.get("status", "active") == "active"]
        if not active_plans:
            return "暂无学习计划，创建一个吧 (｡•ᴗ-｡)♡"

        today = date.today().isoformat()
        result_lines = [f"📊 今日进度 ({today})", ""]

        completed_items = []
        in_progress_items = []

        for plan in active_plans:
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

        for item in completed_items:
            result_lines.append(f"✅ {item}")
        for item in in_progress_items:
            result_lines.append(f"⏳ {item}")

        if not completed_items and not in_progress_items:
            result_lines.append("  暂无记录 (=^･ω･^)")
        else:
            result_lines.append("")
            result_lines.append(
                f"🕐 累计：完成 {len(completed_items)} 项，进行中 {len(in_progress_items)} 项"
            )

        return "\n".join(result_lines)

    @tool(name="get_daily_summary", description="获取每日学习汇总")
    async def get_daily_summary(self, event: object, date_str: str = "") -> str:
        """
        获取每日学习汇总

        Args:
            date_str: 日期，不填则默认今天
        """
        logs = self._load_logs()
        plans = self._load_plans()

        target_date = date_str or date.today().isoformat()

        daily_logs = [l for l in logs if l.get("date") == target_date]

        if not daily_logs:
            return f"📊 {target_date} 暂无学习记录 (=^･ω･^)"

        plan_map = {p["id"]: p for p in plans}
        daily_by_plan: dict[int, list[dict]] = {}
        for log in daily_logs:
            pid = log.get("plan_id")
            daily_by_plan.setdefault(pid, []).append(log)

        lines = [f"📊 {target_date} 学习汇总", ""]

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

    @tool(name="get_study_streak", description="查看连续学习天数")
    async def get_study_streak(self, event: object) -> str:
        """查看连续学习天数"""
        streak = self._get_streak()
        if streak == 0:
            return "今天还没有学习哦，开始吧！(｡•ᴗ-｡)♡"
        msg = f"连续学习 {streak} 天了！"
        if streak >= 30:
            msg += " 超厉害喵！(≧∇≦)ﾉ"
        elif streak >= 7:
            msg += " 继续加油！ヽ(=^･ω･^=)丿"
        else:
            msg += " 坚持就是胜利喵~"
        return msg
