"""Project Shudh Dashboard – Application Entry Point.

Configures logging, ensures required import order for QtWebEngine,
and launches the MainDashboard window.
"""
import sys
import os
import logging

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.DEBUG)
if not any(isinstance(h, logging.StreamHandler) for h in _root_logger.handlers):
    _sh = logging.StreamHandler(sys.stdout)
    _sh.setLevel(logging.DEBUG)
    _sh.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    _root_logger.addHandler(_sh)

# Silence noisy third-party loggers
logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
logging.getLogger("pymodbus.client.sync").setLevel(logging.CRITICAL)

# ---------------------------------------------------------------------------
# Path setup: ensure the project root is on sys.path so all packages
# (core/, hardware/, gui/, utils/) can be imported cleanly.
# Also keep the old GUI/Screens path for backward compatibility with
# imports that other files might still use (e.g. RealisticManholeWidget).
# ---------------------------------------------------------------------------
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

gui_screens_dir = os.path.join(current_dir, "GUI", "Screens")
if gui_screens_dir not in sys.path:
    sys.path.insert(0, gui_screens_dir)

# ---------------------------------------------------------------------------
# CRITICAL: QWebEngineView must be imported BEFORE QApplication is created.
# Importing after QApplication causes SIGABRT / core dump.
# ---------------------------------------------------------------------------
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
from PyQt5.QtWebEngineWidgets import QWebEngineView   # noqa: F401

from PyQt5.QtWidgets import QApplication
from gui.dashboard import MainDashboard


def main():
    app = QApplication(sys.argv)
    window = MainDashboard()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()