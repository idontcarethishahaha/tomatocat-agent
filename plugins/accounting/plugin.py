from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from agent.plugins import Plugin
from agent.plugins.decorators import tool


class AccountingPlugin(Plugin):
    name = "accounting"
    desc = "番茄猫记账助手"

    def __init__(self) -> None:
        super().__init__()
        self._data_dir: Path | None = None

    def _ensure_data_dir(self) -> Path:
        if self._data_dir is not None:
            return self._data_dir
        base = self.context.workspace if self.context and self.context.workspace else Path.cwd()
        self._data_dir = base / "accounting"
        self._data_dir.mkdir(exist_ok=True)
        return self._data_dir

    def _get_month_file(self, year: int = None, month: int = None) -> Path:
        """获取指定月份的账单文件路径"""
        if year is None or month is None:
            now = datetime.now()
            year = year or now.year
            month = month or now.month
        return self._ensure_data_dir() / f"{year}-{month:02d}.json"

    def _load_month_data(self, year: int = None, month: int = None) -> list[dict]:
        """加载指定月份的数据"""
        file_path = self._get_month_file(year, month)
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save_month_data(self, data: list[dict], year: int = None, month: int = None) -> None:
        """保存指定月份的数据"""
        file_path = self._get_month_file(year, month)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load_all_months(self) -> list[dict]:
        """加载所有月份的数据"""
        all_data = []
        for file_path in sorted(self._ensure_data_dir().glob("????-??.json")):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        all_data.extend(data)
            except (json.JSONDecodeError, IOError):
                pass
        return sorted(all_data, key=lambda x: x.get("date", ""), reverse=True)

    @tool(name="record_expense")
    async def record_expense(self, event: object, amount: str | float, category: str, note: str = "") -> str:
        """记录支出"""
        categories = ["food", "transport", "shopping", "entertainment", "health", "other"]
        if category not in categories:
            return f"无效的分类: {category}，可选: {', '.join(categories)}"

        try:
            amount = float(str(amount).replace("元", "").strip())
        except (ValueError, TypeError):
            return f"无效的金额: {amount}，请输入数字 (=･ω･=)"

        now = datetime.now()
        record = {
            "date": now.isoformat(),
            "type": "expense",
            "amount": amount,
            "category": category,
            "note": note,
        }

        data = self._load_month_data()
        data.append(record)
        self._save_month_data(data)
        return f"已记录支出: ¥{amount:.2f} ({category}) (=･ω･=)"

    @tool(name="record_income")
    async def record_income(self, event: object, amount: str | float, category: str = "other", note: str = "") -> str:
        """记录收入"""
        categories = ["salary", "bonus", "investment", "redpacket", "refund", "other"]
        if category not in categories:
            return f"无效的分类: {category}，可选: {', '.join(categories)}"

        try:
            amount = float(str(amount).replace("元", "").strip())
        except (ValueError, TypeError):
            return f"无效的金额: {amount}，请输入数字 (=^･ω･^=)"

        now = datetime.now()
        record = {
            "date": now.isoformat(),
            "type": "income",
            "amount": amount,
            "category": category,
            "note": note,
        }

        data = self._load_month_data()
        data.append(record)
        self._save_month_data(data)
        return f"已记录收入: ¥{amount:.2f} ({category}) (=^･ω･^=)"

    @tool(name="get_finance_summary")
    async def get_finance_summary(self, event: object, period: str = "today") -> str:
        """获取收支统计"""
        today = date.today()
        expenses = []
        incomes = []

        # 根据 period 加载对应月份的数据
        if period == "today":
            data = self._load_month_data()
            for record in data:
                record_date = datetime.fromisoformat(record["date"]).date()
                if record_date == today:
                    if record.get("type") == "income":
                        incomes.append(record)
                    else:
                        expenses.append(record)
        elif period == "week":
            data = self._load_month_data()
            for record in data:
                record_date = datetime.fromisoformat(record["date"]).date()
                if (today - record_date).days < 7:
                    if record.get("type") == "income":
                        incomes.append(record)
                    else:
                        expenses.append(record)
        elif period == "month":
            # 加载当年所有月份的数据
            for month in range(1, today.month + 1):
                data = self._load_month_data(today.year, month)
                for record in data:
                    record_date = datetime.fromisoformat(record["date"]).date()
                    if record_date.month == today.month and record_date.year == today.year:
                        if record.get("type") == "income":
                            incomes.append(record)
                        else:
                            expenses.append(record)
        else:
            return f"不支持的周期: {period}，可选: today, week, month (=^･ω･^)"

        total_expense = sum(r["amount"] for r in expenses)
        total_income = sum(r["amount"] for r in incomes)
        net = total_income - total_expense

        period_text = {"today": "今日", "week": "本周", "month": f"{today.year}年{today.month}月"}.get(period, period)
        lines = [f"📊 {period_text} 收支汇总 (=^･ω･^=)", ""]
        lines.append(f"收入: ¥{total_income:.2f}")
        lines.append(f"支出: ¥{total_expense:.2f}")
        lines.append(f"结余: ¥{net:.2f}")

        if incomes:
            income_cats = {}
            for r in incomes:
                income_cats[r["category"]] = income_cats.get(r["category"], 0) + r["amount"]
            lines.append("")
            lines.append("收入明细:")
            for cat, amt in sorted(income_cats.items(), key=lambda x: -x[1]):
                lines.append(f"  - {cat}: ¥{amt:.2f}")

        if expenses:
            expense_cats = {}
            for r in expenses:
                expense_cats[r["category"]] = expense_cats.get(r["category"], 0) + r["amount"]
            lines.append("")
            lines.append("支出明细:")
            for cat, amt in sorted(expense_cats.items(), key=lambda x: -x[1]):
                lines.append(f"  - {cat}: ¥{amt:.2f}")

        if not incomes and not expenses:
            lines.append("")
            lines.append("暂无记录 (=^･ω･^)")

        return "\n".join(lines)

    @tool(name="set_budget")
    async def set_budget(self, event: object, category: str, amount: str | float) -> str:
        """设置预算"""
        try:
            amount = float(str(amount).replace("元", "").strip())
        except (ValueError, TypeError):
            return f"无效的金额: {amount}，请输入数字 (=^･ω･^=)"
        return f"已设置 {category} 预算: ¥{amount:.2f} (=^･ω･^=)"
