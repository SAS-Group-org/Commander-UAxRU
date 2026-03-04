# sensor.py — physics-based multi-spectrum sensor model

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from constants import BURNTHROUGH_RANGE_KM
from geo import haversine, check_line_of_sight

if TYPE_CHECKING:
    from scenario import PlatformDef, Unit

# ── Physical constants ────────────────────────────────────────────────────────
RCS_REFERENCE_M2: float = 5.0
GROUND_SENSOR_HEIGHT_M: float = 5.0
ECM_SCALE: float = 0.60

FAINT_BAND:    float = 1.00
PROBABLE_BAND: float = 0.75
CONFIRM_BAND:  float = 0.50
CONTACT_TIMEOUT_S: float = 30.0

@dataclass
class Contact:
    uid:            str
    est_lat:        float
    est_lon:        float
    altitude_ft:    float
    classification: str                
    unit_type:      Optional[str]      
    perceived_side: str                
    last_update:    float 
    sensor_type:    str = "NONE"
    pos_error_km:   float = 0.0
    
    # State tracking for smooth error drift
    error_angle:       float = 0.0
    base_pos_error_km: float = 0.0

def classify_detection(sensor_unit: "Unit", target: "Unit", dist_km: float) -> tuple[str, str]:
    if not check_line_of_sight(sensor_unit.lat, sensor_unit.lon, sensor_unit.altitude_ft, 
                               target.lat, target.lon, target.altitude_ft):
        return "NONE", "NONE"

    best_cls = "NONE"
    best_sen = "NONE"
    rank = {"NONE": 0, "FAINT": 1, "PROBABLE": 2, "CONFIRMED": 3}
    
    penalty = getattr(sensor_unit, 'inefficiency_penalty', 0.0)

    # 1. ESM (Passive radar sniffing - Huge Range, Poor Resolution)
    if getattr(target, 'radar_active', False) and target.platform.radar_range_km > 0:
        esm_range = sensor_unit.platform.esm_range_km * (1.0 - penalty)
        if dist_km <= esm_range:
            best_cls = "PROBABLE" 
            best_sen = "ESM"

    # 2. IR / FLIR / Optical (Thermal/Visual - Short Range, Perfect Resolution)
    ir_range = sensor_unit.platform.ir_range_km * (1.0 - penalty)
    if dist_km <= ir_range:
        if rank["CONFIRMED"] > rank[best_cls]:
            best_cls = "CONFIRMED"
            best_sen = "IR"

    # 3. Active Radar (Standard Ping)
    if getattr(sensor_unit, 'radar_active', True) and sensor_unit.platform.radar_range_km > 0:
        rcs_ratio  = max(target.platform.rcs_m2, 0.01) / RCS_REFERENCE_M2
        R_rcs      = (sensor_unit.platform.radar_range_km * sensor_unit.performance_mult) * (rcs_ratio ** 0.25)
        
        ecm_penalty = 0.0
        if target.is_jamming and dist_km > BURNTHROUGH_RANGE_KM:
            ecm_penalty = target.platform.ecm_rating * ECM_SCALE
            
        R_effective = R_rcs * max(0.0, 1.0 - ecm_penalty) * (1.0 - penalty)

        if R_effective > 0.0 and dist_km <= R_effective * FAINT_BAND:
            fraction = dist_km / R_effective       
            cls = "CONFIRMED" if fraction <= CONFIRM_BAND else "PROBABLE" if fraction <= PROBABLE_BAND else "FAINT"
            
            if rank[cls] > rank[best_cls]:
                best_cls = cls
                best_sen = "RADAR"

    return best_cls, best_sen


def update_local_contacts(sensor_units: list["Unit"], target_units: list["Unit"], 
                          local_contacts: dict[str, Contact], game_time: float) -> None:
    _RANK = {"NONE": 0, "FAINT": 1, "PROBABLE": 2, "CONFIRMED": 3}
    refreshed: set[str] = set()

    for target in target_units:
        if not target.alive: continue

        best_cls  = "NONE"
        best_sen  = "NONE"
        best_rank = 0
        best_dist = 9999.0

        for sensor in sensor_units:
            if not sensor.alive: continue
            dist = haversine(sensor.lat, sensor.lon, target.lat, target.lon)
            cls, sen = classify_detection(sensor, target, dist)
            
            if _RANK[cls] > best_rank:
                best_rank = _RANK[cls]
                best_cls  = cls
                best_sen  = sen
                best_dist = dist

        if best_cls == "NONE": continue  
        refreshed.add(target.uid)

        # IFF Interrogation & Perception Logic
        p_side = "UNKNOWN"
        if getattr(target, 'iff_active', False) and target.side == sensor_units[0].side:
            p_side = target.side  
        elif best_sen == "IR" or best_rank >= 3:
            p_side = target.side  
        else:
            existing = local_contacts.get(target.uid)
            if existing and existing.perceived_side != "UNKNOWN":
                p_side = existing.perceived_side

        # Calculate Positional Error (Latency & Sensor Accuracy)
        # TUNED: Reduced base errors to prevent massive UI jumps at long distances
        error_km = 0.0
        if best_sen == "ESM": error_km = best_dist * 0.04  
        elif best_sen == "RADAR": error_km = best_dist * 0.004 
        elif best_sen == "IR": error_km = best_dist * 0.0005    
        
        unit_type = target.platform.unit_type if best_rank >= 2 else None

        contact = local_contacts.get(target.uid)
        if contact is None:
            angle = random.uniform(0, 360)
            dlat = (math.cos(math.radians(angle)) * error_km) / 111.32
            dlon = (math.sin(math.radians(angle)) * error_km) / (111.32 * max(0.0001, math.cos(math.radians(target.lat))))
            
            local_contacts[target.uid] = Contact(
                uid=target.uid, est_lat=target.lat + dlat, est_lon=target.lon + dlon, altitude_ft=target.altitude_ft,
                classification=best_cls, unit_type=unit_type, perceived_side=p_side, last_update=game_time, 
                sensor_type=best_sen, pos_error_km=error_km, error_angle=angle, base_pos_error_km=error_km
            )
        else:
            # TUNED: Extreme slow-down of the random angle walk to stop jitter
            contact.error_angle = (contact.error_angle + random.uniform(-0.5, 0.5)) % 360
            dlat = (math.cos(math.radians(contact.error_angle)) * error_km) / 111.32
            dlon = (math.sin(math.radians(contact.error_angle)) * error_km) / (111.32 * max(0.0001, math.cos(math.radians(target.lat))))
            
            ideal_lat = target.lat + dlat
            ideal_lon = target.lon + dlon
            
            # TUNED: Dropped interpolation rate to 0.2% per frame for heavy visual inertia
            contact.est_lat += (ideal_lat - contact.est_lat) * 0.002
            contact.est_lon += (ideal_lon - contact.est_lon) * 0.002
            
            contact.altitude_ft = target.altitude_ft
            contact.last_update = game_time
            contact.sensor_type = best_sen
            contact.base_pos_error_km = error_km
            contact.pos_error_km = error_km
            
            if best_rank >= _RANK[contact.classification]:
                contact.classification = best_cls
                contact.unit_type = unit_type
            if p_side != "UNKNOWN":
                contact.perceived_side = p_side

    # Extrapolate error for stale tracks
    for uid, c in list(local_contacts.items()):
        if uid not in refreshed:
            staleness = game_time - c.last_update
            c.pos_error_km = c.base_pos_error_km + (staleness * 0.15) 
            c.classification = "FAINT" 
            
            if staleness > CONTACT_TIMEOUT_S:
                del local_contacts[uid]