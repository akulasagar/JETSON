import os
import serial
import csv
import math
import logging
import psycopg2
from psycopg2 import extras
from urllib.parse import urlparse
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QFrame, QApplication,
                             QSizePolicy, QWidget, QLineEdit, QGridLayout, QMessageBox)
from PyQt5.QtCore import Qt, QUrl, QObject, pyqtSlot, pyqtSignal
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
from PyQt5.QtWebChannel import QWebChannel
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

# ---------------------------------------------------------------------------
# GPS placeholder
# ---------------------------------------------------------------------------
CURRENT_LAT = 17.557055
CURRENT_LON = 78.4708128
NEARBY_RADIUS_M = 50 
    
# ---------------------------------------------------------------------------
# Manhole CSV & DB
# ---------------------------------------------------------------------------
MANHOLE_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "manhole.csv")
DATABASE_URL = os.getenv("DATABASE_URL")
SECTION_NAME = "kondapur"
#TABLE_NAME = f"{SECTION_NAME}_manholes"
TABLE_NAME = f"master_manholes"


def _parse_jdbc_url(jdbc_url):
    """Convert JDBC URL to psycopg2 parameters"""
    if jdbc_url.startswith("jdbc:"):
        jdbc_url = jdbc_url.replace("jdbc:", "")

    parsed = urlparse(jdbc_url)

    return {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "dbname": parsed.path.lstrip("/")
    }

# global connection + cursor
_conn = None
_cursor = None
_cached_manholes = None

def _get_db_connection():
    """Returns existing connection or creates a new one."""
    global _conn

    if _conn and not _conn.closed:
        return _conn

    try:
        db_url = DATABASE_URL
        logger.info(f"[MANHOLE-DB] Database URL: {db_url}")
        if not db_url:
            logger.error("[MANHOLE-DB] No DATABASE_URL found.")
            return None

        logger.info("[MANHOLE-DB] Establishing new connection...")
        _conn = psycopg2.connect(db_url, connect_timeout=5)

        return _conn

    except Exception as e:
        logger.error(f"[MANHOLE-DB] ❌ Database connection failed: {e}")
        return None


def _load_manholes_from_db():
    """
    Load manholes from PostgreSQL using existing connection logic.
    Returns None if DB fails so CSV fallback can occur.
    """

    global _cursor, _conn

    try:
        conn = _get_db_connection()
        if not conn:
            logger.warning("[MANHOLE-DB] No DB connection available.")
            return None

        if not _cursor or _cursor.closed:
            _cursor = conn.cursor(cursor_factory=extras.RealDictCursor)

        table_name = f"{TABLE_NAME}"

        query = f"""
        SELECT
            mh_id AS id,
            mh_latitude AS lat,
            mh_longitude AS lon
        FROM {table_name}
        """

        logger.debug(f"[MANHOLE-DB] Executing query: {query}")

        _cursor.execute(query)

        rows = _cursor.fetchall()

        manholes = []

        for r in rows:
            if r["lat"] is not None and r["lon"] is not None:
                manholes.append({
                    "id": str(r["id"]),
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"])
                })

        logger.info(f"[MANHOLE-LOAD] Loaded {len(manholes)} manholes from DB")
        logger.info(manholes)
        return manholes

    except Exception as e:
        logger.warning(f"[MANHOLE-DB] DB load failed, fallback to CSV: {e}")

        # reset connection
        try:
            if _cursor:
                _cursor.close()
        except:
            pass

        try:
            if _conn:
                _conn.close()
        except:
            pass

        _cursor = None
        _conn = None

        return None


def _load_manholes():
    global _cached_manholes
    if _cached_manholes is not None:
        return _cached_manholes

    # 1️⃣ Try DB first
    db_data = _load_manholes_from_db()
    if db_data:
        _cached_manholes = db_data
        return db_data

    # 2️⃣ Fallback to CSV
    manholes = []
    csv_path = os.path.abspath(MANHOLE_CSV)

    if not os.path.exists(csv_path):
        return manholes

    id_fields = ["mh_id"]
    lat_fields = ["mh_latitude"]
    lon_fields = ["mh_longitude"]

    try:
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)

            for row in reader:

                raw_id = None
                for field in id_fields:
                    if field in row and row[field]:
                        raw_id = row[field].strip()
                        break

                if not raw_id:
                    continue

                lat = lon = None

                for field in lat_fields:
                    if field in row and row[field]:
                        try:
                            lat = float(row[field].strip())
                            break
                        except:
                            pass

                for field in lon_fields:
                    if field in row and row[field]:
                        try:
                            lon = float(row[field].strip())
                            break
                        except:
                            pass

                if lat is not None and lon is not None:
                    manholes.append({
                        "id": raw_id,
                        "lat": lat,
                        "lon": lon
                    })

    except Exception as e:
        logger.error(f"CSV error: {e}")

    logger.info(f"Loaded {len(manholes)} manholes from CSV")
    
    _cached_manholes = manholes
    return manholes


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _nearby_manholes(lat, lon, radius_m=NEARBY_RADIUS_M):
    all_mh = _load_manholes()
    nearby = []
    for mh in all_mh:
        d = _haversine_m(lat, lon, mh["lat"], mh["lon"])
        if d <= radius_m:
            nearby.append({**mh, "dist_m": round(d)})
    nearby.sort(key=lambda x: x["dist_m"])
    return nearby



# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------
class _Bridge(QObject):
    manhole_selected = pyqtSignal(str)
    @pyqtSlot(str)
    def on_manhole_selected(self, manhole_id: str):
        self.manhole_selected.emit(manhole_id)

# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------
def _build_leaflet_html(center_lat, center_lon, manholes, radius_m):
    markers_js = []
    for mh in manholes:
        safe_id = mh["id"].replace("'", "\\'")
        markers_js.append(f"{{id: '{safe_id}', lat: {mh['lat']}, lon: {mh['lon']}, dist: {mh['dist_m']}}}")
    markers_str = "[" + ",".join(markers_js) + "]"

    return f"""<!DOCTYPE html>
    <html>
        <head>
            <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
            <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
            <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
            <style>
                * {{ margin: 0; padding: 0; }}
                html, body, #map {{ height: 100%; width: 100%; overflow: hidden; border-radius: 12px; }}
            </style>
        </head>
        <body>
            <div id="map"></div>
            <script>
            var bridge = null;
            var selectedMarker = null;
            var blueIcon = L.divIcon({{className: '', html: '<div style="width:14px;height:14px;border-radius:50%;background:#1E88E5;border:2px solid white;box-shadow:0 2px 4px rgba(0,0,0,0.3);"></div>', iconSize:[14,14], iconAnchor:[7,7]}});
            var greenIcon = L.divIcon({{className: '', html: '<div style="width:18px;height:18px;border-radius:50%;background:#00C853;border:2px solid white;box-shadow:0 2px 6px rgba(0,0,0,0.4);"></div>', iconSize:[18,18], iconAnchor:[9,9]}});
            var centerIcon = L.divIcon({{className: '', html: '<div style="width:14px;height:14px;border-radius:50%;background:#F44336;border:2px solid white;"></div>', iconSize:[14,14], iconAnchor:[7,7]}});

            new QWebChannel(qt.webChannelTransport, function(ch) {{ bridge = ch.objects.bridge; }});

            var map = L.map('map', {{zoomControl: false}}).setView([{center_lat}, {center_lon}], 17);
            L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png').addTo(map);
            L.marker([{center_lat}, {center_lon}], {{icon: centerIcon}}).addTo(map);

            var manholes = {markers_str};
            manholes.forEach(function(mh) {{
                var m = L.marker([mh.lat, mh.lon], {{icon: blueIcon}}).addTo(map);
                m.on('click', function() {{
                    if (selectedMarker) selectedMarker.setIcon(blueIcon);
                    m.setIcon(greenIcon);
                    selectedMarker = m;
                    if (bridge) bridge.on_manhole_selected(mh.id);
                }});
            }});
            </script>
        </body>
    </html>"""

# ---------------------------------------------------------------------------
# UI Helpers
# ---------------------------------------------------------------------------
class Card(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: white;
                border-radius: 12px;
                border: 1px solid #E0E0E0;
            }
        """)

# ---------------------------------------------------------------------------
# Popup
# ---------------------------------------------------------------------------
from PyQt5.QtCore import QThread, pyqtSignal

class DataLoaderThread(QThread):
    data_loaded = pyqtSignal(list)

    def __init__(self, lat, lon, radius_m):
        super().__init__()
        self.lat = lat
        self.lon = lon
        self.radius_m = radius_m

    def run(self):
        nearby = _nearby_manholes(self.lat, self.lon, self.radius_m)
        self.data_loaded.emit(nearby)

class StartOperationPopup(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(1100, 780)
        self.manhole_id = None
        self._bridge = _Bridge()
        self._bridge.manhole_selected.connect(self._on_map_selection)
        self._init_ui()

        # GPS variables
        self.gps_port = '/dev/ttyUSB1'
        self.gps_baudrate = 115200
        self.gps_serial = None
        self.captured_latitude = 0.0
        self.captured_longitude = 0.0
        self.gps_fix = False
        # self._init_gps() # Disable local GPS init, use parent's data

    def _init_ui(self):
        # Outer container for round corners
        container = QFrame(self)
        container.setObjectName("MainContainer")
        container.setStyleSheet("""
            #MainContainer {
                background-color: #F8F9FA;
                border-radius: 15px;
                border: 1px solid #CCC;
            }
        """)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0,0,0,0)
        main_layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # ── Header ──────────────────────────────────
        header = QFrame()
        header.setFixedHeight(60)
        header.setStyleSheet("background-color: #1A92A4; border-top-left-radius: 14px; border-top-right-radius: 14px;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(25, 0, 25, 0)

        title = QLabel("Manhole Selection")
        title.setStyleSheet("color: white; font-size: 20px; font-weight: bold; border: none;")
        
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(30, 30)
        close_btn.setStyleSheet("""
            QPushButton { 
                background-color: white; color: #1A92A4; 
                border-radius: 15px; font-weight: bold; font-size: 16px; border: none;
            }
            QPushButton:hover { background-color: #F8F9FA; }
        """)
        close_btn.clicked.connect(self.reject)

        h_lay.addWidget(title)
        h_lay.addStretch()
        h_lay.addWidget(close_btn)
        container_layout.addWidget(header)

        # ── Body ─────────────────────────────────────
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(30, 30, 30, 30)
        body_layout.setSpacing(25)

        # Left Column
        left_col = QVBoxLayout()
        left_col.setSpacing(20)

        # Instruction Card
        hint_card = Card()
        hint_card.setFixedHeight(50)
        hint_card.setStyleSheet("QFrame { background-color: #F8F9FA; border-radius: 12px; border: 1px solid #E0E0E0; }")
        hint_lay = QHBoxLayout(hint_card)
        hint_lay.setContentsMargins(15, 0, 15, 0)
        hint_icon = QLabel("📍") # Red icon substitute
        hint_text = QLabel("click a manhole marker to select it")
        hint_text.setStyleSheet("color: #333; font-size: 14px; border: none;")
        hint_icon.setStyleSheet("border: none; color: #FF0000; font-size: 18px;")
        hint_lay.addWidget(hint_icon)
        hint_lay.addWidget(hint_text)
        hint_lay.addStretch()
        left_col.addWidget(hint_card)

        # Map Area
        self._web = QWebEngineView()
        self._web.setStyleSheet("border: 1px solid #E0E0E0; border-radius: 12px; background: #EEE;")
        self._web.settings().setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        self._channel = QWebChannel()
        self._channel.registerObject("bridge", self._bridge)
        self._web.page().setWebChannel(self._channel)
        left_col.addWidget(self._web, 1)

        # Manual Card
        manual_card = Card()
        manual_card.setFixedHeight(140)
        manual_lay = QVBoxLayout(manual_card)
        manual_lay.setContentsMargins(20, 15, 20, 15)
        
        m_head = QHBoxLayout()
        m_icon = QLabel("➡️")
        m_title = QLabel("Manual Manhole ID")
        m_title.setStyleSheet("font-weight: bold; font-size: 15px; border: none;")
        m_icon.setStyleSheet("border: none;")
        m_head.addWidget(m_icon)
        m_head.addWidget(m_title)
        m_head.addStretch()
        
        m_body = QVBoxLayout()
        m_label = QLabel("Enter Manhole ID:")
        m_label.setStyleSheet("color: #666; font-size: 13px; border: none;")
        
        m_input_lay = QHBoxLayout()
        self._manual_input = QLineEdit("")
        self._manual_input.setStyleSheet("""
            QLineEdit { 
                padding: 8px; border: 1px solid #DDD; border-radius: 5px; background: #F8F9FA;
            }
        """)
        use_id_btn = QPushButton("Use ID")
        use_id_btn.setFixedSize(120, 36)
        use_id_btn.setStyleSheet("""
            QPushButton { background: #1A92A4; color: white; border-radius: 6px; font-weight: bold; border: none; }
            QPushButton:hover { background: #147A8A; }
        """)
        use_id_btn.clicked.connect(self._on_manual_use)
        m_input_lay.addWidget(self._manual_input)
        m_input_lay.addWidget(use_id_btn)
        
        manual_lay.addLayout(m_head)
        manual_lay.addWidget(m_label)
        manual_lay.addLayout(m_input_lay)
        left_col.addWidget(manual_card)

        body_layout.addLayout(left_col, 2)

        # Right Column
        right_col = QVBoxLayout()
        right_col.setSpacing(20)

        # GPS Location Card
        gps_card = Card()
        gps_lay = QVBoxLayout(gps_card)
        gps_lay.setContentsMargins(20, 20, 20, 20)
        gps_head = QHBoxLayout()
        gps_icon = QLabel("📍")
        gps_title = QLabel("Current GPS Location")
        gps_title.setStyleSheet("font-weight: bold; font-size: 15px; border: none;")
        gps_icon.setStyleSheet("border: none; color: #00C853;")
        gps_head.addWidget(gps_icon)
        gps_head.addWidget(gps_title)
        gps_head.addStretch()
        gps_lay.addLayout(gps_head)
        
        grid = QGridLayout()
        grid.setSpacing(10)
        lat_lbl = QLabel("Latitude:")
        lat_lbl.setStyleSheet("border: none; color: #333;")
        self._lat_val = QLabel(f"{CURRENT_LAT:.10f}")
        self._lat_val.setStyleSheet("background: #F1F3F4; padding: 6px; border-radius: 4px; border: none;")
        long_lbl = QLabel("Longitude:")
        long_lbl.setStyleSheet("border: none; color: #333;")
        self._lon_val = QLabel(f"{CURRENT_LON:.10f}")
        self._lon_val.setStyleSheet("background: #F1F3F4; padding: 6px; border-radius: 4px; border: none;")
        grid.addWidget(lat_lbl, 0, 0)
        grid.addWidget(self._lat_val, 0, 1)
        grid.addWidget(long_lbl, 1, 0)
        grid.addWidget(self._lon_val, 1, 1)
        gps_lay.addLayout(grid)
        right_col.addWidget(gps_card)

        # Nearby Card
        nearby_card = Card()
        nearby_lay = QVBoxLayout(nearby_card)
        nearby_lay.setContentsMargins(20, 20, 20, 20)
        nb_head = QHBoxLayout()
        nb_icon = QLabel("💠")
        nb_title = QLabel("Nearby Manholes")
        nb_title.setStyleSheet("font-weight: bold; font-size: 15px; border: none;")
        nb_icon.setStyleSheet("border: none; color: #FFD600;")
        nb_head.addWidget(nb_icon)
        nb_head.addWidget(nb_title)
        nb_head.addStretch()
        nearby_lay.addLayout(nb_head)
        
        self._table_container = QWidget()
        self._table_container.setStyleSheet("border: 1px solid #EEE; border-radius: 5px;")
        table_lay = QVBoxLayout(self._table_container)
        table_lay.setSpacing(0)
        table_lay.setContentsMargins(0,0,0,0)
        
        # Header row
        h_row = QFrame()
        h_row.setStyleSheet("background: #FCFCFC; border-bottom: 1px solid #EEE;")
        h_row_lay = QHBoxLayout(h_row)
        h_row_lay.addWidget(QLabel("ID"), 1)
        h_row_lay.addWidget(QLabel("Location"), 2)
        table_lay.addWidget(h_row)
        
        self._nearby_list_lay = QVBoxLayout()
        self._nearby_list_lay.setSpacing(0)
        table_lay.addLayout(self._nearby_list_lay)
        table_lay.addStretch()
        
        nearby_lay.addWidget(self._table_container)
        right_col.addWidget(nearby_card, 1)

        # Selection Card
        sel_card = Card()
        sel_card.setFixedHeight(80)
        sel_lay = QHBoxLayout(sel_card)
        sel_lay.setContentsMargins(20, 0, 20, 0)
        sel_icon = QLabel("✅")
        sel_title = QLabel("Selected Location")
        sel_title.setStyleSheet("font-weight: bold; border: none;")
        sel_icon.setStyleSheet("border: none;")
        self._selected_coord_val = QLabel("0.0000000000")
        self._selected_coord_val.setStyleSheet("background: #F1F3F4; padding: 8px; border-radius: 10px; border: none;")
        
        sel_lay.addWidget(sel_icon)
        sel_lay.addWidget(sel_title)
        sel_lay.addStretch()
        sel_lay.addWidget(self._selected_coord_val)
        right_col.addWidget(sel_card)

        # Buttons
        btn_lay = QHBoxLayout()
        btn_lay.setSpacing(15)
        
        cancel_btn = QPushButton("✕ Cancel")
        cancel_btn.setFixedSize(160, 46)
        cancel_btn.setStyleSheet("""
            QPushButton { background: #FF0000; color: white; border-radius: 8px; font-weight: bold; font-size: 14px; border: none; }
            QPushButton:hover { background: #D00000; }
        """)
        cancel_btn.clicked.connect(self.reject)
        
        confirm_btn = QPushButton("✅ Confirm Select")
        confirm_btn.setFixedSize(160, 46)
        confirm_btn.setStyleSheet("""
            QPushButton { background: #00C853; color: white; border-radius: 8px; font-weight: bold; font-size: 14px; border: none; }
            QPushButton:hover { background: #00A847; }
        """)
        confirm_btn.clicked.connect(self.handle_confirm_select)
        
        btn_lay.addWidget(cancel_btn)
        btn_lay.addWidget(confirm_btn)
        right_col.addLayout(btn_lay)

        body_layout.addLayout(right_col, 1)
        container_layout.addWidget(body)

        self._load_map_and_data()
    

    def _init_gps(self):
        try:
            self.gps_serial = serial.Serial(
                self.gps_port,
                self.gps_baudrate,
                timeout=1
            )
            logger.info("[GPS] Connected to GPS device")

        except Exception as e:
            logger.warning(f"[GPS] Connection failed: {e}")
            self.gps_serial = None

    def handle_confirm_select(self):
        """Validates manhole ID before accepting the dialog."""
        # Use either manual input or selection from map/list
        self.manhole_id = self._manual_input.text().strip()
        
        if not self.manhole_id or self.manhole_id == "":
            QMessageBox.warning(self, "Invalid Selection", 
                               "Please select a manhole from the map or enter a valid Manhole ID to start the operation.")
            return
            
        self.accept()


    # def _read_gps(self):
    #     if not self.gps_serial:
    #         return

    #     try:
    #         line = self.gps_serial.readline().decode(errors="ignore")

    #         # Example NMEA parsing for GGA
    #         if line.startswith("$GNGGA") or line.startswith("$GPGGA"):
    #             parts = line.split(",")

    #             if parts[2] and parts[4]:
    #                 lat = float(parts[2])
    #                 lon = float(parts[4])

    #                 lat = (lat // 100) + ((lat % 100) / 60)
    #                 lon = (lon // 100) + ((lon % 100) / 60)

    #                 self.captured_latitude = lat
    #                 self.captured_longitude = lon
    #                 self.gps_fix = True

    #     except Exception:
    #         pass


    def _read_gps(self):
        # Check GPS serial exists
        if not hasattr(self, "gps_serial") or self.gps_serial is None:
            return

        try:
            line = self.gps_serial.readline().decode("utf-8", errors="ignore").strip()

            if line.startswith("$GNGGA") or line.startswith("$GPGGA"):
                parts = line.split(",")

                # Check fix quality
                fix_quality = parts[6]

                if fix_quality and int(fix_quality) > 0:

                    raw_lat = parts[2]
                    lat_dir = parts[3]

                    raw_lon = parts[4]
                    lon_dir = parts[5]

                    if raw_lat and raw_lon:

                        # Convert NMEA → Decimal
                        lat = float(raw_lat)
                        lon = float(raw_lon)

                        lat_deg = int(lat / 100)
                        lat_min = lat - (lat_deg * 100)

                        lon_deg = int(lon / 100)
                        lon_min = lon - (lon_deg * 100)

                        lat = lat_deg + (lat_min / 60)
                        lon = lon_deg + (lon_min / 60)

                        # Direction correction
                        if lat_dir == "S":
                            lat = -lat
                        if lon_dir == "W":
                            lon = -lon

                        self.captured_latitude = lat
                        self.captured_longitude = lon
                        self.gps_fix = True

        except Exception as e:
            logger.debug(f"[GPS] Read error: {e}")

    def _get_valid_gps(self):
        """
        Returns valid GPS coordinates.
        Priority:
        1. Device GPS (passed from MainDashboard)
        2. CURRENT_LAT / CURRENT_LON fallback
        """
        # Try to get GPS from parent (MainDashboard)
        parent = self.parent()
        if parent and hasattr(parent, 'gps_lat') and hasattr(parent, 'gps_lon') and parent.gps_fix:
            if parent.gps_lat != 0.0 and parent.gps_lon != 0.0:
                logger.info("[GPS] Using shared device GPS from parent")
                self.captured_latitude = parent.gps_lat
                self.captured_longitude = parent.gps_lon
                self.gps_fix = True
                return float(parent.gps_lat), float(parent.gps_lon)

        # 2️⃣ CURRENT_LAT / CURRENT_LON fallback
        try:
            lat = float(CURRENT_LAT)
            lon = float(CURRENT_LON)

            if lat != 0 and lon != 0:
                logger.info("[GPS] Using CURRENT_LAT/CURRENT_LON fallback")
                return lat, lon

        except Exception:
            pass

        logger.warning("[GPS] No valid GPS found")
        return None, None

    # def _load_map_and_data(self):
    #     nearby = _nearby_manholes(CURRENT_LAT, CURRENT_LON, NEARBY_RADIUS_M)
    #     html = _build_leaflet_html(CURRENT_LAT, CURRENT_LON, nearby, NEARBY_RADIUS_M)
    #     self._web.setHtml(html, QUrl("about:blank"))

    #     # Populate table
    #     for i in reversed(range(self._nearby_list_lay.count())):
    #         self._nearby_list_lay.itemAt(i).widget().setParent(None)

    #     for mh in nearby[:5]:  # Show top 5
    #         row = QFrame()
    #         row.setStyleSheet("""
    #             QFrame { border-bottom: 1px solid #F0F0F0; }
    #             QFrame:hover { background:#F1F7F8; }
    #         """)

    #         row_lay = QHBoxLayout(row)

    #         id_lbl = QLabel(mh["id"])
    #         row_lay.addWidget(id_lbl, 1)

    #         loc_btn = QPushButton(f"{mh['lat']:.4f},{mh['lon']:.4f}")
    #         loc_btn.setStyleSheet("""
    #             QPushButton {
    #                 color: #1A92A4;
    #                 border: none;
    #                 text-decoration: underline;
    #                 background: transparent;
    #                 text-align: left;
    #             }
    #         """)

    #         row_lay.addWidget(loc_btn, 2)

    #         # click events
    #         loc_btn.clicked.connect(lambda _, m=mh: self._on_nearby_selected(m))
    #         row.mousePressEvent = lambda e, m=mh: self._on_nearby_selected(m)

    #         self._nearby_list_lay.addWidget(row)

    def _load_map_and_data(self):
        self._read_gps()
        lat, lon = self._get_valid_gps()

        # update UI labels
        if lat is not None and lon is not None:
            self._lat_val.setText(f"{lat:.10f}")
            self._lon_val.setText(f"{lon:.10f}")
        else:
            self._lat_val.setText("0.0000000000")
            self._lon_val.setText("0.0000000000")
            lat, lon = CURRENT_LAT, CURRENT_LON

        # Show a loading placeholder in the Web Engine
        self._web.setHtml("<html><body style='display:flex;justify-content:center;align-items:center;height:100%;font-family:sans-serif;color:#666;'><h2>Loading map and nearby manholes...</h2></body></html>", QUrl("about:blank"))

        # Clear previous rows
        for i in reversed(range(self._nearby_list_lay.count())):
            w = self._nearby_list_lay.itemAt(i).widget()
            if w:
                w.deleteLater()
                
        # Add a loading label to nearby list
        loading_lbl = QLabel("Loading nearby manholes...")
        loading_lbl.setStyleSheet("color: #666; padding: 10px;")
        self._nearby_list_lay.addWidget(loading_lbl)

        # Start background thread
        self.loader_thread = DataLoaderThread(lat, lon, NEARBY_RADIUS_M)
        self.loader_thread.data_loaded.connect(self._on_data_loaded)
        self.loader_thread.start()

    def _on_data_loaded(self, nearby):
        lat = self.loader_thread.lat
        lon = self.loader_thread.lon

        html = _build_leaflet_html(lat, lon, nearby, NEARBY_RADIUS_M)
        self._web.setHtml(html, QUrl("about:blank"))

        # Clear previous rows (including loading label)
        for i in reversed(range(self._nearby_list_lay.count())):
            w = self._nearby_list_lay.itemAt(i).widget()
            if w:
                w.deleteLater()

        # Populate nearby list
        for mh in nearby[:5]:

            row = QFrame()
            row.setStyleSheet("""
                QFrame { border-bottom: 1px solid #F0F0F0; }
                QFrame:hover { background:#F1F7F8; }
            """)

            row_lay = QHBoxLayout(row)

            id_lbl = QLabel(mh["id"])
            row_lay.addWidget(id_lbl, 1)

            loc_btn = QPushButton(f"{mh['lat']:.4f},{mh['lon']:.4f}")
            loc_btn.setStyleSheet("""
                QPushButton {
                    color:#1A92A4;
                    border:none;
                    background:transparent;
                    text-decoration:underline;
                    text-align:left;
                }
            """)
            row_lay.addWidget(loc_btn, 2)

            loc_btn.clicked.connect(lambda _, m=mh: self._on_nearby_selected(m))
            row.mousePressEvent = lambda e, m=mh: self._on_nearby_selected(m)

            self._nearby_list_lay.addWidget(row)

    def _on_map_selection(self, manhole_id: str):
        self.manhole_id = manhole_id
        self._manual_input.setText(manhole_id)
        # In a real app we'd lookup coordinates
        self._selected_coord_val.setText("Coordinates Selected")

    def _on_nearby_selected(self, mh):
        self.manhole_id = mh["id"]

        self._manual_input.setText(self.manhole_id)

        self._selected_coord_val.setText(
            f"{mh['id']}  ({mh['lat']:.6f}, {mh['lon']:.6f})"
    )

    def _on_manual_use(self):
        self.manhole_id = self._manual_input.text()
        self._selected_coord_val.setText(f"Manual: {self.manhole_id}")

# ---------------------------------------------------------------------------
# Stop Operation Popup
# ---------------------------------------------------------------------------
class StopOperationPopup(QDialog):
    def __init__(self, manhole_id, elapsed_time, start_time=None, end_time=None, before_depth=None, after_depth=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Operation Stopped")
        self.setFixedSize(460, 380) # Increased height
        self.setStyleSheet("background-color: #f0f2f5;")
        self.manhole_id = manhole_id
        self.elapsed_time = elapsed_time
        self.start_time = start_time
        self.end_time = end_time
        self.before_depth = before_depth
        self.after_depth = after_depth
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(14)
        title = QLabel("📋 Operation Summary")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1a1a2e;")
        info_frame = QFrame()
        info_frame.setStyleSheet("background-color: #e3f2fd; border-radius: 8px; padding: 4px;")
        info_layout = QVBoxLayout(info_frame)
        def _row(label, value):
            lbl = QLabel(f"<b>{label}</b> {value}")
            lbl.setStyleSheet("font-size: 14px; color: #1a1a1a; border: none;")
            return lbl
        info_layout.addWidget(_row("Manhole ID:", self.manhole_id))
        info_layout.addWidget(_row("Elapsed Time:", self.elapsed_time))
        fmt = "%d-%m-%Y %H:%M:%S"
        if self.start_time: info_layout.addWidget(_row("Start Time:", self.start_time.strftime(fmt)))
        if self.end_time: info_layout.addWidget(_row("End Time:", self.end_time.strftime(fmt)))
        
        info_layout.addSpacing(10)
        if self.before_depth is not None:
             info_layout.addWidget(_row("Before Depth:", f"{self.before_depth} CM"))
        if self.after_depth is not None:
             info_layout.addWidget(_row("After Depth:", f"{self.after_depth} CM"))
        if self.before_depth is not None and self.after_depth is not None:
             diff = self.before_depth - self.after_depth
             info_layout.addWidget(_row("Silt Removal:", f"{diff} CM"))
        
        ok_btn = QPushButton("✔ Close & Save")
        ok_btn.setFixedHeight(45)
        ok_btn.setStyleSheet("background-color: #219EBC; color: white; font-weight: bold; border-radius: 6px; font-size: 15px; border: none;")
        ok_btn.clicked.connect(self.accept)
        layout.addWidget(title)
        layout.addWidget(info_frame)
        layout.addStretch()
        layout.addWidget(ok_btn)

if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    window = StartOperationPopup()
    window.show()
    sys.exit(app.exec_())
