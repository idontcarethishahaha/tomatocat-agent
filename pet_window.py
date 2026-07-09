"""Desktop pet main window — transparent, animated, draggable, file-drop target."""
import random
import threading
import asyncio
from pathlib import Path

from PyQt6.QtWidgets import QWidget, QApplication, QLabel
from PyQt6.QtCore import Qt, QTimer, QPoint, QSize, pyqtSignal
from PyQt6.QtGui import (QPainter, QColor, QMovie,
                          QDragEnterEvent, QDropEvent, QMouseEvent)

from sprites import SPRITE_SIZE, SCALE, PALETTE, ANIMATIONS
from chat_dialog import ChatBubble
from file_handler import build_analysis_prompt
from pet_systems import PetSystems
from error_log import log_error
from config_mgr import load_config

ROOT = Path(__file__).parent
DEFAULT_GIF_DIR = ROOT / "sprites_gif"
MYCAT_DIR = Path(r"D:\images\mycat")

PET_SIZE = SPRITE_SIZE * SCALE

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
    _analysis_done = pyqtSignal(str)

    def __init__(self, agent_context=None, agent_loop=None, workspace: str = ""):
        super().__init__()
        self.agent_context = agent_context
        self.agent_loop = agent_loop
        self.workspace = workspace
        self._chat = None

        self._dragging = False
        self._drag_offset = QPoint()
        self._facing_right = True

        self._cfg = load_config()
        self._gif_dir = Path(self._cfg.get("gif_dir", str(MYCAT_DIR)))
        self._use_gif = self._cfg.get("use_gif", True)

        self.setAcceptDrops(True)
        self._setup_window()

        if self._use_gif:
            self._setup_movie()
        else:
            self._setup_sprite_animation()

        self.sys = PetSystems(self, workspace=self.workspace)

        self._anim_name = "idle"
        if self._use_gif:
            self.set_animation("idle")
        else:
            self._anim_frames, self._anim_interval = self._mood_idle()
            self._frame_idx = 0
            self._frame_counter = 0
            self._anim_timer = QTimer(self)
            self._anim_timer.timeout.connect(self._tick_animation)
            self._anim_timer.start(self._anim_interval)

        self.sys.restore_position()

        # 文件分析完成信号 → 主线程更新 UI
        self._analysis_done.connect(self._on_analysis_done)

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("番茄猫")

        screen = QApplication.primaryScreen().availableGeometry()
        self.setGeometry(
            screen.right() - PET_SIZE - 40,
            screen.bottom() - PET_SIZE - 100,
            PET_SIZE + 20, PET_SIZE + 20)

    def _setup_movie(self):
        self._movie_label = QLabel(self)
        self._movie_label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._movie_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._movie = None

    def _setup_sprite_animation(self):
        pass

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
        if self._use_gif:
            self._set_gif_animation(name, duration_ms)
        else:
            self._set_sprite_animation(name, duration_ms)

    def _set_gif_animation(self, name, duration_ms=2000):
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
            self._movie_label.setGeometry(10, 10, PET_SIZE, PET_SIZE)
            self._movie.start()
            self._anim_name = name

        if name != "idle" and duration_ms > 0:
            QTimer.singleShot(duration_ms, self._return_to_idle)

    def _set_sprite_animation(self, name, duration_ms=2000):
        if name == "idle":
            name = self._idle_for_mood()
        elif name == "idle_blink":
            name = self._blink_for_mood()
        if name not in ANIMATIONS:
            return
        if self.sys.is_sleeping and name not in ("sleep", "idle_blink",
                                                   "idle_blink_happy",
                                                   "idle_blink_sad"):
            return
        self._anim_name = name
        self._anim_frames, self._anim_interval = ANIMATIONS[name]
        self._frame_idx = 0
        self._anim_timer.setInterval(self._anim_interval)
        self.update()
        if name not in ("idle", "idle_happy", "idle_sad", "sleep"):
            QTimer.singleShot(duration_ms, self._return_to_idle)

    def _blink_for_mood(self):
        mood = self.sys.mood
        if mood >= 70:
            return "idle_blink_happy"
        elif mood < 40:
            return "idle_blink_sad"
        return "idle_blink"

    def _return_to_idle(self):
        if not self.sys.is_sleeping:
            if self._use_gif:
                self.set_animation("idle", 0)
            else:
                self.set_animation(self._idle_for_mood())

    def _idle_for_mood(self):
        mood = self.sys.mood
        if mood >= 70:
            return "idle_happy"
        elif mood < 40:
            return "idle_sad"
        return "idle"

    def _mood_idle(self):
        return ANIMATIONS[self._idle_for_mood()]

    def _current_frame(self):
        return self._anim_frames[self._frame_idx % len(self._anim_frames)]

    def _tick_animation(self):
        self._frame_counter += 1
        if self._frame_counter >= 4:
            self._frame_counter = 0
            self._frame_idx = (self._frame_idx + 1) % len(self._anim_frames)
            self.update()

    def paintEvent(self, event):
        if self._use_gif:
            pass
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        frame = self._current_frame()
        ps = SCALE
        ox = 10

        for row in range(SPRITE_SIZE):
            for col in range(SPRITE_SIZE):
                ch = frame[row][col]
                color_hex = PALETTE.get(ch)
                if color_hex is None:
                    continue
                dc = SPRITE_SIZE - 1 - col if not self._facing_right else col
                painter.fillRect(ox + dc * ps, row * ps, ps, ps, QColor(color_hex))

        mood = self.sys.mood
        if mood < 40:
            mc = QColor("#ff6666")
        elif mood < 70:
            mc = QColor("#ffcc66")
        else:
            mc = QColor("#66ff66")
        painter.fillRect(ox + 2, 2, 4, 4, mc)

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

            if self.agent_context and "agent" in self.agent_context and self.agent_loop:
                async def _agent_call():
                    result = await self.agent_context["agent"].handle_message(
                        "desktop_file_analysis", user, "desktop"
                    )
                    return result.get("text", "")

                future = asyncio.run_coroutine_threadsafe(_agent_call(), self.agent_loop)
                result = future.result(timeout=120)
            else:
                result = f"文件分析功能需要配置 LLM。路径: {path}"

            # 通过信号把结果发回主线程更新 UI
            self._analysis_done.emit(result)
        except Exception as e:
            log_error(f"File analysis failed: {e}", exc_info=True)
            self._analysis_done.emit(f"（出错了：{e}）")

    def _on_analysis_done(self, result: str):
        """在主线程中更新 UI，显示文件分析结果"""
        if self._chat is None:
            self._chat = ChatBubble(
                agent_context=self.agent_context,
                agent_loop=self.agent_loop,
            )
            self._chat.closed.connect(self._on_chat_closed)
        self._chat.show_analysis(result)
        self._chat.position_near(self.geometry())

    def _open_chat(self):
        if self._chat is None:
            self._chat = ChatBubble(
                agent_context=self.agent_context,
                agent_loop=self.agent_loop,
            )
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
