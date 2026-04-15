"""Unified sensor serial communication thread.

Reads gas sensor data (H2S, CO, CH4), depth distance, and weight
from a single combined ESP32 over serial. Provides a single source of
truth for gas and depth readings to avoid serial port contention.
"""
import os
import re
import time
import logging

import serial as _serial
from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class UnifiedSensorThread(QThread):
    """Reads combined gas and depth sensor data from a single serial port."""

    # Gas Signals
    data_received = pyqtSignal(dict)
    
    # Depth/Motor Signals
    depth_updated = pyqtSignal(int)
    auto_stop_triggered = pyqtSignal()
    connection_status = pyqtSignal(bool)
    port_discovered = pyqtSignal(str)

    def __init__(self, port="/dev/esp32", baudrate=115200, parent=None):
        super().__init__(parent)
        self.requested_port = port
        self.baudrate = baudrate
        self.running = True
        self._paused = False
        self.ser = None
        self._logged_error = False
        self.active_port_name = "N/A"

    def pause(self):
        logger.info(f"[SENSOR] Pausing – releasing {self.requested_port}")
        self._paused = True

    def resume(self):
        logger.info(f"[SENSOR] Resuming – reclaiming {self.requested_port}")
        self._paused = False

    def run(self):
        while self.running:
            if self._paused:
                time.sleep(0.2)
                continue

            # --- Check if device exists ---
            if not os.path.exists(self.requested_port):
                if not self._logged_error:
                    logger.warning(f"[SENSOR] {self.requested_port} not found. Waiting...")
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
                logger.debug(f"[SENSOR] Connecting to {candidate}...")

                ser = _serial.Serial(candidate, self.baudrate, timeout=1)
                time.sleep(2)  # ESP32 reset delay
                ser.reset_input_buffer()

                active_port = candidate
                self.ser = ser
                self.active_port_name = active_port
                self.connection_status.emit(True)
                self.port_discovered.emit(active_port)

                if self._logged_error:
                    logger.info(f"[SENSOR] Reconnected on {active_port}")
                else:
                    logger.info(f"[SENSOR] Connected on {active_port} @ {self.baudrate} baud")

                self._logged_error = False

                _readings = {}

                # --- Data read loop ---
                while self.running and not self._paused:
                    raw_bytes = self.ser.readline()

                    if raw_bytes:
                        line = raw_bytes.decode(errors="ignore").strip()

                        if line:
                            # 1) Gas Sensors
                            if line.startswith("H2S:"):
                                try: _readings["H2S"] = float(line.split(":")[1])
                                except ValueError: pass
                            elif line.startswith("CO:"):
                                try: _readings["CO"] = float(line.split(":")[1])
                                except ValueError: pass
                            elif line.startswith("CH4:"):
                                try: _readings["CH4"] = float(line.split(":")[1])
                                except ValueError: pass
                                
                            # 2) Depth / Weight
                            elif line.startswith("Dist:"):
                                try:
                                    d = float(line.split(":")[1])
                                    self.depth_updated.emit(int(d))
                                except ValueError: pass
                            elif line.startswith("Weight:"):
                                try:
                                    w = float(line.split(":")[1])
                                except ValueError: pass

                            # Check for auto trigger words
                            if "load cell" in line.lower() or "trigger" in line.lower() or "stop" in line.lower():
                                self.auto_stop_triggered.emit()

                            # If a full gas block is read, emit and clear
                            if "H2S" in _readings and "CO" in _readings and "CH4" in _readings:
                                self.data_received.emit(dict(_readings))
                                _readings.clear()

            except Exception as e:
                if not self._logged_error:
                    logger.warning(f"[SENSOR] Device error: {e}")
                    self._logged_error = True

                self.connection_status.emit(False)

                if ser:
                    try: ser.close()
                    except: pass
                ser = None
                self.ser = None

                time.sleep(3)

            finally:
                if ser:
                    try: ser.close()
                    except: pass

    def send_command(self, cmd):
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(f"{cmd}\n".encode())
                logger.info(f"[SENSOR] Sent command: {cmd}")
            except Exception as e:
                logger.error(f"[SENSOR] Failed to send {cmd}: {e}")

    def stop(self):
        self.running = False
        if self.ser:
            try: self.ser.close()
            except: pass
        self.wait()
