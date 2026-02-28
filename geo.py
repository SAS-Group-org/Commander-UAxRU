# geo.py — pure geo-math, no pygame, no game state

import math
from constants import TILE_SIZE


def lat_lon_to_pixel(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Web-Mercator projection → absolute pixel coordinates at given zoom."""
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n * TILE_SIZE
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n * TILE_SIZE
    return x, y


def pixel_to_lat_lon(x: float, y: float, zoom: int) -> tuple[float, float]:
    """Inverse Web-Mercator: absolute pixel → (lat, lon)."""
    n = 2.0 ** zoom
    lon = x / TILE_SIZE / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / TILE_SIZE / n)))
    return math.degrees(lat_rad), lon


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    R = 6_371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """True bearing from point-1 to point-2 in degrees (0 = North)."""
    dlon  = math.radians(lon2 - lon1)
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = (math.cos(lat1r) * math.sin(lat2r)
         - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon))
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def world_to_screen(lat: float, lon: float,
                    cam_px: float, cam_py: float,
                    zoom: int,
                    map_w: int, map_h: int) -> tuple[float, float]:
    """Convert lat/lon to on-screen pixel coordinates."""
    ax, ay = lat_lon_to_pixel(lat, lon, zoom)
    return ax - cam_px + map_w / 2, ay - cam_py + map_h / 2
