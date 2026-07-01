"""记账插件 - 番茄猫记账助手"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tomatocat.plugins import Plugin, tool


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

    def _get_month_file(self, year: int | None = None, month: int | None = None) -> Path:
        if year is None or month is None:
            now = datetime.now()
            year = year or now.year
            month = month or now.month
        return self._ensure_data_dir() / f"{year}-{month:02d}.json"

    def _read_month_data(self, year: int | None = None, month: int | None = None) -> dict:
        file_path = self._get_month_file(year, month)
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"expenses": [], "income": [], "budget": 0}

    def _write_month_data(self, data: dict, year: int | None = None, month: int | None = None) -> None:
        file_path = self._get_month_file(year, month)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @tool(name="record_expense", description="记录一笔支出")
    async def record_expense(
        self,
        event: object,
        amount: str,
        category: str,
        note: str = "",
    ) -> str:
        """
        记录一笔支出

        Args:
            amount: 金额
            category: 支出类别（餐饮、交通、购物等）
            note: 备注
        """
        try:
            amount_val = float(str(amount).replace("元", "").strip())
        except ValueError:
            return f"喵？金额 '{amount}' 不对哦 (=｀ω´=)"

        now = datetime.now()
        data = self._read_month_data()

        record = {
            "id": len(data["expenses"]) + 1,
            "amount": amount_val,
            "category": category,
            "note": note,
            "date": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        data["expenses"].append(record)
        self._write_month_data(data)

        total_expense = sum(e["amount"] for e in data["expenses"])
        budget = data.get("budget", 0)

        msg = f"已记录支出：{category} ¥{amount_val:.2f}\n"
        msg += f"本月总支出：¥{total_expense:.2f}"

        if budget > 0:
            remaining = budget - total_expense
            if remaining >= 0:
                msg += f"\n预算剩余：¥{remaining:.2f} (≧∇≦)ﾉ"
            else:
                msg += f"\n已超支：¥{abs(remaining):.2f} (・_・;)"

        return msg

    @tool(name="record_income", description="记录一笔收入")
    async def record_income(
        self,
        event: object,
        amount: str,
        category: str = "other",
        note: str = "",
    ) -> str:
        """
        记录一笔收入

        Args:
            amount: 金额
            category: 收入类别（工资、奖金、红包等）
            note: 备注
        """
        try:
            amount_val = float(str(amount).replace("元", "").strip())
        except ValueError:
            return f"喵？金额 '{amount}' 不对哦 (=｀ω´=)"

        now = datetime.now()
        data = self._read_month_data()

        record = {
            "id": len(data["income"]) + 1,
            "amount": amount_val,
            "category": category,
            "note": note,
            "date": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        data["income"].append(record)
        self._write_month_data(data)

        total_income = sum(i["amount"] for i in data["income"])
        return f"已记录收入：{category} ¥{amount_val:.2f}\n本月总收入：¥{total_income:.2f} (≧∇≦)ﾉ"

    @tool(name="get_finance_summary", description="获取收支统计")
    async def get_finance_summary(
        self,
        event: object,
        period: str = "month",
    ) -> str:
        """
        获取收支统计

        Args:
            period: 统计周期（today/month）
        """
        data = self._read_month_data()
        now = datetime.now()

        if period == "today":
            today_str = now.strftime("%Y-%m-%d")
            today_expenses = [e for e in data["expenses"] if e["date"].startswith(today_str)]
            today_income = [i for i in data["income"] if i["date"].startswith(today_str)]

            total_expense = sum(e["amount"] for e in today_expenses)
            total_income = sum(i["amount"] for i in today_income)

            msg = f"📊 今日收支\n"
            msg += f"支出：¥{total_expense:.2f}\n"
            msg += f"收入：¥{total_income:.2f}\n"
            msg += f"结余：¥{total_income - total_expense:.2f}"

            if today_expenses:
                msg += "\n\n支出明细："
                for e in today_expenses[:5]:
                    msg += f"\n  {e['category']}: ¥{e['amount']:.2f}"
            return msg
        else:
            total_expense = sum(e["amount"] for e in data["expenses"])
            total_income = sum(i["amount"] for i in data["income"])
            budget = data.get("budget", 0)

            msg = f"📊 {now.month}月收支\n"
            msg += f"支出：¥{total_expense:.2f}\n"
            msg += f"收入：¥{total_income:.2f}\n"
            msg += f"结余：¥{total_income - total_expense:.2f}"

            if budget > 0:
                remaining = budget - total_expense
                if remaining >= 0:
                    msg += f"\n预算剩余：¥{remaining:.2f} (≧∇≦)ﾉ"
                else:
                    msg += f"\n已超支：¥{abs(remaining):.2f} (・_・;)"

            return msg

    @tool(name="get_category_breakdown", description="按分类统计收支")
    async def get_category_breakdown(
        self,
        event: object,
        period: str = "month",
        type: str = "expense",
    ) -> str:
        """
        按分类统计收支

        Args:
            period: 统计周期（today/month）
            type: 统计类型（expense/income/both）
        """
        data = self._read_month_data()
        now = datetime.now()

        if period == "today":
            today_str = now.strftime("%Y-%m-%d")
            expenses = [e for e in data["expenses"] if e["date"].startswith(today_str)]
            income = [i for i in data["income"] if i["date"].startswith(today_str)]
            title = f"📊 今日分类统计"
        else:
            expenses = data["expenses"]
            income = data["income"]
            title = f"📊 {now.month}月分类统计"

        def _group(records: list[dict]) -> dict[str, float]:
            result: dict[str, float] = {}
            for r in records:
                cat = r.get("category", "其他")
                result[cat] = result.get(cat, 0) + r["amount"]
            return dict(sorted(result.items(), key=lambda x: x[1], reverse=True))

        msg = title + "\n"

        if type in ("expense", "both") and expenses:
            exp_by_cat = _group(expenses)
            total_exp = sum(exp_by_cat.values())
            msg += f"\n💰 支出（共 ¥{total_exp:.2f}）\n"
            for cat, amount in exp_by_cat.items():
                pct = (amount / total_exp * 100) if total_exp > 0 else 0
                bar = "█" * int(pct / 10)
                msg += f"  {cat}: ¥{amount:.2f} ({pct:.0f}%) {bar}\n"

        if type in ("income", "both") and income:
            inc_by_cat = _group(income)
            total_inc = sum(inc_by_cat.values())
            msg += f"\n💵 收入（共 ¥{total_inc:.2f}）\n"
            for cat, amount in inc_by_cat.items():
                pct = (amount / total_inc * 100) if total_inc > 0 else 0
                bar = "█" * int(pct / 10)
                msg += f"  {cat}: ¥{amount:.2f} ({pct:.0f}%) {bar}\n"

        return msg

    @tool(name="set_budget", description="设置月度预算")
    async def set_budget(
        self,
        event: object,
        amount: str,
    ) -> str:
        """
        设置本月预算

        Args:
            amount: 预算金额
        """
        try:
            amount_val = float(str(amount).replace("元", "").strip())
        except ValueError:
            return f"喵？金额 '{amount}' 不对哦 (=｀ω´=)"

        data = self._read_month_data()
        data["budget"] = amount_val
        self._write_month_data(data)

        return f"已设置本月预算：¥{amount_val:.2f} 要省着花哦 (｡•ᴗ-｡)♡"
