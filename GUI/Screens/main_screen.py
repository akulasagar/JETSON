import sys
import os
import datetime
import time
import cv2
import numpy as np
import logging
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QFrame, QGridLayout, QStackedWidget, QSizePolicy)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QRect, QRectF
from PyQt5.QtGui import QPixmap, QImage, QPainter, QColor, QLinearGradient, QPainterPath
from PyQt5.QtWidgets import QDialog, QMessageBox

from GUI.Screens.pipe_cleaning import PipeCleaningWidget
from GUI.PopUps.Manhole_popup import StartOperationPopup, StopOperationPopup, _get_db_connection
from GUI.PopUps.MeasureDepth import MeasureDepthPopup
from data_uploader import Uploader, UploadStatus
import json
from pymodbus.client.sync import ModbusSerialClient
import voice_module

# NOTE: logging level is configured in main.py — do NOT call basicConfig here.
logger = logging.getLogger(__name__)

os.makedirs("captures/before", exist_ok=True)
os.makedirs("captures/after", exist_ok=True)

# ---------------------------------------------------------------------------
# Camera Thread  (matches dev_test_load.py logic)
# ---------------------------------------------------------------------------
class CameraThread(QThread):
    frame_available = pyqtSignal(QImage, int)
    camera_error = pyqtSignal(str, int)
    camera_reconnecting = pyqtSignal(str, int)

    def __init__(self, camera_index=0, parent=None):
        super().__init__(parent)
        self.camera_index = camera_index
        self.running = True
        self.last_frame = None
        self.cap = None
        self.target_width = 1280
        self.target_height = 720

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def run(self):
        self._setup_usb_camera()

    # ------------------------------------------------------------------
    # Camera connection logic
    # ------------------------------------------------------------------
    def _setup_usb_camera(self):

        # Port groups for each camera
        if self.camera_index == 0:
            candidate_ports = [0, 1]  # /dev/video0 or /dev/video1
        else:
            candidate_ports = [2, 3]  # /dev/video2 or /dev/video3

        backends = [cv2.CAP_V4L2, cv2.CAP_ANY]

        while self.running:

            if not self.cap or not self.cap.isOpened():

                opened = False

                for device_index in candidate_ports:

                    logger.info(f"[CAM-{self.camera_index}] Trying /dev/video{device_index}")

                    self.camera_reconnecting.emit(
                        f"Connecting camera {self.camera_index + 1}…",
                        self.camera_index
                    )

                    for backend in backends:

                        cap = cv2.VideoCapture(device_index, backend)

                        if cap.isOpened():

                            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.target_width)
                            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.target_height)
                            cap.set(cv2.CAP_PROP_FPS, 30)

                            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                            fps = cap.get(cv2.CAP_PROP_FPS)

                            logger.info(
                                f"[CAM-{self.camera_index}] Connected on /dev/video{device_index} "
                                f"{w}x{h} @ {fps:.1f} fps"
                            )

                            self.cap = cap
                            opened = True

                            self.camera_reconnecting.emit(
                                f"Camera {self.camera_index + 1} connected",
                                self.camera_index
                            )

                            break

                        else:
                            cap.release()

                    if opened:
                        break

                if not opened:

                    logger.error(
                        f"[CAM-{self.camera_index}] Camera not found in ports {candidate_ports}"
                    )

                    self.camera_error.emit(
                        f"Camera {self.camera_index + 1} unavailable",
                        self.camera_index
                    )

                    self._emit_simulation_frame()

                    time.sleep(5)
                    continue

            self._read_frames(device_index)

    # ------------------------------------------------------------------
    # Frame read loop
    # ------------------------------------------------------------------
    def _read_frames(self, device_index):

        while self.running and self.cap and self.cap.isOpened():

            ret, frame = self.cap.read()

            if ret and frame is not None:

                self.last_frame = frame.copy()

                frame_resized = cv2.resize(
                    frame, (self.target_width, self.target_height)
                )

                cx = self.target_width // 2
                cy = self.target_height // 2

                gap = 15
                line_len = 18
                color = (240, 240, 240)
                thickness = 2

                cv2.line(frame_resized, (cx - gap - line_len, cy), (cx - gap, cy),
                         color, thickness, cv2.LINE_AA)
                cv2.line(frame_resized, (cx + gap, cy), (cx + gap + line_len, cy),
                         color, thickness, cv2.LINE_AA)
                cv2.line(frame_resized, (cx, cy - gap - line_len), (cx, cy - gap),
                         color, thickness, cv2.LINE_AA)
                cv2.line(frame_resized, (cx, cy + gap), (cx, cy + gap + line_len),
                         color, thickness, cv2.LINE_AA)

                frame_rgb = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2RGB)

                h, w, ch = frame_rgb.shape

                q_img = QImage(
                    frame_rgb.data,
                    w,
                    h,
                    ch * w,
                    QImage.Format_RGB888
                )

                self.frame_available.emit(q_img.copy(), self.camera_index)

            else:

                logger.warning(
                    f"[CAM-{self.camera_index}] Frame read failed – reconnecting…"
                )

                if self.cap:
                    self.cap.release()
                    self.cap = None

                self.camera_reconnecting.emit(
                    f"Camera {self.camera_index + 1} disconnected",
                    self.camera_index
                )

                break

            time.sleep(0.033)

        if self.cap and self.cap.isOpened():
            self.cap.release()
            self.cap = None

    # ------------------------------------------------------------------
    # Simulation frame
    # ------------------------------------------------------------------
    def _emit_simulation_frame(self):

        frame = np.zeros((self.target_height, self.target_width, 3), dtype=np.uint8)

        color = (40, 40, 50) if self.camera_index == 0 else (30, 30, 45)

        frame[:] = color

        cv2.putText(
            frame,
            f"Camera {self.camera_index + 1} – No Signal",
            (350, self.target_height // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (90, 90, 100),
            2
        )

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        h, w, ch = frame_rgb.shape

        q_img = QImage(
            frame_rgb.data,
            w,
            h,
            ch * w,
            QImage.Format_RGB888
        )

        self.frame_available.emit(q_img.copy(), self.camera_index)

    # ------------------------------------------------------------------
    def get_last_frame(self):
        return self.last_frame

    # ------------------------------------------------------------------
    def stop(self):
        self.running = False
        if self.cap:
            self.cap.release()
        self.wait()

# ---------------------------------------------------------------------------
# GPS Thread
# ---------------------------------------------------------------------------
import serial

class GPSThread(QThread):
    location_updated = pyqtSignal(float, float, bool)  # lat, lon, fix_status
    connection_status = pyqtSignal(bool)

    def __init__(self, port='/dev/ttyUSB1', baudrate=115200, parent=None):
        super().__init__(parent)
        self.port = port
        self.baudrate = baudrate
        self.running = True
        self.current_lat = 0.0
        self.current_lon = 0.0
        self.has_fix = False
        self._logged_error = False  # suppress repeated "not found" logs

    def run(self):
        while self.running:
            ser = None
            try:
                ser = serial.Serial(self.port, self.baudrate, timeout=2)
                # Successfully connected – reset error flag and announce
                if self._logged_error:
                    logger.info(f"[GPS] Reconnected to {self.port}")
                else:
                    logger.info(f"[GPS] Connected to {self.port}")
                self._logged_error = False
                self.connection_status.emit(True)

                while self.running:
                    line = ser.readline().decode("utf-8", errors="ignore").strip()
                    if line.startswith("$GNGGA") or line.startswith("$GPGGA"):
                        parts = line.split(",")
                        if len(parts) > 6:
                            fix_quality = parts[6]
                            if fix_quality and int(fix_quality) > 0:
                                raw_lat = parts[2]
                                lat_dir = parts[3]
                                raw_lon = parts[4]
                                lon_dir = parts[5]

                                if raw_lat and raw_lon:
                                    # Convert NMEA to Decimal
                                    lat = float(raw_lat)
                                    lon = float(raw_lon)

                                    lat_deg = int(lat / 100)
                                    lat_min = lat - (lat_deg * 100)
                                    lon_deg = int(lon / 100)
                                    lon_min = lon - (lon_deg * 100)

                                    lat = lat_deg + (lat_min / 60.0)
                                    lon = lon_deg + (lon_min / 60.0)

                                    if lat_dir == "S": lat = -lat
                                    if lon_dir == "W": lon = -lon

                                    self.current_lat = lat
                                    self.current_lon = lon
                                    self.has_fix = True
                                    self.location_updated.emit(lat, lon, True)
                            else:
                                self.has_fix = False
                                self.location_updated.emit(0.0, 0.0, False)
                    time.sleep(0.1)
            except Exception as e:
                # Only log the first failure; stay silent on repeated retries
                if not self._logged_error:
                    logger.warning(f"[GPS] Device not found on {self.port} – will keep retrying silently.")
                    self._logged_error = True
                self.connection_status.emit(False)
                self.has_fix = False
                self.location_updated.emit(0.0, 0.0, False)
                time.sleep(5)  # Retry after some time
            finally:
                if ser:
                    ser.close()

    def stop(self):
        self.running = False
        self.wait()


# ---------------------------------------------------------------------------
# Modbus / PLC Thread
# ---------------------------------------------------------------------------
class ModbusThread(QThread):
    levels_updated = pyqtSignal(dict)

    def __init__(self, port='/dev/ttyCH341USB0', parent=None):
        super().__init__(parent)
        self.port = port
        self.running = True
        self.slave = 10
        self.registers = [
            ("ROTATION", 44209 - 40001),
            ("TELESCOPE", 44201 - 40001),
            ("LIFT", 44205 - 40001),     # mapped 'Height' to LIFT
            ("EXTEND", 44197 - 40001),   # mapped 'Extension' to EXTEND
        ]

    def run(self):
        client = ModbusSerialClient(
            method='rtu',
            port=self.port,
            baudrate=9600,
            bytesize=8,
            parity='N',
            stopbits=1,
            timeout=1
        )

        retry_delay = 2.0
        max_retry_delay = 15.0
        was_connected = False
        _logged_error = False  # suppress repeated "not found" logs

        while self.running:
            try:
                if not client.connect():
                    # Log only on first failure, then stay silent
                    if not _logged_error:
                        logger.warning(f"[MODBUS] Device not found on {self.port} – will keep retrying silently.")
                        _logged_error = True
                    was_connected = False
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay + 2.0, max_retry_delay)
                    continue

                if not was_connected:
                    if _logged_error:
                        logger.info(f"[MODBUS] Reconnected to {self.port}")
                    else:
                        logger.info(f"[MODBUS] Successfully connected to {self.port}")
                    was_connected = True
                    _logged_error = False
                    retry_delay = 2.0  # reset backoff

                vals_dict = {}
                for name, addr in self.registers:
                    r = client.read_holding_registers(addr, 1, unit=self.slave)
                    if not r.isError():
                        val = r.registers[0]
                        val = val if val < 32768 else val - 65536
                        vals_dict[name] = val

                if vals_dict:
                    self.levels_updated.emit(vals_dict)

            except Exception as e:
                if was_connected:
                    logger.warning(f"[MODBUS] Connection lost: {e}")
                    was_connected = False
                    _logged_error = True
                client.close()
                time.sleep(retry_delay)
                retry_delay = min(retry_delay + 2.0, max_retry_delay)
                continue

            time.sleep(0.3)

    def stop(self):
        self.running = False
        self.wait()




# ---------------------------------------------------------------------------
# Gas Sensor Thread
# ---------------------------------------------------------------------------
class GasThread(QThread):
    data_received = pyqtSignal(dict)

    def __init__(self, port="/dev/esp32", baudrate=115200, parent=None):
        super().__init__(parent)
        self.port = port
        self.baudrate = baudrate
        self.running = True
        self._paused = False
        self._logged_error = False
        self.active_port = None

    def pause(self):
        logger.info(f"[GAS] Pausing – releasing {self.port}")
        self._paused = True

    def resume(self):
        logger.info(f"[GAS] Resuming – reclaiming {self.port}")
        self._paused = False

    def run(self):
        import serial
        import re
        import os
        import time

        while self.running:

            if self._paused:
                time.sleep(0.2)
                continue

            if not os.path.exists(self.port):
                if not self._logged_error:
                    logger.warning(f"[GAS] Device {self.port} not found. Waiting...")
                    self._logged_error = True
                time.sleep(3)
                continue

            ser = None

            try:
                real_port = os.path.realpath(self.port)

                ser = serial.Serial(real_port, self.baudrate, timeout=1)
                time.sleep(2)
                ser.reset_input_buffer()

                self.active_port = real_port

                if self._logged_error:
                    logger.info(f"[GAS] Reconnected on {real_port}")
                else:
                    logger.info(f"[GAS] Connected on {real_port} @ {self.baudrate}")

                self._logged_error = False

                _current_gas = None
                _readings = {}
                _no_data_ticks = 0
                _GAS_ALIASES = {"H2S": "H2S", "CO": "CO", "CH4": "CH4"}

                while self.running and not self._paused:

                    if ser.in_waiting > 0:
                        _no_data_ticks = 0

                        raw = ser.readline().decode("ascii", errors="ignore").strip()

                        if raw:

                            m = re.search(r"\((\w+)\)", raw)

                            if raw.startswith("===") and m:
                                gas_key = m.group(1).upper()
                                _current_gas = _GAS_ALIASES.get(gas_key)

                            elif raw.startswith("Estimated ppm:") and _current_gas:
                                try:
                                    ppm = float(raw.split(":")[1].strip())
                                    _readings[_current_gas] = ppm
                                except:
                                    pass

                        if len(_readings) == 3:
                            self.data_received.emit(dict(_readings))
                            _readings.clear()
                            _current_gas = None

                    else:
                        _no_data_ticks += 1

                        if _no_data_ticks >= 5000:
                            logger.warning("[GAS] No data received. Reconnecting...")
                            break

                    time.sleep(0.1)

            except Exception as e:

                if not self._logged_error:
                    logger.warning(f"[GAS] Device error: {e}")
                    self._logged_error = True

                time.sleep(3)

            finally:
                if ser:
                    try:
                        ser.close()
                    except:
                        pass

    def stop(self):
        self.running = False
        self.wait()


# ---------------------------------------------------------------------------
# Depth Sensor Thread (Shared)
# ---------------------------------------------------------------------------
class DepthThread(QThread):
    depth_updated = pyqtSignal(int)
    auto_stop_triggered = pyqtSignal()
    connection_status = pyqtSignal(bool)
    port_discovered = pyqtSignal(str)

    def __init__(self, port="/dev/arduino", baudrate=57600, parent=None):
        super().__init__(parent)
        self.requested_port = port
        self.baudrate = baudrate
        self.running = True
        self.ser = None
        self._logged_error = False
        self.active_port_name = "N/A"

    def run(self):
        import serial as _serial
        import re as _re
        import os
        import time

        while self.running:

            # --- Check if device exists ---
            if not os.path.exists(self.requested_port):
                if not self._logged_error:
                    logger.warning(f"[DEPTH] {self.requested_port} not found. Waiting...")
                    self._logged_error = True

                self.connection_status.emit(False)
                self.port_discovered.emit("N/A")

                time.sleep(3)
                continue

            ser = None
            active_port = None

            try:
                # --- Resolve real ACM port ---
                candidate = os.path.realpath(self.requested_port)

                logger.debug(f"[DEPTH] Connecting to {candidate}...")

                ser = _serial.Serial(candidate, self.baudrate, timeout=1)

                time.sleep(2)  # Arduino reset delay
                ser.reset_input_buffer()

                active_port = candidate

                # --- Connection successful ---
                self.ser = ser
                self.active_port_name = active_port
                self.connection_status.emit(True)
                self.port_discovered.emit(active_port)

                if self._logged_error:
                    logger.info(f"[DEPTH] Reconnected on {active_port}")
                else:
                    logger.info(f"[DEPTH] Connected on {active_port} @ {self.baudrate} baud")

                self._logged_error = False

                # --- KEEP YOUR EXISTING DATA LOGIC ---
                while self.running:

                    raw_bytes = self.ser.readline()

                    if raw_bytes:
                        line = raw_bytes.decode(errors="ignore").strip()

                        if line:

                            m = _re.search(r"Distance:\s*([0-9.]+)", line, _re.IGNORECASE)

                            if m:
                                d = float(m.group(1))
                                self.depth_updated.emit(int(d))

                            if (
                                "load cell" in line.lower()
                                or "trigger" in line.lower()
                                or "stop" in line.lower()
                            ):
                                self.auto_stop_triggered.emit()

            except Exception as e:

                if not self._logged_error:
                    logger.warning(f"[DEPTH] Device error: {e}")
                    self._logged_error = True

                self.connection_status.emit(False)

                if ser:
                    try:
                        ser.close()
                    except:
                        pass

                ser = None
                self.ser = None

                time.sleep(3)

            finally:
                if ser:
                    try:
                        ser.close()
                    except:
                        pass

    def send_command(self, cmd):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(f"{cmd}\n".encode())
                logger.info(f"[DEPTH] Sent command: {cmd}")
            except Exception as e:
                logger.error(f"[DEPTH] Failed to send {cmd}: {e}")

    def stop(self):
        self.running = False
        if self.ser:
            try:
                self.ser.close()
            except:
                pass
        self.wait()


# ---------------------------------------------------------------------------
# Lever widget
# ---------------------------------------------------------------------------
class PLCLever(QWidget):
    def __init__(self, label_text, parent=None):
        super().__init__(parent)
        self.label_text = label_text
        self.setFixedWidth(100)
        self.value = 50
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.lever_widget = QLabel()
        self.lever_widget.setFixedSize(50, 110)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("color: #000; font-weight: bold; font-size: 11px; font-family: 'Arial';")
        layout.addWidget(self.lever_widget, 0, Qt.AlignCenter)
        layout.addWidget(self.label)
        self.update_lever_ui()

    def set_value(self, val):
        self.value = val
        self.update_lever_ui()

    def update_lever_ui(self):
        name = self.label_text.capitalize() if hasattr(self, 'label_text') else ""
        self.label.setText(f"{name}\n{self.value}")

        visual_value = max(0, min(100, self.value))

        pixmap = QPixmap(self.lever_widget.size())
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRect(0, 0, pixmap.width(), pixmap.height())
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 25, 25)
        painter.fillPath(path, QColor("#000000"))
        
        fill_height = int(pixmap.height() * (visual_value / 100.0))
        if fill_height > 0:
            fill_rect = QRect(0, pixmap.height() - fill_height, pixmap.width(), fill_height)
            gradient  = QLinearGradient(0, pixmap.height() - fill_height, 0, pixmap.height())
            gradient.setColorAt(0, QColor("#219EBC"))
            gradient.setColorAt(1, QColor("#023e8a"))
            painter.setClipPath(path)
            painter.fillRect(fill_rect, gradient)
        painter.end()
        self.lever_widget.setPixmap(pixmap)


# ---------------------------------------------------------------------------
# Manhole screen widget
# ---------------------------------------------------------------------------
class ManholeWidget(QWidget):
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
            voice_module.speak_dual("Operation started", "పని ప్రారంభమైంది")
            
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
        voice_module.speak_dual("Operation completed", "పని పూర్తయింది")
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
            voice_module.speak_dual("Operation is on process be safe and cautious", "పని కొనసాగుతోంది, సురక్షితంగా మరియు జాగ్రత్తగా ఉండండి")

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


# ---------------------------------------------------------------------------
# Main Dashboard
# ---------------------------------------------------------------------------
class MainDashboard(QMainWindow):
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

        # Establish DB connection early
        try:
            import threading
            threading.Thread(target=_get_db_connection, daemon=True).start()
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

        # Mirror Manhole timer and manhole ID to Pipe Screen if needed
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
            # First Escape press: exit full screen. Second press: actually minimize to taskbar.
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showMinimized()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self,
            'Exit Confirmation',
            'Are you sure you want to quit the application?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            event.accept()
        else:
            event.ignore()

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
        # Status can be success, failed, processing, queued, etc.
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

        # Try /dev/video1 for second feed; if unavailable the thread will show
        # "No Signal" and keep retrying
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
        
        # Style matching image
        lbl_style = "font-size: 15px; color: #1a1a1a; font-family: 'Outfit'; font-weight: normal;"
        self.date_lbl.setStyleSheet(lbl_style)
        self.time_lbl.setStyleSheet(lbl_style)
        self.date_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.time_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        
        dt_v.addWidget(self.date_lbl)
        dt_v.addWidget(self.time_lbl)
        h_layout.addWidget(dt_frame)
        
        self.layout.addWidget(header)
        # self._update_datetime() # Will be called by timer

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

        # ── Live Manhole ID ──────────────────────────────────────────────
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

    def closeEvent(self, event):
        self.cam0_thread.stop()
        self.cam1_thread.stop()
        self.gps_thread.stop()
        self.gas_thread.stop()
        if hasattr(self, 'depth_thread'):
            self.depth_thread.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainDashboard()
    window.show()
    sys.exit(app.exec_())
