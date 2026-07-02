"""Desktop pet main window — transparent, animated, draggable, file-drop target."""
import random
import threading
import asyncio
from pathlib import Path

from PyQt6.QtWidgets import QWidget, QApplication, QLabel
from PyQt6.QtCore import Qt, QTimer, QPoint, QSize
from PyQt6.QtGui import (QPainter, QColor, QMovie,
                          QDragEnterEvent, QDropEvent, QMouseEvent)

from chat_dialog import ChatBubble
from file_handler import build_analysis_prompt
from pet_systems import PetSystems
from error_log import log_error
from config_mgr import load_config

ROOT = Path(__file__).parent
DEFAULT_GIF_DIR = ROOT / "sprites_gif"
MYCAT_DIR = Path(r"D:\ai学习项目\akashic-agent-study\mycat")

PET_SIZE = 100

ANIMATION_MAP = {
    "idle": "idle.gif",
    "idle_happy": "idle.gif",
    "idle_sad": "idle.gif",
    "idle_blink": "idle.gif",
    "idle_blink_happy": "idle.gif",
    "idle_blink_sad": "idle.gif",
    "walk": "running.gif",
    "think": "waiting.gif",
    "happy": "waving.gif",
    "sleep": "idle.gif",
}


class PetWindow(QWidget):
    def __init__(self, agent_context=None):
        super().__init__()
        self.agent_context = agent_context
        self._chat = None

        self._dragging = False
        self._drag_offset = QPoint()
        self._facing_right = True

        self._cfg = load_config()
        self._gif_dir = Path(self._cfg.get("gif_dir", str(MYCAT_DIR)))

        self.setAcceptDrops(True)
        self._setup_window()
        self._setup_movie()

        self.sys = PetSystems(self)

        self._anim_name = "idle"
        self.set_animation("idle")

        self.sys.restore_position()

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("番茄猫")

        screen = QApplication.primaryScreen().availableGeometry()
        self.setGeometry(
            screen.right() - PET_SIZE - 40,
            screen.bottom() - PET_SIZE - 100,
            PET_SIZE, PET_SIZE)

    def _setup_movie(self):
        self._movie_label = QLabel(self)
        self._movie_label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._movie_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._movie = None

    def _gif_path(self, anim_name):
        gif_file = ANIMATION_MAP.get(anim_name, "idle.gif")
        path = self._gif_dir / gif_file
        if path.exists():
            return str(path)
        path = MYCAT_DIR / gif_file
        if path.exists():
            return str(path)
        path = DEFAULT_GIF_DIR / gif_file
        if path.exists():
            return str(path)
        return None

    def set_animation(self, name, duration_ms=2000):
        if name in ("idle", "idle_blink"):
            name = "idle"
        elif name in ("idle_happy", "idle_blink_happy"):
            name = "idle"
        elif name in ("idle_sad", "idle_blink_sad"):
            name = "idle"

        gif_path = self._gif_path(name)
        if not gif_path:
            gif_path = self._gif_path("idle")

        if self._movie is None or self._anim_name != name:
            if self._movie:
                self._movie.stop()
            self._movie = QMovie(gif_path)
            self._movie.setScaledSize(QSize(PET_SIZE, PET_SIZE))
            self._movie_label.setMovie(self._movie)
            self._movie_label.setGeometry(0, 0, PET_SIZE, PET_SIZE)
            self._movie.start()
            self._anim_name = name

        if name != "idle" and duration_ms > 0:
            QTimer.singleShot(duration_ms, self._return_to_idle)

    def _return_to_idle(self):
        if not self.sys.is_sleeping:
            self.set_animation("idle", 0)

    def paintEvent(self, event):
        pass

    def _event_pos(self, event):
        if hasattr(event, 'position'):
            return event.position().toPoint()
        return event.pos()

    def _event_global_pos(self, event):
        if hasattr(event, 'globalPosition'):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.sys.is_sleeping:
                self.sys._wake_up()
            self._dragging = True
            self._drag_offset = self._event_pos(event)
            self.sys.boost_mood(3)
            self.set_animation("happy", 1500)
        elif event.button() == Qt.MouseButton.RightButton:
            self.sys.menu.popup(self._event_global_pos(event))

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            new_pos = self._event_global_pos(event) - self._drag_offset
            x, y = self._clamp(new_pos.x(), new_pos.y())
            self.move(x, y)
        else:
            self._facing_right = (self._event_global_pos(event).x() > self.geometry().center().x())

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            was_drag = self._dragging
            self._dragging = False
            if was_drag:
                delta = self._event_global_pos(event) - (self.pos() + self._drag_offset)
                if delta.manhattanLength() < 12:
                    self._open_chat()
                self.sys._save_state()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        self._open_chat()

    def enterEvent(self, event):
        self.sys.boost_mood(2)
        if self.sys.is_sleeping:
            self.sys._wake_up()

    def contextMenuEvent(self, event):
        self.sys.menu.popup(self._event_global_pos(event))

    def _clamp(self, x, y):
        screen = QApplication.primaryScreen().availableGeometry()
        x = max(screen.left(), min(x, screen.right() - self.width()))
        y = max(screen.top(), min(y, screen.bottom() - self.height()))
        return x, y

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.set_animation("think", 3000)

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self.sys.boost_mood(10)
            self.set_animation("happy", 2000)
            threading.Thread(target=self._analyze_file, args=(path,),
                             daemon=True).start()
        event.acceptProposedAction()

    def _analyze_file(self, path):
        try:
            system, user = build_analysis_prompt(path)
            msgs = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]

            if self.agent_context and "agent" in self.agent_context:
                async def _call_agent():
                    result = await self.agent_context["agent"].handle_message(
                        "desktop_file_analysis", user, "desktop"
                    )
                    return result.get("text", "")
                result = asyncio.run(_call_agent())
            else:
                result = f"文件分析功能需要配置 LLM。路径: {path}"

            if self._chat is None:
                self._chat = ChatBubble(agent_context=self.agent_context)
                self._chat.closed.connect(self._on_chat_closed)
            self._chat.show_analysis(result)
            self._chat.position_near(self.geometry())
        except Exception as e:
            log_error(f"File analysis failed: {e}", exc_info=True)

    def _open_chat(self):
        if self._chat is None:
            self._chat = ChatBubble(agent_context=self.agent_context)
            self._chat.closed.connect(self._on_chat_closed)
        self._chat.show()
        self._chat.position_near(self.geometry())
        self._chat.raise_()
        self._chat.input_box.setFocus()

    def _on_chat_closed(self):
        self._chat = None

    def check_clipboard(self):
        self.sys.check_clipboard()

    def _quit_app(self):
        self.sys.save_and_cleanup()
        if self._chat:
            self._chat.close()
        QApplication.quit()

    def closeEvent(self, event):
        self.sys._save_state()
        super().closeEvent(event)

    @property
    def _is_sleeping(self):
        return self.sys.is_sleeping

    @property
    def _mood(self):
        return self.sys.mood

    def _enter_sleep(self):
        self.sys._enter_sleep()

    def _show_bubble(self, text):
        self.sys.show_bubble(text)
