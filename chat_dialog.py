"""Chat bubble dialog for the desktop pet."""
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QTextEdit, QLineEdit, QPushButton, QLabel)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint, QEvent, QRect
from PyQt6.QtGui import QFont, QMouseEvent

import threading
import json
import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from html import escape as html_escape
from config_mgr import load_config

_CHAT_LOG_PATH = None
MAX_CHAT_HISTORY = 50


def set_chat_log_path(path: str | Path):
    global _CHAT_LOG_PATH
    _CHAT_LOG_PATH = Path(path)


def _get_chat_log_path():
    if _CHAT_LOG_PATH is not None:
        return _CHAT_LOG_PATH
    return Path(__file__).parent / "chat-history.json"

_default_theme = {
    "bg": "#FCF6EB",
    "border": "#E46B48",
    "title": "#E66042",
    "history_bg": "#FFF9F1",
    "text_color": "#4A372E",
    "input_bg": "#FFFCF6",
    "input_border": "#E8B19A",
    "btn_bg": "#E46B48",
    "btn_hover": "#E66042",
    "btn_pressed": "#C95138",
    "btn_text": "#FFFFFF",
    "user_color": "#6FA8FF",
    "assistant_color": "#E66042",
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
    _result_signal = pyqtSignal(str, dict)
    _error_signal = pyqtSignal(str, str)
    _stream_signal = pyqtSignal(str, dict)
    _status_signal = pyqtSignal(str, str)

    def __init__(self, parent=None, agent_context=None, agent_loop=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.agent_context = agent_context
        self.agent_loop = agent_loop
        self._history = []
        self._streaming = False
        self._request_seq = 0
        self._active_request_id = None
        self._request_futures = {}
        self._cancelled_requests = set()
        self._theme = _get_theme()

        self._result_signal.connect(self._on_result)
        self._error_signal.connect(self._on_error)
        self._stream_signal.connect(self._on_stream_delta)
        self._status_signal.connect(self._on_status)

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
                background: #F7EBDD; color: #A94A35; border: 1px solid #E8B19A;
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
        send_btn.clicked.connect(self._send_or_stop)

        input_row.addWidget(self.input_box)
        self.send_btn = send_btn
        input_row.addWidget(send_btn)
        layout.addLayout(input_row)

    def _send_or_stop(self):
        if self._streaming:
            self._stop_generation()
        else:
            self._send_message()

    def _load_history(self):
        try:
            path = _get_chat_log_path()
            if path.exists():
                msgs = json.loads(path.read_text(encoding="utf-8"))
                for m in msgs[-MAX_CHAT_HISTORY:]:
                    role = m.get("role", "")
                    content = m.get("content", "")
                    if role == "user":
                        self._append_text("你", self._theme["user_color"], content)
                        self._history.append(m)
                    elif role == "assistant":
                        self._append_text("番茄猫", self._theme["assistant_color"], content)
                        self._history.append(m)
        except Exception:
            pass

    def _save_history(self):
        try:
            path = _get_chat_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            data = self._history[-MAX_CHAT_HISTORY:]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
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
        self._stream_text = ""
        self._request_seq += 1
        request_id = str(self._request_seq)
        self._active_request_id = request_id
        self.send_btn.setText("停止")

        if self.agent_context and "agent" in self.agent_context and self.agent_loop:
            self._append_text("🐾 番茄猫", self._theme["assistant_color"], "思考中...")
            threading.Thread(
                target=self._call_agent,
                args=(request_id, text),
                daemon=True,
            ).start()
        else:
            self._append_text("🐾 番茄猫", self._theme["assistant_color"],
                              "（未配置 LLM，请编辑 config.toml）")
            self._streaming = False
            self.send_btn.setText("发送")
            self.input_box.setEnabled(True)

    def _call_agent(self, request_id, user_text):
        future = None
        try:
            async def _on_delta(channel, session_key, delta_type, delta):
                if request_id in self._cancelled_requests:
                    return
                if delta_type == "streaming_delta":
                    self._stream_signal.emit(request_id, dict(delta))
                elif delta_type == "tool_call_start":
                    self._status_signal.emit(request_id, "正在调用工具…")

            async def _agent_call():
                result = await self.agent_context["agent"].handle_message(
                    "desktop_chat", user_text, "desktop", on_delta=_on_delta
                )
                return result

            future = asyncio.run_coroutine_threadsafe(_agent_call(), self.agent_loop)
            self._request_futures[request_id] = future
            if request_id in self._cancelled_requests:
                future.cancel()
            result = future.result(timeout=120)
            if request_id not in self._cancelled_requests:
                self._result_signal.emit(request_id, result)
        except FutureTimeoutError:
            if future is not None and not future.done():
                future.cancel()
            if request_id not in self._cancelled_requests:
                self._error_signal.emit(request_id, "请求超时，已停止生成")
        except Exception as e:
            if request_id not in self._cancelled_requests:
                self._error_signal.emit(request_id, str(e))
        finally:
            self._request_futures.pop(request_id, None)
            self._cancelled_requests.discard(request_id)

    def _stop_generation(self):
        if not self._streaming:
            return
        request_id = self._active_request_id
        if request_id is None:
            return
        self._cancelled_requests.add(request_id)
        future = self._request_futures.get(request_id)
        if future is not None and not future.done():
            future.cancel()
        self._on_error(request_id, "已停止生成")

    def _on_status(self, request_id, status):
        if request_id == self._active_request_id and self._streaming and not self._stream_text:
            self._replace_last_message(status)

    def _replace_last_message(self, text):
        cursor = self.history_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.select(cursor.SelectionType.BlockUnderCursor)
        cursor.removeSelectedText()
        self.history_view.setTextCursor(cursor)
        self._append_text("TomatoCat", self._theme["assistant_color"], text)

    def _on_stream_delta(self, request_id, delta):
        if request_id != self._active_request_id or not self._streaming:
            return
        content = delta.get("content_delta", "")
        if content:
            self._stream_text += content
            self._replace_last_message(self._stream_text)

    def _on_result(self, request_id, result):
        if request_id != self._active_request_id or not self._streaming:
            return
        text = result.get("text", "")
        media_paths = result.get("media_paths", [])

        cursor = self.history_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.select(cursor.SelectionType.BlockUnderCursor)
        cursor.removeSelectedText()
        self.history_view.setTextCursor(cursor)

        self._append_text("🐾 番茄猫", self._theme["assistant_color"], text)

        for media_path in media_paths:
            try:
                media_str = str(media_path)
                if media_str.lower().endswith(".gif"):
                    from PyQt6.QtCore import QUrl
                    from PyQt6.QtGui import QTextDocument
                    gif_html = f'<img src="{QUrl.fromLocalFile(media_str).toString()}" style="max-height: 150px;"/>'
                    self.history_view.append(gif_html)
                else:
                    from PyQt6.QtCore import QUrl
                    img_html = f'<img src="{QUrl.fromLocalFile(media_str).toString()}" style="max-height: 200px;"/>'
                    self.history_view.append(img_html)
            except Exception:
                pass

        self._streaming = False
        self._active_request_id = None
        self.send_btn.setText("发送")
        self._history.append({"role": "assistant", "content": text})
        self._save_history()
        self.input_box.setEnabled(True)

    def _on_error(self, request_id, err):
        if request_id != self._active_request_id or not self._streaming:
            return
        cursor = self.history_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.select(cursor.SelectionType.BlockUnderCursor)
        cursor.removeSelectedText()
        self.history_view.setTextCursor(cursor)

        self._append_text("🐾 番茄猫", self._theme["assistant_color"], f"（出错了：{err}）")
        self._streaming = False
        self._active_request_id = None
        self.send_btn.setText("发送")
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
                # Do not close the chat bubble via a background/keyboard
                # click; closing is intentionally explicit via the top-right
                # button.
                return True
        return super().eventFilter(obj, event)

    def _dismiss(self):
        if self._streaming:
            self._stop_generation()
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        self.hide()
        self.closed.emit()

    def closeEvent(self, event):
        if self._streaming:
            self._stop_generation()
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)
        self.closed.emit()
        super().closeEvent(event)
