"""Gas gauge radial widget.

Renders a semi-circular gauge with Normal/Warning/Danger zones,
animated needle, PPM readout, and gas name pill.

NOTE: This is the SINGLE canonical definition. Previously duplicated
in pipe_cleaning.py and load.py.
"""
from PyQt5.QtWidgets import QWidget, QSizePolicy
from PyQt5.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt5.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QPainterPath
)


class GasGaugeWidget(QWidget):
    """Compact radial gauge for gas sensor PPM display."""

    def __init__(self, gas_name: str, display_name: str, unit: str = "ppm",
                 warning_val: float = 100.0, danger_val: float = 200.0,
                 max_val: float = 1000.0, parent=None):
        super().__init__(parent)
        self.gas_name = gas_name
        self.display_name = display_name
        self.unit = unit
        self.warning_val = warning_val
        self.danger_val = danger_val
        self.max_val = max_val

        # Lower minimum size to ensure it fits on small screens (like 1024x600)
        self.setMinimumSize(120, 100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._value = 0.0
        self._target_value = 0.0

        self._gas_pill_colors = {
            "Carbon Monoxide (CO)": "#FF5252",
            "Methane (CH4)": "#FFD740",
            "Carbon Dioxide (CO2)": "#4CAF50",
            "Hydrogen Sulfide (H2S)": "#8BC34A",
            "Ammonia (NH3)": "#FFAB91"
        }
        self.pill_color = self._gas_pill_colors.get(display_name, "#4CAF50")

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self._animate)
        self.anim_timer.start(30)

    def set_value(self, value: float):
        self._target_value = max(0, min(self.max_val, value))

    def _animate(self):
        diff = self._target_value - self._value
        if abs(diff) < 0.1:
            self._value = self._target_value
        else:
            self._value += diff * 0.15
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h * 0.75

        radius = min(w * 0.45, h * 0.65)
        thickness = radius * 0.2

        arc_rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)

        # Background grey arc
        painter.setPen(QPen(QColor("#F0F0F0"), thickness, Qt.SolidLine, Qt.FlatCap))
        painter.drawArc(arc_rect, 0 * 16, 180 * 16)

        # Segments (FIXED VISUALS AS REQUESTED)
        # Green: 180 to 130 (50 deg span)
        painter.setPen(QPen(QColor("#4CAF50"), thickness, Qt.SolidLine, Qt.FlatCap))
        painter.drawArc(arc_rect, 130 * 16, 50 * 16)

        # Yellow: 130 to 70 (60 deg span)
        painter.setPen(QPen(QColor("#FFD740"), thickness, Qt.SolidLine, Qt.FlatCap))
        painter.drawArc(arc_rect, 70 * 16, 60 * 16)

        # Red: 70 to 0 (70 deg span)
        painter.setPen(QPen(QColor("#FF5252"), thickness, Qt.SolidLine, Qt.FlatCap))
        painter.drawArc(arc_rect, 0 * 16, 70 * 16)

        # Labels (RESTORED AS REQUESTED)
        label_font = QFont("Arial", max(6, int(radius * 0.1)), QFont.Normal)
        painter.setFont(label_font)
        painter.setPen(QColor("#888"))
        painter.drawText(int(cx - radius - 5), int(cy - 2), "Normal")
        painter.drawText(int(cx - 15), int(cy - radius - 5), "Warning")
        painter.drawText(int(cx + radius - 15), int(cy - 2), "Danger")

        # Needle (Piecewise Linear Mapping to fit fixed segments)
        raw_val = self._value
        if raw_val <= self.warning_val:
            angle = (raw_val / self.warning_val) * 50 if self.warning_val > 0 else 50
        elif raw_val <= self.danger_val:
            angle = 50 + ((raw_val - self.warning_val) / (self.danger_val - self.warning_val)) * 60 if (self.danger_val - self.warning_val) > 0 else 110
        else:
            angle = 110 + ((raw_val - self.danger_val) / (self.max_val - self.danger_val)) * 70 if (self.max_val - self.danger_val) > 0 else 180

        angle = min(180, angle)
        needle_angle = 180 - angle

        painter.save()
        painter.translate(cx, cy)
        painter.rotate(-needle_angle)

        needle_path = QPainterPath()
        needle_path.moveTo(0, -2)
        needle_path.lineTo(radius * 0.9, 0)
        needle_path.lineTo(0, 2)
        needle_path.closeSubpath()

        painter.setBrush(QBrush(QColor("#333")))
        painter.setPen(Qt.NoPen)
        painter.drawPath(needle_path)
        painter.drawEllipse(QPointF(0, 0), 5, 5)
        painter.restore()

        # PPM Box
        val_font = QFont("Arial", max(7, int(radius * 0.12)), QFont.Bold)
        painter.setFont(val_font)
        val_str = f"{int(self._value)} {self.unit}"
        text_w = painter.fontMetrics().width(val_str) + 10
        val_rect = QRectF(cx - text_w/2, cy - radius * 0.4, text_w, radius * 0.25)
        painter.setBrush(QBrush(QColor("#FFF")))
        painter.setPen(QPen(QColor("#EEE"), 1))
        painter.drawRoundedRect(val_rect, 3, 3)
        painter.setPen(QColor("#111"))
        painter.drawText(val_rect, Qt.AlignCenter, val_str)

        # Name Pill
        pill_font = QFont("Arial", max(7, int(radius * 0.11)), QFont.Bold)
        painter.setFont(pill_font)
        disp_name = self.display_name
        text_w = painter.fontMetrics().width(disp_name) + 12
        pill_rect = QRectF(cx - text_w/2, cy + 10, text_w, 20)
        painter.setBrush(QBrush(QColor(self.pill_color)))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(pill_rect, 4, 4)
        painter.setPen(QColor("#FFF"))
        painter.drawText(pill_rect, Qt.AlignCenter, disp_name)
