"""Modbus/PLC communication thread.

Polls holding registers from a Modbus RTU device for lever/actuator values.
"""
import time
import logging

from PyQt5.QtCore import QThread, pyqtSignal
from pymodbus.client.sync import ModbusSerialClient

logger = logging.getLogger(__name__)


class ModbusThread(QThread):
    """Polls PLC lever values over Modbus RTU."""

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
