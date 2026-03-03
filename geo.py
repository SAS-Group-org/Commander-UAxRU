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

def slant_range_km(lat1: float, lon1: float, alt1_ft: float,
                   lat2: float, lon2: float, alt2_ft: float) -> float:
    """True 3D slant range incorporating altitude differences."""
    dist_2d = haversine(lat1, lon1, lat2, lon2)
    dist_vert = (alt1_ft - alt2_ft) * 0.0003048  # Convert feet to km
    return math.hypot(dist_2d, dist_vert)

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

def get_elevation_ft(lat: float, lon: float) -> float:
    """Procedural pseudo-DEM for Eastern Europe / Ukraine without huge files."""
    # Black sea bounding
    if lat < 46.5 and lon < 36.0 and lat < (46.5 - (36.0 - lon)*0.5): return 0.0 
    
    # Base rolling hills for Donbas/Central
    hills = (math.sin(lat * 15.0) * math.cos(lon * 15.0)) * 400.0
    
    # Carpathian mountains in the far west
    if lon < 26.0:
        mountains = (math.sin(lat * 20.0) * math.cos(lon * 20.0)) * 3500.0
        return max(0.0, 1000.0 + hills + mountains)
        
    return max(0.0, 300.0 + hills)

def check_line_of_sight(lat1: float, lon1: float, alt1_ft: float, 
                        lat2: float, lon2: float, alt2_ft: float) -> bool:
    """Checks radar horizon curvature AND intermediate terrain masking."""
    # 1. Earth Curvature (Radar Horizon)
    alt1_m = max(alt1_ft * 0.3048, 5.0)
    alt2_m = max(alt2_ft * 0.3048, 5.0)
    horizon_km = 4.12 * (math.sqrt(alt1_m) + math.sqrt(alt2_m))
    
    dist_km = haversine(lat1, lon1, lat2, lon2)
    if dist_km > horizon_km: 
        return False

    # 2. Fast Midpoint Terrain Raycast (Mountains blocking low-flyers)
    mid_lat = (lat1 + lat2) / 2.0
    mid_lon = (lon1 + lon2) / 2.0
    mid_terrain_ft = get_elevation_ft(mid_lat, mid_lon)
    
    mid_ray_alt_ft = (alt1_ft + alt2_ft) / 2.0
    if mid_ray_alt_ft < mid_terrain_ft:
        return False # A hill/mountain is between the sensor and the target
        
    return True