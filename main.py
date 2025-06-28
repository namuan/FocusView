import logging
import os
import sys

from AppKit import NSWorkspace
from HIServices import AXIsProcessTrusted
from PyQt6.QtCore import QPoint
from PyQt6.QtCore import QPropertyAnimation
from PyQt6.QtCore import Qt
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import QSlider
from PyQt6.QtWidgets import QSystemTrayIcon
from PyQt6.QtWidgets import QVBoxLayout
from PyQt6.QtWidgets import QWidget

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


def fade_overlay(overlay, start, end, duration=500):
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


def get_active_window():
    app = NSWorkspace.sharedWorkspace().activeApplication()
    active_window_name = app.get("NSApplicationName") if app else ""
    logger.info(f"Active window: {active_window_name}")
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
        self.dimming_enabled = False
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
            overlay = Overlay(screen.geometry())
            self.overlays.append(overlay)

    def setup_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_overlays)
        self.timer.start(1000)

    def toggle_dimming(self):
        self.dimming_enabled = not self.dimming_enabled
        self.update_overlays()

    def update_overlays(self):
        if not self.dimming_enabled:
            for overlay in self.overlays:
                if overlay.isVisible():
                    fade_overlay(overlay, overlay.windowOpacity(), 0.0)
                    overlay.hide()
            return

        get_active_window()
        active_window = QApplication.activeWindow()
        for overlay in self.overlays:
            if active_window and overlay.geometry().contains(active_window.geometry()):
                if overlay.isVisible():
                    fade_overlay(overlay, overlay.windowOpacity(), 0.0)
                    overlay.hide()
            else:
                if not overlay.isVisible():
                    overlay.show()
                    fade_overlay(overlay, 0.0, 0.5)

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
