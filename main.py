import logging
import os
import signal
import sys

from AppKit import NSWorkspace
from HIServices import AXIsProcessTrusted
from PyQt6.QtCore import QPropertyAnimation
from PyQt6.QtCore import QRect
from PyQt6.QtCore import Qt
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtGui import QIcon
from PyQt6.QtGui import QPainter
from PyQt6.QtGui import QPen
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import QSystemTrayIcon
from PyQt6.QtWidgets import QWidget
from Quartz import CGWindowListCopyWindowInfo
from Quartz import kCGNullWindowID
from Quartz import kCGWindowListOptionOnScreenOnly

DEBOUNCE_DELAY = 200

# Configure logging
APP_NAME = "FocusView"
LOG_DIR = os.path.expanduser(f"~/.logs/{APP_NAME}")
LOG_FILE = os.path.join(LOG_DIR, f"{APP_NAME}.log")

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)

logger = logging.getLogger(__name__)


class BorderOverlay(QWidget):
    def __init__(self, geometry):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(geometry)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("red"), 8))
        painter.drawRect(self.rect())


def get_active_window_geometry():
    active_app_info = NSWorkspace.sharedWorkspace().activeApplication()
    if not active_app_info:
        logger.info("No active application found.")
        return None

    pid = active_app_info.get("NSApplicationProcessIdentifier")
    if pid is None:
        logger.info("No PID found for active application.")
        return None

    window_list = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly, kCGNullWindowID
    )
    if not window_list:
        logger.info("No windows found in window list.")
        return None

    for window in window_list:
        if window.get("kCGWindowOwnerPID") == pid:
            bounds = window.get("kCGWindowBounds")
            # Skip windows that are too small (e.g., tooltips, pop-ups)
            if bounds["Width"] < 50 or bounds["Height"] < 50:
                continue
            return {
                "x": int(bounds["X"]),
                "y": int(bounds["Y"]),
                "width": int(bounds["Width"]),
                "height": int(bounds["Height"]),
            }

    return None


def check_accessibility():
    if not AXIsProcessTrusted():
        logger.info("Enable Accessibility in System Settings > Privacy & Security.")
        return False
    return True


class FocusViewApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.screen_overlays = {}
        self.tray = None

        # Timer for polling window position
        self.poll_timer = QTimer()
        # Timer to delay showing the border after a move/resize
        self.debounce_timer = QTimer()
        self.last_active_rect = None
        self.animation = None  # Holds a reference to the current animation

        self.setup_signal_handler()
        self.setup_tray()
        self.setup_overlays()
        self.setup_timers()

    def setup_signal_handler(self):
        signal.signal(signal.SIGINT, self.handle_signal)

    def handle_signal(self, signum, frame):
        logger.info("Received interrupt signal, shutting down gracefully...")
        self.cleanup()
        sys.exit(0)

    def setup_tray(self):
        self.tray = QSystemTrayIcon(QIcon("assets/icon.png"))
        menu = QMenu()
        menu.addAction("Quit", self.cleanup)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def setup_overlays(self):
        for screen in QApplication.screens():
            border_overlay = BorderOverlay(screen.geometry())
            self.screen_overlays[screen] = border_overlay

    def setup_timers(self):
        # This timer will constantly check the active window's geometry
        self.poll_timer.timeout.connect(self.check_for_window_changes)
        self.poll_timer.start(100)  # Poll every 100ms

        # This timer will only fire once after a delay, to show the border
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self.show_border_at_final_position)

    def fade_overlay(self, overlay, start, end, duration=200):
        # We store the animation as an instance variable to prevent it
        # from being garbage-collected prematurely.
        self.animation = QPropertyAnimation(overlay, b"windowOpacity")
        self.animation.setStartValue(start)
        self.animation.setEndValue(end)
        self.animation.setDuration(duration)
        self.animation.start()

    def check_for_window_changes(self):
        active_window_geometry_dict = get_active_window_geometry()

        current_rect = None
        if active_window_geometry_dict:
            current_rect = QRect(
                active_window_geometry_dict["x"],
                active_window_geometry_dict["y"],
                active_window_geometry_dict["width"],
                active_window_geometry_dict["height"],
            )

        # Check if the window has moved, resized, or changed focus
        if current_rect != self.last_active_rect:
            self.last_active_rect = current_rect

            # Instantly hide all borders to prevent jitter/artifacts
            for overlay in self.screen_overlays.values():
                overlay.hide()

            # If there's an active window, start the timer to show the border
            # after a short period of inactivity.
            if current_rect:
                self.debounce_timer.start(DEBOUNCE_DELAY)  # 150ms delay

    def show_border_at_final_position(self):
        # This method is called only when the window has stopped moving
        if not self.last_active_rect:
            return

        active_screen = QApplication.screenAt(self.last_active_rect.center())

        for screen, overlay in self.screen_overlays.items():
            if screen == active_screen:
                overlay.setGeometry(self.last_active_rect)
                # Start transparent, show the widget, then fade it in.
                overlay.setWindowOpacity(0.0)
                overlay.show()
                self.fade_overlay(overlay, 0.0, 1.0, duration=500)
            else:
                overlay.hide()

    def cleanup(self):
        logger.info("Cleaning up resources...")
        self.poll_timer.stop()
        self.debounce_timer.stop()
        if self.animation:
            self.animation.stop()
        for overlay in self.screen_overlays.values():
            overlay.hide()
            overlay.deleteLater()
        if self.tray:
            self.tray.hide()
            self.tray.deleteLater()
        self.app.quit()

    def run(self):
        if not check_accessibility():
            logger.error("Accessibility permissions required. Exiting.")
            return
        try:
            sys.exit(self.app.exec())
        except KeyboardInterrupt:
            logger.info("Caught KeyboardInterrupt, shutting down gracefully...")
            self.cleanup()
            sys.exit(0)


if __name__ == "__main__":
    app = FocusViewApp()
    app.run()
