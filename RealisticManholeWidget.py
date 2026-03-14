from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QGroupBox, QCheckBox, QSlider, QSizePolicy
)

from PyQt5.QtCore import pyqtSignal, QTimer, Qt, QRectF

from PyQt5.QtGui import (
    QPainter, QPen, QBrush, QColor, QLinearGradient
)

import logging
import time
import numpy as np

class RealisticManholeWidget(QWidget):
    """Realistic manhole visualization with probe"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(400, 600)
        
        # Measurement variables
        self.current_depth = 0  # in cm
        self.max_depth = 183    # 6 feet in cm
        self.before_depth = None
        self.after_depth = None
        self.measurement_mode = None  # 'before' or 'after'
        
        # Probe animation
        self.probe_y = 100
        self.probe_height = 20
        self.is_measuring = False
        self.returning = False
        self.should_stop = False  # New flag to stop animation
        
        # Return target - stores where to return to
        self.return_target_depth = 0
        
        # Colors
        self.manhole_color = QColor(70, 70, 70)  # Dark gray
        self.rim_color = QColor(50, 50, 50)      # Darker gray
        self.silt_color = QColor(139, 69, 19)    # Brown for silt
        self.water_color = QColor(30, 144, 255)  # Blue for water
        self.probe_color = QColor(255, 215, 0)   # Gold probe
        self.probe_glow = QColor(255, 255, 200, 100)  # Glow effect
        
        # Timer for probe animation
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.animate_probe)
        
        # Timer for return animation
        self.return_timer = QTimer(self)
        self.return_timer.timeout.connect(self.animate_return)
        
        # Labels for depth display
        self.depth_label = QLabel("Depth: 0 cm", self)
        self.depth_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 18px;
                font-weight: bold;
                background-color: rgba(0, 0, 0, 150);
                padding: 10px;
                border-radius: 5px;
            }
        """)
        self.depth_label.setGeometry(10, 10, 200, 40)
        
        self.status_label = QLabel("Ready", self)
        self.status_label.setStyleSheet("""
            QLabel {
                color: #00FF00;
                font-size: 14px;
                font-weight: bold;
                background-color: rgba(0, 0, 0, 150);
                padding: 5px;
                border-radius: 3px;
            }
        """)
        self.status_label.setGeometry(10, 550, 380, 30)
        
    def start_measurement(self, mode):
        """Start measurement process"""
        self.measurement_mode = mode
        self.is_measuring = True
        self.returning = False
        self.should_stop = False  # Reset stop flag
        self.current_depth = 0
        self.probe_y = 100
        
        # Set return target to where we start from
        self.return_target_depth = self.current_depth
        
        if mode == 'before':
            self.status_label.setText("Measuring Before Cleaning...")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #FFA500;
                    font-size: 14px;
                    font-weight: bold;
                    background-color: rgba(0, 0, 0, 150);
                    padding: 5px;
                    border-radius: 3px;
                }
            """)
        else:
            self.status_label.setText("Measuring After Cleaning...")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #00FF00;
                    font-size: 14px;
                    font-weight: bold;
                    background-color: rgba(0, 0, 0, 150);
                    padding: 5px;
                    border-radius: 3px;
                }
            """)
        
        self.animation_timer.start(50)  # Update every 50ms
        
    def stop_measurement(self, depth=None):
        """Stop measurement and save result"""
        self.should_stop = True  # Set stop flag
        
        # Stop timers immediately
        self.animation_timer.stop()
        self.return_timer.stop()
        
        self.is_measuring = False
        self.returning = False
        
        # If depth is provided, save it
        if depth is not None:
            self.current_depth = depth
            self.probe_y = 100 + (depth / self.max_depth) * 400
        
        if self.measurement_mode == 'before':
            self.before_depth = self.current_depth if depth is None else depth
            self.status_label.setText(f"Before: {self.current_depth} cm (STOPPED)")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #f44336;
                    font-size: 14px;
                    font-weight: bold;
                    background-color: rgba(0, 0, 0, 150);
                    padding: 5px;
                    border-radius: 3px;
                }
            """)
        elif self.measurement_mode == 'after':
            self.after_depth = self.current_depth if depth is None else depth
            self.status_label.setText(f"After: {self.current_depth} cm (STOPPED)")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #f44336;
                    font-size: 14px;
                    font-weight: bold;
                    background-color: rgba(0, 0, 0, 150);
                    padding: 5px;
                    border-radius: 3px;
                }
            """)
        else:
            self.status_label.setText(f"Stopped at: {self.current_depth} cm")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #f44336;
                    font-size: 14px;
                    font-weight: bold;
                    background-color: rgba(0, 0, 0, 150);
                    padding: 5px;
                    border-radius: 3px;
                }
            """)
        
        self.update()
        
    def return_to_start(self, target_depth=0):
        """Return the probe to starting position"""
        if self.is_measuring:
            logger.warning("[MANHOLE-WIDGET] Cannot return while measuring")
            return False  # Can't return while measuring
        
        if self.current_depth <= target_depth:
            logger.info(f"[MANHOLE-WIDGET] Already at or below target depth: {self.current_depth} <= {target_depth}")
            return False  # Already at start
        
        # Set return target
        self.return_target_depth = target_depth
        logger.info(f"[MANHOLE-WIDGET] Starting return from {self.current_depth} cm to {target_depth} cm")
        
        # Animate probe returning to target
        self.returning = True
        self.is_measuring = False
        self.should_stop = False  # Reset stop flag
        self.status_label.setText(f"Returning to {target_depth} cm...")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #4285F4;
                font-size: 14px;
                font-weight: bold;
                background-color: rgba(0, 0, 0, 150);
                padding: 5px;
                border-radius: 3px;
            }
        """)
        self.return_timer.start(30)  # Faster return
        return True
    
    def stop_return(self):
        """Stop the return operation"""
        self.should_stop = True
        self.return_timer.stop()
        self.returning = False
        self.status_label.setText(f"Return stopped at: {self.current_depth} cm")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #f44336;
                font-size: 14px;
                font-weight: bold;
                background-color: rgba(0, 0, 0, 150);
                padding: 5px;
                border-radius: 3px;
            }
        """)
        self.update()
        
    def animate_return(self):
        """Animate probe returning to target position"""
        if self.should_stop:
            self.return_timer.stop()
            self.returning = False
            self.status_label.setText(f"Return stopped at: {self.current_depth} cm")
            self.update()
            return
            
        if self.current_depth > self.return_target_depth:
            self.current_depth -= 3  # 3 cm per update (faster than descent)
            self.probe_y = 100 + (self.current_depth / self.max_depth) * 400
            
            remaining = self.current_depth - self.return_target_depth
            self.depth_label.setText(f"Returning: {remaining} cm to go")
            self.update()
        else:
            self.return_timer.stop()
            self.returning = False
            self.current_depth = self.return_target_depth
            self.probe_y = 100 + (self.current_depth / self.max_depth) * 400
            self.depth_label.setText(f"Depth: {self.current_depth} cm")
            self.status_label.setText(f"Ready - Probe at {self.current_depth} cm")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #00FF00;
                    font-size: 14px;
                    font-weight: bold;
                    background-color: rgba(0, 0, 0, 150);
                    padding: 5px;
                    border-radius: 3px;
                }
            """)
            self.update()
            
    def animate_probe(self):
        """Animate probe descending"""        
        if self.should_stop:
            self.animation_timer.stop()
            self.is_measuring = False
            self.status_label.setText(f"Measurement stopped at: {self.current_depth} cm")
            self.update()
            return
            
        if self.is_measuring and self.current_depth < self.max_depth:
            self.current_depth += 2  # 2 cm per update
            self.probe_y = 100 + (self.current_depth / self.max_depth) * 400
            
            # Simulate finding silt (randomly stop between 50-180 cm)
            if self.current_depth >= 50 and self.current_depth < 180:
                if np.random.random() < 0.05:  # 5% chance to find silt at each step
                    self.stop_measurement(self.current_depth)
                    return
            
            # Auto-stop at max depth
            if self.current_depth >= self.max_depth:
                self.stop_measurement(self.current_depth)
            
            self.depth_label.setText(f"Depth: {self.current_depth} cm")
            self.update()
            
    def paintEvent(self, event):
        """Paint clean, modern manhole visualization"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Background
        painter.fillRect(self.rect(), QColor("#121212"))
        
        # Draw manhole shaft
        shaft_width = 240
        shaft_x = (self.width() - shaft_width) / 2
        shaft_height = 450
        shaft_y = 120
        
        # Shaft background
        painter.setBrush(QColor("#2A2A2A"))
        painter.setPen(Qt.NoPen)
        painter.drawRect(int(shaft_x), shaft_y, shaft_width, shaft_height)
        
        # Draw measurement lines and labels (left side)
        painter.setPen(QPen(QColor("#555555"), 1))
        for i in range(5):
            y = shaft_y + (i * (shaft_height / 4))
            painter.drawLine(int(shaft_x - 10), int(y), int(shaft_x + shaft_width), int(y))
            
            # Label
            cm = int((i / 4) * self.max_depth)
            painter.setPen(QPen(QColor("#AAAAAA"), 1))
            painter.drawText(int(shaft_x - 60), int(y + 5), f"{cm}cm")
            painter.setPen(QPen(QColor("#555555"), 1))

        # Top rim (ellipse for perspective)
        painter.setBrush(QColor("#333333"))
        painter.setPen(QPen(QColor("#444444"), 2))
        painter.drawEllipse(int(shaft_x - 10), shaft_y - 20, shaft_width + 20, 60)
        
        # Silt layer logic
        if self.before_depth is not None:
             # Silt
             s_h = (self.before_depth / self.max_depth) * shaft_height
             s_y = shaft_y + shaft_height - s_h
             painter.setBrush(QColor("#3E3E3E"))
             painter.drawRect(int(shaft_x), int(s_y), shaft_width, int(s_h))
             
             # Water (subtle)
             w_h = min(30.0, float(s_h)) # some water above silt
             w_y = s_y - w_h
             painter.setBrush(QColor(33, 158, 188, 100))
             painter.drawRect(int(shaft_x), int(w_y), shaft_width, int(w_h))

        # Probe line
        center_x = self.width() / 2
        painter.setPen(QPen(QColor("#666666"), 2, Qt.DashLine))
        painter.drawLine(int(center_x), shaft_y - 10, int(center_x), int(self.probe_y))

        # Probe body (Yellow capsule like the image)
        probe_w = 14
        probe_h = 24
        painter.setBrush(QColor("#FFD600")) # Bright Yellow
        painter.setPen(QPen(QColor("#000"), 1))
        painter.drawRoundedRect(int(center_x - probe_w/2), int(self.probe_y - probe_h/2), probe_w, probe_h, 7, 7)
        
        # Finish
        painter.end()
            
    def reset(self):
        """Reset measurements"""
        self.before_depth = None
        self.after_depth = None
        self.current_depth = 0
        self.return_target_depth = 0
        self.probe_y = 100
        self.is_measuring = False
        self.returning = False
        self.should_stop = False
        self.animation_timer.stop()
        self.return_timer.stop()
        self.depth_label.setText("Depth: 0 cm")
        self.status_label.setText("Ready")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #00FF00;
                font-size: 14px;
                font-weight: bold;
                background-color: rgba(0, 0, 0, 150);
                padding: 5px;
                border-radius: 3px;
            }
        """)
        self.update()