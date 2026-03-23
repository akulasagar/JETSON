"""Geographic utility functions.

Provides haversine distance calculation for GPS coordinate comparison.
"""
import math


def haversine_m(lat1, lon1, lat2, lon2):
    """Calculate the great-circle distance in metres between two points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
