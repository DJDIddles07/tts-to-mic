# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.1] - 2026-11-16

### Fixed
- **Windows lost after unplugging a monitor.** If the Quick Type window, the screen read area, or the main window had last been used on a second monitor, disconnecting that monitor left the window off-screen where it could not be seen or moved. The app now:
  - checks every 2 seconds that each window is on a connected monitor and moves any stranded window back onto the main monitor;
  - ignores a saved position that is on a monitor that no longer exists when the app starts, for both the Quick Type window and the read area;
  - re-checks the Quick Type window position right before showing it with its hotkey.
  A window that is only partly off the edge of a screen, with its title bar still reachable, is left where it is.
- **App failed to start with an `IndentationError`.** A stray character after `if self.prev_lines is None:` in the screen-scan handler (`_scan_done`) was a syntax error. It has been removed.

## [1.0.0]

- Initial release.

[1.0.1]: https://github.com/DJDIddles07/tts-to-mic/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/DJDIddles07/tts-to-mic/releases/tag/v1.0.0
