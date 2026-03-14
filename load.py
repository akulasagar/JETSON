import sys
import os
import time
import logging
import numpy as np

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGridLayout, QSizePolicy, QProgressBar, QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QSize, QRectF, QPointF, QRect
from PyQt5.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QTransform,
    QLinearGradient, QPainterPath, QPixmap
)

# ─────────────────────────────────────────────────────────────────────────────
# PLC Lever Widget (Replicated here to avoid circular imports)
# ─────────────────────────────────────────────────────────────────────────────
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

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Gas Gauge Widget
# ─────────────────────────────────────────────────────────────────────────────
class GasGaugeWidget(QWidget):
    """
    Compact Radial Gauge.
    Reduces memory and size to fit smaller displays.
    """

    def __init__(self, gas_name: str, display_name: str, unit: str = "ppm", 
                 max_val: float = 1000.0, parent=None):
        super().__init__(parent)
        self.gas_name = gas_name
        self.display_name = display_name
        self.unit = unit
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

        # Segments
        painter.setPen(QPen(QColor("#4CAF50"), thickness, Qt.SolidLine, Qt.FlatCap))
        painter.drawArc(arc_rect, 130 * 16, 50 * 16)
        painter.setPen(QPen(QColor("#FFD740"), thickness, Qt.SolidLine, Qt.FlatCap))
        painter.drawArc(arc_rect, 70 * 16, 60 * 16)
        painter.setPen(QPen(QColor("#FF5252"), thickness, Qt.SolidLine, Qt.FlatCap))
        painter.drawArc(arc_rect, 0 * 16, 70 * 16)

        # Labels
        label_font = QFont("Arial", max(6, int(radius * 0.1)), QFont.Normal)
        painter.setFont(label_font)
        painter.setPen(QColor("#888"))
        painter.drawText(int(cx - radius - 5), int(cy - 2), "Normal")
        painter.drawText(int(cx - 15), int(cy - radius - 5), "Warning")
        painter.drawText(int(cx + radius - 15), int(cy - 2), "Danger")

        # Needle
        angle = (self._value / self.max_val) * 180
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


# ─────────────────────────────────────────────────────────────────────────────
# PipeCleaningWidget (Main Screen)
# ─────────────────────────────────────────────────────────────────────────────
class PipeCleaningWidget(QWidget):
    switch_to_manhole = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PipeCleaningScreen")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        self._init_content()

    def _init_content(self):
        # Container to match Main Screen margins
        content_frame  = QWidget()
        content_layout = QHBoxLayout(content_frame)
        content_layout.setContentsMargins(20, 10, 20, 10)
        content_layout.setSpacing(20)

        # ── LEFT SIDE (72% Stretch) ──
        # Same structure as ManholeWidget
        left_side = QVBoxLayout()
        left_side.setSpacing(15)

        # Gauges Container (REPLACES Main Camera)
        self.g_card = QFrame()
        self.g_card.setObjectName("GaugesCard")
        self.g_card.setStyleSheet("""
            #GaugesCard {
                background-color: #ffffff;
                border: 1px solid #EEE;
                border-radius: 8px;
            }
        """)
        # Set minimal size policies to avoid pushing outside display
        self.g_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        g_grid = QGridLayout(self.g_card)
        g_grid.setContentsMargins(15, 10, 15, 10)
        g_grid.setSpacing(5)

        self.gauges = {
            'CO':  GasGaugeWidget('CO',  'Carbon Monoxide (CO)', max_val=200),
            'CH4': GasGaugeWidget('CH4', 'Methane (CH4)', max_val=5000),
            'CO2': GasGaugeWidget('CO2', 'Carbon Dioxide (CO2)', max_val=5000),
            'H2S': GasGaugeWidget('H2S', 'Hydrogen Sulfide (H2S)', max_val=100),
            'NH3': GasGaugeWidget('NH3', 'Ammonia (NH3)', max_val=100)
        }
        
        # 2-1-2 Layout to fit the space beautifully
        g_grid.addWidget(self.gauges['CO'],  0, 0)
        g_grid.addWidget(self.gauges['CH4'], 0, 2)
        g_grid.addWidget(self.gauges['CO2'], 1, 1)
        g_grid.addWidget(self.gauges['H2S'], 2, 0)
        g_grid.addWidget(self.gauges['NH3'], 2, 2)
        
        left_side.addWidget(self.g_card, 1) # Stretch 1

        # Levers Row (Exactly the same as ManholeWidget)
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
        
        left_side.addLayout(levers_row, 0) # Stretch 0
        content_layout.addLayout(left_side, 72)

        # ── RIGHT SIDEBAR (28% Stretch) ──
        # Shared elements with ManholeWidget logic
        right_side_widget = QWidget()
        sidebar = QVBoxLayout(right_side_widget)
        sidebar.setContentsMargins(0, 0, 0, 0)
        sidebar.setSpacing(15)

        self.measure_btn = QPushButton("🌡 Measure Depth")
        self.measure_btn.setFixedHeight(50)
        self.measure_btn.setStyleSheet("background-color: #219EBC; color: white; font-size: 15px; font-weight: bold; border-radius: 8px;")
        sidebar.addWidget(self.measure_btn)

        op_frame = QFrame()
        op_frame.setStyleSheet("background-color: #f2f4f6; border-radius: 12px;")
        op_lay = QVBoxLayout(op_frame)
        op_lay.setContentsMargins(15, 15, 15, 15)
        op_lay.setSpacing(10)

        op_grid = QGridLayout()
        op_grid.setSpacing(10)

        self.start_btn = QPushButton("▶\nStart Operation")
        self.start_btn.setFixedHeight(80)
        self.start_btn.setStyleSheet("background-color: #00c853; color: white; font-size: 13px; font-weight: bold; border-radius: 8px;")

        self.stop_btn = QPushButton("⏸\nStop Operation")
        self.stop_btn.setFixedHeight(80)
        self.stop_btn.setStyleSheet("background-color: #ff1744; color: white; font-size: 13px; font-weight: bold; border-radius: 8px;")

        self.switch_cam_btn = QPushButton("🔄\nSwitch Camera")
        self.switch_cam_btn.setFixedHeight(80)
        self.switch_cam_btn.setStyleSheet("background-color: #e2e8f0; color: #1a1a1a; font-size: 13px; font-weight: bold; border-radius: 8px;")

        timer_box = QFrame()
        timer_box.setFixedHeight(80)
        timer_box.setStyleSheet("background-color: #e2e8f0; border-radius: 8px;")
        t_lay = QVBoxLayout(timer_box)
        t_lay.setAlignment(Qt.AlignCenter)
        t_lay.setSpacing(2)
        t_icon = QLabel("⏱", styleSheet="font-size: 22px; color: #ff9100; font-weight: bold; background:transparent;")
        self.timer_val = QLabel("0:00", styleSheet="font-size: 20px; font-weight: bold; color: #1a1a1a; background:transparent;")
        t_lay.addWidget(t_icon); t_lay.addWidget(self.timer_val)

        op_grid.addWidget(self.start_btn, 0, 0); op_grid.addWidget(self.stop_btn, 0, 1)
        op_grid.addWidget(self.switch_cam_btn, 1, 0); op_grid.addWidget(timer_box, 1, 1)
        op_lay.addLayout(op_grid)
        sidebar.addWidget(op_frame)

        self.manhole_btn = QPushButton("‖ Manhole Cleaning")
        self.manhole_btn.setFixedHeight(50)
        self.manhole_btn.setStyleSheet("background-color: #f2f4f6; color: #1a1a1a; font-size: 15px; font-weight: bold; border-radius: 8px; border: 1px solid #ddd;")
        self.manhole_btn.clicked.connect(self.switch_to_manhole.emit)
        sidebar.addWidget(self.manhole_btn)

        self.gas_sys_btn = QPushButton("〓 Gases Detection System")
        self.gas_sys_btn.setFixedHeight(50)
        self.gas_sys_btn.setStyleSheet("background-color: #219EBC; color: white; font-size: 15px; font-weight: bold; border-radius: 8px;")
        sidebar.addWidget(self.gas_sys_btn)

        self.sonar_cam_label = QLabel()
        self.sonar_cam_label.setStyleSheet("background-color: #111; color: #555; font-size: 14px; border: 2px solid #ddd; border-radius: 8px;")
        self.sonar_cam_label.setMinimumSize(1, 1) # Crucial for fitting
        self.sonar_cam_label.setScaledContents(True)
        self.sonar_cam_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.sonar_cam_label.setAlignment(Qt.AlignCenter)
        sidebar.addWidget(self.sonar_cam_label, 1)

        content_layout.addWidget(right_side_widget, 28)
        self._layout.addWidget(content_frame, 1)

    # ── Signal Handlers ───────────────────────────────────────────────────────
    def update_feeds(self, main_pixmap, sonar_pixmap):
        if not sonar_pixmap.isNull():
            self.sonar_cam_label.setPixmap(sonar_pixmap)

    def update_gas_data(self, data: dict):
        for gas, ppm in data.items():
            if gas in self.gauges:
                self.gauges[gas].set_value(ppm)

    def update_status_bars(self, vals_dict: dict):
        """Updating levers logic identical to main screen"""
        for name, val in vals_dict.items():
            if name in self.levers_dict:
                self.levers_dict[name].set_value(val)