"""Depth sensor (Arduino) serial communication thread.

Reads ultrasonic distance data from an Arduino, detects load-cell
auto-stop triggers, and provides a send_command interface for motor control.
"""
import os
import re
import time
import logging

import serial as _serial
from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class DepthThread(QThread):
    """Reads depth sensor data from an Arduino serial device."""

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

                # --- Data read loop ---
                while self.running:

                    raw_bytes = self.ser.readline()

                    if raw_bytes:
                        line = raw_bytes.decode(errors="ignore").strip()

                        if line:

                            m = re.search(r"Distance:\s*([0-9.]+)", line, re.IGNORECASE)

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
