"""学习计划插件 - 番茄猫学习助手"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
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

    def _plans_file(self) -> Path:
        return self._ensure_data_dir() / "plans.json"

    def _logs_file(self) -> Path:
        return self._ensure_data_dir() / "logs.json"

    def _read_plans(self) -> dict:
        path = self._plans_file()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"plans": []}

    def _write_plans(self, data: dict) -> None:
        with open(self._plans_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _read_logs(self) -> dict:
        path = self._logs_file()
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"logs": []}

    def _write_logs(self, data: dict) -> None:
        with open(self._logs_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @tool(name="create_study_plan", description="创建一个学习计划")
    async def create_study_plan(
        self,
        event: object,
        title: str,
        subject: str = "",
        total_hours: float = 0,
        deadline: str = "",
    ) -> str:
        """
        创建学习计划

        Args:
            title: 计划标题
            subject: 科目/主题
            total_hours: 预计总时长（小时）
            deadline: 截止日期（YYYY-MM-DD）
        """
        data = self._read_plans()
        plan = {
            "id": len(data["plans"]) + 1,
            "title": title,
            "subject": subject,
            "total_hours": total_hours,
            "completed_hours": 0,
            "deadline": deadline,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "active",
        }
        data["plans"].append(plan)
        self._write_plans(data)

        msg = f"已创建学习计划：{title} (≧∇≦)ﾉ"
        if subject:
            msg += f"\n科目：{subject}"
        if total_hours > 0:
            msg += f"\n预计时长：{total_hours}小时"
        if deadline:
            msg += f"\n截止日期：{deadline}"
        msg += "\n加油喵！"
        return msg

    @tool(name="log_study", description="记录学习打卡")
    async def log_study(
        self,
        event: object,
        hours: str,
        subject: str = "",
        content: str = "",
        plan_id: int = 0,
    ) -> str:
        """
        记录学习时长

        Args:
            hours: 学习时长（小时）
            subject: 学习科目
            content: 学习内容
            plan_id: 关联的计划ID
        """
        try:
            hours_val = float(str(hours).replace("小时", "").replace("h", "").strip())
        except ValueError:
            return f"喵？时长 '{hours}' 不对哦 (=｀ω´=)"

        now = datetime.now()
        logs_data = self._read_logs()

        log = {
            "id": len(logs_data["logs"]) + 1,
            "date": now.strftime("%Y-%m-%d"),
            "hours": hours_val,
            "subject": subject,
            "content": content,
            "plan_id": plan_id,
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        logs_data["logs"].append(log)
        self._write_logs(logs_data)

        if plan_id > 0:
            plans_data = self._read_plans()
            for plan in plans_data["plans"]:
                if plan["id"] == plan_id:
                    plan["completed_hours"] += hours_val
                    self._write_plans(plans_data)
                    break

        streak = self._get_streak()
        msg = f"打卡成功！今天学习了 {hours_val} 小时 (≧∇≦)ﾉ\n"
        msg += f"连续学习：{streak} 天"
        if streak >= 7:
            msg += " 太厉害了喵！"
        return msg

    def _get_streak(self) -> int:
        logs_data = self._read_logs()
        dates = set()
        for log in logs_data["logs"]:
            dates.add(log["date"])

        streak = 0
        today = datetime.now().date()
        for i in range(365):
            check_date = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            if check_date in dates:
                streak += 1
            else:
                if i == 0:
                    continue
                break
        return streak

    @tool(name="get_study_progress", description="查看学习进度")
    async def get_study_progress(
        self,
        event: object,
        plan_id: int = 0,
    ) -> str:
        """
        查看学习进度

        Args:
            plan_id: 计划ID（0表示所有计划）
        """
        plans_data = self._read_plans()

        if plan_id > 0:
            for plan in plans_data["plans"]:
                if plan["id"] == plan_id:
                    progress = 0
                    if plan["total_hours"] > 0:
                        progress = (plan["completed_hours"] / plan["total_hours"]) * 100
                    msg = f"📚 {plan['title']}\n"
                    msg += f"进度：{plan['completed_hours']:.1f}/{plan['total_hours']:.1f}小时 ({progress:.1f}%)"
                    return msg
            return "没有找到这个学习计划哦 (・_・;)"
        else:
            active_plans = [p for p in plans_data["plans"] if p["status"] == "active"]
            if not active_plans:
                return "还没有学习计划哦，创建一个吧 (｡•ᴗ-｡)♡"

            msg = "📚 学习计划列表：\n"
            for plan in active_plans[:5]:
                progress = 0
                if plan["total_hours"] > 0:
                    progress = (plan["completed_hours"] / plan["total_hours"]) * 100
                msg += f"\n{plan['id']}. {plan['title']} - {progress:.1f}%"
            return msg

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
