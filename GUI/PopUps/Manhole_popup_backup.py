import os
import csv
import math
import logging
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QFrame, QApplication,
                             QSizePolicy, QWidget)
from PyQt5.QtCore import Qt, QUrl, QObject, pyqtSlot, pyqtSignal
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings
from PyQt5.QtWebChannel import QWebChannel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GPS placeholder – will be swapped for real GPS data once the module arrives
# ---------------------------------------------------------------------------
CURRENT_LAT = 17.457055
CURRENT_LON = 78.370818
NEARBY_RADIUS_M = 50   # metres – widen if you see no manholes in your area

# ---------------------------------------------------------------------------
# CSV loading (same field-name logic as dev_test_load.py)
# ---------------------------------------------------------------------------
MANHOLE_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "manhole.csv")

def _load_manholes():
    """Load manhole records from CSV. Returns list of {id, lat, lon} dicts."""
    manholes = []
    csv_path = os.path.abspath(MANHOLE_CSV)
    if not os.path.exists(csv_path):
        logger.warning(f"[MANHOLE-CSV] Not found: {csv_path}")
        return manholes

    id_fields  = ["manhole_id", "sw_mh_id", "id", "MH_ID", "Manhole_ID", "MANHOLE_ID", "manhole"]
    lat_fields = ["lat", "latitude", "lat_dd", "LATITUDE", "Latitude", "LAT", "y"]
    lon_fields = ["lon", "longitude", "lon_dd", "LONGITUDE", "Longitude", "LON", "x"]

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
                            lat = float(row[field].strip()); break
                        except ValueError:
                            pass
                for field in lon_fields:
                    if field in row and row[field]:
                        try:
                            lon = float(row[field].strip()); break
                        except ValueError:
                            pass

                if lat is not None and lon is not None:
                    manholes.append({"id": raw_id, "lat": lat, "lon": lon})

    except Exception as e:
        logger.error(f"[MANHOLE-CSV] Error reading csv: {e}")

    logger.info(f"[MANHOLE-CSV] Loaded {len(manholes)} records from {csv_path}")
    return manholes


def _haversine_m(lat1, lon1, lat2, lon2):
    """Return distance in metres between two WGS-84 coordinates."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
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
# Qt ↔ JavaScript bridge
# ---------------------------------------------------------------------------
class _Bridge(QObject):
    """Receives marker-click events from the Leaflet map."""
    manhole_selected = pyqtSignal(str)   # emits manhole ID

    @pyqtSlot(str)
    def on_manhole_selected(self, manhole_id: str):
        logger.info(f"[BRIDGE] Manhole selected from map: {manhole_id}")
        self.manhole_selected.emit(manhole_id)


# ---------------------------------------------------------------------------
# Leaflet HTML template
# ---------------------------------------------------------------------------
def _build_leaflet_html(center_lat, center_lon, manholes, radius_m):
    """Generate a self-contained HTML string with Leaflet + QWebChannel."""

    # Build the JS marker array
    markers_js = []
    for mh in manholes:
        safe_id = mh["id"].replace("'", "\\'")
        markers_js.append(
            f"  {{id: '{safe_id}', lat: {mh['lat']}, lon: {mh['lon']}, dist: {mh['dist_m']}}}"
        )
    markers_str = "[\n" + ",\n".join(markers_js) + "\n]"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Manhole Map</title>
<link rel="stylesheet"
      href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="qrc:///qtwebchannel/qwebchannel.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  html, body {{ height: 100%; width: 100%; }}
  #map {{ position: absolute; top: 0; left: 0; right: 0; bottom: 60px; }}
  #info-bar {{
    position: absolute; bottom: 0; left: 0; right: 0; height: 60px;
    background: #1a1a2e; color: white;
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 16px; font-family: Arial, sans-serif; font-size: 14px;
  }}
  #selected-label {{ font-size: 15px; font-weight: bold; color: #00e676; }}
  #confirm-btn {{
    padding: 10px 24px; background: #00c853; color: white;
    border: none; border-radius: 6px; font-size: 14px; font-weight: bold;
    cursor: pointer; display: none;
  }}
  #confirm-btn:hover {{ background: #00a847; }}
</style>
</head>
<body>
<div id="map"></div>
<div id="info-bar">
  <span id="selected-label">📍 Click a manhole marker to select it</span>
  <button id="confirm-btn" onclick="confirmSelection()">✔ Confirm Selection</button>
</div>
<script>
var bridge = null;
var selectedId = null;
var selectedMarker = null;

var blueIcon = L.divIcon({{
  className: '',
  html: '<div style="width:14px;height:14px;border-radius:50%;background:#2196F3;border:2px solid #0D47A1;box-shadow:0 0 4px rgba(0,0,0,.5);"></div>',
  iconSize:[14,14], iconAnchor:[7,7]
}});
var greenIcon = L.divIcon({{
  className: '',
  html: '<div style="width:18px;height:18px;border-radius:50%;background:#00e676;border:2.5px solid #00a847;box-shadow:0 0 6px rgba(0,200,80,.7);"></div>',
  iconSize:[18,18], iconAnchor:[9,9]
}});
var currentIcon = L.divIcon({{
  className: '',
  html: '<div style="width:16px;height:16px;border-radius:50%;background:#ff5722;border:2px solid #bf360c;box-shadow:0 0 6px rgba(255,80,0,.6);"></div>',
  iconSize:[16,16], iconAnchor:[8,8]
}});

// Init WebChannel
new QWebChannel(qt.webChannelTransport, function(ch) {{
  bridge = ch.objects.bridge;
}});

// Init map
var map = L.map('map').setView([{center_lat}, {center_lon}], 16);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '&copy; OpenStreetMap contributors',
  maxZoom: 20
}}).addTo(map);

// Current location marker
L.marker([{center_lat}, {center_lon}], {{icon: currentIcon}})
 .addTo(map)
 .bindPopup('<b>📍 Current Location</b>')
 .openPopup();

// Radius circle
L.circle([{center_lat}, {center_lon}], {{
  radius: {radius_m},
  color: '#2196F3', weight: 1.5, opacity: 0.5,
  fill: true, fillColor: '#2196F3', fillOpacity: 0.05
}}).addTo(map);

// Manhole markers
var manholes = {markers_str};
var markers = {{}};

manholes.forEach(function(mh) {{
  var m = L.marker([mh.lat, mh.lon], {{icon: blueIcon}}).addTo(map);
  m.bindTooltip('<b>' + mh.id + '</b><br>' + mh.dist + ' m away', {{
    direction: 'top', offset: [0, -8]
  }});
  m.on('click', function() {{
    if (selectedMarker) selectedMarker.setIcon(blueIcon);
    m.setIcon(greenIcon);
    selectedMarker = m;
    selectedId = mh.id;
    document.getElementById('selected-label').textContent = '✅ Selected: ' + mh.id + '   (' + mh.dist + ' m)';
    document.getElementById('confirm-btn').style.display = 'block';
  }});
  markers[mh.id] = m;
}});

function confirmSelection() {{
  if (selectedId && bridge) {{
    bridge.on_manhole_selected(selectedId);
  }}
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Start Operation Popup  (upgraded)
# ---------------------------------------------------------------------------
class StartOperationPopup(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start Operation – Select Manhole")
        self.setMinimumSize(900, 680)
        self.setStyleSheet("""
            QDialog  { background-color: #f0f2f5; }
            QLabel   { color: #1a1a1a; }
        """)

        self.manhole_id = None
        self._bridge = _Bridge()
        self._bridge.manhole_selected.connect(self._on_map_selection)

        self._init_ui()

    # ------------------------------------------------------------------
    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header ──────────────────────────────────────────────────────
        header = QFrame()
        header.setFixedHeight(56)
        header.setStyleSheet("background-color: #1a1a2e; border: none;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(20, 0, 20, 0)

        title = QLabel("🗺  Select Manhole")
        title.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")

        self._coord_label = QLabel(
            f"📍 Current Location: {CURRENT_LAT:.6f}, {CURRENT_LON:.6f}"
        )
        self._coord_label.setStyleSheet(
            "color: #90caf9; font-size: 13px; margin-left: 20px;"
        )

        self._radius_label = QLabel(f"Radius: {NEARBY_RADIUS_M} m")
        self._radius_label.setStyleSheet("color: #aaa; font-size: 12px;")

        h_lay.addWidget(title)
        h_lay.addWidget(self._coord_label)
        h_lay.addStretch()
        h_lay.addWidget(self._radius_label)
        root.addWidget(header)

        # ── Info strip ──────────────────────────────────────────────────
        self._info_strip = QLabel(
            "🔍 Loading nearby manholes from CSV …"
        )
        self._info_strip.setAlignment(Qt.AlignCenter)
        self._info_strip.setFixedHeight(30)
        self._info_strip.setStyleSheet(
            "background:#e3f2fd; color:#0277bd; font-size:13px; font-weight:bold;"
        )
        root.addWidget(self._info_strip)

        # ── Map ─────────────────────────────────────────────────────────
        self._web = QWebEngineView()
        self._web.settings().setAttribute(
            QWebEngineSettings.LocalContentCanAccessRemoteUrls, True
        )
        self._web.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # Wire QWebChannel
        self._channel = QWebChannel()
        self._channel.registerObject("bridge", self._bridge)
        self._web.page().setWebChannel(self._channel)

        root.addWidget(self._web, 1)

        # ── Footer ──────────────────────────────────────────────────────
        footer = QFrame()
        footer.setFixedHeight(56)
        footer.setStyleSheet("background-color: #1a1a2e; border: none;")
        f_lay = QHBoxLayout(footer)
        f_lay.setContentsMargins(20, 0, 20, 0)
        f_lay.setSpacing(12)

        self._sel_label = QLabel("No manhole selected")
        self._sel_label.setStyleSheet("color: #ccc; font-size: 14px;")

        cancel_btn = QPushButton("✕  Cancel")
        cancel_btn.setFixedSize(120, 38)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: #37474f; color: white;
                border-radius: 6px; font-weight: bold; font-size: 14px;
            }
            QPushButton:hover { background: #546e7a; }
        """)
        cancel_btn.clicked.connect(self.reject)

        f_lay.addWidget(self._sel_label)
        f_lay.addStretch()
        f_lay.addWidget(cancel_btn)
        root.addWidget(footer)

        # Load map immediately
        self._load_map()

    # ------------------------------------------------------------------
    def _load_map(self):
        nearby = _nearby_manholes(CURRENT_LAT, CURRENT_LON, NEARBY_RADIUS_M)
        count = len(nearby)

        if count == 0:
            self._info_strip.setText(
                f"⚠  No manholes found within {NEARBY_RADIUS_M} m of current location. "
                "Try increasing NEARBY_RADIUS_M."
            )
            self._info_strip.setStyleSheet(
                "background:#fff3e0; color:#e65100; font-size:13px; font-weight:bold;"
            )
        else:
            self._info_strip.setText(
                f"✅  {count} manholes found within {NEARBY_RADIUS_M} m — click a marker to select"
            )
            self._info_strip.setStyleSheet(
                "background:#e8f5e9; color:#2e7d32; font-size:13px; font-weight:bold;"
            )

        html = _build_leaflet_html(CURRENT_LAT, CURRENT_LON, nearby, NEARBY_RADIUS_M)
        self._web.setHtml(html, QUrl("about:blank"))
        logger.info(f"[MAP] Leaflet map loaded with {count} nearby manholes.")

    # ------------------------------------------------------------------
    def _on_map_selection(self, manhole_id: str):
        """Called when user clicks a marker in the Leaflet map."""
        self.manhole_id = manhole_id
        self._sel_label.setText(f"✅  Selected: {manhole_id}")
        self._sel_label.setStyleSheet(
            "color: #00e676; font-size: 14px; font-weight: bold;"
        )
        logger.info(f"[POPUP] Manhole confirmed: {manhole_id}")
        # Auto-accept after a brief delay so the user can see the highlight
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(600, self.accept)


# ---------------------------------------------------------------------------
# Stop Operation Popup  (unchanged in logic, styled to match)
# ---------------------------------------------------------------------------
class StopOperationPopup(QDialog):
    def __init__(self, manhole_id, elapsed_time, start_time=None, end_time=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Operation Stopped")
        self.setFixedSize(460, 310)
        self.setStyleSheet("background-color: #f0f2f5;")

        self.manhole_id   = manhole_id
        self.elapsed_time = elapsed_time
        self.start_time   = start_time   # datetime object or None
        self.end_time     = end_time     # datetime object or None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(14)

        title = QLabel("📋  Operation Summary")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1a1a2e;")

        info_frame = QFrame()
        info_frame.setStyleSheet("background-color: #e3f2fd; border-radius: 8px; padding: 4px;")
        info_layout = QVBoxLayout(info_frame)
        info_layout.setSpacing(8)

        def _row(label, value):
            lbl = QLabel(f"<b>{label}</b>  {value}")
            lbl.setStyleSheet("font-size: 14px; color: #1a1a1a;")
            return lbl

        info_layout.addWidget(_row("Manhole ID:", self.manhole_id))
        info_layout.addWidget(_row("Elapsed Time:", self.elapsed_time))

        fmt = "%d-%m-%Y  %H:%M:%S"
        if self.start_time:
            info_layout.addWidget(_row("Start Time:", self.start_time.strftime(fmt)))
        if self.end_time:
            info_layout.addWidget(_row("End Time:", self.end_time.strftime(fmt)))

        ok_btn = QPushButton("✔  Close & Save")
        ok_btn.setFixedHeight(45)
        ok_btn.setStyleSheet(
            "background-color: #219EBC; color: white; font-weight: bold; "
            "border-radius: 6px; font-size: 15px;"
        )
        ok_btn.clicked.connect(self.accept)

        layout.addWidget(title)
        layout.addWidget(info_frame)
        layout.addStretch()
        layout.addWidget(ok_btn)

