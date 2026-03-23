"""GPS NMEA parsing thread.

Reads from a serial GPS module, parses $GNGGA/$GPGGA sentences, converts
NMEA coordinates to decimal degrees, and emits location signals.
"""
import time
import logging

import serial
from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class GPSThread(QThread):
    """Reads NMEA data from a serial GPS module."""

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
