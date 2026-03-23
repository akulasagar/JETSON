"""PLC Lever visualization widget.

Renders a rounded vertical bar with gradient fill representing
an actuator/lever position value (0–100).

NOTE: This is the SINGLE canonical definition. Previously duplicated
in main_screen.py, pipe_cleaning.py, and load.py.
"""
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt, QRect, QRectF
from PyQt5.QtGui import (
    QPainter, QColor, QLinearGradient, QPainterPath, QPixmap
)


class PLCLever(QWidget):
    """Visual lever widget showing a 0–100 fill bar."""

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
