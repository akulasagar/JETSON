import sys
import os
import logging

# Force DEBUG output to terminal.
# basicConfig() is a no-op when any imported library already initialised the
# root logger – so we add the handler explicitly to guarantee visibility.
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

# Silence noisy third-party loggers that spam on every retry
logging.getLogger("pymodbus").setLevel(logging.CRITICAL)
logging.getLogger("pymodbus.client.sync").setLevel(logging.CRITICAL)

# Add the GUI/Screens directory to the Python path
# so that modules inside there can import each other without package prefixes
current_dir = os.path.dirname(os.path.abspath(__file__))
gui_screens_dir = os.path.join(current_dir, "GUI", "Screens")
if gui_screens_dir not in sys.path:
    sys.path.insert(0, gui_screens_dir)

# ── CRITICAL: QWebEngineView must be imported BEFORE QApplication is created ──
# Importing after QApplication causes a SIGABRT / core dump.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
from PyQt5.QtWebEngineWidgets import QWebEngineView   # noqa: F401 – side-effect import

from PyQt5.QtWidgets import QApplication
from GUI.Screens.main_screen import MainDashboard


def main():
    app = QApplication(sys.argv)
    window = MainDashboard()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()