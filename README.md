# FocusView

A macOS app that dims inactive windows to highlight the active one, similar to HazeOver. Built with Python and PyQt6, it supports multiple monitors and includes fade animations.

## Features
- Dims inactive windows with transparent overlays
- System tray for toggling dimming and settings
- Adjust dimming opacity via settings dialog
- Smooth fade-in/out animations
- Multi-monitor support
- Accessibility permission check

## Requirements
- macOS 10.15 or later
- Python 3.9 or later

## Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/namuan/FocusView.git
   cd hazeover-clone
   ```
2. Install dependencies:

    ```bash
    make deps
    ```

## Usage

1. Run the app:

   ```bash
   make run
   ```
2. Grant Accessibility permissions in System Settings > Privacy & Security.
3. Use the system tray icon to:
    Toggle dimming
    Open settings to adjust opacity
    Quit the app


## License

MIT License. See [LICENSE](LICENSE) for details.
