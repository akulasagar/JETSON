"""Manhole data loader.

Loads manhole locations from PostgreSQL (primary) or CSV (fallback),
with in-memory caching and nearby search functionality.
"""
import os
import csv
import logging

from utils.geo import haversine_m
from core.database import get_cursor, reset_connection

logger = logging.getLogger(__name__)

MANHOLE_CSV = os.path.join(os.path.dirname(__file__), "..", "manhole.csv")
TABLE_NAME = "master_manholes"
NEARBY_RADIUS_M = 50

# Module-level cache
_cached_manholes = None


def _load_from_db():
    """Load manholes from PostgreSQL. Returns None if DB fails."""
    try:
        cursor = get_cursor()
        if not cursor:
            logger.warning("[MANHOLE-DB] No DB connection available.")
            return None

        query = f"""
        SELECT
            mh_id AS id,
            mh_latitude AS lat,
            mh_longitude AS lon
        FROM {TABLE_NAME}
        """

        logger.debug(f"[MANHOLE-DB] Executing query: {query}")
        cursor.execute(query)
        rows = cursor.fetchall()

        manholes = []
        for r in rows:
            if r["lat"] is not None and r["lon"] is not None:
                manholes.append({
                    "id": str(r["id"]),
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"])
                })

        logger.info(f"[MANHOLE-LOAD] Loaded {len(manholes)} manholes from DB")
        return manholes

    except Exception as e:
        logger.warning(f"[MANHOLE-DB] DB load failed, fallback to CSV: {e}")
        reset_connection()
        return None


def _load_from_csv():
    """Load manholes from CSV file."""
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
    return manholes


def load_manholes():
    """Load all manholes (DB first, CSV fallback). Results are cached."""
    global _cached_manholes
    if _cached_manholes is not None:
        return _cached_manholes

    # Try DB first
    db_data = _load_from_db()
    if db_data:
        _cached_manholes = db_data
        return db_data

    # Fallback to CSV
    csv_data = _load_from_csv()
    _cached_manholes = csv_data
    return csv_data


def nearby_manholes(lat, lon, radius_m=NEARBY_RADIUS_M):
    """Find manholes within radius_m of the given GPS coordinate."""
    all_mh = load_manholes()
    nearby = []
    for mh in all_mh:
        d = haversine_m(lat, lon, mh["lat"], mh["lon"])
        if d <= radius_m:
            nearby.append({**mh, "dist_m": round(d)})
    nearby.sort(key=lambda x: x["dist_m"])
    return nearby


def invalidate_cache():
    """Clear the cached manhole data (e.g. after a DB update)."""
    global _cached_manholes
    _cached_manholes = None
