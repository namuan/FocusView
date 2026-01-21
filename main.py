import contextlib
import logging
import os
import signal
import sys

import AppKit as _AppKit
from AppKit import (
    NSBackingStoreBuffered,
    NSColor,
    NSPanel,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskBorderless,
    NSWorkspace,
)
from Foundation import NSMakeRect
from PyQt6.QtCore import QPropertyAnimation, QRect, QSettings, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QColorDialog, QMenu, QSystemTrayIcon, QWidget
from Quartz import CGWindowListCopyWindowInfo, kCGNullWindowID, kCGWindowListOptionOnScreenOnly

DEBOUNCE_DELAY = 200
BLUR_HOLE_PADDING = 0

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


def _qt_union_geometry():
    screens = QApplication.screens()
    if not screens:
        return QRect(0, 0, 0, 0)

    min_x = min(s.geometry().x() for s in screens)
    min_y = min(s.geometry().y() for s in screens)
    max_x = max(s.geometry().x() + s.geometry().width() for s in screens)
    max_y = max(s.geometry().y() + s.geometry().height() for s in screens)
    return QRect(min_x, min_y, max_x - min_x, max_y - min_y)


def _ns_union_frame():
    screens = NSScreen.screens()
    if not screens:
        return (0.0, 0.0, 0.0, 0.0)

    frames = [s.frame() for s in screens]
    min_x = min(f.origin.x for f in frames)
    min_y = min(f.origin.y for f in frames)
    max_x = max(f.origin.x + f.size.width for f in frames)
    max_y = max(f.origin.y + f.size.height for f in frames)
    return (float(min_x), float(min_y), float(max_x - min_x), float(max_y - min_y))


def _choose_visual_effect_material():
    """Return an NSVisualEffectMaterial constant compatible with current macOS/PyObjC."""

    preferred = [
        "NSVisualEffectMaterialFullScreenUI",
        "NSVisualEffectMaterialHUDWindow",
        "NSVisualEffectMaterialUnderWindowBackground",
        "NSVisualEffectMaterialSidebar",
        "NSVisualEffectMaterialWindowBackground",
    ]
    for name in preferred:
        value = getattr(_AppKit, name, None)
        if value is not None:
            return value

    # Extremely old / unexpected: fall back to 0 (AppearanceBased) if present.
    return getattr(_AppKit, "NSVisualEffectMaterialAppearanceBased", 0)


def _choose_blur_window_level():
    """Choose a window level that stays above normal windows but below the menu bar."""

    main_menu_level = getattr(_AppKit, "NSMainMenuWindowLevel", None)
    if main_menu_level is not None:
        try:
            return int(main_menu_level) - 1
        except (TypeError, ValueError) as exc:
            logger.debug(
                "Failed to coerce NSMainMenuWindowLevel=%r to int: %s",
                main_menu_level,
                exc,
            )

    floating_level = getattr(_AppKit, "NSFloatingWindowLevel", None)
    if floating_level is not None:
        return floating_level

    return NSScreenSaverWindowLevel


class _QtToCocoaMapper:
    """Best-effort conversion between Qt global coords and Cocoa global coords.

    Qt global geometry (as used by QScreen.geometry/QRect here) behaves like a
    top-left origin with Y growing downward.
    Cocoa screen space uses a bottom-left origin with Y growing upward.

    We derive a mapping by aligning the *union* of all screens.
    """

    def __init__(self):
        self.qt_union = _qt_union_geometry()
        self.ns_union = _ns_union_frame()

    def qt_rect_to_ns_rect(self, rect: QRect):
        qt_u = self.qt_union
        ns_left, ns_bottom, _, _ = self.ns_union

        # Map X linearly.
        ns_x = ns_left + float(rect.x() - qt_u.x())

        # Flip Y around the union's bottom edge in Qt space.
        qt_union_bottom = qt_u.y() + qt_u.height()
        ns_y = ns_bottom + float(qt_union_bottom - rect.y() - rect.height())

        return NSMakeRect(ns_x, ns_y, float(rect.width()), float(rect.height()))


class NativeBlurOverlayGroup:
    def __init__(self):
        self._mapper = _QtToCocoaMapper()
        self._panels = [self._create_panel() for _ in range(4)]

    def _create_panel(self):
        rect = NSMakeRect(0, 0, 10, 10)
        style_mask = NSWindowStyleMaskBorderless
        non_activating = getattr(_AppKit, "NSWindowStyleMaskNonactivatingPanel", None)
        if non_activating is not None:
            style_mask |= non_activating

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style_mask, NSBackingStoreBuffered, False
        )
        panel.setLevel_(_choose_blur_window_level())
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setIgnoresMouseEvents_(True)
        # Critical: keep blur visible while another app is focused.
        with contextlib.suppress(Exception):
            panel.setHidesOnDeactivate_(False)
        with contextlib.suppress(Exception):
            panel.setFloatingPanel_(True)
        panel.setReleasedWhenClosed_(False)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        effect_view = NSVisualEffectView.alloc().initWithFrame_(rect)
        effect_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        effect_view.setState_(NSVisualEffectStateActive)
        effect_view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        effect_view.setMaterial_(_choose_visual_effect_material())

        # Some macOS/PyObjC combinations can make NSVisualEffectView appear "too subtle".
        # Provide a tiny translucent backdrop so users can confirm the overlay is present.
        # (This still allows the blur to show through when supported.)
        try:
            effect_view.setWantsLayer_(True)
            layer = effect_view.layer()
            if layer is not None:
                # Very light black tint.
                layer.setBackgroundColor_(_AppKit.NSColor.blackColor().colorWithAlphaComponent_(0.05).CGColor())
        except Exception as exc:
            logger.debug("Failed to set NSVisualEffectView layer background tint: %s", exc)

        panel.setContentView_(effect_view)
        panel.orderOut_(None)
        return panel

    def hide(self):
        for p in self._panels:
            p.orderOut_(None)

    def close(self):
        for p in self._panels:
            with contextlib.suppress(Exception):
                p.close()

    def show_outside_rect(self, screen_rect: QRect, focus_rect: QRect):
        """Show blur in the area of screen_rect excluding focus_rect."""
        if screen_rect.isNull() or focus_rect.isNull():
            self.hide()
            return

        focus = focus_rect.intersected(screen_rect)
        if focus.isNull() or focus.width() <= 0 or focus.height() <= 0:
            # No overlap: blur the whole screen.
            regions = [screen_rect]
        else:
            sx, sy, sw, sh = screen_rect.x(), screen_rect.y(), screen_rect.width(), screen_rect.height()
            fx, fy, fw, fh = focus.x(), focus.y(), focus.width(), focus.height()

            top_h = fy - sy
            bottom_y = fy + fh
            bottom_h = (sy + sh) - bottom_y
            left_w = fx - sx
            right_x = fx + fw
            right_w = (sx + sw) - right_x

            regions = [
                QRect(sx, sy, sw, top_h),
                QRect(sx, bottom_y, sw, bottom_h),
                QRect(sx, fy, left_w, fh),
                QRect(right_x, fy, right_w, fh),
            ]

        # Ensure exactly 4 panels worth of regions.
        padded = regions[:4] + [QRect(0, 0, 0, 0)] * max(0, 4 - len(regions))

        for panel, region in zip(self._panels, padded):
            if region.isNull() or region.width() <= 0 or region.height() <= 0:
                panel.orderOut_(None)
                continue

            ns_rect = self._mapper.qt_rect_to_ns_rect(region)
            panel.setFrame_display_(ns_rect, True)
            panel.orderFrontRegardless()


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
            # Skip windows that are not on the standard layer (0)
            if window.get("kCGWindowLayer", 0) != 0:
                continue

            # Skip windows that are fully transparent
            if window.get("kCGWindowAlpha", 1.0) == 0:
                continue

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
        self.blur_overlays = {}

        # Load settings
        self.settings = QSettings()
        self.highlight_color = self.settings.value("highlight_color", DEFAULT_HIGHLIGHT_COLOR)
        self.blur_enabled = self.settings.value("blur_enabled", True, type=bool)

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

        # Prevent app from exiting when the color picker (or last window) is closed
        self.app.setQuitOnLastWindowClosed(False)

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
            self.blur_overlays[screen] = NativeBlurOverlayGroup()

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

        # Toggle blur action
        toggle_blur_action = QAction("Blur Outside Focused Window", self.app)
        toggle_blur_action.setCheckable(True)
        toggle_blur_action.setChecked(bool(self.blur_enabled))
        toggle_blur_action.triggered.connect(self.toggle_blur)
        tray_menu.addAction(toggle_blur_action)

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

    def toggle_blur(self, checked: bool):
        self.blur_enabled = bool(checked)
        self.settings.setValue("blur_enabled", self.blur_enabled)
        self.settings.sync()
        if not self.blur_enabled:
            for overlay in self.blur_overlays.values():
                overlay.hide()
        else:
            # Force refresh on next poll.
            self.last_active_rect = None

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

            # Instantly hide blur overlays as well
            for overlay in self.blur_overlays.values():
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

                if self.blur_enabled:
                    blur_overlay = self.blur_overlays.get(screen)
                    if blur_overlay:
                        # Avoid covering the macOS menu bar / dock by using availableGeometry().
                        blur_screen_rect = screen.availableGeometry()
                        if BLUR_HOLE_PADDING:
                            focus_rect = self.last_active_rect.adjusted(
                                -BLUR_HOLE_PADDING,
                                -BLUR_HOLE_PADDING,
                                BLUR_HOLE_PADDING,
                                BLUR_HOLE_PADDING,
                            )
                        else:
                            focus_rect = self.last_active_rect
                        blur_overlay.show_outside_rect(blur_screen_rect, focus_rect)
            else:
                overlay.hide()
                blur_overlay = self.blur_overlays.get(screen)
                if blur_overlay:
                    blur_overlay.hide()

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
        for overlay in self.blur_overlays.values():
            overlay.hide()
            overlay.close()
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
