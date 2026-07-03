"""Backend systems for the desktop pet: tray, menu, idle, mood, clipboard."""
import random
import time
import sys

from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QLabel
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPainter, QColor, QIcon, QPixmap, QAction

from sprites import ANIMATIONS
from config_mgr import load_config, save_config
from tomato_timer import TomatoTimer


class IdleDetector:
    """Cross-platform user idle time detection."""
    @staticmethod
    def get_idle_seconds():
        if sys.platform == "win32":
            return IdleDetector._windows_idle()
        elif sys.platform == "darwin":
            return IdleDetector._macos_idle()
        else:
            return IdleDetector._linux_idle()

    @staticmethod
    def _windows_idle():
        try:
            import ctypes
            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint),
                            ("dwTime", ctypes.c_uint)]
            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                return millis / 1000.0
        except Exception:
            pass
        return 0

    @staticmethod
    def _macos_idle():
        try:
            import subprocess
            out = subprocess.check_output(
                ["ioreg", "-c", "IOHIDSystem"],
                stderr=subprocess.DEVNULL
            ).decode()
            for line in out.splitlines():
                if "HIDIdleTime" in line:
                    val = line.split("=")[-1].strip()
                    return int(val) / 1_000_000_000.0
        except Exception:
            pass
        return 0

    @staticmethod
    def _linux_idle():
        try:
            import subprocess
            out = subprocess.check_output(
                ["xprintidle"], stderr=subprocess.DEVNULL
            ).decode()
            return int(out.strip()) / 1000.0
        except Exception:
            pass
        return 0


class PetSystems:
    """Manages tray, menu, idle detection, mood, proactive chat, clipboard."""

    def __init__(self, pet_window):
        self.pet = pet_window
        self._cfg = load_config()
        self._tray = None
        self._menu = None
        self._bubble_label = None
        self._tomato_timer = None

        # Mood
        self._mood = self._cfg.get("mood", 70)

        # Idle sleep
        self._is_sleeping = False
        self._user_idle_seconds = 0

        # Clipboard
        self._last_clipboard = ""

        self._setup_tray()
        self._setup_menu()
        self._setup_timers()

    # ─── timers ─────────────────────────────────────────

    def _setup_timers(self):
        # Idle check every 3s
        self._idle_timer = QTimer(self.pet)
        self._idle_timer.timeout.connect(self._check_user_idle)
        self._idle_timer.start(3000)

        # Mood decay every 60s
        self._mood_timer = QTimer(self.pet)
        self._mood_timer.timeout.connect(self._decay_mood)
        self._mood_timer.start(60000)

        # Proactive behavior every 5-12s
        self._behave_timer = QTimer(self.pet)
        self._behave_timer.timeout.connect(self._random_behavior)
        self._behave_timer.start(random.randint(5000, 12000))

    # ─── tray ───────────────────────────────────────────

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        # 优先使用用户自定义图标
        from pathlib import Path
        ROOT = Path(__file__).parent
        icon_paths = [
            ROOT / "icon.ico",
            ROOT / "icon.png",
            ROOT / "cat.ico",
            ROOT / "cat.png",
            ROOT / "pet.ico",
            ROOT / "pet.png",
        ]
        icon = None
        for ip in icon_paths:
            if ip.exists():
                icon = QIcon(str(ip))
                break

        if icon is None:
            # fallback: draw a simple pixel cat
            pixmap = QPixmap(32, 32)
            pixmap.fill(Qt.GlobalColor.transparent)
            p = QPainter(pixmap)
            p.fillRect(10, 6, 4, 4, QColor("#D4692B"))
            p.fillRect(18, 6, 4, 4, QColor("#D4692B"))
            p.fillRect(4, 10, 24, 16, QColor("#FF8C42"))
            p.fillRect(8, 14, 8, 8, QColor("#FFFFFF"))
            p.fillRect(16, 14, 8, 8, QColor("#FFFFFF"))
            p.fillRect(11, 17, 3, 3, QColor("#2D2D2D"))
            p.fillRect(19, 17, 3, 3, QColor("#2D2D2D"))
            p.end()
            icon = QIcon(pixmap)

        self._tray = QSystemTrayIcon(icon, self.pet)
        self._tray.setToolTip(" 🍅🐱 番茄猫")

        m = QMenu()
        m.addAction("显示/隐藏").triggered.connect(self._toggle_visible)
        m.addAction("聊天").triggered.connect(self.pet._open_chat)
        m.addSeparator()
        m.addAction("退出").triggered.connect(self.pet._quit_app)
        self._tray.setContextMenu(m)
        self._tray.activated.connect(self._on_tray_activate)
        self._tray.show()

    def _on_tray_activate(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._toggle_visible()

    def _toggle_visible(self):
        self.pet.setVisible(not self.pet.isVisible())

    @property
    def tray(self):
        return self._tray

    # ─── menu ───────────────────────────────────────────

    def _setup_menu(self):
        self._menu = QMenu(self.pet)
        self._menu.setStyleSheet("""
            QMenu { background: #FFFFFF; color: #333333;
                    border: 1px solid #FFB6C1; border-radius: 10px;
                    padding: 6px; }
            QMenu::item { padding: 8px 28px; border-radius: 6px; }
            QMenu::item:selected { background: #FFB6C1; color: #FFFFFF; }
            QMenu::separator { height: 1px; background: #FFE4E9; margin: 4px 8px; }
        """)

        # -- Primary actions --
        self._menu.addAction("💬 聊天").triggered.connect(self.pet._open_chat)
        self._menu.addAction("😴 睡觉/醒来").triggered.connect(self._toggle_sleep)
        self._menu.addAction("🍅 番茄钟").triggered.connect(self._open_tomato_timer)
        self._menu.addSeparator()

        # -- Quick launch --
        launch = self._menu.addMenu("🚀 快速启动")
        apps = self._cfg.get("quick_launch", [])
        if not apps:
            import shutil
            for name, exe in [("VS Code", "code"), ("Calculator", "calc")]:
                found = shutil.which(exe)
                if found:
                    apps.append({"name": name, "path": found})
        if apps:
            for app in apps:
                if isinstance(app, dict):
                    a = launch.addAction(app["name"])
                    a.triggered.connect(lambda checked, p=app["path"]: self._launch_app(p))
                elif isinstance(app, list) and len(app) == 2:
                    a = launch.addAction(app[0])
                    a.triggered.connect(lambda checked, p=app[1]: self._launch_app(p))
        else:
            launch.addAction("(在 config.json 中配置)").setEnabled(False)

        # -- Settings --
        settings = self._menu.addMenu("⚙️ 设置")
        self._top_action = settings.addAction("📌 窗口置顶")
        self._top_action.setCheckable(True)
        self._top_action.setChecked(True)
        self._top_action.triggered.connect(self._toggle_top)

        self._auto_sleep_action = settings.addAction("💤 自动休眠")
        self._auto_sleep_action.setCheckable(True)
        self._auto_sleep_action.setChecked(True)
        self._auto_sleep_action.triggered.connect(self._toggle_auto_sleep)

        self._clipboard_action = settings.addAction("📋 剪贴板提示")
        self._clipboard_action.setCheckable(True)
        self._clipboard_action.setChecked(True)
        self._clipboard_action.triggered.connect(self._toggle_clipboard)

        self._auto_chat_action = settings.addAction("💭 主动搭话")
        self._auto_chat_action.setCheckable(True)
        self._auto_chat_action.setChecked(True)
        self._auto_chat_action.triggered.connect(self._toggle_auto_chat)

        # -- Status --
        self._menu.addSeparator()
        mood_label = "😊" if self._mood >= 70 else ("😐" if self._mood >= 40 else "😿")
        self._mood_action = self._menu.addAction(f"心情 {mood_label} {self._mood}%")
        self._mood_action.setEnabled(False)

        # -- About & Quit --
        self._menu.addSeparator()
        self._menu.addAction("ℹ️ 关于").triggered.connect(self._show_about)
        self._menu.addAction("❌ 退出").triggered.connect(self.pet._quit_app)

    def _refresh_mood_display(self):
        if hasattr(self, '_mood_action') and self._mood_action:
            mood_label = "😊" if self._mood >= 70 else ("😐" if self._mood >= 40 else "😿")
            self._mood_action.setText(f"心情 {mood_label} {self._mood}%")

    def _toggle_auto_sleep(self):
        if hasattr(self, '_idle_timer'):
            if self._idle_timer.isActive():
                self._idle_timer.stop()
            else:
                self._idle_timer.start()

    def _toggle_clipboard(self):
        pass  # handled by checkbox state visually; timer always runs

    def _toggle_auto_chat(self):
        if hasattr(self, '_behave_timer'):
            if self._behave_timer.isActive():
                self._behave_timer.stop()
            else:
                self._behave_timer.start()

    def _show_about(self):
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.about(
            self.pet,
            "关于 🍅🐱 番茄猫",
            "🍅🐱 番茄猫 v1.0\n\n"
            "一只住在你桌面上的像素小猫咪。\n"
            "PyQt6 + AI 驱动。\n\n"
            "GitHub: https://github.com/idontcarethishahaha/tomatocat-agent\n"
            "License: MIT"
        )

    @property
    def menu(self):
        return self._menu

    def _launch_app(self, path):
        import os
        os.startfile(path)

    def _toggle_top(self):
        flags = self.pet.windowFlags()
        if flags & Qt.WindowType.WindowStaysOnTopHint:
            self.pet.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.pet.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        self.pet.show()

    # ─── idle / sleep ───────────────────────────────────

    def _check_user_idle(self):
        self._user_idle_seconds = IdleDetector.get_idle_seconds()
        if self._user_idle_seconds > 300 and not self._is_sleeping:
            self._enter_sleep()
        elif self._user_idle_seconds < 2 and self._is_sleeping:
            self._wake_up()

    def _enter_sleep(self):
        self._is_sleeping = True
        self.pet.set_animation("sleep")
        if self.pet._chat:
            self.pet._chat.hide()

    def _wake_up(self):
        self._is_sleeping = False
        self.pet.set_animation("happy", 1500)
        self._mood = min(100, self._mood + 5)
        hour = time.localtime().tm_hour
        if 6 <= hour < 12:
            self._show_bubble("早上好喵~")
        elif 12 <= hour < 18:
            self._show_bubble("下午好喵~")
        elif 18 <= hour < 23:
            self._show_bubble("晚上好喵~")
        else:
            self._show_bubble("还不睡吗？别熬夜太晚喵~")

    def _toggle_sleep(self):
        if self._is_sleeping:
            self._wake_up()
        else:
            self._enter_sleep()

    def _open_tomato_timer(self):
        if self._tomato_timer is None:
            self._tomato_timer = TomatoTimer(pet_window=self.pet)
        self._tomato_timer.show()
        self._tomato_timer.raise_()
        self._tomato_timer.activateWindow()

    @property
    def is_sleeping(self):
        return self._is_sleeping

    # ─── mood ───────────────────────────────────────────

    @property
    def mood(self):
        return self._mood

    def boost_mood(self, amount):
        self._mood = min(100, self._mood + amount)
        self._refresh_mood_display()

    def _decay_mood(self):
        if not self._is_sleeping:
            self._mood = max(0, self._mood - 1)
        self._refresh_mood_display()
        self._save_state()

    def _save_state(self):
        self._cfg["window_pos"] = [self.pet.x(), self.pet.y()]
        self._cfg["mood"] = self._mood
        save_config(self._cfg)

    def restore_position(self):
        pos = self._cfg.get("window_pos")
        if pos and len(pos) == 2:
            x, y = pos
            from PyQt6.QtWidgets import QApplication
            screen = QApplication.primaryScreen().availableGeometry()
            if screen.contains(x, y):
                self.pet.move(x, y)

    # ─── proactive behavior ─────────────────────────────

    def _random_behavior(self):
        if self._is_sleeping:
            return
        actions = ["blink", "blink", "walk", "sit", "sit", "chat"]
        action = random.choice(actions)
        if action == "blink":
            self.pet.set_animation("idle_blink", 900)  # mood-aware in set_animation
        elif action == "walk":
            self._start_auto_walk()
        elif action == "chat":
            self._proactive_chat()
        self._behave_timer.setInterval(random.randint(5000, 12000))

    def _proactive_chat(self):
        hour = time.localtime().tm_hour
        if self._mood < 30:
            msgs = ["好孤独...摸摸我喵~", "没人跟我说话了...喵..."]
        elif hour < 9:
            msgs = ["早安！今天也要元气满满喵~", "该吃早饭了喵！"]
        elif hour < 12:
            msgs = ["在努力工作吗？加油喵！", "别忘了伸个懒腰喵~"]
        elif hour < 14:
            msgs = ["午饭时间到！快去吃饭喵~", "我也饿了喵..."]
        elif hour < 18:
            msgs = ["下午是写代码的好时间喵~", "要喝茶吗？喵？"]
        elif hour < 22:
            msgs = ["今天过得怎么样？喵~", "该放松一下了喵~"]
        else:
            msgs = ["很晚了...该睡了喵~", "还在熬夜？别太累了喵~"]
        if random.random() < 0.4:
            self._show_bubble(random.choice(msgs))

    # ─── auto walk ──────────────────────────────────────

    def _start_auto_walk(self):
        d = random.choice([-1, 1])
        steps = random.randint(5, 15)
        self.pet._facing_right = (d > 0)
        self.pet.set_animation("walk", 3000)

        def step():
            nonlocal steps
            if steps <= 0:
                return
            self.pet.move(self.pet.x() + d * 4, self.pet.y())
            self.pet._facing_right = (d > 0)
            steps -= 1
            if steps > 0:
                QTimer.singleShot(120, step)

        QTimer.singleShot(0, step)

    # ─── clipboard ──────────────────────────────────────

    def check_clipboard(self):
        try:
            from PyQt6.QtWidgets import QApplication
            cb = QApplication.clipboard()
            text = cb.text()
            if text and text != self._last_clipboard and len(text) > 5:
                self._last_clipboard = text
                preview = text[:50].replace("\n", " ")
                self._show_bubble(f"你复制了：{preview}... 需要帮忙吗？")
        except:
            pass

    # ─── bubble ─────────────────────────────────────────

    def _show_bubble(self, text):
        if self._is_sleeping:
            return
        if self._bubble_label:
            self._bubble_label.close()
        bubble = QLabel(text, self.pet)
        bubble.setStyleSheet("""
            background: #FFFFFF; color: #FF6B9D; font-size: 13px;
            border: 1.5px solid #FFB6C1; border-radius: 12px; padding: 6px 12px;
            font-weight: 500;
        """)
        bubble.adjustSize()
        bubble.move(self.pet.width() + 5, -10)
        bubble.show()
        self._bubble_label = bubble
        QTimer.singleShot(4000, bubble.close)

    def show_bubble(self, text):
        self._show_bubble(text)

    # ─── cleanup ────────────────────────────────────────

    def save_and_cleanup(self):
        self._save_state()
        if self._tray:
            self._tray.hide()
