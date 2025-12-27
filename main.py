import logging
import os
import signal
import sys

from AppKit import NSWorkspace
from PyQt6.QtCore import QPropertyAnimation, QRect, QSettings, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QColorDialog, QMenu, QSystemTrayIcon, QWidget
from Quartz import CGWindowListCopyWindowInfo, kCGNullWindowID, kCGWindowListOptionOnScreenOnly

DEBOUNCE_DELAY = 200

# Configure logging
APP_NAME = "FocusView"
ORG_NAME = "FocusView"
LOG_DIR = os.path.expanduser(f"~/.logs/{APP_NAME}")
LOG_FILE = os.path.join(LOG_DIR, f"{APP_NAME}.log")

# Default highlight color
DEFAULT_HIGHLIGHT_COLOR = "#FF0000"

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)

logger = logging.getLogger(__name__)


class BorderOverlay(QWidget):
    def __init__(self, geometry, highlight_color="#FF0000"):
        super().__init__()
        self.highlight_color = highlight_color
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(geometry)

    def set_highlight_color(self, color):
        self.highlight_color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(self.highlight_color), 8))
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

    window_list = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)
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


class FocusViewApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setOrganizationName(ORG_NAME)
        self.app.setApplicationName(APP_NAME)
        self.screen_overlays = {}

        # Load settings
        self.settings = QSettings()
        self.highlight_color = self.settings.value("highlight_color", DEFAULT_HIGHLIGHT_COLOR)

        # Timer for polling window position
        self.poll_timer = QTimer()
        # Timer to delay showing the border after a move/resize
        self.debounce_timer = QTimer()
        self.last_active_rect = None
        self.animation = None  # Holds a reference to the current animation

        self.setup_signal_handler()
        self.setup_overlays()
        self.setup_timers()
        self.setup_system_tray()

    def setup_signal_handler(self):
        signal.signal(signal.SIGINT, self.handle_signal)

    def handle_signal(self, signum, frame):
        logger.info("Received interrupt signal, shutting down gracefully...")
        self.cleanup()
        sys.exit(0)

    def setup_overlays(self):
        for screen in QApplication.screens():
            border_overlay = BorderOverlay(screen.geometry(), self.highlight_color)
            self.screen_overlays[screen] = border_overlay

    def setup_system_tray(self):
        # Create system tray icon
        self.tray_icon = QSystemTrayIcon(self.app)

        # Create a simple icon (colored square)
        from PyQt6.QtGui import QPixmap

        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(self.highlight_color))
        self.tray_icon.setIcon(QIcon(pixmap))

        # Create tray menu
        tray_menu = QMenu()

        # Change highlight color action
        change_color_action = QAction("Change Highlight Color...", self.app)
        change_color_action.triggered.connect(self.show_color_picker)
        tray_menu.addAction(change_color_action)

        tray_menu.addSeparator()

        # Quit action
        quit_action = QAction("Quit", self.app)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.setToolTip("FocusView - Click to access options")
        self.tray_icon.show()

    def show_color_picker(self):
        current_color = QColor(self.highlight_color)
        color = QColorDialog.getColor(current_color, None, "Choose Highlight Color")

        if color.isValid():
            self.highlight_color = color.name()
            self.save_highlight_color()
            self.update_overlay_colors()
            self.update_tray_icon()
            logger.info(f"Highlight color changed to: {self.highlight_color}")

    def save_highlight_color(self):
        self.settings.setValue("highlight_color", self.highlight_color)
        self.settings.sync()

    def update_overlay_colors(self):
        for overlay in self.screen_overlays.values():
            overlay.set_highlight_color(self.highlight_color)

    def update_tray_icon(self):
        from PyQt6.QtGui import QPixmap

        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(self.highlight_color))
        self.tray_icon.setIcon(QIcon(pixmap))

    def quit_app(self):
        logger.info("Quit requested from tray menu")
        self.cleanup()
        sys.exit(0)

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
        if hasattr(self, "tray_icon"):
            self.tray_icon.hide()
        for overlay in self.screen_overlays.values():
            overlay.hide()
            overlay.deleteLater()
        self.app.quit()

    def run(self):
        try:
            sys.exit(self.app.exec())
        except KeyboardInterrupt:
            logger.info("Caught KeyboardInterrupt, shutting down gracefully...")
            self.cleanup()
            sys.exit(0)


def main():
    app = FocusViewApp()
    app.run()


if __name__ == "__main__":
    main()
