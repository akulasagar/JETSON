"""Main application dashboard.

Orchestrates all hardware threads, screen widgets, camera routing,
and upload management. This was previously the MainDashboard class
inside main_screen.py. Fixed the duplicate closeEvent bug.
"""
import sys
import os
import datetime
import threading
import logging

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QFrame, QStackedWidget, QSizePolicy, QMessageBox)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap

from gui.screens.main_screen import ManholeWidget
from gui.screens.pipe_cleaning import PipeCleaningWidget
from hardware.camera_thread import CameraThread
from hardware.gps_thread import GPSThread
from hardware.modbus_thread import ModbusThread
from hardware.gas_thread import GasThread
from hardware.depth_thread import DepthThread
from core.data_uploader import Uploader, UploadStatus
from core.database import get_connection
from core import voice_module

logger = logging.getLogger(__name__)


class MainDashboard(QMainWindow):
    """Top-level window that orchestrates all subsystems."""

    upload_status_signal = pyqtSignal(str, str, str, dict)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Project Shudh Dashboard")
        self.showFullScreen()
        self.setStyleSheet("background-color: #ffffff;")

        # GPS State
        self.gps_lat = 0.0
        self.gps_lon = 0.0
        self.gps_fix = False
        self.gps_connected = False

        self.is_camera_switched = False
        self.latest_gas_data = {}

        # Establish DB connection early (background thread)
        try:
            threading.Thread(target=get_connection, daemon=True).start()
        except Exception as e:
            logger.error(f"[DB-INIT] Error starting background DB connection thread: {e}")

        voice_module.speak_dual("System started", "సిస్టమ్ ప్రారంభమైంది")

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)

        self._init_header()

        self.stack = QStackedWidget()
        self.manhole_screen = ManholeWidget()
        self.pipe_screen    = PipeCleaningWidget()

        self.stack.addWidget(self.manhole_screen)
        self.stack.addWidget(self.pipe_screen)
        self.stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.layout.addWidget(self.stack, 1)

        self._init_footer()

        # Navigation signals
        self.manhole_screen.switch_to_pipe.connect(lambda: self.stack.setCurrentIndex(1))
        self.manhole_screen.switch_cam_signal.connect(self.switch_cameras)
        self.manhole_screen.manhole_id_changed.connect(self.set_footer_manhole_id)
        self.manhole_screen.depths_changed.connect(self.set_footer_depths)

        self.pipe_screen.switch_to_manhole.connect(lambda: self.stack.setCurrentIndex(0))
        self.pipe_screen.switch_cam_btn.clicked.connect(self.switch_cameras)
        
        # Link Pipe Screen buttons to Manhole Screen handlers (sharing logic)
        self.pipe_screen.measure_btn.clicked.connect(self.manhole_screen.handle_measure_operation)
        self.pipe_screen.start_btn.clicked.connect(self.manhole_screen.handle_start_operation)
        self.pipe_screen.stop_btn.clicked.connect(self.manhole_screen.handle_stop_operation)

        # Mirror Manhole timer to Pipe Screen
        self.manhole_screen.operation_timer.timeout.connect(
            lambda: self.pipe_screen.timer_val.setText(self.manhole_screen.timer_val.text())
        )

        # Clock
        self.clock_timer = QTimer()
        self.clock_timer.timeout.connect(self._update_datetime)
        self.clock_timer.start(1000)

        # Pixel buffers
        self.last_px0 = QPixmap()
        self.last_px1 = QPixmap()

        # Data Uploader
        self.upload_status_signal.connect(self._handle_upload_status)
        self.uploader = Uploader()
        self.uploader.set_status_callback(lambda op_id, st, msg, det: self.upload_status_signal.emit(op_id, st, msg, det if det is not None else {}))

        # Camera threads
        self._start_cameras()

        # Start GPS Thread
        self.gps_thread = GPSThread(port='/dev/ttyUSB1')
        self.gps_thread.location_updated.connect(self._handle_gps_update)
        self.gps_thread.connection_status.connect(self._handle_gps_connection)
        self.gps_thread.start()

        # Start Modbus Thread
        self.modbus_thread = ModbusThread(port='/dev/ttyCH341USB0')
        self.modbus_thread.levels_updated.connect(self.manhole_screen.update_levers)
        self.modbus_thread.levels_updated.connect(self.pipe_screen.update_status_bars)
        self.modbus_thread.start()

        # Start Gas Sensor Thread
        self.gas_thread = GasThread()
        self.gas_thread.data_received.connect(self.pipe_screen.update_gas_data)
        self.gas_thread.data_received.connect(self._handle_gas_update)
        self.gas_thread.start()

        # Start Depth Thread (Centralized)
        self.depth_thread = DepthThread()
        self.depth_thread.start()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showMinimized()
        else:
            super().keyPressEvent(event)

    # -- GPS Handlers --------------------------------------------------------
    def _handle_gps_update(self, lat, lon, has_fix):
        self.gps_lat = lat
        self.gps_lon = lon
        self.gps_fix = has_fix
        
        # Share with screens
        self.manhole_screen.gps_lat = lat
        self.manhole_screen.gps_lon = lon
        self.manhole_screen.gps_fix = has_fix

        if has_fix:
            self.gps_val_lbl.setText(f"Connected (Fix: {lat:.4f}, {lon:.4f})")
            self.gps_val_lbl.setStyleSheet("font-size: 14px; color: #00e676; font-weight: bold;")
        else:
            self.gps_val_lbl.setText("No Fix")
            self.gps_val_lbl.setStyleSheet("font-size: 14px; color: #ffcc80; font-weight: bold;")

    def _handle_gps_connection(self, connected):
        self.gps_connected = connected
        if not connected:
            self.gps_val_lbl.setText("Disconnected")
            self.gps_val_lbl.setStyleSheet("font-size: 14px; color: #ff1744; font-weight: bold;")

    def _handle_gas_update(self, data):
        self.latest_gas_data = data

        # Accumulate readings while an operation is running
        if self.manhole_screen.operation_timer.isActive():
            self.manhole_screen._gas_readings.append(dict(data))

    # -- Upload Status Handler -----------------------------------------------
    def _handle_upload_status(self, operation_id, status, message, details):
        """Callback from Uploader to update UI status"""
        status_text = status.upper()
        self.upload_status_val.setText(status_text)
        
        if status == 'success':
            self.upload_status_val.setStyleSheet("font-size: 14px; color: #00e676; font-weight: bold; border: none; background: transparent;")
            voice_module.speak_dual("Data Uploading success", "అప్లోడ్ విజయవంతమైంది")
        elif status == 'failed':
            self.upload_status_val.setStyleSheet("font-size: 14px; color: #ff1744; font-weight: bold; border: none; background: transparent;")
            voice_module.speak_dual("Data Upload failed", "డేటా అప్లోడ్ విఫలమైంది")
        else:
            self.upload_status_val.setStyleSheet("font-size: 14px; color: #ffcc80; font-weight: bold; border: none; background: transparent;")

    # -- Camera startup ------------------------------------------------------
    def _start_cameras(self):
        """Start camera threads for all available devices."""
        self.cam0_thread = CameraThread(camera_index=0)
        self.cam0_thread.frame_available.connect(self._handle_frames)
        self.cam0_thread.camera_error.connect(
            lambda msg, idx: logger.warning(f"[CAM-{idx}] {msg}")
        )
        self.cam0_thread.camera_reconnecting.connect(
            lambda msg, idx: logger.info(f"[CAM-{idx}] {msg}")
        )
        self.cam0_thread.start()

        self.cam1_thread = CameraThread(camera_index=1)
        self.cam1_thread.frame_available.connect(self._handle_frames)
        self.cam1_thread.camera_error.connect(
            lambda msg, idx: logger.warning(f"[CAM-{idx}] {msg}")
        )
        self.cam1_thread.camera_reconnecting.connect(
            lambda msg, idx: logger.info(f"[CAM-{idx}] {msg}")
        )
        self.cam1_thread.start()

        # Give the ManholeWidget a reference so it can capture frames
        self.manhole_screen.cam0_thread = self.cam0_thread
        self.manhole_screen.cam1_thread = self.cam1_thread

    # -- Header --------------------------------------------------------------
    def _init_header(self):
        header = QFrame()
        header.setFixedHeight(70)
        header.setStyleSheet("background-color: #ffffff; border-bottom: 1px solid #EEEEEE;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(40, 5, 40, 5)
        h_layout.setSpacing(0)

        # Logo on the Left
        logo_container = QWidget()
        logo_lay = QHBoxLayout(logo_container)
        logo_lay.setContentsMargins(0,0,0,0)
        logo = QLabel()
        px = QPixmap("IMAGES/bot_factory.png")
        if px.isNull(): px = QPixmap("bot_factory.png")
        if px.isNull(): px = QPixmap("IMAGES/primary_white.png")
        logo.setPixmap(px.scaled(220, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo_lay.addWidget(logo)
        h_layout.addWidget(logo_container)
        
        h_layout.addStretch(1)

        # Centered Title
        title = QLabel("Project Shudh")
        title.setStyleSheet("font-size: 26px; color: #1a1a1a; font-family: 'Outfit'; font-weight: 500;")
        title.setAlignment(Qt.AlignCenter)
        h_layout.addWidget(title)
        
        h_layout.addStretch(1)

        # Date & Time on the Right
        dt_frame   = QWidget()
        dt_v       = QVBoxLayout(dt_frame)
        dt_v.setContentsMargins(0, 0, 0, 0)
        dt_v.setSpacing(2)
        
        self.date_lbl = QLabel()
        self.time_lbl = QLabel()
        
        lbl_style = "font-size: 15px; color: #1a1a1a; font-family: 'Outfit'; font-weight: normal;"
        self.date_lbl.setStyleSheet(lbl_style)
        self.time_lbl.setStyleSheet(lbl_style)
        self.date_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        
        dt_v.addWidget(self.date_lbl)
        dt_v.addWidget(self.time_lbl)
        h_layout.addWidget(dt_frame)
        
        self.layout.addWidget(header)

    def _update_datetime(self):
        now = datetime.datetime.now()
        self.date_lbl.setText(f"Date: {now.strftime('%d-%m-%Y')}")
        self.time_lbl.setText(f"Time: {now.strftime('%H:%M:%S %p')}")

    # -- Footer --------------------------------------------------------------
    def _init_footer(self):
        footer = QFrame()
        footer.setFixedHeight(35)
        footer.setStyleSheet("background-color: #219EBC; color: white;")
        f_layout = QHBoxLayout(footer)
        f_layout.setContentsMargins(20, 0, 20, 0)
        f_layout.setSpacing(10)

        device_id = os.environ.get("DEVICE_ID", os.environ.get("device_id"))
        if not device_id and os.path.exists(".env"):
            try:
                with open(".env", "r") as f:
                    for line in f:
                        if line.strip().lower().startswith("device_id="):
                            device_id = line.strip().split("=", 1)[1].strip('"\'')
                            break
            except Exception as e:
                logger.error(f"Error reading .env: {e}")

        if not device_id or device_id == "UNKNOWN":
            try:
                import config as cfg_module
                if hasattr(cfg_module, 'device_id'):
                    val = cfg_module.device_id
                    if val and val != "UNKNOWN":
                        device_id = val
            except Exception as e:
                logger.error(f"Error reading config.py: {e}")

        if not device_id or device_id == "UNKNOWN":
            device_id = "007"

        static_left = [("Device ID:", device_id)]
        for l_t, v_t in static_left:
            lbl = QLabel(f"<b>{l_t}</b> {v_t}")
            lbl.setStyleSheet("font-size: 14px; color: white; border: none; background: transparent;")
            f_layout.addWidget(lbl)
            f_layout.addStretch()

        self.footer_depth_lbl = QLabel("<b>Depth:</b> 0/0")
        self.footer_depth_lbl.setStyleSheet("font-size: 14px; color: white; border: none; background: transparent;")
        f_layout.addWidget(self.footer_depth_lbl)
        f_layout.addStretch()

        # ── Live Manhole ID ──
        self.footer_manhole_lbl = QLabel("<b>Manhole ID:</b> N/A")
        self.footer_manhole_lbl.setStyleSheet(
            "font-size: 14px; color: #ffcc80; border: none; background: transparent;"
        )
        f_layout.addWidget(self.footer_manhole_lbl)
        f_layout.addStretch()

        static_right = [("Upload Status:", "")]
        for l_t, v_t in static_right:
            lbl = QLabel(f"<b>{l_t}</b>")
            lbl.setStyleSheet("font-size: 14px; color: white; border: none; background: transparent;")
            f_layout.addWidget(lbl)
            
            self.upload_status_val = QLabel("Ready")
            self.upload_status_val.setStyleSheet("font-size: 14px; color: #00e676; font-weight: bold; border: none; background: transparent;")
            f_layout.addWidget(self.upload_status_val)
            f_layout.addStretch()

        # GPS Status in footer
        gps_title_lbl = QLabel("<b>GPS:</b>")
        gps_title_lbl.setStyleSheet("font-size: 14px; color: white; border: none; background: transparent;")
        f_layout.addWidget(gps_title_lbl)
        
        self.gps_val_lbl = QLabel("Initializing...")
        self.gps_val_lbl.setStyleSheet("font-size: 14px; color: #ffcc80; border: none; background: transparent;")
        f_layout.addWidget(self.gps_val_lbl)
        f_layout.addStretch()

        static_cams = [("Cam 1:", "USB"), ("Cam 2:", "USB")]
        for idx, (l_t, v_t) in enumerate(static_cams):
            lbl = QLabel(f"<b>{l_t}</b> {v_t}")
            lbl.setStyleSheet("font-size: 14px; color: white; border: none; background: transparent;")
            f_layout.addWidget(lbl)
            if idx < len(static_cams) - 1:
                f_layout.addStretch()

        self.layout.addWidget(footer)

    def set_footer_manhole_id(self, manhole_id: str):
        self.footer_manhole_lbl.setText(f"<b>Manhole ID:</b> {manhole_id}")
        color = "#00e676" if manhole_id != "N/A" else "#ffcc80"
        self.footer_manhole_lbl.setStyleSheet(
            f"font-size: 14px; color: {color}; border: none; background: transparent;"
        )

    def set_footer_depths(self, before_val, after_val):
        b = f"{before_val:.1f}" if before_val else "0"
        a = f"{after_val:.1f}" if after_val else "0"
        self.footer_depth_lbl.setText(f"<b>Depth:</b> {b}/{a}")

    # -- Frame routing -------------------------------------------------------
    def _handle_frames(self, q_img, idx):
        px = QPixmap.fromImage(q_img)
        if idx == 0:
            self.last_px0 = px
        else:
            self.last_px1 = px

        if not self.is_camera_switched:
            disp_a, disp_b = self.last_px0, self.last_px1
        else:
            disp_a, disp_b = self.last_px1, self.last_px0

        if self.stack.currentIndex() == 0:
            self.manhole_screen.update_feeds(disp_a, disp_b)
        else:
            self.pipe_screen.update_feeds(disp_a, disp_b)

    def switch_cameras(self):
        self.is_camera_switched = not self.is_camera_switched
        logger.info(f"[CAM] Switched feeds: {self.is_camera_switched}")

    # -- FIXED: Single closeEvent (original had two, second silently overrode first) --
    def closeEvent(self, event):
        """Handle window close with confirmation and proper thread cleanup."""
        reply = QMessageBox.question(
            self,
            'Exit Confirmation',
            'Are you sure you want to quit the application?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            # Graceful shutdown of all threads
            self.cam0_thread.stop()
            self.cam1_thread.stop()
            self.gps_thread.stop()
            self.gas_thread.stop()
            if hasattr(self, 'modbus_thread'):
                self.modbus_thread.stop()
            if hasattr(self, 'depth_thread'):
                self.depth_thread.stop()
            if hasattr(self, 'uploader'):
                self.uploader.stop_upload_thread()
            event.accept()
        else:
            event.ignore()
