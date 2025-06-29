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

# Configure logging
APP_NAME = "FocusView"
LOG_DIR = os.path.expanduser(f"~/.logs/{APP_NAME}")
LOG_FILE = os.path.join(LOG_DIR, f"{APP_NAME}.log")

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


class BorderOverlay(QWidget):
    def __init__(self, screen_geometry):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.screen_geometry = screen_geometry
        self.setGeometry(screen_geometry)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("red"), 8))
        painter.drawRect(self.rect())


def fade_overlay(overlay, start, end, duration=200):
    animation = QPropertyAnimation(overlay, b"windowOpacity")
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.setDuration(duration)
    animation.start()


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
            logger.info(f"Found window with bounds: {bounds}")
            return {
                "x": int(bounds["X"]),
                "y": int(bounds["Y"]),
                "width": int(bounds["Width"]),
                "height": int(bounds["Height"]),
            }

    logger.info(f"No window found for PID {pid}.")
    return None


def check_accessibility():
    if not AXIsProcessTrusted():
        logger.info("Enable Accessibility in System Settings > Privacy & Security.")
        return False
    return True


class FocusViewApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.border_overlays = []
        self.tray = None
        self.timer = None
        self.setup_signal_handler()
        self.setup_tray()
        self.setup_overlays()
        self.setup_timer()

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
            self.border_overlays.append({"overlay": border_overlay, "screen": screen})

    def setup_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_overlays)
        self.timer.start(100)

    def update_overlays(self):
        active_window_geometry_dict = get_active_window_geometry()
        active_window_rect = None
        if active_window_geometry_dict:
            active_window_rect = QRect(
                active_window_geometry_dict["x"],
                active_window_geometry_dict["y"],
                active_window_geometry_dict["width"],
                active_window_geometry_dict["height"],
            )

        for border_info in self.border_overlays:
            border_overlay = border_info["overlay"]
            screen = border_info["screen"]
            screen_rect = screen.geometry()

            if active_window_rect and screen_rect.intersects(active_window_rect):
                # Calculate the intersection of the active window and the screen
                intersection = screen_rect.intersected(active_window_rect)
                # Adjust the geometry relative to the screen's top-left corner
                adjusted_geometry = QRect(
                    intersection.x() - screen_rect.x(),
                    intersection.y() - screen_rect.y(),
                    intersection.width(),
                    intersection.height(),
                )
                border_overlay.setGeometry(adjusted_geometry)
                border_overlay.show()
            else:
                border_overlay.hide()

    def cleanup(self):
        logger.info("Cleaning up resources...")
        if self.timer:
            self.timer.stop()
        for border_info in self.border_overlays:
            overlay = border_info["overlay"]
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
