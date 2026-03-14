
#!/usr/bin/env python3
import sys
import os
import datetime
import time
import cv2
import numpy as np
import json
import csv
import serial
import logging
import pytz
import signal
import traceback
from logging.handlers import RotatingFileHandler
import math
import requests
import threading
import uuid
from io import BytesIO
from PIL import Image



from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QLabel, QPushButton, QFrame, QMessageBox,
                             QSizePolicy, QInputDialog, QComboBox, QDialog, QSpacerItem,
                             QLineEdit, QGroupBox, QGridLayout, QDoubleSpinBox, QRadioButton, 
                             QButtonGroup, QSlider, QScrollArea, QGraphicsView, QGraphicsScene,
                             QGraphicsPixmapItem, QSplitter, QGraphicsEllipseItem,
                             QGraphicsTextItem, QGraphicsOpacityEffect, QGraphicsRectItem,
                             QGraphicsPolygonItem,QCheckBox, QProgressBar)
from PyQt5.QtGui import (QPixmap, QImage, QPainter, QPen, QColor, QBrush,
                         QFont, QFontMetrics, QIcon, QMouseEvent, QCursor,
                         QRadialGradient, QLinearGradient, QPolygonF, QPainterPath)
from PyQt5.QtCore import (Qt, QTimer, QRectF, QPointF, QSize, QThread, pyqtSignal, QPoint,
                         QEvent, QObject, QPropertyAnimation, QRect)

# Import the load cell functionality (we'll embed it)
try:
    from PyQt5.QtWidgets import QProgressBar, QRadioButton
except ImportError:
    pass

# Global exception handler
def handle_exception(exc_type, exc_value, exc_traceback):
    """Global exception handler"""
    if issubclass(exc_type, KeyboardInterrupt):
        # Allow keyboard interrupts
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    
    logger = logging.getLogger(__name__)
    logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
    
    # Try to show error message if possible
    try:
        app = QApplication.instance()
        if app:
            QMessageBox.critical(None, "Unhandled Error", 
                                f"An unhandled error occurred:\n\n{exc_type.__name__}: {exc_value}\n\nCheck logs for details.")
    except:
        pass

# Install the exception handler
sys.excepthook = handle_exception

# Ensure data_uploader.py is in the same directory
try:
    from data_uploader import Uploader, UploadStatus
except ImportError:
    logging.error("[ERROR] data_uploader.py not found. Please ensure it's in the same directory.")
    logging.error("Cannot run without data_uploader.py. Exiting.")
    sys.exit(1)

# Create required directories (also ensure 'logs' for app.log)
os.makedirs("captures/before", exist_ok=True)
os.makedirs("captures/after", exist_ok=True)
os.makedirs("logs", exist_ok=True) # Ensure logs directory exists
os.makedirs("uploads", exist_ok=True)
os.makedirs("uploads/pending", exist_ok=True)

# --- Configure Logging ---
log_file = os.path.join("logs", "app.log")
log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Configure console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter(log_format))
console_handler.setLevel(logging.INFO)

# Configure file handler with rotation
file_handler = RotatingFileHandler(log_file, maxBytes=1*1024*1024, backupCount=5)
file_handler.setFormatter(logging.Formatter(log_format))
file_handler.setLevel(logging.DEBUG)

# Get the root logger and add handlers
logging.basicConfig(level=logging.DEBUG, handlers=[console_handler, file_handler])
logger = logging.getLogger(__name__)

# Load configuration
DEFAULT_CONFIG = {
    "device_id": "UNKNOWN",
    "area": "UNKNOWN",
    "division": "UNKNOWN",
    "district": "UNKNOWN",
    "azure_connection_string": "",
    "azure_container_name": "",
    "API_URL": "https://sewage-bot.onrender.com/api/upload",
    "mapbox_token": "pk.eyJ1Ijoic2h1YmhhbWd2IiwiYSI6ImNtZDV2cmJneDAydngyanFzaW1vNTM3M24ifQ.7Jb5OXpznWqjyMeAuiXhrQ"
}

CONFIG_FILE_PATH = "config.json"

if not os.path.exists(CONFIG_FILE_PATH):
    logger.warning(f"'{CONFIG_FILE_PATH}' not found. Creating a default configuration file.")
    try:
        with open(CONFIG_FILE_PATH, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        logger.info("Default 'config.json' file created successfully.")
        config = DEFAULT_CONFIG
    except IOError as e:
        logger.error(f"Error creating default '{CONFIG_FILE_PATH}': {e}")
        config = DEFAULT_CONFIG
else:
    try:
        with open(CONFIG_FILE_PATH, 'r') as f:
            config = json.load(f)
        for key, default_value in DEFAULT_CONFIG.items():
            if key not in config:
                config[key] = default_value
                logger.warning(f"Missing key '{key}' in '{CONFIG_FILE_PATH}'. Using default value: {default_value}")
    except json.JSONDecodeError:
        logger.error(f"Error decoding '{CONFIG_FILE_PATH}'. Using default configuration.")
        config = DEFAULT_CONFIG
    except IOError as e:
        logger.error(f"Error reading '{CONFIG_FILE_PATH}': {e}")
        config = DEFAULT_CONFIG
logger.info(f"Loaded configuration: {config}")

# Initialize log file
LOG_CSV = "logs/capture_log.csv"
if not os.path.exists(LOG_CSV):
    with open(LOG_CSV, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "filename", "image_type", "status", "device_id", "area", "division", "district", "latitude", "longitude", "operation_id"])

# --- Helper function for consistent operation IDs ---
def get_next_operation_id(device_id):
    """Generate consistent operation ID that won't conflict"""
    counter_file = "operation_counter.json"
    
    # Load or create counter
    if os.path.exists(counter_file):
        try:
            with open(counter_file, 'r') as f:
                counters = json.load(f)
        except:
            counters = {}
    else:
        counters = {}
    
    # Initialize device counter if not exists
    if device_id not in counters:
        counters[device_id] = 0
    
    # Increment counter
    counters[device_id] += 1
    
    # Save counter
    with open(counter_file, 'w') as f:
        json.dump(counters, f, indent=2)
    
    # Generate operation ID: device_id_counter
    operation_id = f"{device_id}_{counters[device_id]}"
    logger.info(f"[ID-GEN] Generated operation ID: {operation_id}")
    return operation_id

# --- Manhole CSV loader (global) ---
MANHOLE_FILE = "manhole.csv"
manholes = []

def load_manholes():
    global manholes
    manholes = []

    abs_path = os.path.abspath(MANHOLE_FILE)
    logger.info(f"[MANHOLE-LOAD] Looking for CSV at: {abs_path}")

    if not os.path.exists(MANHOLE_FILE):
        logger.error(f"[MANHOLE-LOAD] ❌ CSV NOT FOUND: {abs_path}")
        try:
            QMessageBox.warning(None, "Manhole CSV Missing",
                                f"Manhole file not found:\n{abs_path}")
        except Exception:
            pass
        return

    try:
        with open(MANHOLE_FILE, "r") as f:
            reader = csv.DictReader(f)
            row_count = 0
            logger.info(f"[MANHOLE-LOAD] CSV headers: {reader.fieldnames}")

            for row in reader:
                row_count += 1
                try:
                    # Try different possible column names for manhole ID
                    raw_id = None
                    possible_id_fields = ["manhole_id", "sw_mh_id", "id", "MH_ID", "Manhole_ID", "MANHOLE_ID", "manhole"]
                    
                    for field in possible_id_fields:
                        if field in row and row[field]:
                            raw_id = row[field].strip()
                            logger.debug(f"[MANHOLE-LOAD] Row {row_count}: Using field '{field}' = '{raw_id}'")
                            break
                    
                    if not raw_id:
                        # Try to find any field containing 'MH' or 'manhole' in the value
                        for key, value in row.items():
                            if value and isinstance(value, str) and ('MH' in value.upper() or 'manhole' in value.lower()):
                                raw_id = value.strip()
                                logger.debug(f"[MANHOLE-LOAD] Row {row_count}: Found ID in field '{key}' = '{raw_id}'")
                                break
                    
                    # Try latitude fields
                    lat = None
                    possible_lat_fields = ["lat", "latitude", "lat_dd", "LATITUDE", "Latitude", "LAT", "y"]
                    for field in possible_lat_fields:
                        if field in row and row[field]:
                            try:
                                lat = float(row[field].strip())
                                break
                            except ValueError:
                                continue
                    
                    # Try longitude fields
                    lon = None
                    possible_lon_fields = ["lon", "longitude", "lon_dd", "LONGITUDE", "Longitude", "LON", "x"]
                    for field in possible_lon_fields:
                        if field in row and row[field]:
                            try:
                                lon = float(row[field].strip())
                                break
                            except ValueError:
                                continue
                    
                    if not raw_id:
                        logger.warning(f"[MANHOLE-LOAD] Row {row_count}: No manhole ID found. Row data: {row}")
                        continue
                    
                    if not lat or not lon:
                        logger.warning(f"[MANHOLE-LOAD] Row {row_count}: Missing coordinates for ID '{raw_id}'")
                        continue
                    
                    manholes.append({
                        "id": raw_id,
                        "lat": lat,
                        "lon": lon
                    })
                    
                    logger.debug(f"[MANHOLE-LOAD] Added: ID='{raw_id}', Lat={lat}, Lon={lon}")
                    
                except Exception as e:
                    logger.error(f"[MANHOLE-LOAD] ❌ Row parse error at line {row_count}: {e}")
                    logger.error(f"[MANHOLE-LOAD] Row data: {row}")

        logger.info(f"[MANHOLE-LOAD] ✅ Successfully loaded {len(manholes)} manholes.")
        
        # Show sample of loaded manholes
        for i, mh in enumerate(manholes[:10]):
            logger.info(f"[MANHOLE-LOAD-SAMPLE] {i+1}. ID: {mh['id']}, Lat: {mh['lat']:.6f}, Lon: {mh['lon']:.6f}")
        
        if len(manholes) == 0:
            logger.warning("[MANHOLE-LOAD] No manholes loaded from CSV!")
            try:
                QMessageBox.warning(None, "No Manholes Loaded",
                                    "Manhole CSV was loaded but no valid entries found.")
            except Exception:
                pass

    except Exception as e:
        logger.error(f"[MANHOLE-LOAD] ❌ Failed reading CSV: {e}")
        try:
            QMessageBox.critical(None, "CSV Error",
                                 f"Error reading manhole CSV:\n{str(e)}")
        except Exception:
            pass

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
        """Paint realistic manhole visualization"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Draw background gradient
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor(20, 20, 30))
        gradient.setColorAt(1, QColor(10, 10, 20))
        painter.fillRect(self.rect(), gradient)
        
        # Draw manhole rim (top)
        painter.setBrush(self.rim_color)
        painter.setPen(QPen(QColor(30, 30, 30), 3))
        painter.drawEllipse(100, 80, 200, 40)
        
        # Draw manhole shaft
        painter.setBrush(self.manhole_color)
        painter.setPen(QPen(QColor(40, 40, 40), 2))
        shaft_rect = QRectF(120, 100, 160, 400)
        painter.drawRect(shaft_rect)
        
        # Draw inner shadow
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 80)))
        painter.drawRect(shaft_rect)
        
        # Draw silt layer if before measurement exists
        if self.before_depth is not None:
            silt_height = (self.before_depth / self.max_depth) * 400
            silt_y = 100 + 400 - silt_height
            
            # Draw water layer above silt (20cm = ~44 pixels)
            water_height = min(44, silt_height)  # Max 20cm water
            water_y = silt_y - water_height
            
            painter.setBrush(self.water_color)
            painter.setPen(QPen(QColor(20, 120, 220), 1))
            painter.drawRect(120, int(water_y), 160, int(water_height))
            
            # Draw silt
            painter.setBrush(self.silt_color)
            painter.setPen(QPen(QColor(110, 55, 15), 2))
            painter.drawRect(120, int(silt_y), 160, int(silt_height))
            
            # Draw silt texture
            painter.setPen(QPen(QColor(100, 50, 10), 1))
            for i in range(int(silt_y), int(silt_y + silt_height), 10):
                painter.drawLine(120, i, 280, i)
        
        # Draw probe
        if self.is_measuring or self.returning:
            # Draw probe glow - red if stopped
            if self.should_stop:
                glow_color = QColor(244, 67, 54, 100)  # Red for stopped
            elif self.returning:
                glow_color = QColor(66, 133, 244, 100)  # Blue for returning
            else:
                glow_color = QColor(255, 255, 200, 100)  # Yellow for measuring
                
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(glow_color))
            painter.drawEllipse(int(190), int(self.probe_y - 5), 20, 20)
        
        # Draw probe body - red if stopped
        if self.should_stop:
            probe_color = QColor(244, 67, 54)  # Red for stopped
        elif self.returning:
            probe_color = QColor(66, 133, 244)  # Blue for returning
        else:
            probe_color = self.probe_color  # Gold for measuring
            
        painter.setBrush(probe_color)
        painter.setPen(QPen(QColor(200, 170, 0), 2))
        
        # Probe body (rounded rectangle)
        probe_rect = QRectF(195, int(self.probe_y), 10, self.probe_height)
        painter.drawRoundedRect(probe_rect, 3, 3)
        
        # Probe tip
        painter.drawEllipse(195, int(self.probe_y + self.probe_height - 5), 10, 10)
        
        # Draw measurement lines
        painter.setPen(QPen(QColor(150, 150, 150, 100), 1))
        for i in range(0, 5):  # 5 depth markers
            y = 100 + (i * 100)
            painter.drawLine(120, int(y), 280, int(y))
            
            # Depth label
            depth_cm = int((i * 100 / 400) * self.max_depth)
            painter.setPen(QPen(QColor(200, 200, 200), 1))
            painter.drawText(85, int(y + 5), f"{depth_cm}cm")
            painter.setPen(QPen(QColor(150, 150, 150, 100), 1))
        
        # Draw manhole cover details
        painter.setPen(QPen(QColor(100, 100, 100), 2))
        painter.drawLine(150, 90, 250, 90)
        painter.drawLine(200, 70, 200, 110)
        
        # Draw small holes
        painter.setBrush(Qt.black)
        for x, y in [(170, 85), (230, 85), (185, 75), (215, 75)]:
            painter.drawEllipse(int(x), int(y), 4, 4)
        
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
        
        
class LoadCellDialog(QDialog):
    """Load Cell Measurement Dialog with Light Theme"""
    
    measurement_complete = pyqtSignal(str, int)  # mode, depth
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manhole Depth Measurement")
        self.setMinimumSize(1200, 850)
        self.setStyleSheet("""
            QDialog {
                background-color: #f5f5f5;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QLabel {
                color: #333333;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QPushButton {
                border-radius: 6px;
                font-weight: bold;
                font-size: 14px;
                padding: 12px;
                border: 1px solid #d4d4d4;
                min-height: 40px;
            }
            QGroupBox {
                border: 2px solid #e0e0e0;
                border-radius: 10px;
                margin-top: 10px;
                padding-top: 15px;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 10px 0 10px;
                color: #555555;
                font-weight: bold;
            }
            QCheckBox {
                color: #333333;
                font-size: 14px;
                font-weight: normal;
                padding: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        
        self.ser = None
        self.running = False
        self.simulation_mode = True  # Default to simulation mode
        
        self.before_depth = None
        self.after_depth = None
        self.current_measurement = None
        self.is_measuring = False
        self.is_returning = False
        self.action_in_progress = False
        
        # Track the starting position for return
        self.start_position_depth = 0  # Depth where measurement started (usually 0)
        self.target_return_depth = 0   # Depth to return to (the original start position)
        
        # Simulation variables
        self.simulation_timer = None
        self.simulation_depth = 0
        self.simulation_speed = 5  # cm per second
        self.simulation_direction = 1  # 1 = down, -1 = up
        
        self.init_ui()
        QTimer.singleShot(1000, self.connect_serial)
        
    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(20)
        
        # Left panel - Realistic manhole visualization (40%)
        left_panel = QWidget()
        left_panel.setMinimumWidth(450)
        left_panel.setMaximumWidth(600)
        left_panel.setStyleSheet("""
            QWidget {
                background-color: white;
                border-radius: 10px;
                border: 2px solid #e0e0e0;
            }
        """)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(15, 15, 15, 15)
        left_layout.setSpacing(10)
        
        # Visualization title
        viz_title = QLabel("MANHOLE CROSS-SECTION")
        viz_title.setStyleSheet("""
            QLabel {
                color: #555555;
                font-size: 18px;
                font-weight: bold;
                qproperty-alignment: 'AlignCenter';
                padding-bottom: 10px;
                border-bottom: 2px solid #9c27b0;
            }
        """)
        left_layout.addWidget(viz_title)
        
        # Realistic manhole widget
        self.manhole_widget = RealisticManholeWidget()
        self.manhole_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_layout.addWidget(self.manhole_widget, 1)
        
        # Connection status
        self.connection_status = QLabel("⚡ Device: Connecting...")
        self.connection_status.setStyleSheet("""
            QLabel {
                color: #f57c00;
                font-size: 14px;
                font-weight: bold;
                background-color: #fff3e0;
                padding: 10px;
                border-radius: 8px;
                border: 1px solid #ffcc80;
                qproperty-alignment: 'AlignCenter';
            }
        """)
        self.connection_status.setFixedHeight(50)
        left_layout.addWidget(self.connection_status)
        
        # Simulation controls
        sim_controls = QWidget()
        sim_controls.setFixedHeight(60)
        sim_layout = QHBoxLayout(sim_controls)
        sim_layout.setContentsMargins(0, 5, 0, 5)
        sim_layout.setSpacing(10)
        
        sim_label = QLabel("Simulation Speed:")
        sim_label.setStyleSheet("color: #666666; font-weight: bold;")
        sim_layout.addWidget(sim_label)
        
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(1, 10)
        self.speed_slider.setValue(5)
        self.speed_slider.setTickPosition(QSlider.TicksBelow)
        self.speed_slider.setTickInterval(1)
        self.speed_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 8px;
                background: #e0e0e0;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #9c27b0;
                width: 20px;
                height: 20px;
                margin: -6px 0;
                border-radius: 10px;
            }
        """)
        self.speed_slider.valueChanged.connect(self.update_simulation_speed)
        sim_layout.addWidget(self.speed_slider, 1)
        
        self.speed_label = QLabel("5 cm/s")
        self.speed_label.setStyleSheet("color: #9c27b0; font-weight: bold; min-width: 70px;")
        sim_layout.addWidget(self.speed_label)
        
        left_layout.addWidget(sim_controls)
        
        main_layout.addWidget(left_panel, 4)
        
        # Right panel - Controls (60% - wider)
        right_panel = QWidget()
        right_panel.setMinimumWidth(600)
        right_panel.setStyleSheet("""
            QWidget {
                background-color: white;
                border-radius: 10px;
                border: 2px solid #e0e0e0;
            }
        """)
        
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(15, 15, 15, 15)
        right_layout.setSpacing(15)
        
        # Header for right panel
        controls_header = QLabel("📊 DEPTH MEASUREMENT CONTROL")
        controls_header.setStyleSheet("""
            QLabel {
                color: #9c27b0;
                font-size: 22px;
                font-weight: bold;
                padding: 15px;
                background-color: #f3e5f5;
                border-radius: 10px;
                border: 2px solid #ce93d8;
                qproperty-alignment: 'AlignCenter';
            }
        """)
        controls_header.setFixedHeight(70)
        right_layout.addWidget(controls_header)
        
        # Two column layout for measurements
        measurements_container = QWidget()
        measurements_container.setMaximumHeight(280)
        measurements_layout = QHBoxLayout(measurements_container)
        measurements_layout.setContentsMargins(0, 0, 0, 0)
        measurements_layout.setSpacing(15)
        
        # Before cleaning measurement
        before_group = QGroupBox("🔄 BEFORE CLEANING")
        before_group.setStyleSheet("""
            QGroupBox {
                background-color: #fff3e0;
                border: 2px solid #ffb74d;
                border-radius: 10px;
                min-height: 200px;
                max-height: 250px;
            }
            QGroupBox::title {
                color: #f57c00;
                font-size: 16px;
                font-weight: bold;
            }
        """)
        before_layout = QVBoxLayout(before_group)
        before_layout.setContentsMargins(15, 25, 15, 15)
        before_layout.setSpacing(10)
        
        # Depth display
        before_depth_container = QWidget()
        before_depth_container.setMaximumHeight(100)
        before_depth_layout = QVBoxLayout(before_depth_container)
        before_depth_layout.setContentsMargins(0, 0, 0, 0)
        
        before_depth_label = QLabel("Depth")
        before_depth_label.setStyleSheet("""
            QLabel {
                color: #666666;
                font-size: 13px;
                font-weight: bold;
                qproperty-alignment: 'AlignCenter';
            }
        """)
        before_depth_layout.addWidget(before_depth_label)
        
        self.before_depth_display = QLabel("-- cm")
        self.before_depth_display.setStyleSheet("""
            QLabel {
                color: #e65100;
                font-size: 32px;
                font-weight: bold;
                qproperty-alignment: 'AlignCenter';
                padding: 10px;
                background-color: #ffecb3;
                border-radius: 8px;
                border: 2px solid #ffb74d;
                min-height: 80px;
                max-height: 90px;
            }
        """)
        self.before_depth_display.setAlignment(Qt.AlignCenter)
        before_depth_layout.addWidget(self.before_depth_display)
        
        before_layout.addWidget(before_depth_container, 1)
        
        # Before measurement buttons
        before_buttons_container = QWidget()
        before_buttons_container.setMaximumHeight(70)
        before_buttons_layout = QHBoxLayout(before_buttons_container)
        before_buttons_layout.setContentsMargins(0, 0, 0, 0)
        before_buttons_layout.setSpacing(5)
        
        self.start_before_btn = QPushButton("▶ START")
        self.start_before_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff9800;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                min-height: 50px;
                border-radius: 6px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: #f57c00;
            }
            QPushButton:disabled {
                background-color: #bdbdbd;
                color: #757575;
            }
        """)
        self.start_before_btn.clicked.connect(lambda: self.start_measurement('before'))
        before_buttons_layout.addWidget(self.start_before_btn)
        
        self.stop_before_btn = QPushButton("⏹ STOP")
        self.stop_before_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                min-height: 50px;
                border-radius: 6px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
            QPushButton:disabled {
                background-color: #bdbdbd;
                color: #757575;
            }
        """)
        self.stop_before_btn.clicked.connect(lambda: self.stop_current_action('before'))
        self.stop_before_btn.setEnabled(False)
        before_buttons_layout.addWidget(self.stop_before_btn)
        
        self.return_before_btn = QPushButton("⬆ RETURN")
        self.return_before_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196f3;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                min-height: 50px;
                border-radius: 6px;
                padding: 5px 10px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #1976d2;
            }
            QPushButton:disabled {
                background-color: #bdbdbd;
                color: #757575;
            }
        """)
        self.return_before_btn.clicked.connect(lambda: self.start_return('before'))
        self.return_before_btn.setEnabled(False)
        before_buttons_layout.addWidget(self.return_before_btn)
        
        before_layout.addWidget(before_buttons_container)
        
        # After cleaning measurement
        after_group = QGroupBox("✅ AFTER CLEANING")
        after_group.setStyleSheet("""
            QGroupBox {
                background-color: #e8f5e9;
                border: 2px solid #81c784;
                border-radius: 10px;
                min-height: 200px;
                max-height: 250px;
            }
            QGroupBox::title {
                color: #388e3c;
                font-size: 16px;
                font-weight: bold;
            }
        """)
        after_layout = QVBoxLayout(after_group)
        after_layout.setContentsMargins(15, 25, 15, 15)
        after_layout.setSpacing(10)
        
        # Depth display
        after_depth_container = QWidget()
        after_depth_container.setMaximumHeight(100)
        after_depth_layout = QVBoxLayout(after_depth_container)
        after_depth_layout.setContentsMargins(0, 0, 0, 0)
        
        after_depth_label = QLabel("Depth")
        after_depth_label.setStyleSheet("""
            QLabel {
                color: #666666;
                font-size: 13px;
                font-weight: bold;
                qproperty-alignment: 'AlignCenter';
            }
        """)
        after_depth_layout.addWidget(after_depth_label)
        
        self.after_depth_display = QLabel("-- cm")
        self.after_depth_display.setStyleSheet("""
            QLabel {
                color: #1b5e20;
                font-size: 32px;
                font-weight: bold;
                qproperty-alignment: 'AlignCenter';
                padding: 10px;
                background-color: #c8e6c9;
                border-radius: 8px;
                border: 2px solid #81c784;
                min-height: 80px;
                max-height: 90px;
            }
        """)
        self.after_depth_display.setAlignment(Qt.AlignCenter)
        after_depth_layout.addWidget(self.after_depth_display)
        
        after_layout.addWidget(after_depth_container, 1)
        
        # After measurement buttons
        after_buttons_container = QWidget()
        after_buttons_container.setMaximumHeight(70)
        after_buttons_layout = QHBoxLayout(after_buttons_container)
        after_buttons_layout.setContentsMargins(0, 0, 0, 0)
        after_buttons_layout.setSpacing(5)
        
        self.start_after_btn = QPushButton("▶ START")
        self.start_after_btn.setStyleSheet("""
            QPushButton {
                background-color: #4caf50;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                min-height: 50px;
                border-radius: 6px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: #388e3c;
            }
            QPushButton:disabled {
                background-color: #bdbdbd;
                color: #757575;
            }
        """)
        self.start_after_btn.clicked.connect(lambda: self.start_measurement('after'))
        self.start_after_btn.setEnabled(False)
        after_buttons_layout.addWidget(self.start_after_btn)
        
        self.stop_after_btn = QPushButton("⏹ STOP")
        self.stop_after_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                min-height: 50px;
                border-radius: 6px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
            QPushButton:disabled {
                background-color: #bdbdbd;
                color: #757575;
            }
        """)
        self.stop_after_btn.clicked.connect(lambda: self.stop_current_action('after'))
        self.stop_after_btn.setEnabled(False)
        after_buttons_layout.addWidget(self.stop_after_btn)
        
        self.return_after_btn = QPushButton("⬆ RETURN")
        self.return_after_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196f3;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                min-height: 50px;
                border-radius: 6px;
                padding: 5px 10px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #1976d2;
            }
            QPushButton:disabled {
                background-color: #bdbdbd;
                color: #757575;
            }
        """)
        self.return_after_btn.clicked.connect(lambda: self.start_return('after'))
        self.return_after_btn.setEnabled(False)
        after_buttons_layout.addWidget(self.return_after_btn)
        
        after_layout.addWidget(after_buttons_container)
        
        measurements_layout.addWidget(before_group, 1)
        measurements_layout.addWidget(after_group, 1)
        
        right_layout.addWidget(measurements_container)
        
        # Return control section
        return_group = QGroupBox("🔄 PROBE CONTROL")
        return_group.setStyleSheet("""
            QGroupBox {
                background-color: #e3f2fd;
                border: 2px solid #90caf9;
                border-radius: 10px;
                min-height: 100px;
            }
            QGroupBox::title {
                color: #1976d2;
                font-size: 16px;
                font-weight: bold;
            }
        """)
        return_layout = QHBoxLayout(return_group)
        return_layout.setContentsMargins(15, 25, 15, 15)
        return_layout.setSpacing(15)
        
        # Manual return button
        self.manual_return_btn = QPushButton("⬆ RETURN PROBE")
        self.manual_return_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196f3;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                min-height: 50px;
                border-radius: 6px;
                padding: 5px 15px;
            }
            QPushButton:hover {
                background-color: #1976d2;
            }
            QPushButton:disabled {
                background-color: #bdbdbd;
                color: #757575;
            }
        """)
        self.manual_return_btn.clicked.connect(lambda: self.start_return('manual'))
        return_layout.addWidget(self.manual_return_btn)
        
        self.stop_return_btn = QPushButton("⏹ STOP RETURN")
        self.stop_return_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                min-height: 50px;
                border-radius: 6px;
                padding: 5px 15px;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
            QPushButton:disabled {
                background-color: #bdbdbd;
                color: #757575;
            }
        """)
        self.stop_return_btn.clicked.connect(lambda: self.stop_current_action('return'))
        self.stop_return_btn.setEnabled(False)
        return_layout.addWidget(self.stop_return_btn)
        
        # Auto-return checkbox
        auto_return_container = QWidget()
        auto_return_layout = QVBoxLayout(auto_return_container)
        auto_return_layout.setContentsMargins(0, 0, 0, 0)
        auto_return_layout.setSpacing(2)
        
        self.auto_return_cb = QCheckBox("Auto-return after measurement")
        self.auto_return_cb.setChecked(True)
        self.auto_return_cb.setStyleSheet("""
            QCheckBox {
                color: #333333;
                font-size: 14px;
                font-weight: bold;
                padding: 3px;
            }
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
            }
        """)
        
        auto_return_note = QLabel("(Auto returns after measurement completes)")
        auto_return_note.setStyleSheet("""
            QLabel {
                color: #666666;
                font-size: 12px;
                font-style: italic;
                padding-left: 3px;
            }
        """)
        
        auto_return_layout.addWidget(self.auto_return_cb)
        auto_return_layout.addWidget(auto_return_note)
        return_layout.addWidget(auto_return_container, 1)
        
        right_layout.addWidget(return_group)
        
        # Results section
        results_group = QGroupBox("📈 MEASUREMENT RESULTS")
        results_group.setStyleSheet("""
            QGroupBox {
                background-color: #f3e5f5;
                border: 2px solid #ab47bc;
                border-radius: 10px;
                min-height: 150px;
            }
            QGroupBox::title {
                color: #7b1fa2;
                font-size: 16px;
                font-weight: bold;
            }
        """)
        results_layout = QVBoxLayout(results_group)
        results_layout.setContentsMargins(15, 25, 15, 15)
        results_layout.setSpacing(10)
        
        self.results_text = QLabel("Take 'Before Cleaning' measurement to begin")
        self.results_text.setStyleSheet("""
            QLabel {
                color: #333333;
                font-size: 16px;
                font-weight: normal;
                qproperty-alignment: 'AlignCenter';
                padding: 15px;
                background-color: white;
                border-radius: 8px;
                border: 2px solid #ce93d8;
                line-height: 1.5;
            }
        """)
        self.results_text.setWordWrap(True)
        self.results_text.setAlignment(Qt.AlignCenter)
        self.results_text.setMinimumHeight(100)
        results_layout.addWidget(self.results_text)
        
        right_layout.addWidget(results_group)
        
        # Action buttons
        button_container = QWidget()
        button_container.setFixedHeight(70)
        button_layout = QHBoxLayout(button_container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(15)
        
        self.reset_btn = QPushButton("🔄 RESET ALL")
        self.reset_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                min-height: 50px;
                border-radius: 6px;
                padding: 5px 15px;
            }
            QPushButton:hover {
                background-color: #d32f2f;
            }
        """)
        self.reset_btn.clicked.connect(self.reset_all)
        button_layout.addWidget(self.reset_btn)
        
        self.save_btn = QPushButton("💾 SAVE & CLOSE")
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #4caf50;
                color: white;
                font-size: 14px;
                font-weight: bold;
                border: none;
                min-height: 50px;
                border-radius: 6px;
                padding: 5px 20px;
            }
            QPushButton:hover {
                background-color: #388e3c;
            }
        """)
        self.save_btn.clicked.connect(self.save_and_close)
        button_layout.addWidget(self.save_btn)
        
        right_layout.addWidget(button_container)
        
        main_layout.addWidget(right_panel, 6)
    
    def find_acm_ports(self):
        """Find available ACM ports (ACM0-9)"""
        acm_ports = []
        
        # Check for ACM0 through ACM9
        for i in range(10):
            port_path = f"/dev/ttyACM{i}"
            if os.path.exists(port_path):
                acm_ports.append(port_path)
                logger.info(f"[LOADCELL] Found ACM port: {port_path}")
        
        return acm_ports
    
    def connect_serial(self):
        """Connect to load cell device - falls back to simulation mode"""
        device_connected = False
        
        # Find available ACM ports
        acm_ports = self.find_acm_ports()
        
        if acm_ports:
            # Try each ACM port until we find one that works
            for port_path in acm_ports:
                try:
                    logger.info(f"[LOADCELL] Attempting to connect to {port_path}")
                    self.ser = serial.Serial(port_path, 57600, timeout=0.2)
                    time.sleep(2)
                    
                    if self.ser.is_open:
                        # Clear any existing data in buffer
                        self.ser.reset_input_buffer()
                        
                        # Try multiple different approaches to detect if it's a load cell
                        test_passed = False
                        
                        # Approach 1: Send simple "HELLO" or "VERSION" command
                        test_commands = ["HELLO", "VERSION", "INFO", "ID"]
                        for cmd in test_commands:
                            self.ser.write(f"{cmd}\n".encode())
                            time.sleep(0.5)
                            if self.ser.in_waiting:
                                response = self.ser.readline().decode(errors="ignore").strip()
                                logger.info(f"[LOADCELL] Response to {cmd}: {response}")
                                if response:
                                    test_passed = True
                                    break
                        
                        # Approach 2: Just listen for any data (some devices send data automatically)
                        if not test_passed:
                            logger.info(f"[LOADCELL] Listening for auto-data from {port_path}")
                            start_time = time.time()
                            while time.time() - start_time < 3.0:  # Listen for 3 seconds
                                if self.ser.in_waiting:
                                    response = self.ser.readline().decode(errors="ignore").strip()
                                    logger.info(f"[LOADCELL] Auto-data: {response}")
                                    if "Distance:" in response or "LOAD" in response or "WEIGHT" in response:
                                        test_passed = True
                                        break
                                time.sleep(0.1)
                        
                        # Approach 3: If port opens successfully, assume it's a load cell
                        # (Many load cell Arduino sketches just send data continuously)
                        if not test_passed:
                            logger.info(f"[LOADCELL] Port {port_path} opened successfully, assuming load cell")
                            test_passed = True
                        
                        if test_passed:
                            device_connected = True
                            self.running = True
                            self.simulation_mode = False
                            self.connection_status.setText(f"✅ Device: Connected to {port_path}")
                            self.connection_status.setStyleSheet("""
                                QLabel {
                                    color: #388e3c;
                                    font-size: 14px;
                                    font-weight: bold;
                                    background-color: #e8f5e9;
                                    padding: 10px;
                                    border-radius: 8px;
                                    border: 1px solid #a5d6a7;
                                    qproperty-alignment: 'AlignCenter';
                                }
                            """)
                            threading.Thread(target=self.read_serial, daemon=True).start()
                            logger.info(f"[LOADCELL] Successfully connected to load cell on {port_path}")
                            self.setWindowTitle(f"Manhole Depth Measurement - DEVICE MODE ({port_path})")
                            
                            # Send initial configuration if needed
                            self.ser.write(b"CONFIG:57600\n")  # Set baud rate
                            time.sleep(0.1)
                            
                            break
                        else:
                            # Close if no response
                            self.ser.close()
                            self.ser = None
                            logger.warning(f"[LOADCELL] No meaningful response from {port_path}, trying next port")
                            
                except serial.SerialException as e:
                    logger.error(f"[LOADCELL] Serial error on {port_path}: {e}")
                    if self.ser:
                        try:
                            self.ser.close()
                        except:
                            pass
                        self.ser = None
                except Exception as e:
                    logger.error(f"[LOADCELL] Failed to connect to {port_path}: {e}")
                    if self.ser:
                        try:
                            self.ser.close()
                        except:
                            pass
                        self.ser = None
        
        # If no device connected, use simulation mode
        if not device_connected:
            self.simulation_mode = True
            if acm_ports:
                self.connection_status.setText("🎮 SIMULATION MODE (Device found but not responding)")
            else:
                self.connection_status.setText("🎮 SIMULATION MODE (No ACM ports found)")
            
            self.connection_status.setStyleSheet("""
                QLabel {
                    color: #9c27b0;
                    font-size: 14px;
                    font-weight: bold;
                    background-color: #f3e5f5;
                    padding: 10px;
                    border-radius: 8px;
                    border: 1px solid #ce93d8;
                    qproperty-alignment: 'AlignCenter';
                }
            """)
            self.setWindowTitle("Manhole Depth Measurement - SIMULATION MODE")
            logger.info("[LOADCELL] Running in simulation mode")
    
    def read_serial(self):
        """Read data from serial port - only runs when device is connected"""
        if not self.ser or not self.ser.is_open:
            return
            
        while self.running and not self.simulation_mode:
            try:
                if self.ser.in_waiting:
                    try:
                        line = self.ser.readline().decode(errors="ignore").strip()
                        
                        # Skip empty lines
                        if not line:
                            continue
                            
                        logger.debug(f"[LOADCELL] Raw data: {line}")
                        
                        # Parse different possible formats
                        # Format 1: "Distance: 123 cm"
                        if "Distance:" in line or "DISTANCE:" in line:
                            try:
                                # Extract distance value
                                parts = line.split(":")
                                if len(parts) >= 2:
                                    value_part = parts[1].strip()
                                    # Remove non-numeric characters except decimal point
                                    depth_str = ''.join(c for c in value_part if c.isdigit() or c == '.')
                                    if depth_str:
                                        depth = int(float(depth_str))
                                        QTimer.singleShot(0, lambda d=depth: self.update_depth(d))
                                        logger.debug(f"[LOADCELL] Parsed depth: {depth} cm")
                            except Exception as e:
                                logger.warning(f"[LOADCELL] Parse error for line: {line} - {e}")
                        
                        # Format 2: "123.45" (just a number)
                        elif line.replace('.', '').isdigit():
                            try:
                                depth = int(float(line))
                                QTimer.singleShot(0, lambda d=depth: self.update_depth(d))
                                logger.debug(f"[LOADCELL] Parsed numeric depth: {depth} cm")
                            except:
                                pass
                        
                        # Format 3: "LOAD: 123" or "WEIGHT: 123"
                        elif "LOAD:" in line or "WEIGHT:" in line:
                            try:
                                parts = line.split(":")
                                if len(parts) >= 2:
                                    value_part = parts[1].strip()
                                    depth_str = ''.join(c for c in value_part if c.isdigit() or c == '.')
                                    if depth_str:
                                        depth = int(float(depth_str))
                                        QTimer.singleShot(0, lambda d=depth: self.update_depth(d))
                                        logger.debug(f"[LOADCELL] Parsed load/weight: {depth} cm")
                            except Exception as e:
                                logger.warning(f"[LOADCELL] Parse error for line: {line} - {e}")
                        
                        # Stop commands
                        elif "STOP" in line or "STOPPED" in line:
                            QTimer.singleShot(0, lambda: self.stop_current_action('serial'))
                            logger.info("[LOADCELL] Received STOP command from device")
                        
                        # Error messages
                        elif "ERROR" in line or "FAULT" in line or "ERR" in line:
                            logger.error(f"[LOADCELL] Device error: {line}")
                            
                    except UnicodeDecodeError:
                        # Try to read raw bytes
                        if self.ser.in_waiting:
                            raw_data = self.ser.readline()
                            logger.debug(f"[LOADCELL] Raw bytes: {raw_data}")
                        
            except serial.SerialException as e:
                logger.error(f"[LOADCELL] Serial port error: {e}")
                self.simulation_mode = True
                QTimer.singleShot(0, lambda: self.connection_status.setText("⚠️ Device disconnected - SIMULATION MODE"))
                QTimer.singleShot(0, lambda: self.connection_status.setStyleSheet("""
                    QLabel {
                        color: #f57c00;
                        font-size: 14px;
                        font-weight: bold;
                        background-color: #fff3e0;
                        padding: 10px;
                        border-radius: 8px;
                        border: 1px solid #ffcc80;
                        qproperty-alignment: 'AlignCenter';
                    }
                """))
                break
            except Exception as e:
                logger.error(f"[LOADCELL] Error reading serial: {e}")
                
            time.sleep(0.05)  # 20Hz update rate
    
    def send_serial_command(self, command):
        """Send command to serial device"""
        if not self.simulation_mode and self.ser and self.ser.is_open:
            try:
                full_command = f"{command}\n".encode()
                self.ser.write(full_command)
                logger.info(f"[LOADCELL] Sent command: {command}")
                return True
            except Exception as e:
                logger.error(f"[LOADCELL] Failed to send command {command}: {e}")
                return False
        return False
    
    def start_measurement(self, mode):
        """Start measurement process"""
        if self.action_in_progress:
            QMessageBox.warning(self, "Action in Progress", 
                              "Another action is currently in progress. Please stop it first.")
            return
            
        if mode == 'before' and self.before_depth is not None:
            reply = QMessageBox.question(self, "Already Measured", 
                                       "Before cleaning has already been measured.\nDo you want to measure again?",
                                       QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.No:
                return
            else:
                self.before_depth = None
                self.before_depth_display.setText("-- cm")
                
        if mode == 'after' and self.after_depth is not None:
            reply = QMessageBox.question(self, "Already Measured", 
                                       "After cleaning has already been measured.\nDo you want to measure again?",
                                       QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.No:
                return
            else:
                self.after_depth = None
                self.after_depth_display.setText("-- cm")
        
        self._start_measurement(mode)
    
    def _start_measurement(self, mode):
        """Internal method to start measurement"""
        # IMPORTANT: Record where we're starting from for return
        self.start_position_depth = self.manhole_widget.current_depth
        self.target_return_depth = self.start_position_depth  # We want to return to where we started
        
        logger.info(f"[LOADCELL] Starting measurement from depth: {self.start_position_depth} cm")
        logger.info(f"[LOADCELL] Will return to: {self.target_return_depth} cm")
        
        self.current_measurement = mode
        self.is_measuring = True
        self.is_returning = False
        self.action_in_progress = True
        self.simulation_direction = 1  # Moving down
        
        # Reset simulation depth to current position
        self.simulation_depth = self.manhole_widget.current_depth
        
        # Update widget
        self.manhole_widget.should_stop = False
        self.manhole_widget.is_measuring = True
        self.manhole_widget.returning = False
        self.manhole_widget.measurement_mode = mode
        
        # Update button states
        if mode == 'before':
            self.stop_before_btn.setEnabled(True)
            self.start_before_btn.setEnabled(False)
            self.start_before_btn.setText("MEASURING...")
            self.stop_after_btn.setEnabled(False)
        else:
            self.stop_after_btn.setEnabled(True)
            self.start_after_btn.setEnabled(False)
            self.start_after_btn.setText("MEASURING...")
            self.stop_before_btn.setEnabled(False)
        
        # Disable other buttons
        if mode == 'before':
            self.start_after_btn.setEnabled(False)
        else:
            self.start_before_btn.setEnabled(False)
        
        self.return_before_btn.setEnabled(True)
        self.return_after_btn.setEnabled(True)
        self.manual_return_btn.setEnabled(True)
        self.reset_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        
        # Send command to device if connected
        if not self.simulation_mode:
            if self.send_serial_command("START"):
                logger.info("[LOADCELL] Sent START command to device")
        
        # Start visualization
        self.manhole_widget.start_measurement(mode)
        
        # Start simulation timer
        self.start_simulation_timer()
        
        # Set auto-stop timer (5 seconds timeout for simulation)
        timeout_ms = 5000 if self.simulation_mode else 30000  # 30 seconds for real device
        self.measurement_timer = QTimer(self)
        self.measurement_timer.timeout.connect(lambda: self.stop_current_measurement(force=True))
        self.measurement_timer.start(timeout_ms)
        
        # Update results text
        mode_text = "SIMULATION" if self.simulation_mode else "DEVICE"
        self.results_text.setText(
            f"<span style='font-size: 16px; font-weight: bold; color: #ff9800;'>▶ MEASURING... ({mode_text})</span><br>"
            f"Probe descending at {self.simulation_speed} cm/s<br>"
            f"Started from: {self.start_position_depth} cm<br>"
            f"<span style='color: #666; font-size: 13px;'>"
            f"Click STOP button to stop at current depth"
            f"</span>"
        )
    
    def start_return(self, source):
        """Start returning probe to original start position"""
        if self.action_in_progress:
            QMessageBox.warning(self, "Action in Progress", 
                              "Another action is currently in progress. Please stop it first.")
            return
        
        # Check if we're already at the start position
        current_depth = self.manhole_widget.current_depth
        if current_depth <= self.target_return_depth:
            QMessageBox.information(self, "Already at Start", 
                                  f"Probe is already at start position ({current_depth} cm)")
            return
        
        logger.info(f"[LOADCELL] Starting return from {current_depth} cm to {self.target_return_depth} cm")
        
        self.is_returning = True
        self.is_measuring = False
        self.action_in_progress = True
        self.simulation_direction = -1  # Moving up
        
        # Calculate how far we need to return
        return_distance = current_depth - self.target_return_depth
        logger.info(f"[LOADCELL] Need to return {return_distance} cm")
        
        # Update widget
        self.manhole_widget.should_stop = False
        
        # Update button states
        self.stop_return_btn.setEnabled(True)
        self.manual_return_btn.setEnabled(False)
        self.start_before_btn.setEnabled(False)
        self.start_after_btn.setEnabled(False)
        self.return_before_btn.setEnabled(False)
        self.return_after_btn.setEnabled(False)
        self.reset_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        
        # Send command to device if connected
        if not self.simulation_mode:
            if self.send_serial_command("RETURN"):
                logger.info("[LOADCELL] Sent RETURN command to device")
        
        # Start return animation
        if self.manhole_widget.return_to_start():
            # Start simulation timer
            self.start_simulation_timer()
            
            # Set return timeout based on distance
            estimated_time = (return_distance / self.simulation_speed) * 1000  # Convert to ms
            self.return_timer = QTimer(self)
            self.return_timer.timeout.connect(lambda: self.complete_return(force=True))
            self.return_timer.start(int(estimated_time) + 2000)  # Add 2 second buffer
            
            # Update results text
            mode_text = "SIMULATION" if self.simulation_mode else "DEVICE"
            self.results_text.setText(
                f"<span style='font-size: 16px; font-weight: bold; color: #2196f3;'>⬆ RETURNING... ({mode_text})</span><br>"
                f"Returning {return_distance} cm to start position<br>"
                f"From: {current_depth} cm → To: {self.target_return_depth} cm<br>"
                f"<span style='color: #666; font-size: 13px;'>"
                f"Click STOP RETURN button to stop"
                f"</span>"
            )
    
    def start_simulation_timer(self):
        """Start the simulation timer for motor movement"""
        if self.simulation_timer:
            self.simulation_timer.stop()
        
        self.simulation_timer = QTimer(self)
        self.simulation_timer.timeout.connect(self.update_simulation)
        interval = 1000 // (self.simulation_speed * 2)  # Update based on speed
        self.simulation_timer.start(max(50, interval))  # Min 50ms interval
    
    def update_simulation(self):
        """Update simulation depth based on current direction and speed"""
        if self.is_measuring and self.simulation_direction == 1:
            # Moving down (measuring)
            increment = self.simulation_speed * 0.05  # Adjust for timer interval
            new_depth = self.simulation_depth + increment
            
            # Limit to max depth (183 cm)
            if new_depth >= 183:
                new_depth = 183
                self.stop_current_measurement()
                return
            
            self.simulation_depth = new_depth
            
            # Update widget
            self.manhole_widget.current_depth = int(self.simulation_depth)
            self.manhole_widget.probe_y = 100 + (self.simulation_depth / 183) * 400
            self.manhole_widget.depth_label.setText(f"Depth: {int(self.simulation_depth)} cm")
            self.manhole_widget.update()
            
        elif self.is_returning and self.simulation_direction == -1:
            # Moving up (returning) - target is self.target_return_depth
            decrement = self.simulation_speed * 0.05  # Adjust for timer interval
            new_depth = self.simulation_depth - decrement
            
            # Limit to target return depth
            if new_depth <= self.target_return_depth:
                new_depth = self.target_return_depth
                self.complete_return()
                return
            
            self.simulation_depth = new_depth
            
            # Update widget
            self.manhole_widget.current_depth = int(self.simulation_depth)
            self.manhole_widget.probe_y = 100 + (self.simulation_depth / 183) * 400
            remaining = int(self.simulation_depth - self.target_return_depth)
            self.manhole_widget.depth_label.setText(f"Returning: {remaining} cm to go")
            self.manhole_widget.update()
    
    def update_simulation_speed(self, value):
        """Update simulation speed from slider"""
        self.simulation_speed = value
        self.speed_label.setText(f"{value} cm/s")
        
        # Restart timer with new speed if action is in progress
        if self.action_in_progress:
            self.start_simulation_timer()
    
    def stop_current_action(self, source):
        """Stop current action (measurement or return)"""
        logger.info(f"[LOADCELL] Stopping current action (source: {source})")
        
        # Stop simulation timer
        if self.simulation_timer:
            self.simulation_timer.stop()
        
        # Set stop flag in widget
        self.manhole_widget.should_stop = True
        
        # Send stop command to device if connected
        if not self.simulation_mode:
            if self.send_serial_command("STOP"):
                logger.info("[LOADCELL] Sent STOP command to device")
        
        # Update simulation depth to match widget
        self.simulation_depth = self.manhole_widget.current_depth
        
        # Stop measurement if in progress
        if self.is_measuring:
            self.manhole_widget.stop_measurement()
            self.stop_current_measurement(force=True)
        
        # Stop return if in progress
        if self.is_returning:
            self.manhole_widget.stop_return()
            self.complete_return(force=True)
        
        # Update UI
        QTimer.singleShot(100, self.update_button_states_after_stop)
    
    def update_button_states_after_stop(self):
        """Update button states after stopping"""
        # Re-enable start buttons
        if self.before_depth is None:
            self.start_before_btn.setEnabled(True)
            self.start_before_btn.setText("START")
        else:
            self.start_before_btn.setEnabled(True)
            self.start_before_btn.setText("MEASURE AGAIN")
            
        if self.after_depth is None and self.before_depth is not None:
            self.start_after_btn.setEnabled(True)
            self.start_after_btn.setText("START")
        elif self.after_depth is not None:
            self.start_after_btn.setEnabled(True)
            self.start_after_btn.setText("MEASURE AGAIN")
        
        # Disable all stop buttons
        self.stop_before_btn.setEnabled(False)
        self.stop_after_btn.setEnabled(False)
        self.stop_return_btn.setEnabled(False)
        
        # Enable return buttons if probe is not at start
        current_depth = self.manhole_widget.current_depth
        if current_depth > self.target_return_depth:
            self.return_before_btn.setEnabled(True)
            self.return_after_btn.setEnabled(True)
            self.manual_return_btn.setEnabled(True)
        else:
            self.return_before_btn.setEnabled(False)
            self.return_after_btn.setEnabled(False)
            self.manual_return_btn.setEnabled(False)
        
        # Re-enable reset and save buttons
        self.reset_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        
        # Reset action flags
        self.action_in_progress = False
        self.is_measuring = False
        self.is_returning = False
        
        # Update results text
        current_depth = self.manhole_widget.current_depth
        mode_text = "SIMULATION MODE" if self.simulation_mode else "DEVICE MODE"
        remaining_to_start = current_depth - self.target_return_depth
        if remaining_to_start > 0:
            self.results_text.setText(
                f"<span style='font-size: 16px; font-weight: bold; color: #f44336;'>⏹ OPERATION STOPPED</span><br>"
                f"Current depth: {current_depth} cm<br>"
                f"Need to return {remaining_to_start} cm to start position<br>"
                f"Mode: {mode_text}<br>"
                f"<span style='color: #666; font-size: 13px;'>"
                f"Click RETURN to retract probe to start position"
                f"</span>"
            )
        else:
            self.results_text.setText(
                f"<span style='font-size: 16px; font-weight: bold; color: #f44336;'>⏹ OPERATION STOPPED</span><br>"
                f"Current depth: {current_depth} cm<br>"
                f"Probe is at start position<br>"
                f"Mode: {mode_text}<br>"
                f"<span style='color: #666; font-size: 13px;'>"
                f"Ready for next measurement"
                f"</span>"
            )
    
    def stop_current_measurement(self, force=False):
        """Stop current measurement"""
        # Stop simulation timer
        if self.simulation_timer:
            self.simulation_timer.stop()
        
        # Stop measurement timer
        if hasattr(self, 'measurement_timer'):
            self.measurement_timer.stop()
        
        # Get depth from simulation
        depth = int(self.simulation_depth)
        
        # Save measurement
        if self.current_measurement == 'before' or (force and self.is_measuring):
            self.before_depth = depth
            self.before_depth_display.setText(f"{depth} cm")
            
            # Enable after measurement button
            self.start_after_btn.setEnabled(True)
            self.start_before_btn.setText("MEASURE AGAIN")
            self.start_before_btn.setEnabled(True)
            
        elif self.current_measurement == 'after' or (force and self.is_measuring):
            self.after_depth = depth
            self.after_depth_display.setText(f"{depth} cm")
            self.start_after_btn.setText("MEASURE AGAIN")
            self.start_after_btn.setEnabled(True)
            self.start_before_btn.setEnabled(True)
        
        # Stop visualization
        self.manhole_widget.stop_measurement(depth)
        
        # Emit signal
        if self.current_measurement:
            self.measurement_complete.emit(self.current_measurement, depth)
        
        # Auto-return if enabled
        if self.auto_return_cb.isChecked() and not force:
            QTimer.singleShot(1000, lambda: self.start_return('auto'))
        
        # Update results
        self.update_results()
        
        self.current_measurement = None
        self.is_measuring = False
    
    def complete_return(self, force=False):
        """Complete return operation"""
        # Stop simulation timer
        if self.simulation_timer:
            self.simulation_timer.stop()
        
        # Stop return timer
        if hasattr(self, 'return_timer'):
            self.return_timer.stop()
        
        # Set simulation depth to target
        self.simulation_depth = self.target_return_depth
        
        # Update widget
        self.manhole_widget.returning = False
        self.manhole_widget.is_measuring = False
        self.manhole_widget.should_stop = False
        self.manhole_widget.current_depth = self.target_return_depth
        self.manhole_widget.probe_y = 100 + (self.target_return_depth / 183) * 400
        self.manhole_widget.depth_label.setText(f"Depth: {self.target_return_depth} cm")
        self.manhole_widget.status_label.setText(f"Ready - Probe at {self.target_return_depth} cm")
        self.manhole_widget.update()
        
        # Update UI
        self.stop_return_btn.setEnabled(False)
        self.manual_return_btn.setEnabled(True)
        
        # Re-enable buttons
        if self.before_depth is None:
            self.start_before_btn.setEnabled(True)
            self.start_before_btn.setText("START")
        else:
            self.start_before_btn.setEnabled(True)
            self.start_before_btn.setText("MEASURE AGAIN")
            
        if self.after_depth is None and self.before_depth is not None:
            self.start_after_btn.setEnabled(True)
            self.start_after_btn.setText("START")
        elif self.after_depth is not None:
            self.start_after_btn.setEnabled(True)
            self.start_after_btn.setText("MEASURE AGAIN")
        
        # Only enable return buttons if not at start
        if self.target_return_depth > 0:
            self.return_before_btn.setEnabled(True)
            self.return_after_btn.setEnabled(True)
            self.manual_return_btn.setEnabled(True)
        else:
            self.return_before_btn.setEnabled(False)
            self.return_after_btn.setEnabled(False)
            self.manual_return_btn.setEnabled(True)  # Manual return always enabled
        
        # Reset action flags
        self.action_in_progress = False
        self.is_returning = False
        
        # Update results if not from auto-return
        if not force:
            mode_text = "SIMULATION MODE" if self.simulation_mode else "DEVICE MODE"
            self.results_text.setText(
                f"<span style='font-size: 16px; font-weight: bold; color: #2196f3;'>✅ PROBE RETURNED</span><br>"
                f"Probe returned to start position ({self.target_return_depth} cm)<br>"
                f"Mode: {mode_text}<br>"
                f"<span style='color: #666; font-size: 13px;'>"
                f"Ready for next measurement"
                f"</span>"
            )
    
    def update_depth(self, depth):
        """Update depth from serial reading - only in device mode"""
        if not self.simulation_mode and self.is_measuring and self.current_measurement:
            self.manhole_widget.current_depth = depth
            self.manhole_widget.probe_y = 100 + (depth / 183) * 400
            self.manhole_widget.depth_label.setText(f"Depth: {depth} cm")
            self.manhole_widget.update()
            self.simulation_depth = depth
        
    def update_results(self):
        """Update results display"""
        if self.before_depth is not None and self.after_depth is not None:
            reduction = self.before_depth - self.after_depth
            if self.before_depth > 0:
                percentage = (reduction / self.before_depth) * 100
                mode_text = "SIMULATION" if self.simulation_mode else "DEVICE"
                self.results_text.setText(
                    f"<span style='font-size: 18px; font-weight: bold; color: #7b1fa2;'>✅ COMPLETE! ({mode_text})</span><br>"
                    f"<span style='color: #f57c00;'>Before:</span> {self.before_depth} cm<br>"
                    f"<span style='color: #388e3c;'>After:</span> {self.after_depth} cm<br>"
                    f"<span style='color: #d32f2f;'>Reduction:</span> {reduction} cm<br>"
                    f"<span style='color: #1976d2;'>Improvement:</span> {percentage:.1f}%"
                )
        elif self.before_depth is not None:
            mode_text = "SIMULATION" if self.simulation_mode else "DEVICE"
            self.results_text.setText(
                f"<span style='font-size: 16px; font-weight: bold; color: #f57c00;'>✅ BEFORE MEASURED ({mode_text})</span><br>"
                f"Silt Depth: {self.before_depth} cm<br>"
                f"Started from: {self.start_position_depth} cm<br>"
                f"<span style='color: #666; font-size: 13px;'>"
                f"1. Click RETURN to retract probe to {self.target_return_depth} cm<br>"
                f"2. Perform cleaning<br>"
                f"3. Click START under 'After Cleaning'"
                f"</span>"
            )
        elif self.after_depth is not None:
            mode_text = "SIMULATION" if self.simulation_mode else "DEVICE"
            self.results_text.setText(
                f"<span style='font-size: 16px; font-weight: bold; color: #388e3c;'>✅ AFTER MEASURED ({mode_text})</span><br>"
                f"Remaining Silt: {self.after_depth} cm<br>"
                f"Started from: {self.start_position_depth} cm<br>"
                f"<span style='color: #666; font-size: 13px;'>"
                f"Click RETURN to retract probe to {self.target_return_depth} cm"
                f"</span>"
            )
            
    def reset_all(self):
        """Reset all measurements and return probe"""
        reply = QMessageBox.question(self, "Reset Measurements", 
                                   "Reset all measurements?\nProbe will return to start.",
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            # Reset target return depth to 0 (fully up)
            self.target_return_depth = 0
            
            # Stop any ongoing action first
            if self.action_in_progress:
                self.stop_current_action('reset')
                QTimer.singleShot(500, lambda: self.start_return('reset'))
            else:
                self.start_return('reset')
            
            # Reset measurements after return completes
            QTimer.singleShot(2000, self._complete_reset)
            
    def _complete_reset(self):
        """Complete the reset after probe returns"""
        self.before_depth = None
        self.after_depth = None
        self.start_position_depth = 0
        self.target_return_depth = 0
        
        # Reset displays
        self.before_depth_display.setText("-- cm")
        self.after_depth_display.setText("-- cm")
        
        # Reset buttons
        self.start_before_btn.setText("START")
        self.start_before_btn.setEnabled(True)
        self.start_after_btn.setText("START")
        self.start_after_btn.setEnabled(False)
        
        # Reset other buttons
        self.stop_before_btn.setEnabled(False)
        self.stop_after_btn.setEnabled(False)
        self.stop_return_btn.setEnabled(False)
        self.return_before_btn.setEnabled(False)
        self.return_after_btn.setEnabled(False)
        self.manual_return_btn.setEnabled(True)
        
        # Reset results text
        self.results_text.setText("Take 'Before Cleaning' measurement to begin")
        
        # Reset visualization
        self.manhole_widget.reset()
        
        # Reset simulation
        self.simulation_depth = 0
        
        self.current_measurement = None
        self.is_measuring = False
        self.is_returning = False
        self.action_in_progress = False
        
    def save_and_close(self):
        """Save measurements and close dialog"""
        if self.before_depth is not None or self.after_depth is not None:
            mode = "SIMULATION" if self.simulation_mode else "DEVICE"
            logger.info(f"[LOADCELL] Measurements saved ({mode}) - Before: {self.before_depth}, After: {self.after_depth}")
        self.accept()
        
    def closeEvent(self, event):
        """Handle dialog close"""
        # Stop any ongoing operations
        if self.action_in_progress:
            self.stop_current_action('close')
        
        # Stop simulation timer
        if self.simulation_timer:
            self.simulation_timer.stop()
        
        self.running = False
        if self.ser:
            self.ser.close()
        super().closeEvent(event)
        
# --- Mapbox Interactive Map Dialog ---
class CustomGraphicsView(QGraphicsView):
    """Custom graphics view with pinch zoom support"""
    def __init__(self, scene, parent_dialog):
        super().__init__(scene)
        self.parent_dialog = parent_dialog
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
    
    def wheelEvent(self, event):
        """Handle mouse wheel for zoom"""
        if event.angleDelta().y() > 0:
            self.parent_dialog.zoom_in()
        else:
            self.parent_dialog.zoom_out()
        event.accept()
    
    def mousePressEvent(self, event):
        """Handle mouse press events"""
        if event.button() == Qt.LeftButton:
            # Check if instructions were clicked
            scene_pos = self.mapToScene(event.pos())
            items = self.scene().items(scene_pos)
            for item in items:
                if hasattr(item, 'data') and item.data(0) == "instructions":
                    self.scene().removeItem(item)
                    if hasattr(self.parent_dialog, 'instruction_text'):
                        self.scene().removeItem(self.parent_dialog.instruction_text)
        
        super().mousePressEvent(event)
    
    def event(self, event):
        """Handle touch events for pinch zoom"""
        if event.type() == QEvent.TouchBegin:
            self.parent_dialog.is_pinching = True
            self.parent_dialog.last_pinch_distance = 0
            return True
        elif event.type() == QEvent.TouchUpdate:
            if len(event.touchPoints()) == 2:
                p1 = event.touchPoints()[0].pos()
                p2 = event.touchPoints()[1].pos()
                distance = ((p2.x() - p1.x())**2 + (p2.y() - p1.y())**2)**0.5
                
                if self.parent_dialog.last_pinch_distance > 0:
                    if distance > self.parent_dialog.last_pinch_distance + 20:
                        self.parent_dialog.zoom_in()
                        self.parent_dialog.last_pinch_distance = distance
                    elif distance < self.parent_dialog.last_pinch_distance - 20:
                        self.parent_dialog.zoom_out()
                        self.parent_dialog.last_pinch_distance = distance
                else:
                    self.parent_dialog.last_pinch_distance = distance
                return True
        elif event.type() == QEvent.TouchEnd:
            self.parent_dialog.is_pinching = False
            self.parent_dialog.last_pinch_distance = 0
            return True
        
        return super().event(event)


class DraggablePixmapItem(QGraphicsPixmapItem):
    """Draggable pixmap item for the marker"""
    def __init__(self, pixmap, parent_dialog):
        super().__init__(pixmap)
        self.setFlag(QGraphicsPixmapItem.ItemIsMovable)
        self.setFlag(QGraphicsPixmapItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.dragging = False
        self.parent_dialog = parent_dialog
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = True
            self.parent_dialog.on_marker_drag_start()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event):
        if self.dragging:
            super().mouseMoveEvent(event)
            self.parent_dialog.on_marker_dragged(self.pos())
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.dragging:
            self.dragging = False
            self.parent_dialog.on_marker_drag_end()
            self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)
    
    def hoverEnterEvent(self, event):
        self.setCursor(Qt.OpenHandCursor)
        super().hoverEnterEvent(event)
    
    def hoverLeaveEvent(self, event):
        if not self.dragging:
            self.setCursor(Qt.ArrowCursor)
        super().hoverLeaveEvent(event)


class MapboxMapDialog(QDialog):
    """Interactive Mapbox map dialog with click-to-select functionality"""
    
    location_selected = pyqtSignal(float, float, str)  # lat, lon, manhole_id
    
    def __init__(self, parent=None, center_lat=17.4569, center_lon=78.3711, zoom=15, nearby_manholes=None):
        super().__init__(parent)
        logger.info(f"[MAPBOX-DIALOG] Initializing with lat={center_lat}, lon={center_lon}, zoom={zoom}")
        
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.zoom = zoom
        self.selected_lat = center_lat
        self.selected_lon = center_lon
        self.selected_manhole_id = None
        self.nearby_manholes = nearby_manholes or []
        self.marker_pixmap = None
        self.map_pixmap = None
        
        # Mapbox configuration - use YOUR token and styles
        self.mapbox_token = config.get('mapbox_token', '')
        if not self.mapbox_token:
            logger.warning("[MAPBOX] No Mapbox token found in config")
            self.mapbox_token = "pk.eyJ1Ijoic2h1YmhhbWd2IiwiYSI6ImNtZDV2cmJneDAydngyanFzaW1vNTM3M24ifQ.7Jb5OXpznWqjyMeAuiXhrQ"
        
        # Map styles - using YOUR custom styles
        self.map_styles = [
            {"url": "mapbox://styles/shubhamgv/cmiofroih003501sm90m2hn06", "name": "Street", "icon_color": "#4285F4"},
            {"url": "mapbox://styles/shubhamgv/cmiof1gt5003c01s43hud0zmd", "name": "Satellite", "icon_color": "#34A853"},
            {"url": "mapbox://styles/shubhamgv/cmiof9l0900o201sc3mdc6tsc", "name": "Diameter", "icon_color": "#FBBC05"}
        ]
        self.current_style_index = 0
        
        # Pinch zoom variables
        self.last_pinch_distance = 0
        self.is_pinching = False
        
        # Draggable marker variables
        self.dragging_marker = False
        self.marker_offset = QPointF(0, 0)
        
        self.setWindowTitle("Select Manhole Location")
        self.setGeometry(100, 100, 1200, 800)
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a1a;
            }
            QLabel {
                color: #ffffff;
            }
            QGroupBox {
                color: #ffffff;
                font-weight: bold;
                border: 2px solid #404040;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                background-color: #2d2d2d;
                border-radius: 4px;
            }
        """)
        
        self.init_ui()
        self.load_map_image()
    
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)
        
        # Header with gradient background
        header = QFrame()
        header.setFixedHeight(70)
        header.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1a237e, stop:1 #311b92);
                border-radius: 10px;
                padding: 10px;
            }
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 10, 20, 10)
        
        # Title with icon
        title_layout = QHBoxLayout()
        title_icon = QLabel("📍")
        title_icon.setStyleSheet("font-size: 24px;")
        title_text = QLabel("MANHOLE LOCATION SELECTOR")
        title_text.setStyleSheet("""
            QLabel {
                font-size: 18px;
                font-weight: bold;
                color: white;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
        """)
        title_layout.addWidget(title_icon)
        title_layout.addWidget(title_text)
        title_layout.addStretch()
        header_layout.addLayout(title_layout)
        
        main_layout.addWidget(header)
        
        # Main splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #404040;
                width: 3px;
            }
        """)
        main_layout.addWidget(splitter)
        
        # Left side: Interactive Map
        left_widget = QWidget()
        left_widget.setStyleSheet("background-color: #2d2d2d; border-radius: 10px;")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(15, 15, 15, 15)
        left_layout.setSpacing(15)
        
        # Map controls toolbar
        controls_toolbar = QWidget()
        controls_toolbar.setFixedHeight(50)
        controls_toolbar.setStyleSheet("""
            QWidget {
                background-color: #363636;
                border-radius: 8px;
                padding: 5px;
            }
        """)
        controls_layout = QHBoxLayout(controls_toolbar)
        controls_layout.setContentsMargins(10, 5, 10, 5)
        
        # Zoom controls
        zoom_group = QWidget()
        zoom_layout = QHBoxLayout(zoom_group)
        zoom_layout.setSpacing(5)
        
        zoom_out_btn = QPushButton("−")
        zoom_out_btn.setFixedSize(35, 35)
        zoom_out_btn.setStyleSheet("""
            QPushButton {
                background-color: #4285F4;
                color: white;
                font-size: 18px;
                font-weight: bold;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover {
                background-color: #3367D6;
            }
            QPushButton:pressed {
                background-color: #2A56C6;
            }
        """)
        zoom_out_btn.clicked.connect(self.zoom_out)
        
        self.zoom_label = QLabel(f"Zoom: {self.zoom}")
        self.zoom_label.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-size: 14px;
                font-weight: bold;
                padding: 5px 10px;
                background-color: #404040;
                border-radius: 6px;
                min-width: 100px;
                text-align: center;
            }
        """)
        
        zoom_in_btn = QPushButton("+")
        zoom_in_btn.setFixedSize(35, 35)
        zoom_in_btn.setStyleSheet("""
            QPushButton {
                background-color: #4285F4;
                color: white;
                font-size: 18px;
                font-weight: bold;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover {
                background-color: #3367D6;
            }
            QPushButton:pressed {
                background-color: #2A56C6;
            }
        """)
        zoom_in_btn.clicked.connect(self.zoom_in)
        
        zoom_layout.addWidget(zoom_out_btn)
        zoom_layout.addWidget(self.zoom_label)
        zoom_layout.addWidget(zoom_in_btn)
        controls_layout.addWidget(zoom_group)
        
        # Style selector with colored buttons
        style_group = QWidget()
        style_layout = QHBoxLayout(style_group)
        style_layout.setSpacing(5)
        
        style_label = QLabel("Map Style:")
        style_label.setStyleSheet("color: #ffffff; font-weight: bold;")
        style_layout.addWidget(style_label)
        
        for i, style in enumerate(self.map_styles):
            style_btn = QPushButton()
            style_btn.setFixedSize(40, 35)
            style_btn.setToolTip(style["name"])
            style_btn.setCheckable(True)
            style_btn.setChecked(i == self.current_style_index)
            style_btn.clicked.connect(lambda checked, idx=i: self.set_style(idx))
            
            # Create colored button
            style_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {style['icon_color']};
                    border: 2px solid transparent;
                    border-radius: 6px;
                }}
                QPushButton:checked {{
                    border: 2px solid #ffffff;
                }}
                QPushButton:hover {{
                    background-color: {self.adjust_color(style['icon_color'], 30)};
                }}
            """)
            
            # Add icon based on style
            icon_label = QLabel()
            if style["name"] == "Street": icon_label.setText("🗺️")
            elif style["name"] == "Satellite": icon_label.setText("🛰️")
            else: icon_label.setText("📐")
            icon_label.setAlignment(Qt.AlignCenter)
            icon_label.setStyleSheet("font-size: 16px; background: transparent;")
            
            btn_layout = QVBoxLayout(style_btn)
            btn_layout.setContentsMargins(0, 0, 0, 0)
            btn_layout.addWidget(icon_label)
            
            style_layout.addWidget(style_btn)
        
        controls_layout.addWidget(style_group)
        controls_layout.addStretch()
        
        # Test Mapbox connection button
        test_btn = QPushButton("Test Map")
        test_btn.setFixedSize(80, 35)
        test_btn.setStyleSheet("""
            QPushButton {
                background-color: #FBBC05;
                color: white;
                font-weight: bold;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover {
                background-color: #F4B400;
            }
        """)
        test_btn.clicked.connect(self.test_mapbox_connection)
        controls_layout.addWidget(test_btn)
        
        # Current location button
        location_btn = QPushButton("📍 Current")
        location_btn.setFixedSize(100, 35)
        location_btn.setStyleSheet("""
            QPushButton {
                background-color: #34A853;
                color: white;
                font-weight: bold;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover {
                background-color: #2E8B47;
            }
        """)
        location_btn.clicked.connect(self.center_on_current)
        controls_layout.addWidget(location_btn)
        
        left_layout.addWidget(controls_toolbar)
        
        # Map display area with custom graphics view
        map_container = QWidget()
        map_container.setStyleSheet("""
            QWidget {
                background-color: #1a1a1a;
                border-radius: 10px;
                border: 2px solid #404040;
            }
        """)
        map_layout = QVBoxLayout(map_container)
        map_layout.setContentsMargins(0, 0, 0, 0)
        
        self.map_scene = QGraphicsScene()
        self.map_view = CustomGraphicsView(self.map_scene, self)
        self.map_view.setRenderHint(QPainter.Antialiasing)
        self.map_view.setRenderHint(QPainter.SmoothPixmapTransform)
        self.map_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.map_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.map_view.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.map_view.setStyleSheet("border: none; background: transparent;")
        self.map_view.setMinimumSize(600, 400)
        
        # Enable touch events for pinch zoom
        self.map_view.viewport().setAttribute(Qt.WA_AcceptTouchEvents, True)
        
        map_layout.addWidget(self.map_view)
        left_layout.addWidget(map_container, 1)
        
        # Coordinates display with modern design
        coords_container = QWidget()
        coords_container.setFixedHeight(60)
        coords_container.setStyleSheet("""
            QWidget {
                background-color: #363636;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        coords_layout = QHBoxLayout(coords_container)
        coords_layout.setContentsMargins(15, 5, 15, 5)
        
        self.coords_label = QLabel("Click and drag the marker to select location")
        self.coords_label.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 15px;
                background-color: #404040;
                border-radius: 6px;
            }
        """)
        self.coords_label.setAlignment(Qt.AlignCenter)
        coords_layout.addWidget(self.coords_label)
        
        left_layout.addWidget(coords_container)
        
        splitter.addWidget(left_widget)
        
        # Right side: Controls Panel
        right_widget = QWidget()
        right_widget.setStyleSheet("background-color: #2d2d2d; border-radius: 10px;")
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(15, 15, 15, 15)
        right_layout.setSpacing(15)
        
        # Current GPS Location card
        gps_card = QWidget()
        gps_card.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0d47a1, stop:1 #1565c0);
                border-radius: 10px;
                padding: 15px;
            }
        """)
        gps_layout = QVBoxLayout(gps_card)
        
        gps_title = QLabel("📍 CURRENT GPS LOCATION")
        gps_title.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 16px;
                font-weight: bold;
                padding-bottom: 10px;
                border-bottom: 1px solid rgba(255,255,255,0.2);
            }
        """)
        gps_layout.addWidget(gps_title)
        
        gps_coords = QWidget()
        gps_coords_layout = QGridLayout(gps_coords)
        gps_coords_layout.setVerticalSpacing(10)
        
        gps_coords_layout.addWidget(QLabel("Latitude:"), 0, 0)
        self.lat_label = QLabel(f"{self.center_lat:.6f}")
        self.lat_label.setStyleSheet("""
            QLabel {
                color: white;
                font-weight: bold;
                font-size: 14px;
                background-color: rgba(255,255,255,0.1);
                padding: 8px;
                border-radius: 5px;
            }
        """)
        gps_coords_layout.addWidget(self.lat_label, 0, 1)
        
        gps_coords_layout.addWidget(QLabel("Longitude:"), 1, 0)
        self.lon_label = QLabel(f"{self.center_lon:.6f}")
        self.lon_label.setStyleSheet("""
            QLabel {
                color: white;
                font-weight: bold;
                font-size: 14px;
                background-color: rgba(255,255,255,0.1);
                padding: 8px;
                border-radius: 5px;
            }
        """)
        gps_coords_layout.addWidget(self.lon_label, 1, 1)
        
        gps_layout.addWidget(gps_coords)
        right_layout.addWidget(gps_card)
        
        # Nearby Manholes card
        manholes_card = QWidget()
        manholes_card.setStyleSheet("""
            QWidget {
                background-color: #363636;
                border-radius: 10px;
                padding: 15px;
            }
        """)
        manholes_layout = QVBoxLayout(manholes_card)
        
        manholes_title = QLabel("📡 NEARBY MANHOLES")
        manholes_title.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 16px;
                font-weight: bold;
                padding-bottom: 10px;
                border-bottom: 1px solid #404040;
            }
        """)
        manholes_layout.addWidget(manholes_title)
        
        # Scroll area for manholes
        self.nearby_scroll = QScrollArea()
        self.nearby_scroll.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
        """)
        self.nearby_scroll.setWidgetResizable(True)
        
        self.nearby_widget = QWidget()
        self.nearby_layout = QVBoxLayout(self.nearby_widget)
        self.nearby_layout.setSpacing(8)
        self.nearby_scroll.setWidget(self.nearby_widget)
        
        manholes_layout.addWidget(self.nearby_scroll)
        right_layout.addWidget(manholes_card, 1)
        
        # Selected Location card
        selected_card = QWidget()
        selected_card.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4CAF50, stop:1 #66BB6A);
                border-radius: 10px;
                padding: 15px;
            }
        """)
        selected_layout = QVBoxLayout(selected_card)
        
        selected_title = QLabel("✅ SELECTED LOCATION")
        selected_title.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 16px;
                font-weight: bold;
                padding-bottom: 10px;
                border-bottom: 1px solid rgba(255,255,255,0.2);
            }
        """)
        selected_layout.addWidget(selected_title)
        
        self.selected_info_label = QLabel("No location selected yet")
        self.selected_info_label.setStyleSheet("""
            QLabel {
                color: white;
                font-weight: bold;
                font-size: 13px;
                background-color: rgba(255,255,255,0.15);
                padding: 12px;
                border-radius: 8px;
                line-height: 1.4;
            }
        """)
        self.selected_info_label.setWordWrap(True)
        selected_layout.addWidget(self.selected_info_label)
        right_layout.addWidget(selected_card)
        
        # Manual Entry card
        manual_card = QWidget()
        manual_card.setStyleSheet("""
            QWidget {
                background-color: #363636;
                border-radius: 10px;
                padding: 15px;
            }
        """)
        manual_layout = QVBoxLayout(manual_card)
        
        manual_title = QLabel("✏️ MANUAL ENTRY")
        manual_title.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 16px;
                font-weight: bold;
                padding-bottom: 10px;
                border-bottom: 1px solid #404040;
            }
        """)
        manual_layout.addWidget(manual_title)
        
        manual_input_container = QWidget()
        manual_input_layout = QVBoxLayout(manual_input_container)
        manual_input_layout.setSpacing(10)
        
        manual_input_layout.addWidget(QLabel("If manhole not in list, enter ID:"))
        
        self.manual_input = QLineEdit()
        self.manual_input.setPlaceholderText("Enter manhole ID...")
        self.manual_input.setStyleSheet("""
            QLineEdit {
                background-color: #404040;
                border: 2px solid #505050;
                border-radius: 6px;
                padding: 10px;
                color: white;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 2px solid #4285F4;
            }
        """)
        manual_input_layout.addWidget(self.manual_input)
        
        manual_btn = QPushButton("🔗 Use Manual ID")
        manual_btn.setStyleSheet("""
            QPushButton {
                background-color: #FBBC05;
                color: white;
                font-weight: bold;
                padding: 12px;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover {
                background-color: #F4B400;
            }
            QPushButton:pressed {
                background-color: #E6A200;
            }
        """)
        manual_btn.clicked.connect(self.use_manual_id)
        manual_input_layout.addWidget(manual_btn)
        
        manual_layout.addWidget(manual_input_container)
        right_layout.addWidget(manual_card)
        
        # Action buttons
        button_container = QWidget()
        button_layout = QHBoxLayout(button_container)
        button_layout.setSpacing(10)
        
        cancel_btn = QPushButton("❌ Cancel")
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #EA4335;
                color: white;
                font-weight: bold;
                font-size: 14px;
                padding: 15px;
                border-radius: 8px;
                border: none;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        self.confirm_btn = QPushButton("✅ Confirm Selection")
        self.confirm_btn.setStyleSheet("""
            QPushButton {
                background-color: #34A853;
                color: white;
                font-weight: bold;
                font-size: 14px;
                padding: 15px;
                border-radius: 8px;
                border: none;
            }
            QPushButton:disabled {
                background-color: #666666;
                color: #aaaaaa;
            }
            QPushButton:hover:!disabled {
                background-color: #2E8B47;
            }
        """)
        self.confirm_btn.clicked.connect(self.confirm_selection)
        self.confirm_btn.setEnabled(False)
        button_layout.addWidget(self.confirm_btn)
        
        right_layout.addWidget(button_container)
        
        splitter.addWidget(right_widget)
        
        # Set splitter sizes
        splitter.setSizes([800, 400])
        
        # Update nearby manholes display
        self.update_nearby_display()
        
        # Add instructions overlay AFTER map loads
        # Will be added in load_map_image
    
    def adjust_color(self, color, amount):
        """Adjust color brightness"""
        import re
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        
        r = min(255, max(0, r + amount))
        g = min(255, max(0, g + amount))
        b = min(255, max(0, b + amount))
        
        return f"#{r:02x}{g:02x}{b:02x}"
    
    def create_draggable_marker(self):
        """Create a modern draggable blue pointer marker"""
        if not self.map_pixmap:
            # Default size if map not loaded yet
            map_width = 1280
        else:
            map_width = self.map_pixmap.width()
        
        # Calculate marker size based on map width (2% of map width, min 40px, max 80px)
        size = max(40, min(80, int(map_width * 0.02)))
        logger.info(f"[MARKER] Creating pointer marker with size: {size}px (map width: {map_width}px)")
        
        self.marker_pixmap = QPixmap(size, size)
        self.marker_pixmap.fill(Qt.transparent)
        
        painter = QPainter(self.marker_pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Draw outer glow/aura
        gradient = QRadialGradient(size/2, size/2, size/2)
        gradient.setColorAt(0, QColor(66, 133, 244, 80))  # Blue glow
        gradient.setColorAt(0.5, QColor(66, 133, 244, 40))
        gradient.setColorAt(1, QColor(66, 133, 244, 0))
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, size, size)
        
        # Create a blue pointer/pin shape (arrow pointing down)
        pointer_size = int(size * 0.7)
        center_x = size // 2
        center_y = size // 2
        
        # Create polygon for pointer shape (arrow pointing down)
        pointer = QPolygonF()
        
        # Top point
        pointer.append(QPointF(center_x, center_y - pointer_size/2))
        # Right wing
        pointer.append(QPointF(center_x + pointer_size/3, center_y + pointer_size/6))
        # Bottom point (tip)
        pointer.append(QPointF(center_x, center_y + pointer_size/2))
        # Left wing
        pointer.append(QPointF(center_x - pointer_size/3, center_y + pointer_size/6))
        
        # Draw blue pointer with gradient
        pointer_gradient = QLinearGradient(center_x, center_y - pointer_size/2, center_x, center_y + pointer_size/2)
        pointer_gradient.setColorAt(0, QColor(66, 133, 244, 255))  # Bright blue at top
        pointer_gradient.setColorAt(1, QColor(26, 115, 232, 255))  # Darker blue at bottom
        
        painter.setBrush(QBrush(pointer_gradient))
        painter.setPen(QPen(Qt.white, max(2, int(size/40))))
        painter.drawPolygon(pointer)
        
        # Add inner white circle/dot at center
        dot_size = int(size * 0.15)
        painter.setBrush(QBrush(Qt.white))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center_x - dot_size//2, center_y - dot_size//2, dot_size, dot_size)
        
        # Add shadow effect at bottom of pointer
        painter.setBrush(QBrush(QColor(0, 0, 0, 60)))
        painter.setPen(Qt.NoPen)
        shadow_offset = int(size * 0.02)
        shadow_polygon = QPolygonF()
        shadow_polygon.append(QPointF(center_x - pointer_size/3 + shadow_offset, center_y + pointer_size/6 + shadow_offset))
        shadow_polygon.append(QPointF(center_x + pointer_size/3 + shadow_offset, center_y + pointer_size/6 + shadow_offset))
        shadow_polygon.append(QPointF(center_x + shadow_offset, center_y + pointer_size/2 + shadow_offset))
        painter.drawPolygon(shadow_polygon)
        
        painter.end()
        
        # Add marker to scene
        self.marker_item = DraggablePixmapItem(self.marker_pixmap, self)
        self.marker_item.setZValue(100)
        self.map_scene.addItem(self.marker_item)
        
        # Position marker at center (adjust for pointer tip being at bottom)
        self.center_marker()
    
    def center_marker(self):
        """Center the marker on the map"""
        if self.map_pixmap and self.marker_item:
            map_width = self.map_pixmap.width()
            map_height = self.map_pixmap.height()
            marker_width = self.marker_pixmap.width() if self.marker_pixmap else 60
            marker_height = self.marker_pixmap.height() if self.marker_pixmap else 60
            
            # Center the marker, with pointer tip at the center
            self.marker_item.setPos(map_width/2 - marker_width/2, 
                                   map_height/2 - marker_height/2)
            logger.info(f"[MARKER] Centered at: ({map_width/2 - marker_width/2:.1f}, {map_height/2 - marker_height/2:.1f})")
    
    def on_marker_drag_start(self):
        """When marker dragging starts"""
        self.dragging_marker = True
        self.map_view.setCursor(Qt.ClosedHandCursor)
    
    def on_marker_dragged(self, pos):
        """When marker is being dragged"""
        if self.map_pixmap:
            # Convert marker position to lat/lon (center of marker)
            x = pos.x() + self.marker_pixmap.width()/2
            y = pos.y() + self.marker_pixmap.height()/2
            
            self.selected_lat, self.selected_lon = self.pixel_to_latlon(x, y)
            
            # Update coordinates display
            self.coords_label.setText(f"Dragging: {self.selected_lat:.6f}, {self.selected_lon:.6f}")
            
            # Find nearest manhole
            self.find_and_select_nearest_manhole()
    
    def on_marker_drag_end(self):
        """When marker dragging ends"""
        self.dragging_marker = False
        self.map_view.setCursor(Qt.ArrowCursor)
        
        # Final update after drag
        self.find_and_select_nearest_manhole()
        self.coords_label.setText(f"Selected: {self.selected_lat:.6f}, {self.selected_lon:.6f}")
    
    def find_and_select_nearest_manhole(self):
        """Find and select nearest manhole to current marker position"""
        nearest_manhole = None
        min_distance = float('inf')
        
        for manhole in self.nearby_manholes:
            mh_lat = manhole.get('lat')
            mh_lon = manhole.get('lon')
            if mh_lat is not None and mh_lon is not None:
                distance = self.calculate_distance(self.selected_lat, self.selected_lon, mh_lat, mh_lon)
                if distance < min_distance:
                    min_distance = distance
                    nearest_manhole = manhole
        
        # Update UI
        if nearest_manhole and min_distance < 30:  # Within 30m
            self.selected_manhole_id = nearest_manhole.get('id')
            self.selected_info_label.setText(
                f"📌 Selected Manhole:\n"
                f"🔢 ID: {nearest_manhole.get('id')}\n"
                f"📍 Location: {self.selected_lat:.6f}, {self.selected_lon:.6f}\n"
                f"📏 Distance: {min_distance:.1f}m"
            )
        else:
            self.selected_manhole_id = None
            self.selected_info_label.setText(
                f"📍 Selected Location:\n"
                f"🌐 Coordinates: {self.selected_lat:.6f}, {self.selected_lon:.6f}\n"
                f"⚠️ No nearby manhole found\n"
                f"✏️ Enter manhole ID manually"
            )
        
        self.confirm_btn.setEnabled(True)
    
    def add_instructions_overlay(self):
        """Add interactive instructions overlay"""
        instructions = QGraphicsRectItem(0, 0, 300, 120)
        instructions.setBrush(QBrush(QColor(0, 0, 0, 180)))
        instructions.setPen(QPen(Qt.NoPen))
        instructions.setPos(10, 10)
        instructions.setZValue(1000)
        instructions.setData(0, "instructions")
        
        self.instruction_text = QGraphicsTextItem()
        self.instruction_text.setHtml("""
            <div style='color: white; padding: 10px;'>
                <h3 style='margin: 0; color: #4285F4;'>📌 HOW TO USE:</h3>
                <ul style='margin: 5px 0; padding-left: 15px;'>
                    <li>Drag the <b>blue pointer</b> to select location</li>
                    <li>Use <b>mouse wheel</b> or <b>+/- buttons</b> to zoom</li>
                    <li><b>Pinch</b> on touchscreen to zoom</li>
                    <li>Click manhole from list or enter manually</li>
                </ul>
                <p style='margin: 5px 0; font-size: 12px; color: #aaa;'>
                    Click anywhere to hide this message
                </p>
            </div>
        """)
        self.instruction_text.setPos(15, 15)
        self.instruction_text.setTextWidth(270)
        self.instruction_text.setTextInteractionFlags(Qt.NoTextInteraction)
        
        # Make instructions clickable to hide
        instructions.setFlag(QGraphicsRectItem.ItemIsSelectable)
        
        self.map_scene.addItem(instructions)
        self.map_scene.addItem(self.instruction_text)
        
        # Store references
        self.instructions_item = instructions
    
    def zoom_in(self):
        """Zoom in"""
        self.zoom = min(self.zoom + 1, 20)
        self.zoom_label.setText(f"Zoom: {self.zoom}")
        self.load_map_image()
    
    def zoom_out(self):
        """Zoom out"""
        self.zoom = max(self.zoom - 1, 10)
        self.zoom_label.setText(f"Zoom: {self.zoom}")
        self.load_map_image()
    
    def set_style(self, index):
        """Set map style"""
        self.current_style_index = index
        self.load_map_image()
    
    def center_on_current(self):
        """Center map on current GPS location"""
        self.selected_lat = self.center_lat
        self.selected_lon = self.center_lon
        self.center_marker()
        self.find_and_select_nearest_manhole()
        self.coords_label.setText("Centered on current location")
    
    def load_map_image(self):
        """Load Mapbox static map image with proper error handling"""
        try:
            logger.info("[MAPBOX] Starting map load...")
            
            # Calculate bounds for the map
            width = 1280
            height = 960
            
            # Get current style
            current_style = self.map_styles[self.current_style_index]
            style_url = current_style["url"]
            
            # Extract style ID from URL - handle both formats
            style_parts = style_url.split("/")
            if len(style_parts) >= 5:
                # Extract username and style ID
                username = style_parts[-2] if len(style_parts) >= 5 else "mapbox"
                style_id = style_parts[-1] if style_parts[-1] else "streets-v11"
                style_path = f"{username}/{style_id}"
            else:
                # Fallback to default style
                style_path = "mapbox/streets-v11"
            
            logger.info(f"[MAPBOX] Using style: {current_style['name']} ({style_path})")
            logger.info(f"[MAPBOX] Coordinates: {self.center_lon:.6f}, {self.center_lat:.6f}, Zoom: {self.zoom}")
            
            # Build Mapbox Static Images API URL (correct format)
            # Format: https://api.mapbox.com/styles/v1/{username}/{style_id}/static/{lon},{lat},{zoom}/{width}x{height}
            base_url = "https://api.mapbox.com/styles/v1"
            
            # Correct URL format
            url = f"{base_url}/{style_path}/static/"
            url += f"{self.center_lon},{self.center_lat},{self.zoom}"
            url += f"/{width}x{height}"
            
            # Add parameters
            params = {
                'access_token': self.mapbox_token,
                'attribution': 'false',
                'logo': 'false'
            }
            
            # Construct URL with parameters
            url_with_params = f"{url}?access_token={self.mapbox_token}"
            
            logger.info(f"[MAPBOX] Request URL: {url_with_params[:150]}...")
            
            # Add headers to mimic browser request
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            
            # Download map image with timeout
            try:
                response = requests.get(url_with_params, timeout=30, headers=headers)
                logger.info(f"[MAPBOX] Response status: {response.status_code}")
                
                if response.status_code == 200:
                    # Check if response is actually an image
                    content_type = response.headers.get('content-type', '')
                    if 'image' not in content_type.lower():
                        # Try to read error message from response
                        error_text = response.text[:200] if response.text else "No error text"
                        logger.error(f"[MAPBOX] Server returned non-image: {content_type}")
                        logger.error(f"[MAPBOX] Error response: {error_text}")
                        
                        # Try alternative style
                        self.show_error_map(f"Server returned non-image: {content_type[:50]}")
                        return
                    
                    # Load the image
                    self.map_pixmap = QPixmap()
                    success = self.map_pixmap.loadFromData(response.content)
                    
                    if success:
                        logger.info(f"[MAPBOX] Pixmap loaded: {self.map_pixmap.width()}x{self.map_pixmap.height()}")
                        
                        # Clear scene and add map
                        self.map_scene.clear()
                        self.map_item = self.map_scene.addPixmap(self.map_pixmap)
                        
                        # Set scene rect to match pixmap
                        self.map_scene.setSceneRect(self.map_item.boundingRect())
                        
                        # Recreate marker and instructions
                        self.create_draggable_marker()
                        self.add_instructions_overlay()
                        
                        # Add manhole markers
                        self.add_manhole_markers()
                        
                        # Update view
                        self.map_view.setSceneRect(self.map_item.boundingRect())
                        self.map_view.fitInView(self.map_item, Qt.KeepAspectRatio)
                        self.map_view.centerOn(self.map_item)
                        
                        # Force update
                        self.map_view.update()
                        
                        logger.info("[MAPBOX] Map loaded and displayed successfully")
                    else:
                        logger.error("[MAPBOX] Failed to load pixmap from response data")
                        self.show_error_map("Failed to create image from map data")
                elif response.status_code == 401:
                    logger.error("[MAPBOX] Authentication error - check Mapbox token")
                    self.show_error_map("Authentication failed. Check Mapbox token.")
                elif response.status_code == 403:
                    logger.error("[MAPBOX] Forbidden - token may be invalid or have no permissions")
                    self.show_error_map("Access forbidden. Check token permissions.")
                elif response.status_code == 404:
                    logger.error("[MAPBOX] Not found - style may not exist")
                    self.show_error_map("Map style not found.")
                else:
                    logger.error(f"[MAPBOX] HTTP Error {response.status_code}")
                    self.show_error_map(f"Map server error: HTTP {response.status_code}")
                    
            except requests.exceptions.Timeout:
                logger.error("[MAPBOX] Request timeout")
                self.show_error_map("Request timeout - check internet connection")
            except requests.exceptions.ConnectionError:
                logger.error("[MAPBOX] Connection error")
                self.show_error_map("Connection error - check internet connection")
            except Exception as e:
                logger.error(f"[MAPBOX] Error loading map: {str(e)}")
                logger.error(traceback.format_exc())
                self.show_error_map(f"Error: {str(e)[:100]}")
                
        except Exception as e:
            logger.error(f"[MAPBOX] Unexpected error in load_map_image: {str(e)}")
            logger.error(traceback.format_exc())
            self.show_error_map(f"Unexpected error: {str(e)[:100]}")
    
    def test_mapbox_connection(self):
        """Test Mapbox connection"""
        try:
            # Test URL
            test_url = f"https://api.mapbox.com/styles/v1/mapbox/streets-v11/static/78.3711,17.4569,15/400x300?access_token={self.mapbox_token}"
            
            logger.info(f"[MAPBOX TEST] Testing connection with URL: {test_url[:100]}...")
            
            response = requests.get(test_url, timeout=10)
            
            if response.status_code == 200:
                content_type = response.headers.get('content-type', '')
                if 'image' in content_type:
                    QMessageBox.information(self, "Mapbox Test", "✅ Mapbox connection successful!")
                    logger.info("[MAPBOX TEST] Connection successful")
                else:
                    QMessageBox.warning(self, "Mapbox Test", 
                                      f"⚠️ Got response but not an image\nContent-Type: {content_type}")
                    logger.warning(f"[MAPBOX TEST] Not an image: {content_type}")
            else:
                QMessageBox.critical(self, "Mapbox Test", 
                                   f"❌ Mapbox connection failed\nHTTP {response.status_code}\nResponse: {response.text[:200]}")
                logger.error(f"[MAPBOX TEST] Failed: HTTP {response.status_code}")
                
        except Exception as e:
            QMessageBox.critical(self, "Mapbox Test", 
                               f"❌ Mapbox test error: {str(e)}")
            logger.error(f"[MAPBOX TEST] Error: {e}")
    
    def add_manhole_markers(self):
        """Add markers for nearby manholes with proper scaling"""
        if not self.map_pixmap:
            return
            
        map_width = self.map_pixmap.width()
        base_marker_size = max(15, min(30, int(map_width * 0.012)))  # Scale with map
        
        for manhole in self.nearby_manholes[:20]:
            mh_id = manhole.get('id', 'Unknown')
            mh_lat = manhole.get('lat')
            mh_lon = manhole.get('lon')
            
            if mh_lat is None or mh_lon is None:
                continue
            
            # Convert lat/lon to pixel coordinates
            x, y = self.latlon_to_pixel(mh_lat, mh_lon)
            
            # Create modern marker with shadow
            marker_size = base_marker_size
            marker = QGraphicsEllipseItem(x - marker_size//2, y - marker_size//2, 
                                         marker_size, marker_size)
            
            # Gradient fill
            gradient = QRadialGradient(x, y, marker_size)
            gradient.setColorAt(0, QColor(76, 175, 80, 220))
            gradient.setColorAt(1, QColor(56, 142, 60, 180))
            marker.setBrush(QBrush(gradient))
            
            marker.setPen(QPen(Qt.white, max(1, int(marker_size/10))))
            marker.setToolTip(f"📌 Manhole: {mh_id}\nClick to select")
            marker.setData(0, mh_id)
            marker.setData(1, mh_lat)
            marker.setData(2, mh_lon)
            
            # Make marker clickable
            marker.setFlag(QGraphicsEllipseItem.ItemIsSelectable)
            
            self.map_scene.addItem(marker)
    
    def latlon_to_pixel(self, lat, lon):
        """Convert latitude/longitude to pixel coordinates on the map"""
        if not self.map_pixmap:
            return 0, 0
            
        map_width = self.map_pixmap.width()
        map_height = self.map_pixmap.height()
        
        # Calculate pixel coordinates based on zoom level
        zoom_factor = 2 ** (self.zoom - 15)
        x = map_width / 2 + (lon - self.center_lon) * 100000 * zoom_factor
        y = map_height / 2 - (lat - self.center_lat) * 100000 * zoom_factor
        
        # Clamp to map bounds
        x = max(0, min(x, map_width))
        y = max(0, min(y, map_height))
        
        return x, y
    
    def pixel_to_latlon(self, x, y):
        """Convert pixel coordinates to latitude/longitude"""
        if not self.map_pixmap:
            return self.center_lat, self.center_lon
            
        map_width = self.map_pixmap.width()
        map_height = self.map_pixmap.height()
        
        # Calculate lat/lon based on zoom level
        zoom_factor = 2 ** (self.zoom - 15)
        lon = self.center_lon + (x - map_width/2) / (100000 * zoom_factor)
        lat = self.center_lat - (y - map_height/2) / (100000 * zoom_factor)
        
        return lat, lon
    
    def update_nearby_display(self):
        """Update the nearby manholes display with modern cards"""
        # Clear existing
        while self.nearby_layout.count():
            child = self.nearby_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        if self.nearby_manholes:
            for i, manhole in enumerate(self.nearby_manholes[:10]):
                mh_id = manhole.get('id', 'Unknown')
                distance = manhole.get('distance', 0)
                
                # Create manhole card
                card = QWidget()
                card.setFixedHeight(70)
                card.setStyleSheet(f"""
                    QWidget {{
                        background-color: {'#404040' if i % 2 == 0 else '#363636'};
                        border-radius: 8px;
                        border-left: 4px solid #4285F4;
                    }}
                    QWidget:hover {{
                        background-color: #505050;
                        border-left: 4px solid #34A853;
                    }}
                """)
                
                card_layout = QHBoxLayout(card)
                card_layout.setContentsMargins(15, 10, 15, 10)
                
                # Icon
                icon = QLabel("🕳️")
                icon.setStyleSheet("font-size: 24px;")
                card_layout.addWidget(icon)
                
                # Info
                info_widget = QWidget()
                info_layout = QVBoxLayout(info_widget)
                info_layout.setSpacing(2)
                
                id_label = QLabel(f"<b>{mh_id}</b>")
                id_label.setStyleSheet("color: white; font-size: 14px;")
                info_layout.addWidget(id_label)
                
                dist_label = QLabel(f"📏 {distance:.1f}m away")
                dist_label.setStyleSheet("color: #aaa; font-size: 12px;")
                info_layout.addWidget(dist_label)
                
                card_layout.addWidget(info_widget, 1)
                
                # Select button
                select_btn = QPushButton("Select")
                select_btn.setFixedSize(80, 35)
                select_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #4285F4;
                        color: white;
                        font-weight: bold;
                        border-radius: 6px;
                        border: none;
                    }
                    QPushButton:hover {
                        background-color: #3367D6;
                    }
                """)
                select_btn.clicked.connect(lambda checked, mh_id=mh_id: self.select_manhole_from_list(mh_id))
                card_layout.addWidget(select_btn)
                
                self.nearby_layout.addWidget(card)
            
            self.nearby_layout.addStretch()
        else:
            empty_label = QLabel("No manholes found nearby")
            empty_label.setStyleSheet("""
                QLabel {
                    color: #888;
                    font-size: 14px;
                    font-style: italic;
                    padding: 20px;
                    text-align: center;
                }
            """)
            empty_label.setAlignment(Qt.AlignCenter)
            self.nearby_layout.addWidget(empty_label)
    
    def select_manhole_from_list(self, manhole_id):
        """Select manhole from the list"""
        for manhole in manholes:
            if manhole.get('id') == manhole_id:
                self.selected_lat = manhole.get('lat')
                self.selected_lon = manhole.get('lon')
                self.selected_manhole_id = manhole_id
                
                # Move marker to selected location
                if self.map_pixmap:
                    x, y = self.latlon_to_pixel(self.selected_lat, self.selected_lon)
                    self.marker_item.setPos(x - self.marker_pixmap.width()/2, 
                                           y - self.marker_pixmap.height()/2)
                
                # Update display
                distance = self.calculate_distance(self.center_lat, self.center_lon, self.selected_lat, self.selected_lon)
                self.selected_info_label.setText(
                    f"📌 Selected Manhole:\n"
                    f"🔢 ID: {manhole_id}\n"
                    f"📍 Location: {self.selected_lat:.6f}, {self.selected_lon:.6f}\n"
                    f"📏 Distance from current: {distance:.1f}m"
                )
                
                self.confirm_btn.setEnabled(True)
                self.coords_label.setText(f"Selected: {self.selected_lat:.6f}, {self.selected_lon:.6f}")
                break

    def show_error_map(self, custom_message=None):
        """Show error message when map fails to load"""
        self.map_scene.clear()
        
        if custom_message:
            error_text = f"Map Load Failed\n\n{custom_message}\n\nToken: {self.mapbox_token[:20]}..."
        else:
            error_text = "Map Load Failed\n\nCheck:\n1. Internet connection\n2. Mapbox token\n3. Token restrictions"
        
        error_label = self.map_scene.addText(error_text)
        error_label.setDefaultTextColor(Qt.red)
        error_label.setFont(QFont("Arial", 10, QFont.Bold))
        
        # Center the text
        rect = error_label.boundingRect()
        error_label.setPos(-rect.width()/2, -rect.height()/2)
        
        logger.error(f"[MAPBOX-ERROR] {custom_message}")
    
    def calculate_distance(self, lat1, lon1, lat2, lon2):
        """Calculate distance between two coordinates in meters"""
        from math import radians, cos, sin, asin, sqrt
        
        # Convert to radians
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        
        # Haversine formula
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        r = 6371000  # Earth radius in meters
        return c * r
    
    def use_manual_id(self):
        """Use manually entered ID"""
        manhole_id = self.manual_input.text().strip()
        if not manhole_id:
            QMessageBox.warning(self, "Input Required", "Please enter a manhole ID")
            return
        
        self.selected_manhole_id = manhole_id
        self.selected_info_label.setText(
            f"Manual Entry:\n"
            f"Manhole ID: {manhole_id}\n"
            f"Location: {self.selected_lat:.6f}, {self.selected_lon:.6f}"
        )
        
        self.confirm_btn.setEnabled(True)
    
    def confirm_selection(self):
        """Confirm the selection"""
        if not self.selected_manhole_id:
            # Ask for manual ID
            manhole_id, ok = QInputDialog.getText(
                self, 
                "Enter Manhole ID",
                f"Selected location:\nLat: {self.selected_lat:.6f}, Lon: {self.selected_lon:.6f}\n\nEnter manhole ID for this location:"
            )
            if ok and manhole_id:
                self.selected_manhole_id = manhole_id.strip()
            else:
                return
        
        # Emit selection
        self.location_selected.emit(self.selected_lat, self.selected_lon, self.selected_manhole_id)
        self.accept()


# --- Worker thread for camera feeds (with retry logic) ---
class CameraThread(QThread):
    frame_available = pyqtSignal(QImage, int)
    camera_error = pyqtSignal(str, int)
    camera_reconnecting = pyqtSignal(str, int)

    def __init__(self, parent=None, camera_index=0, camera_type="auto"):
        super().__init__(parent)
        self.running = True
        self.last_frame = None
        self.cap = None
        self.camera_index = camera_index
        self.camera_type = camera_type  # "rtsp", "usb", or "auto"
        self.target_width = 1280
        self.target_height = 720
        
        # Camera assignments based on index and type
        if camera_type == "rtsp":
            self.is_rtsp = True
            self.is_usb = False
        elif camera_type == "usb":
            self.is_rtsp = False
            self.is_usb = True
        else:
            # Auto-detect based on index
            self.is_rtsp = (camera_index == 0)  # Camera 0 is RTSP
            self.is_usb = (camera_index == 1 or camera_index == 2)  # Camera 1 and 2 are USB

    def run(self):
        if self.is_rtsp:
            self.setup_rtsp_camera()
        else:
            self.setup_usb_camera()

    def setup_rtsp_camera(self):
        camera_urls = [
            "rtsp://admin:Anvi%40252525@192.168.1.67:554/stream2",
            "rtsp://admin:Anvi%40252525@192.168.1.68:554/stream2"
        ]
        
        # Use first RTSP URL for camera index 0, second for camera index 1
        rtsp_index = min(self.camera_index, len(camera_urls) - 1)
        current_url = camera_urls[rtsp_index]
        
        logger.info(f"[CAMERA-THREAD {self.camera_index}] Setting up RTSP camera: {current_url}")
        
        gst_pipeline = (
            f"rtspsrc location={current_url} latency=50 ! "
            "rtph264depay ! h264parse ! avdec_h264 ! "
            "videoconvert ! video/x-raw,format=BGR ! "
            "appsink drop=true sync=false"
        )

        while self.running:
            if not self.cap or not self.cap.isOpened():
                self.camera_reconnecting.emit(f"Connecting to RTSP camera {self.camera_index + 1}...", self.camera_index)
                logger.info(f"[CAMERA-THREAD {self.camera_index}] Opening RTSP camera...")
                self.cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)
                if not self.cap.isOpened():
                    self.camera_error.emit(f"Failed to open RTSP camera {self.camera_index + 1}", self.camera_index)
                    logger.error(f"[CAMERA-THREAD {self.camera_index}] Failed to open RTSP. Retrying in 5s.")
                    time.sleep(5)
                    continue
                else:
                    logger.info(f"[CAMERA-THREAD {self.camera_index}] RTSP camera connected.")
                    self.camera_reconnecting.emit(f"RTSP Camera {self.camera_index + 1} connected", self.camera_index)

            self.read_frames()

    def setup_usb_camera(self):
        # Map camera indices to USB device paths
        # Camera 1 in GUI -> /dev/video2 (secondary USB camera)
        # Camera 2 in GUI -> /dev/video0 (pipe inspection camera)
        usb_device_mapping = {
            1: 0,  # Camera 1 in GUI -> /dev/video2
            2: 2   # Camera 2 in GUI (pipe inspection) -> /dev/video0
        }
        
        # Get the actual USB device index
        usb_device_index = usb_device_mapping.get(self.camera_index, self.camera_index)
        
        while self.running:
            if not self.cap or not self.cap.isOpened():
                camera_name = "Pipe Inspection" if self.camera_index == 0 else "USB Secondary"
                self.camera_reconnecting.emit(f"Connecting to {camera_name} camera...", self.camera_index)
                logger.info(f"[CAMERA-THREAD {self.camera_index}] Opening USB camera /dev/video{usb_device_index}")
                
                # Try different backends
                backends = [cv2.CAP_V4L2, cv2.CAP_ANY]
                
                for backend in backends:
                    self.cap = cv2.VideoCapture(usb_device_index, backend)
                    if self.cap.isOpened():
                        # Set camera properties
                        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.target_width)
                        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.target_height)
                        self.cap.set(cv2.CAP_PROP_FPS, 30)
                        
                        # Log actual properties
                        actual_width = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                        actual_height = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
                        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
                        
                        logger.info(f"[CAMERA-THREAD {self.camera_index}] USB camera on /dev/video{usb_device_index}: {actual_width}x{actual_height} @ {actual_fps}fps")
                        
                        self.camera_reconnecting.emit(f"{camera_name} Camera connected", self.camera_index)
                        break
                    else:
                        self.cap = None
                
                if not self.cap or not self.cap.isOpened():
                    self.camera_error.emit(f"Failed to open USB camera /dev/video{usb_device_index}", self.camera_index)
                    logger.error(f"[CAMERA-THREAD {self.camera_index}] Failed to open USB camera. Retrying in 5s.")
                    time.sleep(5)
                    continue

            self.read_frames()

    def read_frames(self):
        while self.running and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret and frame is not None:
                frame = cv2.resize(frame, (self.target_width, self.target_height))
                self.last_frame = frame
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = frame_rgb.shape
                bytes_per_line = ch * w
                q_img = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
                self.frame_available.emit(q_img, self.camera_index)
            else:
                logger.warning(f"[CAMERA-THREAD {self.camera_index}] Failed to read frame")
                if self.cap.isOpened():
                    self.cap.release()
                if self.camera_index == 2:
                    self.camera_reconnecting.emit("Pipe inspection camera disconnected", self.camera_index)
                else:
                    self.camera_reconnecting.emit(f"USB Camera disconnected", self.camera_index)
                break
            time.sleep(0.033)

        if self.cap and self.cap.isOpened():
            self.cap.release()
        logger.info(f"[CAMERA-THREAD {self.camera_index}] Camera thread stopped.")

    def get_last_frame(self):
        return self.last_frame

    def stop(self):
        self.running = False
        self.wait()


class SmartWasteDashboard(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("SHUDH HMI Dashboard")
        self.setWindowFlags(Qt.FramelessWindowHint)
        
        self.screen = QApplication.primaryScreen()
        self.screen_rect = self.screen.availableGeometry()
        self.screen_width = self.screen_rect.width()
        self.screen_height = self.screen_rect.height()
        
        self.is_minimized = False
        self.normal_geometry = None

        # Initialize uploader
        self.uploader = Uploader()
        self.uploader.set_status_callback(self.handle_upload_status)
        logger.info("[INIT] Uploader initialized.")

        self.current_before_capture_path = None
        self.current_after_capture_path = None
        self.current_operation_id = None

        # Variables to store operation start and end times
        self.operation_start_time = None
        self.operation_end_time = None

        # Timer variables
        self.timer_start_time = None
        self.timer_running = False
        self.blink_state = True

        # GPS variables
        self.gps_port = '/dev/ttyUSB1'
        self.gps_baudrate = 115200
        self.gps_serial = None
        self.captured_latitude = 0.0
        self.captured_longitude = 0.0
        self.gps_fix = False

        # Camera variables
        self.camera_threads = []
        self.current_main_camera = 0

        # Manhole selection variable
        self.selected_manhole_id = None

        # Upload flags
        self.upload_attempted = False
        self.upload_in_progress = False

        # Load cell measurements
        self.loadcell_before_depth = None
        self.loadcell_after_depth = None
     
        self.elapsed_label = None

        # Initialize UI
        self.init_ui()

        # Initialize camera status labels
        self.status_labels["camera1"].setText("CAM1: RTSP ✗")
        self.status_labels["camera1"].setStyleSheet("""
            QLabel {
                color: white;
                background-color: #e74c3c;
                padding: 5px 15px;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
        """)
        self.status_labels["camera2"].setText("CAM2: USB ✗ (/dev/video2)")
        self.status_labels["camera2"].setStyleSheet("""
            QLabel {
                color: white;
                background-color: #e74c3c;
                padding: 5px 15px;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }
        """)

        # Load manholes CSV
        load_manholes()

        self.init_camera_threads()
        self.init_gps()

        # Initialize timers
        self.init_timers()

    def check_camera_availability(self):
        """Check which cameras are available and working"""
        logger.info("[CAMERA-CHECK] Checking camera availability...")
        
        available_cameras = []
        
        # Check first 5 video devices
        for i in range(5):
            device_path = f"/dev/video{i}"
            if os.path.exists(device_path):
                cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
                if not cap.isOpened():
                    cap = cv2.VideoCapture(i, cv2.CAP_ANY)
                
                if cap.isOpened():
                    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    
                    logger.info(f"[CAMERA-CHECK] /dev/video{i}: Available - {width}x{height} @ {fps:.1f}fps")
                    available_cameras.append({
                        'index': i,
                        'device': f"/dev/video{i}",
                        'width': width,
                        'height': height,
                        'fps': fps,
                        'cap': cap
                    })
                else:
                    logger.info(f"[CAMERA-CHECK] /dev/video{i}: Exists but not accessible")
                
                if 'cap' in locals() and cap.isOpened():
                    cap.release()
        
        logger.info(f"[CAMERA-CHECK] Found {len(available_cameras)} accessible cameras")
        return available_cameras

    def handle_upload_status(self, operation_id, status, message, details):
        """Handle upload status updates from Uploader"""
        logger.info(f"[UPLOAD STATUS] {operation_id}: {status} - {message}")
        
        # Update UI status
        if status == UploadStatus.SUCCESS.value:
            self.status_labels["upload"].setText("Upload status: Success")
            self.status_labels["upload"].setStyleSheet("""
                QLabel {
                    color: white;
                    background-color: #2ecc71;
                    padding: 5px 15px;
                    border-radius: 5px;
                    font-size: 14px;
                    font-weight: bold;
                }
            """)
        elif status == UploadStatus.FAILED.value:
            self.status_labels["upload"].setText("Upload status: Failed")
            self.status_labels["upload"].setStyleSheet("""
                QLabel {
                    color: white;
                    background-color: #e74c3c;
                    padding: 5px 15px;
                    border-radius: 5px;
                    font-size: 14px;
                    font-weight: bold;
                }
            """)
        elif status == UploadStatus.PROCESSING.value:
            self.status_labels["upload"].setText("Upload status: Processing...")
        elif status == UploadStatus.AZURE_UPLOADING.value:
            self.status_labels["upload"].setText("Upload status: Uploading to Azure...")
        elif status == UploadStatus.QUEUED.value:
            self.status_labels["upload"].setText("Upload status: Queued")

    def init_timers(self):
        # Clock timer
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)

        # GPS timer
        self.gps_timer = QTimer(self)
        self.gps_timer.timeout.connect(self.update_gps)
        self.gps_timer.start(1000)

        # Timer for blinking effect
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.update_timer_display)
        self.blink_timer.start(500)

        # Upload status check timer
        self.upload_status_timer = QTimer(self)
        self.upload_status_timer.timeout.connect(self.check_upload_status)
        self.upload_status_timer.start(5000)

    def check_upload_status(self):
        """Check and update upload status periodically"""
        try:
            stats = self.uploader.get_stats()
            if stats['queued_now'] > 0:
                status_text = f"Upload: {stats['queued_now']} pending"
            else:
                status_text = f"Upload: {stats['successful']} success, {stats['failed']} failed"
            
            # Update status label if not already showing detailed status
            current_text = self.status_labels["upload"].text()
            if not any(status in current_text for status in ["Success", "Failed", "Processing", "Uploading"]):
                self.status_labels["upload"].setText(f"Upload status: {status_text}")
        except Exception as e:
            logger.error(f"[STATUS CHECK ERROR] {e}")

    def init_camera_threads(self):
        # Initialize two camera threads for main operation (no pipe inspection)
        # Camera 0: RTSP main camera
        camera0_thread = CameraThread(camera_index=0, camera_type="rtsp")
        camera0_thread.frame_available.connect(self.process_camera_frame)
        camera0_thread.camera_error.connect(self.handle_camera_error)
        camera0_thread.camera_reconnecting.connect(self.handle_camera_reconnecting)
        camera0_thread.start()
        self.camera_threads.append(camera0_thread)
        
        # Camera 1: USB secondary camera (/dev/video2)
        camera1_thread = CameraThread(camera_index=1, camera_type="usb")
        camera1_thread.frame_available.connect(self.process_camera_frame)
        camera1_thread.camera_error.connect(self.handle_camera_error)
        camera1_thread.camera_reconnecting.connect(self.handle_camera_reconnecting)
        camera1_thread.start()
        self.camera_threads.append(camera1_thread)
        
        self.update_camera_labels()
        
        # Update status labels to show proper device mapping
        self.status_labels["camera1"].setText("CAM1: RTSP ↻")
        self.status_labels["camera2"].setText("CAM2: USB ↻ (/dev/video2)")

    def process_camera_frame(self, q_img, camera_index):
        if camera_index == 0:  # RTSP camera (main or secondary)
            if self.current_main_camera == 0:
                target_label = self.camera_label
                display_name = "Main"
            else:
                target_label = self.secondary_camera_label
                display_name = "Secondary"
            
            camera_type = "RTSP Camera"
            status_key = "camera1"
            status_text = "CAM1: RTSP ✓"
            color = "#3498db"
            
        elif camera_index == 1:  # USB camera (main or secondary)
            if self.current_main_camera == 1:
                target_label = self.camera_label
                display_name = "Main"
            else:
                target_label = self.secondary_camera_label
                display_name = "Secondary"
            
            camera_type = "USB Camera (/dev/video2)"
            status_key = "camera2"
            status_text = "CAM2: USB ✓ (/dev/video2)"
            color = "#3498db"
        
        # Process frame for display
        scaled_pixmap = QPixmap.fromImage(q_img).scaled(
            target_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        
        final_pixmap = QPixmap(scaled_pixmap.size())
        final_pixmap.fill(Qt.black)
        
        painter = QPainter(final_pixmap)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        
        x = (final_pixmap.width() - scaled_pixmap.width()) // 2
        y = (final_pixmap.height() - scaled_pixmap.height()) // 2
        painter.drawPixmap(x, y, scaled_pixmap)
        
        # Update camera status when frame is received
        self.status_labels[status_key].setText(status_text)
        self.status_labels[status_key].setStyleSheet(f"""
            QLabel {{
                color: white;
                background-color: {color};
                padding: 5px 15px;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }}
        """)
        
        # Draw camera info on the image
        painter.setBrush(QBrush(QColor(0, 0, 0, 150)))
        painter.setPen(Qt.NoPen)
        painter.drawRect(0, 0, final_pixmap.width(), 30)
        
        painter.setPen(QPen(Qt.white))
        painter.setFont(QFont("Arial", 10, QFont.Bold))
        info_text = f"{camera_type} - {display_name} View"
        painter.drawText(10, 20, info_text)
        
        painter.end()
        
        target_label.setPixmap(final_pixmap)

    def switch_cameras(self):
        self.current_main_camera = 1 - self.current_main_camera
        self.update_camera_labels()
        QTimer.singleShot(100, self.refresh_camera_displays)

    def update_camera_labels(self):
        # Reset camera statuses when switching
        if self.current_main_camera == 0:
            # RTSP is main, USB is secondary
            self.camera_label.setText("RTSP Camera (Main)\nInitializing...")
            self.secondary_camera_label.setText("USB Camera (Secondary)\nInitializing...")
            
            # Update status labels
            self.status_labels["camera1"].setText("CAM1: RTSP ↻")
            self.status_labels["camera1"].setStyleSheet("""
                QLabel {
                    color: white;
                    background-color: #f39c12;
                    padding: 5px 15px;
                    border-radius: 5px;
                    font-size: 14px;
                    font-weight: bold;
                }
            """)
            self.status_labels["camera2"].setText("CAM2: USB ↻ (/dev/video2)")
            self.status_labels["camera2"].setStyleSheet("""
                QLabel {
                    color: white;
                    background-color: #f39c12;
                    padding: 5px 15px;
                    border-radius: 5px;
                    font-size: 14px;
                    font-weight: bold;
                }
            """)
        else:
            # USB is main, RTSP is secondary
            self.camera_label.setText("USB Camera (Main)\nInitializing...")
            self.secondary_camera_label.setText("RTSP Camera (Secondary)\nInitializing...")
            
            # Update status labels (CAM1 always RTSP, CAM2 always USB regardless of which is main)
            self.status_labels["camera1"].setText("CAM1: RTSP ↻")
            self.status_labels["camera1"].setStyleSheet("""
                QLabel {
                    color: white;
                    background-color: #f39c12;
                    padding: 5px 15px;
                    border-radius: 5px;
                    font-size: 14px;
                    font-weight: bold;
                }
            """)
            self.status_labels["camera2"].setText("CAM2: USB ↻ (/dev/video2)")
            self.status_labels["camera2"].setStyleSheet("""
                QLabel {
                    color: white;
                    background-color: #f39c12;
                    padding: 5px 15px;
                    border-radius: 5px;
                    font-size: 14px;
                    font-weight: bold;
                }
            """)

    def refresh_camera_displays(self):
        for i, thread in enumerate(self.camera_threads):
            if thread.last_frame is not None:
                frame_rgb = cv2.cvtColor(thread.last_frame, cv2.COLOR_BGR2RGB)
                h, w, ch = frame_rgb.shape
                bytes_per_line = ch * w
                q_img = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
                self.process_camera_frame(q_img, i)

    def handle_camera_error(self, error_message, camera_index):
        logger.error(f"[CAMERA-{camera_index}] Camera error: {error_message}")
        
        # Update specific camera status
        if camera_index == 0:  # RTSP camera
            status_text = "CAM1: RTSP ✗"
            color = "#e74c3c"
            status_key = "camera1"
        elif camera_index == 1:  # USB camera
            status_text = "CAM2: USB ✗ (/dev/video2)"
            color = "#e74c3c"
            status_key = "camera2"
        
        # Update the specific camera status label if it exists
        if status_key in self.status_labels:
            self.status_labels[status_key].setText(status_text)
            self.status_labels[status_key].setStyleSheet(f"""
                QLabel {{
                    color: white;
                    background-color: {color};
                    padding: 5px 15px;
                    border-radius: 5px;
                    font-size: 14px;
                    font-weight: bold;
                }}
            """)
        
        # Update camera display label
        if camera_index == self.current_main_camera:
            self.camera_label.setText(f"Camera Error: {error_message}")
        elif camera_index == (1 - self.current_main_camera):
            self.secondary_camera_label.setText(f"Camera Error: {error_message}")

    def handle_camera_reconnecting(self, status_message, camera_index):
        logger.info(f"[CAMERA-{camera_index}] Camera status: {status_message}")
        
        # Update specific camera status
        if camera_index == 0:  # RTSP camera
            status_text = "CAM1: RTSP ↻"
            color = "#f39c12"
            status_key = "camera1"
        elif camera_index == 1:  # USB camera
            status_text = "CAM2: USB ↻ (/dev/video2)"
            color = "#f39c12"
            status_key = "camera2"
        
        # Update the specific camera status label if it exists
        if status_key in self.status_labels:
            self.status_labels[status_key].setText(status_text)
            self.status_labels[status_key].setStyleSheet(f"""
                QLabel {{
                    color: white;
                    background-color: {color};
                    padding: 5px 15px;
                    border-radius: 5px;
                    font-size: 14px;
                    font-weight: bold;
                }}
            """)
        
        # Update display label
        if camera_index == self.current_main_camera:
            self.camera_label.setText(status_message)
        elif camera_index == (1 - self.current_main_camera):
            self.secondary_camera_label.setText(status_message)

    def init_gps(self):
        if self.gps_serial and self.gps_serial.is_open:
            self.gps_serial.close()
            self.gps_serial = None
        try:
            self.gps_serial = serial.Serial(
                port=self.gps_port,
                baudrate=self.gps_baudrate,
                timeout=1
            )
            logger.info(f"[GPS] Connected to {self.gps_port}")
            self.status_labels["gps"].setText("GPS: Connected")
        except serial.SerialException as e:
            logger.error(f"[GPS ERROR] Could not connect to GPS: {str(e)}")
            self.status_labels["gps"].setText(f"GPS: Error - {str(e)}")
            self.gps_serial = None

    def parse_gps_data(self, line):
        if line.startswith('$GPGGA'):
            try:
                parts = line.split(',')
                if len(parts) > 6 and parts[6] != '0':
                    lat = parts[2]
                    lat_deg = float(lat[:2])
                    lat_min = float(lat[2:])
                    latitude = lat_deg + (lat_min / 60)
                    if parts[3] == 'S':
                        latitude *= -1

                    lon = parts[4]
                    lon_deg = float(lon[:3])
                    lon_min = float(lon[3:])
                    longitude = lon_deg + (lon_min / 60)
                    if parts[5] == 'W':
                        longitude *= -1

                    self.gps_fix = True
                    logger.info(f"[GPS] GPS fix obtained. Lat: {latitude:.6f}, Lon: {longitude:.6f}")
                    return latitude, longitude
            except (ValueError, IndexError) as e:
                logger.error(f"[GPS PARSE ERROR] {str(e)} for line: {line}")
        return None, None
        
    def update_gps(self):
        if self.gps_serial and self.gps_serial.is_open and self.gps_serial.in_waiting > 0:
            try:
                line = self.gps_serial.readline().decode('ascii', errors='ignore').strip()
                if line:
                    lat, lon = self.parse_gps_data(line)
                    if lat is not None and lon is not None:
                        self.captured_latitude = lat
                        self.captured_longitude = lon
                        self.status_labels["gps"].setText(f"GPS: Fix - {lat:.4f}, {lon:.4f}")
            except Exception as e:
                logger.error(f"[GPS READ ERROR] {str(e)}")
                self.status_labels["gps"].setText(f"GPS: Error - {str(e)}")
        elif not self.gps_serial or not self.gps_serial.is_open:
            logger.warning("[GPS] GPS serial port not open, attempting re-initialization.")
            self.status_labels["gps"].setText("GPS: Reconnecting...")
            self.init_gps()

    def get_current_location(self):
        return f"{self.captured_latitude:.6f},{self.captured_longitude:.6f}"

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.toggle_window_state()
            event.accept()
        elif event.key() == Qt.Key_F11:
            if self.isFullScreen():
                self.showNormal()
                logger.info("[WINDOW] Changed to windowed mode")
            else:
                self.showFullScreen()
                logger.info("[WINDOW] Changed to full screen mode")
            event.accept()
        else:
            super().keyPressEvent(event)

    def toggle_window_state(self):
        if self.isFullScreen():
            self.showMinimized()
            self.is_minimized = True
            logger.info("[WINDOW] Minimized from full screen")
        elif self.isMinimized():
            self.showFullScreen()
            self.is_minimized = False
            logger.info("[WINDOW] Restored to full screen from minimized state")
        else:
            self.showFullScreen()
            self.is_minimized = False
            logger.info("[WINDOW] Changed to full screen from windowed mode")

    def create_logo(self):
        """Create logo with fallback if image not found"""
        logo_label = QLabel()
        
        # Try to load logo image
        logo_path = "primary_white.png"
        if os.path.exists(logo_path):
            try:
                logo_pixmap = QPixmap(logo_path)
                # Scale to fit
                logo_pixmap = logo_pixmap.scaled(210, 210, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                logo_label.setPixmap(logo_pixmap)
                logo_label.setStyleSheet("background: transparent;")
                logger.info("[UI] Custom logo loaded successfully")
            except Exception as e:
                logger.error(f"[UI] Failed to load logo image: {e}")
                logo_label = self.create_fallback_logo()
        else:
            logger.info("[UI] Using fallback logo - add 'logo.png' for custom logo")
            logo_label = self.create_fallback_logo()
        
        logo_label.setAlignment(Qt.AlignCenter)
        return logo_label

    def create_fallback_logo(self):
        """Create a fallback logo when no image is available"""
        logo_label = QLabel()
        fallback_logo = QPixmap(60, 60)
        fallback_logo.fill(Qt.transparent)
        
        painter = QPainter(fallback_logo)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Draw blue circle background
        painter.setBrush(QBrush(QColor("#3498db")))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, 60, 60)
        
        # Draw white manhole symbol (Unicode: ⛁)
        painter.setPen(QPen(Qt.white, 2))
        painter.setFont(QFont("Arial", 24, QFont.Bold))
        painter.drawText(fallback_logo.rect(), Qt.AlignCenter, "⛁")
        
        painter.end()
        
        logo_label.setPixmap(fallback_logo)
        logo_label.setStyleSheet("background: transparent;")
        
        return logo_label

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Header with centered title
        header = QFrame()
        header.setFixedHeight(80)
        header.setStyleSheet("background-color: #34495e;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 10, 15, 10)
        header_layout.setSpacing(15)

        # Logo (left)
        logo_label = self.create_logo()
        header_layout.addWidget(logo_label, 0, Qt.AlignLeft | Qt.AlignVCenter)

        # Spacer to push title to center
        header_layout.addSpacerItem(QSpacerItem(20, 0, QSizePolicy.Expanding, QSizePolicy.Minimum))

        # Title (centered)
        title_widget = QWidget()
        title_widget.setStyleSheet("background: transparent;")
        title_layout = QVBoxLayout(title_widget)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(2)

        main_title = QLabel("SHUDH")
        main_title.setStyleSheet("""
            QLabel {
                color: #ecf0f1; 
                font-size: 32px; 
                font-weight: bold;
                background: transparent;
            }
        """)
        main_title.setAlignment(Qt.AlignCenter)
        
        sub_title = QLabel("powered by ANVI ROBOTICS")
        sub_title.setStyleSheet("""
            QLabel {
                color: #bdc3c7; 
                font-size: 16px; 
                font-weight: normal;
                background: transparent;
            }
        """)
        sub_title.setAlignment(Qt.AlignCenter)

        title_layout.addWidget(main_title)
        title_layout.addWidget(sub_title)
        
        header_layout.addWidget(title_widget, 0)

        # Spacer to balance the layout
        header_layout.addSpacerItem(QSpacerItem(20, 0, QSizePolicy.Expanding, QSizePolicy.Minimum))

        # Clock (right)
        clock_widget = QWidget()
        clock_widget.setFixedWidth(180)
        clock_widget.setStyleSheet("background: transparent;")
        clock_layout = QVBoxLayout(clock_widget)
        clock_layout.setContentsMargins(0, 5, 0, 5)
        clock_layout.setSpacing(2)

        self.date_label = QLabel(datetime.datetime.now().strftime("%Y-%m-%d"))
        self.date_label.setStyleSheet("""
            QLabel {
                color: #bdc3c7; 
                font-size: 14px; 
                font-weight: normal;
                background: transparent;
            }
        """)
        self.date_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        
        self.clock_label = QLabel(datetime.datetime.now().strftime("%H:%M:%S"))
        self.clock_label.setStyleSheet("""
            QLabel {
                color: #ecf0f1; 
                font-size: 20px; 
                font-weight: bold;
                background: transparent;
            }
        """)
        self.clock_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        clock_layout.addWidget(self.date_label)
        clock_layout.addWidget(self.clock_label)
        
        header_layout.addWidget(clock_widget, 0, Qt.AlignRight | Qt.AlignVCenter)

        main_layout.addWidget(header)

        # Main content area
        content_widget = QWidget()
        content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content_layout = QHBoxLayout(content_widget)
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(10)

        # Left panel - Main camera (70%)
        left_panel = QWidget()
        left_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        self.camera_label = QLabel()
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setStyleSheet("""
            QLabel {
                background: #1a1a1a;
                border: 3px solid #34495e;
                border-radius: 10px;
            }
        """)
        self.camera_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.camera_label.setMinimumSize(640, 480)
        left_layout.addWidget(self.camera_label)
        
        content_layout.addWidget(left_panel, 7)

        # Right panel - Controls (30%)
        right_panel = QWidget()
        right_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        # New LOAD CELL button at the top
        self.loadcell_btn = QPushButton("📏 MEASURE DEPTH")
        self.loadcell_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.loadcell_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF9800; 
                color: white; 
                border-radius: 10px; 
                font-weight: bold; 
                border: none; 
                font-size: 16px;
                min-height: 80px;
            }
            QPushButton:hover { background-color: #F57C00; }
        """)
        self.loadcell_btn.clicked.connect(self.open_loadcell_dialog)
        right_layout.addWidget(self.loadcell_btn)

        # Buttons container - REMOVED PIPE INSPECTION BUTTON
        buttons_widget = QWidget()
        buttons_widget.setFixedHeight(120)
        buttons_layout = QGridLayout(buttons_widget)
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(10)

        # Start Operation button - now spans 2 columns
        self.start_btn = QPushButton("START\nOPERATION")
        self.start_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #2ecc71; 
                color: white; 
                border-radius: 10px; 
                font-weight: bold; 
                border: none; 
                font-size: 18px;
                min-height: 100px;
            }
            QPushButton:hover { background-color: #27ae60; }
            QPushButton:disabled { background-color: #bdc3c7; }
        """)
        self.start_btn.clicked.connect(self.start_timer)
        buttons_layout.addWidget(self.start_btn, 0, 0, 1, 2)  # Span 2 columns

        # Stop Operation button - now spans 2 columns
        self.stop_btn = QPushButton("STOP\nOPERATION")
        self.stop_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c; 
                color: white; 
                border-radius: 10px; 
                font-weight: bold; 
                border: none; 
                font-size: 18px;
                min-height: 100px;
            }
            QPushButton:hover { background-color: #c0392b; }
            QPushButton:disabled { background-color: #bdc3c7; }
        """)
        self.stop_btn.clicked.connect(self.stop_timer)
        self.stop_btn.setEnabled(False)
        buttons_layout.addWidget(self.stop_btn, 0, 2, 1, 2)  # Span 2 columns

        right_layout.addWidget(buttons_widget)

        # Combined row for Timer and Switch Cameras
        timer_switch_row = QWidget()
        timer_switch_row.setFixedHeight(140)
        timer_switch_layout = QHBoxLayout(timer_switch_row)
        timer_switch_layout.setContentsMargins(0, 0, 0, 0)
        timer_switch_layout.setSpacing(10)

        # Timer container
        timer_container = QWidget()
        timer_container_layout = QVBoxLayout(timer_container)
        timer_container_layout.setContentsMargins(0, 0, 0, 0)
        timer_container_layout.setSpacing(2)

        timer_title = QLabel("OPERATION TIMER")
        timer_title.setStyleSheet("color: #ecf0f1; font-weight: bold; font-size: 14px; qproperty-alignment: 'AlignCenter';")
        timer_container_layout.addWidget(timer_title)

        self.timer_label = QLabel("00:00:00")
        self.timer_label.setStyleSheet("""
            QLabel { 
                color: #e74c3c; font-weight: bold; font-size: 28px; 
                qproperty-alignment: 'AlignCenter'; background-color: #2c3e50; 
                border-radius: 10px; border: 2px solid #7f8c8d;
                padding: 10px 5px;
            } 
        """)
        self.timer_label.setAlignment(Qt.AlignCenter)
        self.timer_label.setMinimumHeight(70)
        timer_container_layout.addWidget(self.timer_label)

        self.elapsed_label = QLabel("0 seconds")
        self.elapsed_label.setStyleSheet("color: #bdc3c7; font-size: 11px; qproperty-alignment: 'AlignCenter';")
        timer_container_layout.addWidget(self.elapsed_label)
        
        timer_switch_layout.addWidget(timer_container, 3) # Give timer more space

        # Switch camera button
        self.switch_cam_btn = QPushButton("SWITCH\nCAMERAS")
        self.switch_cam_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.switch_cam_btn.setStyleSheet("""
            QPushButton { 
                background-color: #9b59b6; color: white; border-radius: 10px; 
                font-weight: bold; border: none; font-size: 16px;
                min-height: 80px;
            } 
            QPushButton:hover { background-color: #8e44ad; }
            QPushButton:pressed { background-color: #6c3483; }
        """)
        self.switch_cam_btn.clicked.connect(self.switch_cameras)
        timer_switch_layout.addWidget(self.switch_cam_btn, 2)

        right_layout.addWidget(timer_switch_row)

        # PLC Progress Bar
        self.plc_progress = QProgressBar()
        self.plc_progress.setRange(0, 100)
        self.plc_progress.setValue(0)
        self.plc_progress.setFormat("Lever Progress: %p%")
        self.plc_progress.setFixedHeight(40)
        self.plc_progress.setStyleSheet("""
            QProgressBar {
                border: 2px solid #34495e;
                border-radius: 10px;
                background-color: #2c3e50;
                text-align: center;
                color: white;
                font-weight: bold;
                font-size: 13px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:0, stop:0 #3498db, stop:1 #2ecc71);
                border-radius: 8px;
            }
        """)
        right_layout.addWidget(self.plc_progress)


        # Secondary camera
        secondary_widget = QWidget()
        secondary_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        secondary_layout = QVBoxLayout(secondary_widget)
        secondary_layout.setContentsMargins(0, 0, 0, 0)

        self.secondary_camera_label = QLabel()
        self.secondary_camera_label.setAlignment(Qt.AlignCenter)
        self.secondary_camera_label.setStyleSheet("""
            QLabel {
                background: #1a1a1a;
                border: 3px solid #95a5a6;
                border-radius: 10px;
            }
        """)
        self.secondary_camera_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.secondary_camera_label.setMinimumSize(320, 240)
        secondary_layout.addWidget(self.secondary_camera_label)
        
        right_layout.addWidget(secondary_widget, 1)

        content_layout.addWidget(right_panel, 3)
        main_layout.addWidget(content_widget, 1)

        # Footer
        footer = QFrame()
        footer.setFixedHeight(40)
        footer.setStyleSheet("background-color: #2c3e50;")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 0, 10, 0)
        footer_layout.setSpacing(10)

        self.status_labels = {}

        self.add_status_label(footer_layout, "device", f"Device ID: {config.get('device_id', 'UNKNOWN')}", "#3498db")
        self.add_status_label(footer_layout, "gps", "GPS: Initializing...", "#f1c40f")
        # Replace single camera status with two separate ones
        self.add_status_label(footer_layout, "camera1", "CAM1: RTSP ✗", "#e74c3c")
        self.add_status_label(footer_layout, "camera2", "CAM2: USB ✗ (/dev/video2)", "#e74c3c")
        self.add_status_label(footer_layout, "manhole", "Manhole ID: N/A", "#9b59b6")
        self.add_status_label(footer_layout, "upload", "Upload status: Idle", "#7f8c8d")

        # Load cell status
        self.add_status_label(footer_layout, "loadcell", "Depth: --/-- cm", "#FF9800")

        # Keep backward compatibility - add old camera label but hide it
        self.status_labels["camera"] = QLabel("Camera: Initializing...")
        self.status_labels["camera"].setVisible(False)  # Hide the old label

        main_layout.addWidget(footer)

        self.update_camera_labels()

    def add_status_label(self, layout, key, text, color):
        label = QLabel(text)
        label.setStyleSheet(f"""
            QLabel {{
                color: white;
                background-color: {color};
                padding: 5px 15px;
                border-radius: 5px;
                font-size: 14px;
                font-weight: bold;
            }}
        """)
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label, 1)
        self.status_labels[key] = label

    def update_clock(self):
        now = datetime.datetime.now()
        self.clock_label.setText(now.strftime("%H:%M:%S"))
        
        # Update date label if date has changed
        current_date = now.strftime("%Y-%m-%d")
        if self.date_label.text() != current_date:
            self.date_label.setText(current_date)

    def update_timer_display(self):
        if self.timer_running:
            elapsed = datetime.datetime.now() - self.timer_start_time
            total_seconds = int(elapsed.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            timer_text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            self.timer_label.setText(timer_text)
            
            # Update elapsed time label if it exists
            if hasattr(self, 'elapsed_label') and self.elapsed_label:
                self.elapsed_label.setText(f"Elapsed Time: {total_seconds} seconds")
            
            self.blink_state = not self.blink_state
            if self.blink_state:
                self.timer_label.setStyleSheet("""
                    QLabel { 
                        color: #2ecc71; 
                        font-weight: bold; 
                        font-size: 28px; 
                        qproperty-alignment: 'AlignCenter'; 
                        background-color: #2c3e50; 
                        border-radius: 10px; 
                        padding: 10px 5px; 
                        border: 2px solid #2ecc71; 
                    } 
                """)
            else:
                self.timer_label.setStyleSheet("""
                    QLabel { 
                        color: #1a1a1a; 
                        font-weight: bold; 
                        font-size: 28px; 
                        qproperty-alignment: 'AlignCenter'; 
                        background-color: #2c3e50; 
                        border-radius: 10px; 
                        padding: 10px 5px; 
                        border: 2px solid #2ecc71; 
                    } 
                """)

    def update_finished_timer_display(self):
        if self.timer_start_time and self.operation_end_time:
            elapsed = self.operation_end_time - self.operation_start_time
            total_seconds = int(elapsed.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            timer_text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            self.timer_label.setText(timer_text)
            
            # Update elapsed time label if it exists
            if hasattr(self, 'elapsed_label') and self.elapsed_label:
                self.elapsed_label.setText(f"Total Operation Time: {total_seconds} seconds")
                
            self.timer_label.setStyleSheet("""
                QLabel { 
                    color: #2ecc71; 
                    font-weight: bold; 
                    font-size: 28px; 
                    qproperty-alignment: 'AlignCenter'; 
                    background-color: #2c3e50; 
                    border-radius: 10px; 
                    padding: 10px 5px; 
                    border: 2px solid #2ecc71; 
                } 
            """)
        else:
            self.timer_label.setText("00:00:00")
            
            # Update elapsed time label if it exists
            if hasattr(self, 'elapsed_label') and self.elapsed_label:
                self.elapsed_label.setText("Elapsed Time: 0 seconds")
                
            self.timer_label.setStyleSheet("""
                QLabel { 
                    color: #e74c3c; 
                    font-weight: bold; 
                    font-size: 28px; 
                    qproperty-alignment: 'AlignCenter'; 
                    background-color: #2c3e50; 
                    border-radius: 10px; 
                    padding: 10px 5px; 
                    border: 2px solid #7f8c8d; 
                } 
            """)
    def haversine(self, lat1, lon1, lat2, lon2):
        R = 6371e3
        p1, p2 = math.radians(lat1), math.radians(lon1)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        
        a = sin(dlat/2)**2 + cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

    def find_nearby_manholes(self, lat, lon, radius=50):
        """Find manholes within given radius"""
        logger.info(f"[MANHOLE-SEARCH] Searching within {radius}m of ({lat:.6f}, {lon:.6f})")
        logger.info(f"[MANHOLE-SEARCH] Total manholes loaded: {len(manholes)}")
        
        nearby = []
        for mh in manholes:
            try:
                distance = self.haversine(lat, lon, mh["lat"], mh["lon"])
                if distance <= radius:
                    mh_data = mh.copy()
                    mh_data["distance"] = distance
                    nearby.append(mh_data)
                    logger.debug(f"[MANHOLE-SEARCH] Found: {mh['id']} at {distance:.1f}m")
            except Exception as e:
                logger.error(f"[MANHOLE-SEARCH] Error computing distance: {e}")
        
        # Sort by distance
        nearby.sort(key=lambda x: x["distance"])
        
        logger.info(f"[MANHOLE-SEARCH] Found {len(nearby)} manholes within {radius}m")
        return nearby

    def select_manhole_with_mapbox(self, current_lat, current_lon):
        """Show Mapbox map dialog for manhole selection"""
        # Find nearby manholes
        nearby_manholes = self.find_nearby_manholes(current_lat, current_lon, radius=50)
        
        # Create and show Mapbox dialog
        map_dialog = MapboxMapDialog(
            self,
            center_lat=current_lat,
            center_lon=current_lon,
            zoom=16,
            nearby_manholes=nearby_manholes
        )
        
        # Variables to store selection result
        selected_data = None
        
        def handle_location_selected(lat, lon, manhole_id):
            nonlocal selected_data
            selected_data = {
                "lat": lat,
                "lon": lon,
                "manhole_id": manhole_id
            }
            map_dialog.accept()
        
        # Connect signal
        map_dialog.location_selected.connect(handle_location_selected)
        
        # Show dialog
        if map_dialog.exec_() == QDialog.Accepted:
            if selected_data:
                logger.info(f"[MAPBOX SELECTION] Selected: {selected_data['manhole_id']} at {selected_data['lat']:.6f}, {selected_data['lon']:.6f}")
                return selected_data["manhole_id"]
        
        return None

    def open_loadcell_dialog(self):
        """Open the load cell measurement dialog"""
        logger.info("[LOADCELL] Opening load cell dialog")
        
        dialog = LoadCellDialog(self)
        dialog.measurement_complete.connect(self.on_loadcell_measurement)
        
        if dialog.exec_() == QDialog.Accepted:
            logger.info("[LOADCELL] Dialog closed with measurements")
            # Update status label
            status_text = f"Depth: "
            if self.loadcell_before_depth:
                status_text += f"{self.loadcell_before_depth}cm"
            else:
                status_text += "--"
            status_text += " / "
            if self.loadcell_after_depth:
                status_text += f"{self.loadcell_after_depth}cm"
            else:
                status_text += "--"
            
            self.status_labels["loadcell"].setText(status_text)
            
            # Show summary
            if self.loadcell_before_depth and self.loadcell_after_depth:
                improvement = ((self.loadcell_before_depth - self.loadcell_after_depth) / self.loadcell_before_depth) * 100
                QMessageBox.information(self, "Depth Measurements",
                                      f"✅ Measurements Complete!\n\n"
                                      f"<b style='color: #f57c00;'>Before cleaning:</b> {self.loadcell_before_depth} cm<br>"
                                      f"<b style='color: #388e3c;'>After cleaning:</b> {self.loadcell_after_depth} cm<br>"
                                      f"<b>Improvement:</b> {improvement:.1f}% reduction")
            elif self.loadcell_before_depth:
                QMessageBox.information(self, "Depth Measurements",
                                      f"📏 <b style='color: #f57c00;'>Before cleaning:</b> {self.loadcell_before_depth} cm<br><br>"
                                      f"Remember to measure after cleaning for comparison.")
            elif self.loadcell_after_depth:
                QMessageBox.information(self, "Depth Measurements",
                                      f"✅ <b style='color: #388e3c;'>After cleaning:</b> {self.loadcell_after_depth} cm<br><br>"
                                      f"Remember to measure before cleaning for comparison.")

    def on_loadcell_measurement(self, mode, depth):
        """Handle measurement from load cell"""
        logger.info(f"[LOADCELL] Measurement: {mode} = {depth} cm")
        
        if mode == 'before':
            self.loadcell_before_depth = depth
        else:
            self.loadcell_after_depth = depth
            
        # Update status immediately
        status_text = f"Depth: "
        if self.loadcell_before_depth:
            status_text += f"{self.loadcell_before_depth}cm"
        else:
            status_text += "--"
        status_text += " / "
        if self.loadcell_after_depth:
            status_text += f"{self.loadcell_after_depth}cm"
        else:
            status_text += "--"
        
        self.status_labels["loadcell"].setText(status_text)

    def start_timer(self):
        if self.timer_running:
            QMessageBox.warning(self, "Timer Running", "Operation already started.")
            return

        if not self.gps_fix:
            reply = QMessageBox.warning(self, "GPS Warning", 
                                       "No current GPS fix. Do you want to proceed anyway?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                return

        current_lat = self.captured_latitude
        current_lon = self.captured_longitude
        
        logger.info(f"[MANHOLE-SELECT] Current GPS: Lat={current_lat:.6f}, Lon={current_lon:.6f}")
        
        # Use Mapbox map for selection
        selected_manhole_id = self.select_manhole_with_mapbox(current_lat, current_lon)
        
        if not selected_manhole_id:
            QMessageBox.warning(self, "Missing Manhole", "Manhole ID is required.")
            return
        
        self.selected_manhole_id = selected_manhole_id

        logger.info(f"[MANHOLE-SELECT] Final selected manhole ID: {self.selected_manhole_id}")
        
        # Display selected manhole ID in status
        self.status_labels["manhole"].setText(f"Manhole ID: {self.selected_manhole_id}")
        
        if not self.timer_running:
            self.timer_start_time = datetime.datetime.now()
            self.timer_running = True
            self.blink_state = True
            
            self.timer_label.setStyleSheet("""
                QLabel { 
                    color: #e74c3c; 
                    font-weight: bold; 
                    font-size: 28px; 
                    qproperty-alignment: 'AlignCenter'; 
                    background-color: #2c3e50; 
                    border-radius: 10px; 
                    padding: 10px 5px; 
                    border: 2px solid #e74c3c; 
                } 
            """)
            
            device_id = config.get('device_id', 'UNKNOWN')
            self.current_operation_id = get_next_operation_id(device_id)
            self.current_after_capture_path = None
            self.current_before_capture_path = None
            self.operation_start_time = datetime.datetime.now(pytz.utc)
            
            logger.info(f"[TIMER] Timer started — Operation ID: {self.current_operation_id}")
            logger.info(f"[TIMER] Selected manhole ID: {self.selected_manhole_id}")
            logger.info(f"[TIMER] GPS location: Lat={current_lat:.6f}, Lon={current_lon:.6f}")

            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)

            QTimer.singleShot(500, lambda: self.capture_and_upload("before"))

    def stop_timer(self):
        if not self.timer_running:
            QMessageBox.warning(self, "Timer Not Running", "No operation is currently running.")
            return

        try:
            logger.info(f"[TIMER] Stopping timer for operation: {self.current_operation_id}")
            logger.info(f"[TIMER] Manhole ID at stop: {self.selected_manhole_id}")
            
            self.timer_running = False
            self.blink_state = True
            self.operation_end_time = datetime.datetime.now(pytz.utc)

            logger.info(f"[TIMER] Timer stopped. Duration: {self.operation_end_time - self.operation_start_time}")

            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.update_finished_timer_display()
            self.upload_in_progress = True
            
            logger.info("[TIMER] Scheduling after image capture...")
            QTimer.singleShot(500, self.capture_after_image)
            
        except Exception as e:
            logger.error(f"[STOP TIMER ERROR] {e}")
            logger.error(traceback.format_exc())
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            QMessageBox.critical(self, "Error", f"Error stopping timer: {str(e)}")

    def capture_after_image(self):
        try:
            logger.info("[CAPTURE] Starting after image capture...")
            self.capture_and_upload("after")
        except Exception as e:
            logger.error(f"[AFTER CAPTURE ERROR] {e}")
            logger.error(traceback.format_exc())

    def capture_and_upload(self, tag):
        try:
            logger.info(f"[CAPTURE] Starting {tag} image capture...")
            
            if not self.current_operation_id:
                logger.error(f"[CAPTURE] Cannot capture '{tag}'. Operation not started.")
                return

            camera_thread = self.camera_threads[1]
            captured_frame = camera_thread.get_last_frame()

            if captured_frame is not None:
                temp_filename = os.path.join("uploads", f"{self.current_operation_id}_{tag}.jpg")
                os.makedirs("uploads", exist_ok=True)
                success = cv2.imwrite(temp_filename, captured_frame)
                if success:
                    logger.info(f"[CAPTURE] {tag.capitalize()} image saved: {temp_filename}")
                else:
                    logger.error(f"[CAPTURE] Failed to save {tag} image: {temp_filename}")
                    placeholder = np.zeros((720, 1280, 3), dtype=np.uint8)
                    cv2.imwrite(temp_filename, placeholder)
                    logger.warning(f"[CAPTURE] Created placeholder image for '{tag}'.")
            else:
                placeholder = np.zeros((720, 1280, 3), dtype=np.uint8)
                temp_filename = os.path.join("uploads", f"{self.current_operation_id}_{tag}.jpg")
                os.makedirs("uploads", exist_ok=True)
                cv2.imwrite(temp_filename, placeholder)
                logger.warning(f"[CAPTURE] No frame available. Created placeholder image for '{tag}'.")

            if tag == "before":
                self.current_before_capture_path = temp_filename
                self.status_labels["upload"].setText("Upload status: Before image captured")
                logger.info(f"[CAPTURE] Before image captured. Operation ID: {self.current_operation_id}")
                logger.info(f"[CAPTURE] Manhole ID for before image: {self.selected_manhole_id}")
            else:
                self.current_after_capture_path = temp_filename
                logger.info(f"[CAPTURE] After image captured. Operation ID: {self.current_operation_id}")
                logger.info(f"[CAPTURE] Manhole ID for after image: {self.selected_manhole_id}")
                self.upload_attempted = True
                QTimer.singleShot(1000, self.start_upload_process)
                
        except Exception as e:
            logger.error(f"[CAPTURE ERROR] Failed to capture {tag} image: {e}")
            logger.error(traceback.format_exc())
            self.status_labels["upload"].setText(f"Upload status: {tag.capitalize()} capture failed")
            
            if tag == "after" and self.current_before_capture_path:
                logger.info("[CAPTURE] After image capture failed, but will attempt upload with before image only")
                QTimer.singleShot(1000, self.start_upload_process)

    def start_upload_process(self):
        try:
            logger.info("[UPLOAD] Starting upload process...")
            logger.info(f"[UPLOAD] Operation ID: {self.current_operation_id}")
            logger.info(f"[UPLOAD] Selected manhole ID: {self.selected_manhole_id}")
            logger.info(f"[UPLOAD] GPS location: {self.captured_latitude:.6f}, {self.captured_longitude:.6f}")
            
            self.status_labels["upload"].setText("Upload status: Preparing upload...")
            
            required_data_ok = True
            error_message = ""
            
            if not self.current_before_capture_path:
                error_message = "Missing before image path"
                logger.error(f"[UPLOAD] {error_message}")
                required_data_ok = False
            elif not os.path.exists(self.current_before_capture_path):
                error_message = f"Before image file not found: {self.current_before_capture_path}"
                logger.error(f"[UPLOAD] {error_message}")
                required_data_ok = False
                
            if not self.current_after_capture_path:
                logger.warning("[UPLOAD] Missing after image path, will attempt upload with before image only")
            elif not os.path.exists(self.current_after_capture_path):
                logger.warning(f"[UPLOAD] After image file not found: {self.current_after_capture_path}, will attempt upload with before image only")
                self.current_after_capture_path = None

            if not self.operation_start_time or not self.operation_end_time:
                error_message = "Missing start or end time"
                logger.error(f"[UPLOAD] {error_message}")
                required_data_ok = False

            if not self.selected_manhole_id:
                error_message = "Missing manhole ID"
                logger.error(f"[UPLOAD] {error_message}")
                required_data_ok = False
                
            if not required_data_ok:
                self.status_labels["upload"].setText(f"Upload status: {error_message}")
                return

            duration = self.operation_end_time - self.operation_start_time
            
            location_data = {
                "latitude": self.captured_latitude,
                "longitude": self.captured_longitude,
                "gps_fix": self.gps_fix
            }

            logger.info(f"[UPLOAD] Attempting to upload operation {self.current_operation_id}")
            logger.info(f"[UPLOAD] Manhole ID to upload: {self.selected_manhole_id}")
            logger.info(f"[UPLOAD] Duration: {duration.total_seconds()} seconds")
            
            self.status_labels["upload"].setText("Upload status: Uploading...")
            
            # CRITICAL: Ensure manhole_id is not None
            if not self.selected_manhole_id:
                logger.error("[UPLOAD] selected_manhole_id is None! Using emergency ID")
                timestamp = datetime.datetime.now().strftime("%H%M%S")
                self.selected_manhole_id = f"EMERGENCY_{timestamp}"
        
            logger.info(f"[UPLOAD] Final manhole_id being sent: {self.selected_manhole_id}")
            
            try:
                logger.info(f"[UPLOAD] Queuing operation {self.current_operation_id} for upload")
                logger.info(f"[UPLOAD] Manhole ID being passed: {self.selected_manhole_id}")
                
                # Prepare operation data for manhole cleaning
                operation_data = {
                    'operation_id': self.current_operation_id,
                    'operation_type': 'manhole_cleaning',
                    'before_path': self.current_before_capture_path,
                    'after_path': self.current_after_capture_path,
                    'config': config,
                    'location': location_data,
                    'start_time': self.operation_start_time,
                    'end_time': self.operation_end_time,
                    'duration_seconds': int(duration.total_seconds()),
                    'manhole_id': self.selected_manhole_id,
                    'device_id': config.get('device_id', 'UNKNOWN'),
                    'area': config.get('area', 'UNKNOWN'),
                    'division': config.get('division', 'UNKNOWN'),
                    'district': config.get('district', 'UNKNOWN')
                }
                
                operation_id = self.uploader.queue_operation(**operation_data)
                
                if operation_id:
                    logger.info(f"[UPLOAD SUCCESS] Operation {operation_id} queued successfully")
                    logger.info(f"[UPLOAD SUCCESS] Manhole ID queued: {self.selected_manhole_id}")
                    self.status_labels["upload"].setText("Upload status: Queued for upload")
                    # Schedule timer reset after upload is queued
                    QTimer.singleShot(3000, self.reset_timer_and_operation)
                else:
                    logger.error(f"[UPLOAD FAILED] Failed to queue operation {self.current_operation_id}")
                    self.status_labels["upload"].setText("Upload status: Queue failed")
                    # Still reset timer even if queue failed
                    QTimer.singleShot(3000, self.reset_timer_and_operation)
                    
            except Exception as upload_error:
                logger.error(f"[UPLOAD QUEUE ERROR] Failed to queue upload: {upload_error}")
                logger.error(traceback.format_exc())
                self.status_labels["upload"].setText("Upload status: Queue failed")
                QMessageBox.warning(self, "Upload Warning", f"Upload queuing failed: {str(upload_error)}\nData will be saved for retry.")
                # Reset timer even on error
                QTimer.singleShot(3000, self.reset_timer_and_operation)
                    
        except Exception as e:
            logger.error(f"[UPLOAD PROCESS ERROR] {e}")
            logger.error(traceback.format_exc())
            self.status_labels["upload"].setText("Upload status: Process failed")
            QMessageBox.warning(self, "Upload Error", f"Upload process failed: {str(e)}")
            # Reset timer on process error
            QTimer.singleShot(3000, self.reset_timer_and_operation)

    def reset_timer_and_operation(self):
        """Reset timer and operation data after upload process completes"""
        try:
            logger.info("[TIMER-RESET] Starting timer and operation reset...")
            
            # Reset timer variables
            self.timer_start_time = None
            self.timer_running = False
            self.blink_state = True
            
            # Reset timer display to 00:00:00
            self.timer_label.setText("00:00:00")
            self.timer_label.setStyleSheet("""
                QLabel { 
                    color: #e74c3c; 
                    font-weight: bold; 
                    font-size: 36px; 
                    qproperty-alignment: 'AlignCenter'; 
                    background-color: #2c3e50; 
                    border-radius: 15px; 
                    padding: 20px 15px; 
                    border: 3px solid #7f8c8d; 
                    min-height: 100px;
                } 
            """)
            
            # Reset elapsed time label if it exists
            if hasattr(self, 'elapsed_label') and self.elapsed_label:
                self.elapsed_label.setText("Elapsed Time: 0 seconds")
            
            # Reset operation data
            self.reset_operation_data()
            
            logger.info("[TIMER-RESET] Timer and operation reset completed successfully")
            
            # Optional: Show a brief notification
            self.status_labels["upload"].setText("Upload status: Ready for next operation")
            
        except Exception as e:
            logger.error(f"[TIMER-RESET ERROR] Failed to reset timer: {e}")
            logger.error(traceback.format_exc())

    def reset_operation_data(self):
        """Reset all operation-related data"""
        try:
            logger.info("[RESET] Starting operation data reset...")
            
            self.current_before_capture_path = None
            self.current_after_capture_path = None
            self.current_operation_id = None
            self.operation_start_time = None
            self.operation_end_time = None
            self.selected_manhole_id = None
            self.upload_attempted = False
            self.upload_in_progress = False
            
            # Reset status labels
            self.status_labels["manhole"].setText("Manhole ID: N/A")
            
            logger.info("[RESET] All operation data reset successfully")
            
        except Exception as e:
            logger.error(f"[RESET ERROR] Failed to reset operation data: {e}")
            logger.error(traceback.format_exc())

    def closeEvent(self, event):
        logger.info("[MAIN] Application shutting down...")
        
        if self.upload_in_progress:
            reply = QMessageBox.question(self, 'Upload in Progress',
                                        'An upload is currently in progress. Are you sure you want to exit?\nThe upload will continue in background if possible.',
                                        QMessageBox.Yes | QMessageBox.No,
                                        QMessageBox.No)
            if reply == QMessageBox.No:
                event.ignore()
                return
        
        reply = QMessageBox.question(self, 'Confirm Exit',
                                     'Are you sure you want to exit?',
                                     QMessageBox.Yes | QMessageBox.No,
                                     QMessageBox.No)

        if reply == QMessageBox.Yes:
            for thread in self.camera_threads:
                thread.stop()

            if self.gps_serial and self.gps_serial.is_open:
                self.gps_serial.close()
                logger.info("[GPS] GPS serial port closed.")

            event.accept()
        else:
            event.ignore()


def signal_handler(sig, frame):
    logger.info("[MAIN] Received interrupt signal. Exiting gracefully.")
    app = QApplication.instance()
    if app:
        for widget in app.topLevelWidgets():
            widget.close()
    QApplication.quit()


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    app = QApplication(sys.argv)
    
    def app_exception_handler(exception, value, traceback):
        logger = logging.getLogger(__name__)
        logger.critical(f"Application exception: {exception}", exc_info=(exception, value, traceback))
        
        try:
            QMessageBox.critical(None, "Application Error", 
                               f"An error occurred:\n\n{exception.__name__}: {value}\n\nCheck logs for details.")
        except:
            pass
    
    try:
        window = SmartWasteDashboard()
        window.showFullScreen()
        
        sys.excepthook = app_exception_handler
        
        sys.exit(app.exec_())
    except Exception as e:
        logger.critical(f"Application crashed: {e}", exc_info=True)
        QMessageBox.critical(None, "Application Crash", 
                           f"The application crashed:\n\n{type(e).__name__}: {str(e)}\n\nCheck logs for details.")
        sys.exit(1)
