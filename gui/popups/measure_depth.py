"""Depth measurement popup dialog.

Provides the UI for measuring manhole depth before and after cleaning,
using the RealisticManholeWidget for visualization and communicating
with the depth sensor thread.

Refactored to import from new module paths.
"""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QFrame, QSizePolicy, QGridLayout, QMessageBox
)
from PyQt5.QtCore import pyqtSignal, QTimer, Qt, QRect
from PyQt5.QtGui import QColor, QFont
import logging
import time
import os
import sys

from gui.widgets.realistic_manhole import RealisticManholeWidget
from core import voice_module

logger = logging.getLogger(__name__)

class MeasureDepthPopup(QDialog):
    """Modern Depth Measurement Dialog according to UI design"""
    
    measurement_complete = pyqtSignal(str, float)  # mode, depth
    depth_updated = pyqtSignal(int)
    auto_stop_triggered = pyqtSignal()
    
    def __init__(self, parent=None, before_depth=None, after_depth=None, depth_thread=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(1100, 780)
        
        # State
        self.before_depth = before_depth
        self.after_depth = after_depth
        self.current_measurement = 0.0
        self.peak_depth = 0.0
        
        self.depth_thread = depth_thread
        self.is_measuring = False
        self.measure_mode = None # 'before' or 'after'
        
        # Initialize UI FIRST so labels exist before signals update them
        self._init_ui()
        self.update_ui_state()

        # Connect to shared depth thread if available
        if self.depth_thread:
            self.depth_thread.depth_updated.connect(self.update_depth_data)
            self.depth_thread.auto_stop_triggered.connect(self.stop_measuring_auto)
            self.depth_thread.connection_status.connect(self._handle_connection_status)
            self.depth_thread.port_discovered.connect(self._handle_port_discovery)
            
            # Set initial status
            if self.depth_thread.ser and self.depth_thread.ser.is_open:
                self._handle_connection_status(True)
                self._handle_port_discovery(self.depth_thread.active_port_name)
            else:
                self._handle_connection_status(False)

    def _init_ui(self):
        # Outer container with rounded corners
        self.container = QFrame(self)
        self.container.setObjectName("MainContainer")
        self.container.setStyleSheet("""
            #MainContainer {
                background-color: #F8F9FA;
                border_radius: 15px;
                border: 1px solid #CCC;
            }
        """)
        
        main_v = QVBoxLayout(self)
        main_v.setContentsMargins(0,0,0,0)
        main_v.addWidget(self.container)
        
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # --- Header ---
        header = QFrame()
        header.setFixedHeight(60)
        header.setStyleSheet("background-color: #1A92A4; border-top-left-radius: 14px; border-top-right-radius: 14px;")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(25, 0, 25, 0)
        
        title = QLabel("Measure Depth")
        title.setStyleSheet("color: white; font-size: 20px; font-weight: bold; border: none;")
        
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(30, 30)
        close_btn.setStyleSheet("""
            QPushButton { 
                background-color: white; color: #1A92A4; 
                border-radius: 15px; font-weight: bold; font-size: 16px; border: none;
            }
            QPushButton:hover { background-color: #E0F2F1; }
        """)
        close_btn.clicked.connect(self.reject)
        
        h_layout.addWidget(title)
        h_layout.addStretch()
        h_layout.addWidget(close_btn)
        layout.addWidget(header)
        
        # --- Body ---
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(30, 30, 30, 30)
        body_layout.setSpacing(25)
        
        # --- Left Column: Live Depth ---
        left_col = QFrame()
        left_col.setFixedWidth(400)
        left_col.setStyleSheet("background-color: white; border-radius: 12px; border: 1px solid #E0E0E0;")
        left_v = QVBoxLayout(left_col)
        left_v.setContentsMargins(15, 15, 15, 15)
        
        l_header = QHBoxLayout()
        l_icon = QLabel("✅")
        l_title = QLabel("Manhole Live Depth")
        l_title.setStyleSheet("font-weight: bold; font-size: 16px; border: none;")
        l_header.addWidget(l_icon)
        l_header.addWidget(l_title)
        l_header.addStretch()
        left_v.addLayout(l_header)
        
        self.manhole_widget = RealisticManholeWidget()
        left_v.addWidget(self.manhole_widget, 1)
        
        l_footer = QHBoxLayout()
        self.status_lbl = QLabel("Device: Connecting...")
        self.ready_lbl = QLabel("Device: Ready")
        style = "color: #555; font-size: 13px; border: none;"
        self.status_lbl.setStyleSheet(style)
        self.ready_lbl.setStyleSheet(style)
        l_footer.addWidget(self.status_lbl)
        l_footer.addStretch()
        l_footer.addWidget(self.ready_lbl)
        left_v.addLayout(l_footer)
        
        body_layout.addWidget(left_col)
        
        # --- Right Column: Controls ---
        right_v = QVBoxLayout()
        right_v.setSpacing(20)
        
        # Control Card 1: Depth Measurement Control
        ctrl_card = QFrame()
        ctrl_card.setStyleSheet("background-color: white; border-radius: 12px; border: 1px solid #E0E0E0;")
        ctrl_v = QVBoxLayout(ctrl_card)
        ctrl_v.setContentsMargins(20, 15, 20, 15)
        
        c_header = QHBoxLayout()
        c_icon = QLabel("📊")
        c_title = QLabel("Depth Measurement Control")
        c_title.setStyleSheet("font-weight: bold; font-size: 16px; border: none;")
        c_header.addWidget(c_icon)
        c_header.addWidget(c_title)
        c_header.addStretch()
        ctrl_v.addLayout(c_header)
        
        # Before / After row
        ba_row = QHBoxLayout()
        ba_row.setSpacing(15)
        
        # Before / After Boxes
        self.before_box = self._create_measurement_box("Before Cleaning", "before")
        self.after_box = self._create_measurement_box("After Cleaning", "after")
        ba_row.addWidget(self.before_box)
        ba_row.addWidget(self.after_box)
        ctrl_v.addLayout(ba_row)
        
        right_v.addWidget(ctrl_card)
        
        # Card 2: Manual Control
        man_card = QFrame()
        man_card.setFixedHeight(100)
        man_card.setStyleSheet("background-color: white; border-radius: 12px; border: 1px solid #E0E0E0;")
        man_h = QHBoxLayout(man_card)
        man_h.setContentsMargins(20, 15, 20, 15)
        
        m_icon = QLabel("🏗")
        m_title = QLabel("Manual Probe Control")
        m_title.setStyleSheet("font-weight: bold; font-size: 15px; border: none;")
        man_h.addWidget(m_icon)
        man_h.addWidget(m_title)
        man_h.addStretch()
        
        self.up_btn = QPushButton("↑ Up")
        self.down_btn = QPushButton("↓ Down")
        for b in [self.up_btn, self.down_btn]:
            b.setFixedSize(100, 42)
            b.setStyleSheet("background: #F8F9FA; border: 1px solid #CCC; border-radius: 6px; font-weight: bold;")
        man_h.addWidget(self.up_btn)
        man_h.addWidget(self.down_btn)
        self.up_btn.clicked.connect(lambda: self.send_serial_command("b"))
        self.down_btn.clicked.connect(lambda: self.send_serial_command("f"))
        
        right_v.addWidget(man_card)
        
        # Card 3: Results
        res_card = QFrame()
        res_card.setStyleSheet("background-color: white; border-radius: 12px; border: 1px solid #E0E0E0;")
        res_v = QVBoxLayout(res_card)
        res_v.setContentsMargins(20, 15, 20, 15)
        
        r_header = QHBoxLayout()
        r_icon = QLabel("✅")
        r_title = QLabel("Measurement Results")
        r_title.setStyleSheet("font-weight: bold; font-size: 15px; border: none;")
        r_header.addWidget(r_icon)
        r_header.addWidget(r_title)
        r_header.addStretch()
        res_v.addLayout(r_header)
        
        self.results_instruct = QLabel("Take \"Before Cleaning\" measurement to begin")
        self.results_instruct.setStyleSheet("background: #F8F9FA; border-radius: 8px; border: 1px solid #EEE; padding: 25px; font-size: 14px;")
        self.results_instruct.setAlignment(Qt.AlignCenter)
        res_v.addWidget(self.results_instruct)
        
        right_v.addWidget(res_card, 1)
        
        # Footer buttons
        f_btns = QHBoxLayout()
        f_btns.setSpacing(15)
        
        self.reset_btn = QPushButton("Reset All")
        self.reset_btn.setFixedSize(160, 46)
        self.reset_btn.setStyleSheet("background: #FF1744; color: white; border-radius: 8px; font-weight: bold; border: none;")
        self.reset_btn.clicked.connect(self.reset_all)
        
        self.save_btn = QPushButton("Save & Close")
        self.save_btn.setFixedSize(160, 46)
        self.save_btn.setStyleSheet("background: #007BFF; color: white; border-radius: 8px; font-weight: bold; border: none;")
        self.save_btn.clicked.connect(self.save_and_close)
        
        f_btns.addWidget(self.reset_btn)
        f_btns.addStretch()
        f_btns.addWidget(self.save_btn)
        right_v.addLayout(f_btns)
        
        body_layout.addLayout(right_v, 1)
        layout.addWidget(body)

    def update_ui_state(self):
        if self.before_depth is not None:
             self.before_depth_lbl.setText(f"Depth : <span style='font-weight:bold'>{self.before_depth:.1f} CM</span>")
             self.manhole_widget.before_depth = self.before_depth
        if self.after_depth is not None:
             self.after_depth_lbl.setText(f"Depth : <span style='font-weight:bold'>{self.after_depth:.1f} CM</span>")
             self.manhole_widget.after_depth = self.after_depth
        
        if self.before_depth is not None and self.after_depth is not None:
             reduction = self.before_depth - self.after_depth
             self.results_instruct.setText(f"Process Complete<br><b>Reduction: {reduction:.1f} CM</b>")
        elif self.before_depth is not None:
             self.results_instruct.setText(f"Before depth captured: <b>{self.before_depth:.1f} CM</b><br>Continue to After Cleaning.")
        
        self.manhole_widget.update()

    def _create_measurement_box(self, title, mode):
        box = QFrame()
        box.setStyleSheet("border: 1px solid #EEE; border-radius: 8px; background: #FFF;")
        v = QVBoxLayout(box)
        v.setContentsMargins(15, 15, 15, 15)
        
        header = QHBoxLayout()
        icon = QLabel("✅" if mode == 'after' else "✔")
        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight: bold; font-size: 14px; border: none;")
        header.addWidget(icon)
        header.addWidget(lbl)
        header.addStretch()
        v.addLayout(header)
        
        d_lbl = QLabel(f"Depth : <span style='font-weight:bold'>0 CM</span>")
        d_lbl.setStyleSheet("border: none; font-size: 13px; margin: 10px 0;")
        v.addWidget(d_lbl)
        
        btns = QHBoxLayout()
        start = QPushButton("▶ Start")
        stop = QPushButton("⏸ Stop")
        start.setFixedHeight(40)
        stop.setFixedHeight(40)
        start.setStyleSheet("background: #E8F5E9; color: #2E7D32; border: 1px solid #A5D6A7; border-radius: 4px; font-weight: bold;")
        stop.setStyleSheet("background: #FFEBEE; color: #C62828; border: 1px solid #EF9A9A; border-radius: 4px; font-weight: bold;")
        btns.addWidget(start)
        btns.addWidget(stop)
        v.addLayout(btns)
        
        # Store references
        if mode == 'before':
            self.before_depth_lbl = d_lbl
            self.before_start_btn = start
        else:
            self.after_depth_lbl = d_lbl
            self.after_start_btn = start
            
        start.clicked.connect(lambda: self.start_measuring(mode))
        stop.clicked.connect(self.stop_measuring)
        
        return box

    # --- Communication ---
    def _handle_connection_status(self, connected):
        if connected:
            self.status_lbl.setText(f"Device: Connected")
            self.status_lbl.setStyleSheet("color: #2E7D32; font-weight: bold;")
        else:
            self.status_lbl.setText("Device: Disconnected")
            self.status_lbl.setStyleSheet("color: #D32F2F; font-weight: bold;")

    def _handle_port_discovery(self, port_name):
        if port_name and port_name != "N/A":
            self.status_lbl.setText(f"Device: {port_name}")
            self.status_lbl.setStyleSheet("color: #2E7D32; font-weight: bold;")
        else:
            self.status_lbl.setText("Device: Not Found")
            self.status_lbl.setStyleSheet("color: #D32F2F; font-weight: bold;")

    def send_serial_command(self, cmd):
        if self.depth_thread:
            self.depth_thread.send_command(cmd)

    # --- Logic ---
    def start_measuring(self, mode):
        if self.is_measuring: return
        self.measure_mode = mode
        self.is_measuring = True
        self.peak_depth = 0.0
        
        self.results_instruct.setText(f"Measuring {mode} cleaning depth...")
        voice_module.speak_dual("Measuring Depth", "లోతు కొలవడం")
        self.send_serial_command("start") # Triggers motor sequence

    def update_depth_data(self, depth):
        # Safety check: ensure widget still exists
        try:
            if not self.manhole_widget: return
        except RuntimeError:
            return

        self.current_measurement = depth
        logger.debug(f"Depth updated from serial: {depth}")
        self.manhole_widget.probe_y = 120 + (min(depth, 183.0) / 183.0) * 450
        self.manhole_widget.update()
        
        if self.is_measuring:
            if depth > self.peak_depth:
                self.peak_depth = float(depth)
            
            if self.measure_mode == 'before' and hasattr(self, 'before_depth_lbl'):
                self.before_depth_lbl.setText(f"Depth : <span style='font-weight:bold'>{self.peak_depth:.1f} CM</span>")
            elif self.measure_mode == 'after' and hasattr(self, 'after_depth_lbl'):
                self.after_depth_lbl.setText(f"Depth : <span style='font-weight:bold'>{self.peak_depth:.1f} CM</span>")

    def stop_measuring_auto(self):
        if self.is_measuring:
            logger.info("Load cell triggered – saving peak depth automatically")
            self.stop_measuring(send_cmd=False)

    def stop_measuring(self, send_cmd=True):
        if not self.is_measuring: return
        self.is_measuring = False
        
        if send_cmd:
            self.send_serial_command("s")
        
        if self.measure_mode == 'before':
            self.before_depth = self.peak_depth
            self.manhole_widget.before_depth = self.before_depth
            self.before_depth_lbl.setText(f"Depth : <span style='font-weight:bold'>{self.before_depth:.1f} CM</span>")
            self.results_instruct.setText(f"Before depth captured: <b>{self.before_depth:.1f} CM</b><br>Proceed with cleaning.")
        else:
            self.after_depth = self.peak_depth
            self.manhole_widget.after_depth = self.after_depth
            self.after_depth_lbl.setText(f"Depth : <span style='font-weight:bold'>{self.after_depth:.1f} CM</span>")
            diff = (self.before_depth or 0.0) - self.after_depth
            self.results_instruct.setText(f"Process Complete<br><b>Reduction: {diff:.1f} CM</b>")
            
        self.manhole_widget.update()
        
    def reset_all(self):
        self.before_depth = None
        self.after_depth = None
        self.peak_depth = 0
        
        if hasattr(self, 'before_depth_lbl') and self.before_depth_lbl:
            try:
                self.before_depth_lbl.setText("Depth : <span style='font-weight:bold'>0 CM</span>")
            except RuntimeError: pass
            
        if hasattr(self, 'after_depth_lbl') and self.after_depth_lbl:
            try:
                self.after_depth_lbl.setText("Depth : <span style='font-weight:bold'>0 CM</span>")
            except RuntimeError: pass
            
        self.results_instruct.setText("Take \"Before Cleaning\" measurement to begin")
        
        if hasattr(self, 'manhole_widget') and self.manhole_widget:
            try:
                self.manhole_widget.before_depth = None
                self.manhole_widget.after_depth = None
                self.manhole_widget.probe_y = 120
                self.manhole_widget.update()
            except RuntimeError: pass

    def _cleanup(self):
        """Disconnect signals to avoid ghost updates when closed"""
        if self.depth_thread:
            try:
                self.depth_thread.depth_updated.disconnect(self.update_depth_data)
                self.depth_thread.auto_stop_triggered.disconnect(self.stop_measuring_auto)
                self.depth_thread.connection_status.disconnect(self._handle_connection_status)
            except Exception:
                pass

    def save_and_close(self):
        if self.is_measuring:
            logger.info("Save & Close clicked while measuring. Auto-saving peak depth.")
            self.stop_measuring(send_cmd=False)
        self._cleanup()
        self.accept()

    def reject(self):
        self._cleanup()
        super().reject()
