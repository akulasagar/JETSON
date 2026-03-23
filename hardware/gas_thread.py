"""Gas sensor serial communication thread.

Reads gas sensor data (H2S, CO, CH4) from an ESP32 over serial and
emits parsed PPM readings.
"""
import os
import re
import time
import logging

import serial
from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class GasThread(QThread):
    """Reads gas sensor PPM values from an ESP32 serial device."""

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
