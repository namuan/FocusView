import logging
import os
import sys

from AppKit import NSWorkspace
from HIServices import AXIsProcessTrusted
from PyQt6.QtCore import QPoint
from PyQt6.QtCore import QPropertyAnimation
from PyQt6.QtCore import QRect
from PyQt6.QtCore import Qt
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtGui import QIcon
from PyQt6.QtGui import QPainter
from PyQt6.QtGui import QPen
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import QSlider
from PyQt6.QtWidgets import QSystemTrayIcon
from PyQt6.QtWidgets import QVBoxLayout
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


class Overlay(QWidget):
    def __init__(self, geometry):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setStyleSheet("background-color: rgba(0, 0, 0, 0.5);")
        self.setGeometry(geometry)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().window())


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


def fade_overlay(overlay, start, end, duration=200):
    animation = QPropertyAnimation(overlay, b"windowOpacity")
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.setDuration(duration)
    animation.start()


class SettingsDialog(QDialog):
    def __init__(self, overlays):
        super().__init__()
        self.overlays = overlays
        self.setWindowTitle("Settings")
        layout = QVBoxLayout()
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(10, 90)
        self.opacity_slider.setValue(50)
        layout.addWidget(self.opacity_slider)
        self.setLayout(layout)
        self.opacity_slider.valueChanged.connect(self.update_opacity)

    def update_opacity(self, value):
        opacity = value / 100.0
        for overlay in self.overlays:
            overlay.setStyleSheet(f"background-color: rgba(0, 0, 0, {opacity});")


def get_active_window_geometry():
    """
    Gets the geometry (x, y, width, height) of the active application's main window.

    Returns:
        A dictionary containing {'x', 'y', 'width', 'height'} or None if no
        active window is found.
    """
    # 1. Get the active application's information
    active_app_info = NSWorkspace.sharedWorkspace().activeApplication()
    if not active_app_info:
        print("Could not get active application info.")
        return None

    # 2. Get the Process ID (PID) of the active application
    pid = active_app_info.get("NSApplicationProcessIdentifier")
    if pid is None:
        print("Could not get PID of the active application.")
        return None

    # 3. Get a list of all on-screen windows
    # The list is ordered from front to back, so the first match is the active window.
    window_list = CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly, kCGNullWindowID
    )

    # 4. Find the window that belongs to the active application's PID
    for window in window_list:
        # Check if the window's owner PID matches the active app's PID
        if window.get("kCGWindowOwnerPID") == pid:
            # 5. Get the window's geometry
            bounds = window.get("kCGWindowBounds")

            # The first window in the list that matches the PID is the active one.
            # We can stop searching now.
            return {
                "x": int(bounds["X"]),
                "y": int(bounds["Y"]),
                "width": int(bounds["Width"]),
                "height": int(bounds["Height"]),
            }

    # If no matching window was found
    return None


def get_active_window():
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    logging.info(f"Frontmost app is {app}")
    active_window_name = app.get("NSApplicationName") if app else ""
    return active_window_name


def check_accessibility():
    if not AXIsProcessTrusted():
        logger.info("Enable Accessibility in System Settings > Privacy & Security.")
        return False
    return True


class HazeOverApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.overlays = []
        self.border_overlays = []  # Store border overlays
        self.dimming_enabled = True
        self.setup_tray()
        self.setup_overlays()
        self.setup_timer()

    def setup_tray(self):
        self.tray = QSystemTrayIcon(QIcon("assets/icon.png"))
        menu = QMenu()
        menu.addAction("Toggle Dimming", self.toggle_dimming)
        menu.addAction("Settings", self.show_settings)
        menu.addAction("Quit", self.app.quit)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def setup_overlays(self):
        for screen in QApplication.screens():
            # Create dimming overlay
            overlay = Overlay(screen.geometry())
            self.overlays.append(overlay)
            # Create border overlay
            border_overlay = BorderOverlay(screen.geometry())
            self.border_overlays.append(border_overlay)

    def setup_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_overlays)
        self.timer.start(100)

    def toggle_dimming(self):
        self.dimming_enabled = not self.dimming_enabled
        self.update_overlays()

    def update_overlays(self):
        if not self.dimming_enabled:
            for overlay in self.overlays:
                if overlay.isVisible():
                    fade_overlay(overlay, overlay.windowOpacity(), 0.0)
                    overlay.hide()
            for border_overlay in self.border_overlays:
                border_overlay.hide()
            return

        active_window_geometry_dict = get_active_window_geometry()
        active_window_rect = None
        # --- FIX START ---
        # 1. Check if we got a valid dictionary
        if active_window_geometry_dict:
            # 2. Convert the dictionary to a QRect object
            active_window_rect = QRect(
                active_window_geometry_dict["x"],
                active_window_geometry_dict["y"],
                active_window_geometry_dict["width"],
                active_window_geometry_dict["height"],
            )

        for overlay, border_overlay in zip(self.overlays, self.border_overlays):
            # 3. Use the new QRect object for the 'contains' check
            if active_window_rect and overlay.geometry().contains(active_window_rect):
                if overlay.isVisible():
                    fade_overlay(overlay, overlay.windowOpacity(), 0.0)
                    overlay.hide()
                # 4. Use the QRect object for setGeometry as well
                border_overlay.setGeometry(active_window_rect)
                border_overlay.show()
            else:
                if not overlay.isVisible():
                    overlay.show()
                    fade_overlay(overlay, 0.0, 0.5)
                border_overlay.hide()
            overlay.update()
            border_overlay.update()
        # --- FIX END ---

    def show_settings(self):
        dialog = SettingsDialog(self.overlays)
        dialog.move(dialog.pos() - QPoint(0, 50))
        animation = QPropertyAnimation(dialog, b"pos")
        animation.setStartValue(dialog.pos() - QPoint(0, 50))
        animation.setEndValue(dialog.pos())
        animation.setDuration(300)
        animation.start()
        dialog.exec()

    def run(self):
        if check_accessibility():
            sys.exit(self.app.exec())


if __name__ == "__main__":
    app = HazeOverApp()
    app.run()
