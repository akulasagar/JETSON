"""Pipe cleaning / gas detection screen.

Shows gas gauge widgets, lever status, camera feed, and shares
operation controls with the manhole screen.

This version imports PLCLever and GasGaugeWidget from the shared
gui.widgets package, eliminating the previous duplication.
"""
import logging

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGridLayout, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSignal

from gui.widgets.plc_lever import PLCLever
from gui.widgets.gas_gauge import GasGaugeWidget

logger = logging.getLogger(__name__)


class PipeCleaningWidget(QWidget):
    """Gas detection system screen with gauges, levers, and camera feed."""

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
        self.g_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        g_grid = QGridLayout(self.g_card)
        g_grid.setContentsMargins(15, 10, 15, 10)
        g_grid.setSpacing(5)

        # Gas thresholds based on reference image
        self.gauges = {
            'CO':  GasGaugeWidget('CO',  'Carbon Monoxide (CO)', warning_val=35, danger_val=200, max_val=401),
            'CH4': GasGaugeWidget('CH4', 'Methane (CH4)', warning_val=1000, danger_val=10000, max_val=12501),
            'CO2': GasGaugeWidget('CO2', 'Carbon Dioxide (CO2)', warning_val=1000, danger_val=5000, max_val=10001),
            'H2S': GasGaugeWidget('H2S', 'Hydrogen Sulfide (H2S)', warning_val=10, danger_val=100, max_val=201),
            'NH3': GasGaugeWidget('NH3', 'Ammonia (NH3)', warning_val=25, danger_val=50, max_val=101)
        }
        
        # 2-1-2 Layout
        g_grid.addWidget(self.gauges['CO'],  0, 0)
        g_grid.addWidget(self.gauges['CH4'], 0, 2)
        g_grid.addWidget(self.gauges['CO2'], 1, 1)
        g_grid.addWidget(self.gauges['H2S'], 2, 0)
        g_grid.addWidget(self.gauges['NH3'], 2, 2)
        
        left_side.addWidget(self.g_card, 1)

        # Levers Row
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

        # ── RIGHT SIDEBAR (28% Stretch) ──
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

        self.manhole_btn = QPushButton("Manhole Cleaning")
        self.manhole_btn.setFixedHeight(50)
        self.manhole_btn.setStyleSheet("background-color: #f2f4f6; color: #1a1a1a; font-size: 15px; font-weight: bold; border-radius: 8px; border: 1px solid #ddd;")
        self.manhole_btn.clicked.connect(self.switch_to_manhole.emit)
        sidebar.addWidget(self.manhole_btn)

        self.gas_sys_btn = QPushButton("Gases Detection System")
        self.gas_sys_btn.setFixedHeight(50)
        self.gas_sys_btn.setStyleSheet("background-color: #219EBC; color: white; font-size: 15px; font-weight: bold; border-radius: 8px;")
        sidebar.addWidget(self.gas_sys_btn)

        self.sonar_cam_label = QLabel()
        self.sonar_cam_label.setStyleSheet("background-color: #111; color: #555; font-size: 14px; border: 2px solid #ddd; border-radius: 8px;")
        self.sonar_cam_label.setMinimumSize(1, 1)
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
