"""Tomato timer widget for desktop pet."""
import os
import sys
import json
import time
import threading
from pathlib import Path
from dataclasses import dataclass, asdict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QApplication,
    QFrame
)
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, QRectF, pyqtSignal, QSize
from PyQt6.QtGui import QPainter, QColor, QFont, QFontDatabase, QPen, QMouseEvent, QBrush, QPainterPath

ROOT = Path(__file__).parent
TIMER_STATE_FILE = ROOT / "tomato_timer_state.json"

DEFAULT_MODES = {
    "work": {"label": "专注", "minutes": 25, "color": "#FF6B6B"},
    "short": {"label": "短休", "minutes": 5, "color": "#4ECDC4"},
    "long": {"label": "长休", "minutes": 15, "color": "#45B7D1"},
}


@dataclass
class TaskItem:
    text: str
    done: bool = False
    pomodoros: int = 0


class CircleProgress(QWidget):
    """Circular progress bar with time label."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._progress = 1.0
        self._color = QColor("#FF6B6B")
        self.setMinimumSize(200, 200)
        self.setMaximumSize(260, 260)

    def set_progress(self, value: float, color: str = None):
        self._progress = max(0.0, min(1.0, value))
        if color:
            self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRect(8, 8, self.width() - 16, self.height() - 16)

        # background ring
        pen = QPen(QColor("#FFE4E9"))
        pen.setWidth(12)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, 0, 360 * 16)

        # progress ring
        pen = QPen(self._color)
        pen.setWidth(12)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        span = int(-self._progress * 360 * 16)
        painter.drawArc(rect, 90 * 16, span)


class TomatoTimer(QWidget):
    """Floating tomato timer window."""

    finished = pyqtSignal(str)  # mode label
    state_changed = pyqtSignal(str)

    def __init__(self, pet_window=None):
        super().__init__()
        self.pet = pet_window
        self._dragging = False
        self._drag_offset = QPoint()

        self._mode = "work"
        self._remaining_seconds = DEFAULT_MODES["work"]["minutes"] * 60
        self._total_seconds = self._remaining_seconds
        self._running = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self._tasks: list[TaskItem] = []
        self._today_minutes = 0
        self._load_state()

        self._setup_ui()
        self._update_display()
        self.setWindowTitle("🍅 番茄钟")

    def _setup_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(280, 460)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        self._container = QFrame(self)
        self._container.setObjectName("container")
        self._container.setStyleSheet(self._stylesheet())
        main_layout.addWidget(self._container)

        layout = QVBoxLayout(self._container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Header
        header = QHBoxLayout()
        self._title = QLabel("🍅 番茄猫专注钟")
        self._title.setObjectName("title")
        header.addWidget(self._title)
        header.addStretch()

        self._close_btn = QPushButton("✕")
        self._close_btn.setObjectName("iconBtn")
        self._close_btn.setFixedSize(26, 26)
        self._close_btn.clicked.connect(self.hide)
        header.addWidget(self._close_btn)
        layout.addLayout(header)

        # Mode tabs
        mode_layout = QHBoxLayout()
        self._mode_buttons = {}
        for key, cfg in DEFAULT_MODES.items():
            btn = QPushButton(cfg["label"])
            btn.setCheckable(True)
            btn.setProperty("mode", key)
            btn.clicked.connect(lambda checked, k=key: self._set_mode(k))
            mode_layout.addWidget(btn)
            self._mode_buttons[key] = btn
        layout.addLayout(mode_layout)

        # Circle progress
        self._progress = CircleProgress(self._container)
        self._progress.setFixedSize(180, 180)
        layout.addWidget(self._progress, alignment=Qt.AlignmentFlag.AlignCenter)

        # Time label
        self._time_label = QLabel("25:00")
        self._time_label.setObjectName("timeLabel")
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._time_label)

        # Status label
        self._status_label = QLabel("准备好开始专注了吗喵~")
        self._status_label.setObjectName("statusLabel")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        # Control buttons
        ctrl = QHBoxLayout()
        self._start_btn = QPushButton("▶ 开始")
        self._start_btn.setObjectName("primaryBtn")
        self._start_btn.clicked.connect(self._toggle_timer)
        ctrl.addWidget(self._start_btn)

        self._reset_btn = QPushButton("↺ 重置")
        self._reset_btn.setObjectName("secondaryBtn")
        self._reset_btn.clicked.connect(self._reset_timer)
        ctrl.addWidget(self._reset_btn)

        self._skip_btn = QPushButton("⏭ 跳过")
        self._skip_btn.setObjectName("secondaryBtn")
        self._skip_btn.clicked.connect(self._skip_phase)
        ctrl.addWidget(self._skip_btn)
        layout.addLayout(ctrl)

        # Stats
        self._stats_label = QLabel("今日：0 分钟 · 0 🍅")
        self._stats_label.setObjectName("statsLabel")
        self._stats_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._stats_label)

        # Task input
        task_input_layout = QHBoxLayout()
        self._task_input = QLineEdit()
        self._task_input.setPlaceholderText("加个任务喵~")
        self._task_input.returnPressed.connect(self._add_task)
        task_input_layout.addWidget(self._task_input)

        add_btn = QPushButton("+")
        add_btn.setObjectName("iconBtn")
        add_btn.setFixedSize(28, 28)
        add_btn.clicked.connect(self._add_task)
        task_input_layout.addWidget(add_btn)
        layout.addLayout(task_input_layout)

        # Task list
        self._task_list = QListWidget()
        self._task_list.setObjectName("taskList")
        self._task_list.itemClicked.connect(self._task_selected)
        layout.addWidget(self._task_list, stretch=1)

        task_ctrl = QHBoxLayout()
        self._done_btn = QPushButton("✓ 完成")
        self._done_btn.setObjectName("successBtn")
        self._done_btn.clicked.connect(self._toggle_task_done)
        task_ctrl.addWidget(self._done_btn)

        del_btn = QPushButton("🗑 删除")
        del_btn.setObjectName("dangerBtn")
        del_btn.clicked.connect(self._delete_task)
        task_ctrl.addWidget(del_btn)
        layout.addLayout(task_ctrl)

        self._refresh_task_list()
        self._set_mode("work", start=False)
        self.setWindowTitle("🍅 番茄钟")

    def _stylesheet(self):
        return """
            #container {
                background: rgba(255, 255, 255, 245);
                border: 1px solid rgba(255, 182, 193, 100);
                border-radius: 18px;
            }
            QLabel {
                background: transparent;
                color: #5A4A4A;
                font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
            }
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 500;
                color: #5A4A4A;
                font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
            }
            QPushButton:checked {
                background: #FF6B6B;
                color: white;
            }
            QPushButton:hover {
                background: rgba(255, 107, 107, 0.1);
            }
            #title {
                font-size: 15px;
                font-weight: bold;
                color: #FF6B6B;
            }
            #timeLabel {
                font-size: 36px;
                font-weight: bold;
                color: #FF6B6B;
            }
            #statusLabel {
                font-size: 12px;
                color: #999999;
            }
            #statsLabel {
                font-size: 11px;
                color: #AAAAAA;
            }
            #primaryBtn {
                background: #FF6B6B;
                color: white;
            }
            #primaryBtn:hover {
                background: #FF8585;
            }
            #secondaryBtn {
                background: #FFF0F3;
                color: #FF6B6B;
            }
            #secondaryBtn:hover {
                background: #FFE4E9;
            }
            #successBtn {
                background: #4ECDC4;
                color: white;
            }
            #successBtn:hover {
                background: #6EDDD6;
            }
            #dangerBtn {
                background: #FFE4E9;
                color: #FF6B6B;
            }
            #dangerBtn:hover {
                background: #FFD0DA;
            }
            #iconBtn {
                background: transparent;
                color: #999999;
                font-size: 13px;
            }
            #iconBtn:hover {
                background: #FFE4E9;
                color: #FF6B6B;
            }
            QLineEdit {
                background: #FFF8F9;
                border: 1px solid #FFD0DA;
                border-radius: 8px;
                padding: 6px;
                color: #5A4A4A;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 1px solid #FF6B6B;
            }
            #taskList {
                background: #FFF8F9;
                border: 1px solid #FFE4E9;
                border-radius: 10px;
                padding: 4px;
                outline: none;
                font-size: 12px;
            }
            #taskList::item {
                padding: 6px;
                border-radius: 6px;
                margin: 1px 0px;
            }
            #taskList::item:selected {
                background: #FFE4E9;
                color: #FF6B6B;
            }
        """

    # ─── timer control ──────────────────────────────────

    def _set_mode(self, mode: str, start: bool = False):
        self._mode = mode
        cfg = DEFAULT_MODES[mode]
        self._total_seconds = cfg["minutes"] * 60
        self._remaining_seconds = self._total_seconds
        self._running = False
        self._timer.stop()

        for key, btn in self._mode_buttons.items():
            btn.setChecked(key == mode)

        self._update_display()
        self._start_btn.setText("▶ 开始")
        self._status_label.setText(
            f"{cfg['label']}模式：{cfg['minutes']} 分钟喵~"
        )
        if start:
            self._toggle_timer()

    def _toggle_timer(self):
        if self._running:
            self._running = False
            self._timer.stop()
            self._start_btn.setText("▶ 继续")
            self._status_label.setText("休息一下吧，我会等你的喵~")
        else:
            self._running = True
            self._timer.start(1000)
            self._start_btn.setText("⏸ 暂停")
            self._status_label.setText(
                f"{DEFAULT_MODES[self._mode]['label']}中... 加油喵！"
            )

    def _reset_timer(self):
        self._running = False
        self._timer.stop()
        self._remaining_seconds = self._total_seconds
        self._update_display()
        self._start_btn.setText("▶ 开始")
        self._status_label.setText("重置好啦，重新开始喵~")

    def _skip_phase(self):
        self._finish_phase(skipped=True)

    def _tick(self):
        self._remaining_seconds -= 1
        self._update_display()
        if self._remaining_seconds <= 0:
            self._finish_phase()

    def _finish_phase(self, skipped: bool = False):
        self._running = False
        self._timer.stop()

        if not skipped and self._mode == "work":
            self._today_minutes += DEFAULT_MODES["work"]["minutes"]
            self._increment_active_task_pomodoro()
            self._notify("专注时间结束！休息一下吧喵~ 🍅")
            self._set_mode("short")
        elif not skipped and self._mode in ("short", "long"):
            self._notify("休息结束！继续加油喵~ 💪")
            self._set_mode("work")
        else:
            self._notify("已跳过当前阶段喵~")
            self._set_mode("work")

        self._save_state()
        self._update_stats()

    # ─── display ────────────────────────────────────────

    def _update_display(self):
        mins, secs = divmod(max(0, self._remaining_seconds), 60)
        self._time_label.setText(f"{mins:02d}:{secs:02d}")

        progress = (
            self._remaining_seconds / self._total_seconds
            if self._total_seconds else 0
        )
        cfg = DEFAULT_MODES[self._mode]
        self._progress.set_progress(progress, cfg["color"])
        self._time_label.setStyleSheet(f"color: {cfg['color']}; font-size: 42px; font-weight: bold;")

    def _update_stats(self):
        pomodoros = self._today_minutes // DEFAULT_MODES["work"]["minutes"]
        self._stats_label.setText(
            f"今日专注：{self._today_minutes} 分钟 · 完成番茄：{pomodoros} 个"
        )

    # ─── tasks ──────────────────────────────────────────

    def _refresh_task_list(self):
        self._task_list.clear()
        for i, task in enumerate(self._tasks):
            mark = "✓" if task.done else "○"
            text = f"{mark} {task.text}"
            if task.pomodoros > 0:
                text += f"  {'🍅' * task.pomodoros}"
            item = QListWidgetItem(text)
            if task.done:
                item.setForeground(QColor("#999999"))
            self._task_list.addItem(item)
        self._update_stats()

    def _add_task(self):
        text = self._task_input.text().strip()
        if not text:
            return
        self._tasks.append(TaskItem(text=text))
        self._task_input.clear()
        self._refresh_task_list()
        self._save_state()

    def _task_selected(self):
        pass

    def _toggle_task_done(self):
        idx = self._task_list.currentRow()
        if 0 <= idx < len(self._tasks):
            self._tasks[idx].done = not self._tasks[idx].done
            self._refresh_task_list()
            self._save_state()

    def _delete_task(self):
        idx = self._task_list.currentRow()
        if 0 <= idx < len(self._tasks):
            del self._tasks[idx]
            self._refresh_task_list()
            self._save_state()

    def _increment_active_task_pomodoro(self):
        """Add a pomodoro to the first unfinished task."""
        for task in self._tasks:
            if not task.done:
                task.pomodoros += 1
                self._refresh_task_list()
                return

    # ─── notifications ──────────────────────────────────

    def _notify(self, text: str):
        self._status_label.setText(text)
        if self.pet and hasattr(self.pet, "_show_bubble"):
            self.pet._show_bubble(text)
        # simple beep
        try:
            if sys.platform == "win32":
                import winsound
                winsound.MessageBeep()
        except Exception:
            pass

    # ─── persistence ────────────────────────────────────

    def _load_state(self):
        try:
            if TIMER_STATE_FILE.exists():
                data = json.loads(TIMER_STATE_FILE.read_text(encoding="utf-8"))
                self._tasks = [TaskItem(**t) for t in data.get("tasks", [])]
                self._today_minutes = data.get("today_minutes", 0)
                # reset date
                saved_date = data.get("date", "")
                today = time.strftime("%Y-%m-%d")
                if saved_date != today:
                    self._today_minutes = 0
        except Exception:
            pass

    def _save_state(self):
        try:
            data = {
                "tasks": [asdict(t) for t in self._tasks],
                "today_minutes": self._today_minutes,
                "date": time.strftime("%Y-%m-%d"),
            }
            TIMER_STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ─── window dragging ────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.position().toPoint()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False

    def closeEvent(self, event):
        self._running = False
        self._timer.stop()
        self._save_state()
        event.ignore()
        self.hide()
