"""Manhole screen widget.

Contains the ManholeWidget which shows camera feeds, lever status,
and provides operation start/stop controls.

This file has been slimmed from the original monolithic main_screen.py
by extracting hardware threads and shared widgets into separate modules.
"""
import sys
import os
import datetime
import time
import cv2
import numpy as np
import logging

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QFrame, QGridLayout, QSizePolicy)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtWidgets import QDialog

from gui.widgets.plc_lever import PLCLever
from gui.popups.manhole_popup import StartOperationPopup
from gui.popups.measure_depth import MeasureDepthPopup
from core import voice_module

logger = logging.getLogger(__name__)

os.makedirs("captures/before", exist_ok=True)
os.makedirs("captures/after", exist_ok=True)


class ManholeWidget(QWidget):
    """Main screen showing camera feeds, lever status, and operation controls."""

    switch_to_pipe     = pyqtSignal()
    switch_cam_signal  = pyqtSignal()
    manhole_id_changed = pyqtSignal(str)   # live-updates footer label
    depths_changed     = pyqtSignal(float, float) # before, after

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # State
        self.current_manhole_id   = "N/A"
        self.operation_start_time = None   # datetime object
        self.operation_end_time   = None

        # Manhole location (from DB, set when manhole is selected in popup)
        self.manhole_lat = 0.0
        self.manhole_lon = 0.0
        
        # Depth measurements
        self.before_depth = None
        self.after_depth = None
        
        # GPS State (mirrored from MainDashboard)
        self.gps_lat = 0.0
        self.gps_lon = 0.0
        self.gps_fix = False

        # Gas data accumulation for averaging over the operation
        self._gas_readings = []   # list of dicts, one per sensor cycle

        # Camera thread references set by MainDashboard
        self.cam0_thread = None
        self.cam1_thread = None

        self._init_content()

    # -- UI ------------------------------------------------------------------
    def _init_content(self):
        content_frame  = QWidget()
        content_layout = QHBoxLayout(content_frame)
        content_layout.setContentsMargins(20, 10, 20, 10)
        content_layout.setSpacing(20)

        # ---- Left: main video + levers ----
        left_side = QVBoxLayout()
        left_side.setSpacing(15)

        self.main_cam_label = QLabel()
        self.main_cam_label.setStyleSheet("background-color: #000; border-radius: 4px;")
        self.main_cam_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.main_cam_label.setMinimumSize(1, 1)
        self.main_cam_label.setScaledContents(True)
        self.main_cam_label.setAlignment(Qt.AlignCenter)
        self.main_cam_label.setText("Connecting camera…")
        self.main_cam_label.setStyleSheet(
            "background-color: #111; color: #555; font-size: 18px; border-radius: 4px;"
        )
        left_side.addWidget(self.main_cam_label, 1)

        levers_row = QHBoxLayout()
        levers_row.setContentsMargins(10, 0, 10, 0)
        levers_row.setSpacing(30)
        levers_row.setAlignment(Qt.AlignCenter)
        self.levers_dict = {}
        for name in ["ROTATION", "LIFT", "EXTEND", "CLAW", "TELESCOPE"]:
            lever = PLCLever(name)
            lever.set_value(0)
            levers_row.addWidget(lever)
            self.levers_dict[name] = lever
        left_side.addLayout(levers_row, 0)
        content_layout.addLayout(left_side, 72)

        # ---- Right: controls + side cam ----
        right_side_widget = QWidget()
        right_side = QVBoxLayout(right_side_widget)
        right_side.setContentsMargins(0, 0, 0, 0)
        right_side.setSpacing(15)

        measure_depth_btn = QPushButton("🌡 Measure Depth")
        measure_depth_btn.setFixedHeight(50)
        measure_depth_btn.setStyleSheet(
            "background-color: #219EBC; color: white; font-size: 15px; font-weight: bold; border-radius: 8px;"
        )
        right_side.addWidget(measure_depth_btn)
        measure_depth_btn.clicked.connect(self.handle_measure_operation)

        op_frame = QFrame()
        op_frame.setStyleSheet("background-color: #f2f4f6; border-radius: 12px;")
        op_frame_layout = QVBoxLayout(op_frame)
        op_frame_layout.setContentsMargins(15, 15, 15, 15)
        op_frame_layout.setSpacing(10)

        op_grid = QGridLayout()
        op_grid.setSpacing(10)

        self.start_btn = QPushButton("▶\nStart Operation")
        self.start_btn.setFixedHeight(80)
        self.start_btn.setStyleSheet(
            "background-color: #00c853; color: white; font-size: 13px; font-weight: bold; border-radius: 8px;"
        )
        self.start_btn.clicked.connect(self.handle_start_operation)

        self.stop_btn = QPushButton("⏸\nStop Operation")
        self.stop_btn.setFixedHeight(80)
        self.stop_btn.setStyleSheet(
            "background-color: #ff1744; color: white; font-size: 13px; font-weight: bold; border-radius: 8px;"
        )
        self.stop_btn.clicked.connect(self.handle_stop_operation)

        switch_btn = QPushButton("🔄\nSwitch Camera")
        switch_btn.setFixedHeight(80)
        switch_btn.setStyleSheet(
            "background-color: #e2e8f0; color: #1a1a1a; font-size: 13px; font-weight: bold; border-radius: 8px;"
        )
        switch_btn.clicked.connect(self.switch_cam_signal.emit)

        timer_box = QFrame()
        timer_box.setFixedHeight(80)
        timer_box.setStyleSheet("background-color: #e2e8f0; border-radius: 8px;")
        t_layout = QVBoxLayout(timer_box)
        t_layout.setAlignment(Qt.AlignCenter)
        t_layout.setSpacing(2)
        t_icon = QLabel("⏱")
        t_icon.setAlignment(Qt.AlignCenter)
        t_icon.setStyleSheet("font-size: 22px; color: #ff9100; font-weight: bold; background: transparent;")
        self.timer_val = QLabel("0:00")
        self.timer_val.setAlignment(Qt.AlignCenter)
        self.timer_val.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: #1a1a1a; background: transparent;"
        )
        t_layout.addWidget(t_icon)
        t_layout.addWidget(self.timer_val)

        op_grid.addWidget(self.start_btn,  0, 0)
        op_grid.addWidget(self.stop_btn,   0, 1)
        op_grid.addWidget(switch_btn, 1, 0)
        op_grid.addWidget(timer_box,  1, 1)
        op_frame_layout.addLayout(op_grid)
        right_side.addWidget(op_frame)

        manhole_clean_btn = QPushButton(" Manhole Cleaning")
        manhole_clean_btn.setFixedHeight(50)
        manhole_clean_btn.setStyleSheet(
            "background-color: #219EBC; color: white; font-size: 15px; font-weight: bold; border-radius: 8px;"
        )
        right_side.addWidget(manhole_clean_btn)

        pipeline_clean_btn = QPushButton(" Gases Detection System")
        pipeline_clean_btn.setFixedHeight(50)
        pipeline_clean_btn.setStyleSheet(
            "background-color: #f2f4f6; color: #1a1a1a; font-size: 15px; font-weight: bold; "
            "border-radius: 8px; border: 1px solid #ddd;"
        )
        pipeline_clean_btn.clicked.connect(self.switch_to_pipe.emit)
        right_side.addWidget(pipeline_clean_btn)

        self.side_cam_label = QLabel()
        self.side_cam_label.setStyleSheet(
            "background-color: #111; color: #555; font-size: 14px; "
            "border: 2px solid #ddd; border-radius: 8px;"
        )
        self.side_cam_label.setMinimumSize(1, 1)
        self.side_cam_label.setScaledContents(True)
        self.side_cam_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.side_cam_label.setAlignment(Qt.AlignCenter)
        self.side_cam_label.setText("Connecting camera…")
        right_side.addWidget(self.side_cam_label, 1)

        content_layout.addWidget(right_side_widget, 28)
        self.layout.addWidget(content_frame, 1)

        # Operation timer (elapsed display)
        self.operation_timer = QTimer(self)
        self.operation_timer.timeout.connect(self._update_operation_time)

    # -- Helpers -------------------------------------------------------------
    def _capture_frame(self, tag: str, operation_id: str):
        """Save current camera frame as a JPEG.  tag = 'before' | 'after'"""
        frame = None
        if self.cam0_thread:
            frame = self.cam0_thread.get_last_frame()

        save_dir = os.path.join("captures", tag)
        os.makedirs(save_dir, exist_ok=True)
        filename = os.path.join(save_dir, f"{operation_id}_{tag}.jpg")

        if frame is not None:
            success = cv2.imwrite(filename, frame)
            if success:
                logger.info(f"[CAPTURE] {tag} image saved: {filename}")
            else:
                logger.error(f"[CAPTURE] Failed to write {filename}")
                self._write_placeholder(filename)
        else:
            logger.warning(f"[CAPTURE] No frame available for {tag}. Saving placeholder.")
            self._write_placeholder(filename)

        return filename

    @staticmethod
    def _write_placeholder(path):
        ph = np.zeros((720, 1280, 3), dtype=np.uint8)
        cv2.putText(ph, "No camera frame", (400, 360),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.5, (60, 60, 60), 2)
        cv2.imwrite(path, ph)

    # -- Measure Operation handler -------------------------------------------
    def handle_measure_operation(self):
        # Use shared depth thread from dashboard
        main_win = self.window()
        depth_thread = getattr(main_win, 'depth_thread', None)
        
        popup = MeasureDepthPopup(
            self, 
            before_depth=self.before_depth, 
            after_depth=self.after_depth,
            depth_thread=depth_thread
        )

        if popup.exec_() == QDialog.Accepted:
            self.before_depth = popup.before_depth
            self.after_depth = popup.after_depth
            logger.info(f"[MEASURE] Depths updated - Before: {self.before_depth}, After: {self.after_depth}")
            self.depths_changed.emit(self.before_depth or 0.0, self.after_depth or 0.0)
        

    # -- Operation handlers --------------------------------------------------
    def handle_start_operation(self):
        popup = StartOperationPopup(self)
        if popup.exec_() == QDialog.Accepted:
            self.current_manhole_id   = popup.manhole_id
            self.manhole_lat          = popup.manhole_lat
            self.manhole_lon          = popup.manhole_lon
            self.operation_start_time = datetime.datetime.now()
            self.operation_end_time   = None

            # Capture "before" frame
            op_id = f"{self.current_manhole_id}_{self.operation_start_time.strftime('%Y%m%d_%H%M%S')}"
            self._capture_frame("before", op_id)

            # Clear previous gas accumulation and start fresh
            self._gas_readings = []

            # Start elapsed timer
            self._raw_start = time.time()
            self.timer_val.setText("0:00")
            self.operation_timer.start(1000)

            self.manhole_id_changed.emit(self.current_manhole_id)
            logger.info(f"[OP] Started – Manhole: {self.current_manhole_id}  "
                        f"Location: ({self.manhole_lat}, {self.manhole_lon})  "
                        f"at {self.operation_start_time.strftime('%H:%M:%S')}")
            voice_module.speak_dual("Operation started", "ఆపరేషన్ ప్రారంభమైంది")
            
            if hasattr(self, 'start_btn'):
                self.start_btn.setDisabled(True)
            main_win = self.window()
            if hasattr(main_win, 'pipe_screen') and hasattr(main_win.pipe_screen, 'start_btn'):
                main_win.pipe_screen.start_btn.setDisabled(True)

    def handle_stop_operation(self):
        if not self.operation_timer.isActive():
            return

        self.operation_timer.stop()
        self.operation_end_time = datetime.datetime.now()
        elapsed_str = self.timer_val.text()
        elapsed_seconds = int(time.time() - self._raw_start)

        # Capture "after" frame
        op_id = (f"{self.current_manhole_id}_"
                 f"{self.operation_start_time.strftime('%Y%m%d_%H%M%S')}")
        after_path = self._capture_frame("after", op_id)
        before_path = os.path.join("captures", "before", f"{op_id}_before.jpg")

        # instead of summary popup, queue for upload
        main_win = self.window()
        if hasattr(main_win, 'uploader'):
            # Load config for device info
            config_dict = {}
            try:
                import config as cfg_module
                config_dict = {
                    'device_id': getattr(cfg_module, 'device_id', 'UNKNOWN'),
                    'area': getattr(cfg_module, 'area', 'UNKNOWN'),
                    'division': getattr(cfg_module, 'division', 'UNKNOWN'),
                    'district': getattr(cfg_module, 'district', 'UNKNOWN'),
                    'azure_connection_string': getattr(cfg_module, 'azure_connection_string', ''),
                    'azure_container_name': getattr(cfg_module, 'azure_container_name', ''),
                }
            except Exception as e:
                logger.error(f"Error loading config.py: {e}")

            # Calculate average gas values over the entire operation
            avg_gas_data = {}
            if self._gas_readings:
                all_keys = set()
                for rd in self._gas_readings:
                    all_keys.update(rd.keys())
                for key in all_keys:
                    vals = [rd[key] for rd in self._gas_readings if key in rd]
                    avg_gas_data[key] = round(sum(vals) / len(vals), 2) if vals else 0.0
                logger.info(f"[OP] Gas avg over {len(self._gas_readings)} samples: {avg_gas_data}")
            else:
                logger.warning("[OP] No gas readings collected during operation.")

            main_win.uploader.queue_operation(
                operation_id=op_id,
                operation_type='manhole_cleaning',
                manhole_id=self.current_manhole_id,
                before_path=before_path,
                after_path=after_path,
                before_depth=self.before_depth,
                after_depth=self.after_depth,
                start_time=self.operation_start_time,
                end_time=self.operation_end_time,
                duration_seconds=elapsed_seconds,
                gas_data=avg_gas_data,
                location={
                    'latitude': self.manhole_lat,
                    'longitude': self.manhole_lon,
                    'gps_fix': self.manhole_lat != 0.0 and self.manhole_lon != 0.0
                },
                config=config_dict
            )

        # Reset
        
        self.timer_val.setText("0:00")
        self.current_manhole_id = "N/A"
        self.manhole_lat = 0.0
        self.manhole_lon = 0.0
        self.before_depth = None
        self.after_depth = None
        self._gas_readings = []   # clear accumulated gas data
        self.manhole_id_changed.emit("N/A")
        self.depths_changed.emit(0.0, 0.0)
        logger.info(f"[OP] Stopped and queued: {op_id}")
        voice_module.speak_dual("Operation completed", "ఆపరేషన్ పూర్తయింది")
        voice_module.speak_dual("Uploading is in progress", "అప్లోడ్ అవుతోంది")
        
        if hasattr(self, 'start_btn'):
            self.start_btn.setEnabled(True)
        main_win = self.window()
        if hasattr(main_win, 'pipe_screen') and hasattr(main_win.pipe_screen, 'start_btn'):
            main_win.pipe_screen.start_btn.setEnabled(True)

    def _update_operation_time(self):
        elapsed = int(time.time() - self._raw_start)
        mins, secs = divmod(elapsed, 60)
        self.timer_val.setText(f"{mins}:{secs:02d}")
        if elapsed > 0 and elapsed % 60 == 0:
            voice_module.speak_dual("Operation is on process be safe and cautious", "ఆపరేషన్ కొనసాగుతోంది, సురక్షితంగా మరియు జాగ్రత్తగా ఉండండి")

    def update_feeds(self, main_pixmap, side_pixmap):
        if not main_pixmap.isNull():
            self.main_cam_label.setPixmap(main_pixmap)
            self.main_cam_label.setStyleSheet(
                "background-color: #000; border-radius: 4px;"
            )
            self.main_cam_label.setText("")
        if not side_pixmap.isNull():
            self.side_cam_label.setPixmap(side_pixmap)
            self.side_cam_label.setStyleSheet(
                "background-color: #000; border: 2px solid #ddd; border-radius: 8px;"
            )
            self.side_cam_label.setText("")

    def update_levers(self, vals_dict):
        for name, val in vals_dict.items():
            if name in self.levers_dict:
                self.levers_dict[name].set_value(val)
