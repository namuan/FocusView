# Adding macOS Accessibility API Support

This document outlines the requirements for implementing macOS Accessibility (AX) APIs in FocusView.

## Why Use Accessibility APIs?

The current implementation uses `CGWindowListCopyWindowInfo` which works without special permissions but has limitations:

- Cannot get detailed information about UI elements within windows
- Cannot interact with or control windows programmatically
- May not reliably detect focused windows in all scenarios

The Accessibility APIs provide:

- More reliable focused window detection
- Ability to get detailed UI element information
- Window manipulation capabilities (move, resize, focus)
- Better support for accessibility features

## Requirements

### 1. User Permissions

Users must grant Accessibility permissions in **System Settings > Privacy & Security > Accessibility**.

### 2. Code Changes

#### Import Required Modules

```python
from HIServices import AXIsProcessTrusted, kAXTrustedCheckOptionPrompt
from ApplicationServices import (
    AXUIElementCreateSystemWide,
    AXUIElementCreateApplication,
    AXUIElementCopyAttributeValue,
    AXUIElementCopyAttributeNames,
)
from CoreFoundation import (
    CFDictionaryCreate,
    kCFAllocatorDefault,
    kCFTypeDictionaryKeyCallBacks,
    kCFTypeDictionaryValueCallBacks,
)
from objc import YES as kCFBooleanTrue
```

#### Permission Check Function

```python
def check_accessibility():
    """
    Check if the application has accessibility permissions.
    If not, prompt the user to grant them via System Settings.
    Returns True if permissions are granted, False otherwise.
    """
    # First check if we already have permission
    if AXIsProcessTrusted():
        return True

    # Create options dictionary with prompt option set to True
    # This will show the standard macOS dialog asking the user to grant permissions
    from HIServices import AXIsProcessTrustedWithOptions

    options = CFDictionaryCreate(
        kCFAllocatorDefault,
        [kAXTrustedCheckOptionPrompt],
        [kCFBooleanTrue],
        1,
        kCFTypeDictionaryKeyCallBacks,
        kCFTypeDictionaryValueCallBacks
    )

    # This call will show the permission prompt dialog
    AXIsProcessTrustedWithOptions(options)

    # Check again after the prompt
    if not AXIsProcessTrusted():
        return False

    return True
```

#### Error Dialog for Missing Permissions

```python
from PyQt6.QtWidgets import QMessageBox

def show_accessibility_error_dialog():
    """
    Display an error dialog informing the user that accessibility permissions are required.
    """
    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Icon.Critical)
    msg_box.setWindowTitle("Accessibility Permissions Required")
    msg_box.setText("FocusView requires Accessibility permissions to function.")
    msg_box.setInformativeText(
        "Please go to System Settings > Privacy & Security > Accessibility "
        "and enable FocusView.\n\n"
        "After granting permissions, please restart the application."
    )
    msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
    msg_box.exec()
```

#### Using AX APIs for Window Detection

```python
def get_focused_window_ax():
    """
    Get the focused window using Accessibility APIs.
    Returns window geometry dict or None.
    """
    system_wide = AXUIElementCreateSystemWide()

    # Get the focused application
    err, focused_app = AXUIElementCopyAttributeValue(
        system_wide, "AXFocusedApplication", None
    )
    if err != 0 or focused_app is None:
        return None

    # Get the focused window of that application
    err, focused_window = AXUIElementCopyAttributeValue(
        focused_app, "AXFocusedWindow", None
    )
    if err != 0 or focused_window is None:
        return None

    # Get window position
    err, position = AXUIElementCopyAttributeValue(
        focused_window, "AXPosition", None
    )

    # Get window size
    err, size = AXUIElementCopyAttributeValue(
        focused_window, "AXSize", None
    )

    if position and size:
        from Quartz import CGPointGetX, CGPointGetY, CGSizeGetWidth, CGSizeGetHeight
        return {
            "x": int(position.x),
            "y": int(position.y),
            "width": int(size.width),
            "height": int(size.height),
        }

    return None
```

### 3. Application Startup Changes

Update the `run()` method in `FocusViewApp`:

```python
def run(self):
    if not check_accessibility():
        logger.error("Accessibility permissions required. Exiting.")
        show_accessibility_error_dialog()
        sys.exit(1)
    try:
        sys.exit(self.app.exec())
    except KeyboardInterrupt:
        logger.info("Caught KeyboardInterrupt, shutting down gracefully...")
        self.cleanup()
        sys.exit(0)
```

### 4. PyInstaller Configuration (main.spec)

#### Create Entitlements File

Create `entitlements.plist` in the project root:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.get-task-allow</key>
    <false/>

    <key>com.apple.security.app-sandbox</key>
    <false/>
</dict>
</plist>
```

#### Update main.spec

Add to the BUNDLE configuration:

```python
app = BUNDLE(coll,
             name='FocusView.app',
             icon='assets/icon.icns',
             bundle_identifier='com.github.namuan.focusview',
             info_plist={
                'CFBundleName': 'FocusView',
                'CFBundleVersion': '1.0.0',
                'CFBundleShortVersionString': '1.0.0',
                'NSPrincipalClass': 'NSApplication',
                'NSHighResolutionCapable': True,
                'NSAppleEventsUsageDescription': 'FocusView needs access to control other applications to highlight the active window.',
                'NSSystemAdministrationUsageDescription': 'FocusView needs system administration access to monitor window changes.',
                'LSBackgroundOnly': False,
                'LSApplicationCategoryType': 'public.app-category.productivity',
                },
             codesign_identity=None,
             entitlements_file='entitlements.plist',
             )
```

## AX API Error Codes

Common error codes when using Accessibility APIs:

| Code   | Name                                      | Description                         |
| ------ | ----------------------------------------- | ----------------------------------- |
| 0      | kAXErrorSuccess                           | Success                             |
| -25200 | kAXErrorFailure                           | General failure                     |
| -25201 | kAXErrorIllegalArgument                   | Invalid argument                    |
| -25202 | kAXErrorInvalidUIElement                  | UI element no longer exists         |
| -25203 | kAXErrorInvalidUIElementObserver          | Observer is invalid                 |
| -25204 | kAXErrorCannotComplete                    | Cannot complete operation           |
| -25205 | kAXErrorAttributeUnsupported              | Attribute not supported             |
| -25206 | kAXErrorActionUnsupported                 | Action not supported                |
| -25207 | kAXErrorNotificationUnsupported           | Notification not supported          |
| -25208 | kAXErrorNotImplemented                    | Not implemented                     |
| -25209 | kAXErrorNotificationAlreadyRegistered     | Notification already registered     |
| -25210 | kAXErrorNotificationNotRegistered         | Notification not registered         |
| -25211 | kAXErrorAPIDisabled                       | Accessibility API disabled          |
| -25212 | kAXErrorNoValue                           | No value                            |
| -25213 | kAXErrorParameterizedAttributeUnsupported | Parameterized attribute unsupported |
| -25214 | kAXErrorNotEnoughPrecision                | Not enough precision                |

## Common AX Attributes

Useful attributes for window management:

- `AXFocusedApplication` - The currently focused application
- `AXFocusedWindow` - The focused window of an application
- `AXWindows` - List of all windows for an application
- `AXPosition` - Window position (CGPoint)
- `AXSize` - Window size (CGSize)
- `AXTitle` - Window title
- `AXMinimized` - Whether window is minimized
- `AXFullScreen` - Whether window is in full screen

## Testing Without Building

To test if accessibility is working:

```bash
source .venv/bin/activate
python3 -c "
from HIServices import AXIsProcessTrusted
from ApplicationServices import AXUIElementCreateSystemWide, AXUIElementCopyAttributeValue

print('Trusted:', AXIsProcessTrusted())

system_wide = AXUIElementCreateSystemWide()
err, focused = AXUIElementCopyAttributeValue(system_wide, 'AXFocusedApplication', None)
print('Error code:', err)  # 0 = success
print('Focused app:', focused)
"
```

## References

- [Apple Accessibility Programming Guide](https://developer.apple.com/library/archive/documentation/Accessibility/Conceptual/AccessibilityMacOSX/)
- [AXUIElement Reference](https://developer.apple.com/documentation/applicationservices/axuielement_h)
- [PyObjC Documentation](https://pyobjc.readthedocs.io/)
