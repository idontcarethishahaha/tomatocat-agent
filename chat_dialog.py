"""Chat bubble dialog for the desktop pet."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QTextEdit, QLineEdit, QPushButton, QLabel)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint, QEvent, QRect
from PyQt6.QtGui import QFont, QMouseEvent

import threading
import json
import asyncio
from pathlib import Path
from html import escape as html_escape
from config_mgr import load_config

CHAT_LOG_PATH = Path(__file__).parent / "chat-history.json"
MAX_CHAT_HISTORY = 50

_default_theme = {
    "bg": "#FFFFFF",
    "border": "#FFB6C1",
    "title": "#FF6B9D",
    "history_bg": "#FFF9FB",
    "text_color": "#333333",
    "input_bg": "#FFFFFF",
    "input_border": "#FFB6C1",
    "btn_bg": "#FFB6C1",
    "btn_hover": "#FF9BB0",
    "btn_pressed": "#FF8599",
    "btn_text": "#FFFFFF",
    "user_color": "#6FA8FF",
    "assistant_color": "#FF6B9D",
}


def _get_theme():
    cfg = load_config()
    theme = dict(_default_theme)
    custom = cfg.get("theme", {})
    theme.update(custom)
    return theme


def _build_style():
    t = _get_theme()
    return f"""
QWidget#ChatBubble {{
    background: {t['bg']};
    border: 2px solid {t['border']};
    border-radius: 16px;
}}
QTextEdit#ChatHistory {{
    background: {t['history_bg']};
    color: {t['text_color']};
    border: none;
    border-radius: 10px;
    font-size: 14px;
    padding: 8px;
    selection-background-color: {t['border']};
}}
QLineEdit#ChatInput {{
    background: {t['input_bg']};
    color: {t['text_color']};
    border: 1px solid {t['input_border']};
    border-radius: 8px;
    font-size: 14px;
    padding: 6px 10px;
}}
QLineEdit#ChatInput:focus {{
    border: 2px solid {t['border']};
}}
QPushButton#SendBtn {{
    background: {t['btn_bg']};
    color: {t['btn_text']};
    border: none;
    border-radius: 8px;
    padding: 6px 16px;
    font-weight: bold;
    font-size: 14px;
}}
QPushButton#SendBtn:hover {{ background: {t['btn_hover']}; }}
QPushButton#SendBtn:pressed {{ background: {t['btn_pressed']}; }}
"""


class ChatBubble(QWidget):
    closed = pyqtSignal()
    _result_signal = pyqtSignal(str)
    _error_signal = pyqtSignal(str)

    def __init__(self, parent=None, agent_context=None, agent_loop=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.agent_context = agent_context
        self.agent_loop = agent_loop
        self._history = []
        self._streaming = False
        self._theme = _get_theme()

        self._result_signal.connect(self._on_result)
        self._error_signal.connect(self._on_error)

        self._setup_ui()
        self._load_history()
        self.resize(360, 420)

    def _setup_ui(self):
        self.setObjectName("ChatBubble")
        self.setStyleSheet(_build_style())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel("  🐾 番茄猫")
        title.setStyleSheet(f"color: {self._theme['title']}; font-weight: bold; font-size: 16px;")
        title_row.addWidget(title)
        title_row.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: #999; border: none;
                font-size: 16px; font-weight: bold;
                border-radius: 14px;
            }}
            QPushButton:hover {{ color: {self._theme['title']}; background: {self._theme['history_bg']}; }}
        """)
        close_btn.clicked.connect(self._dismiss)
        title_row.addWidget(close_btn)
        layout.addLayout(title_row)

        self.history_view = QTextEdit()
        self.history_view.setObjectName("ChatHistory")
        self.history_view.setReadOnly(True)
        self.history_view.setFont(QFont("Microsoft YaHei", 10))
        layout.addWidget(self.history_view)

        input_row = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setObjectName("ChatInput")
        self.input_box.setPlaceholderText("说点什么吧...")
        self.input_box.setFont(QFont("Microsoft YaHei", 10))
        self.input_box.returnPressed.connect(self._send_message)

        send_btn = QPushButton("发送")
        send_btn.setObjectName("SendBtn")
        send_btn.clicked.connect(self._send_message)

        input_row.addWidget(self.input_box)
        input_row.addWidget(send_btn)
        layout.addLayout(input_row)

    def _load_history(self):
        try:
            if CHAT_LOG_PATH.exists():
                msgs = json.loads(CHAT_LOG_PATH.read_text(encoding="utf-8"))
                for m in msgs[-MAX_CHAT_HISTORY:]:
                    role = m.get("role", "")
                    content = m.get("content", "")
                    if role == "user":
                        self._append_text("你", self._theme["user_color"], content)
                        self._history.append(m)
                    elif role == "assistant":
                        self._append_text("🐾 番茄猫", self._theme["assistant_color"], content)
                        self._history.append(m)
        except Exception:
            pass

    def _save_history(self):
        try:
            data = self._history[-MAX_CHAT_HISTORY:]
            CHAT_LOG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
        except Exception:
            pass

    def position_near(self, pet_geometry):
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().availableGeometry()

        px = pet_geometry.center().x()
        py = pet_geometry.center().y()

        positions = [
            (pet_geometry.right() + 12, py - self.height() // 2),
            (pet_geometry.left() - self.width() - 12, py - self.height() // 2),
            (px - self.width() // 2, pet_geometry.top() - self.height() - 12),
            (px - self.width() // 2, pet_geometry.bottom() + 12),
        ]

        for x, y in positions:
            x = max(screen.left(), min(x, screen.right() - self.width()))
            y = max(screen.top(), min(y, screen.bottom() - self.height()))
            dialog_rect = QRect(x, y, self.width(), self.height())
            if not dialog_rect.intersects(pet_geometry):
                self.move(QPoint(x, y))
                return

        x = max(screen.left(), min(px - self.width() // 2, screen.right() - self.width()))
        y = max(screen.top(), pet_geometry.top() - self.height() - 12)
        self.move(QPoint(x, y))

    def _append_text(self, role, color, text):
        safe = html_escape(text).replace("\n", "<br>")
        self.history_view.append(
            f'<p style="margin: 4px 0;"><b style="color:{color}; font-size: 13px;">{role}:</b> '
            f'<span style="font-size: 14px; line-height: 1.6;">{safe}</span></p>'
        )

    def _send_message(self):
        text = self.input_box.text().strip()
        if not text or self._streaming:
            return
        self.input_box.clear()
        self.input_box.setEnabled(False)

        self._append_text("你", self._theme["user_color"], text)
        self._history.append({"role": "user", "content": text})
        self._save_history()
        self._streaming = True

        if self.agent_context and "agent" in self.agent_context and self.agent_loop:
            self._append_text("🐾 番茄猫", self._theme["assistant_color"], "思考中...")
            threading.Thread(target=self._call_agent, args=(text,), daemon=True).start()
        else:
            self._append_text("🐾 番茄猫", self._theme["assistant_color"],
                              "（未配置 LLM，请编辑 config.toml）")
            self._streaming = False
            self.input_box.setEnabled(True)

    def _call_agent(self, user_text):
        try:
            async def _agent_call():
                result = await self.agent_context["agent"].handle_message(
                    "desktop_chat", user_text, "desktop"
                )
                return result.get("text", "")

            future = asyncio.run_coroutine_threadsafe(_agent_call(), self.agent_loop)
            result = future.result(timeout=120)
            self._result_signal.emit(result)
        except Exception as e:
            self._error_signal.emit(str(e))

    def _on_result(self, text):
        cursor = self.history_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.select(cursor.SelectionType.BlockUnderCursor)
        cursor.removeSelectedText()
        self.history_view.setTextCursor(cursor)

        self._append_text("🐾 番茄猫", self._theme["assistant_color"], text)
        self._streaming = False
        self._history.append({"role": "assistant", "content": text})
        self._save_history()
        self.input_box.setEnabled(True)

    def _on_error(self, err):
        cursor = self.history_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.select(cursor.SelectionType.BlockUnderCursor)
        cursor.removeSelectedText()
        self.history_view.setTextCursor(cursor)

        self._append_text("🐾 番茄猫", self._theme["assistant_color"], f"（出错了：{err}）")
        self._streaming = False
        self.input_box.setEnabled(True)

    def show_analysis(self, text):
        self.history_view.clear()
        self._append_text("🐾 番茄猫", self._theme["assistant_color"], text)
        self._history.append({"role": "assistant", "content": text})
        self._save_history()
        self.show()
        self.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)
        self.input_box.setFocus()

    def _event_global_pos(self, event):
        if hasattr(event, 'globalPosition'):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self._dismiss()
                return True
        if event.type() == QEvent.Type.MouseButtonPress:
            pos = self._event_global_pos(event)
            if not self.geometry().contains(pos):
                self._dismiss()
                return True
        return super().eventFilter(obj, event)

    def _dismiss(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        self.hide()
        self.closed.emit()

    def closeEvent(self, event):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        self.closed.emit()
        super().closeEvent(event)
