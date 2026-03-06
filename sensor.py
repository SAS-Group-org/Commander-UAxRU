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
    is_emitting = getattr(target, 'search_radar_active', False) or getattr(target, 'fc_radar_active', False)
    if is_emitting and target.platform.radar_range_km > 0:
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

    # 3. Active Radar (Requires Search Radar to be active)
    if getattr(sensor_unit, 'search_radar_active', True) and sensor_unit.platform.radar_range_km > 0:
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

        # ─── IFF SPOOFING & FRATRICIDE LOGIC ──────────────────────────────────
        actual_side = target.side
        observer_side = sensor_units[0].side
        opp_side = "Red" if actual_side == "Blue" else "Blue"
        
        existing = local_contacts.get(target.uid)
        
        # Decide if we need to make a new probabilistic IFF roll
        make_new_roll = False
        if not existing or existing.perceived_side == "UNKNOWN":
            make_new_roll = True
        elif best_sen == "IR" and existing.perceived_side != actual_side:
            make_new_roll = True # Visual ID instantly corrects IFF errors
        elif best_rank >= 2 and existing.perceived_side != actual_side and random.random() < 0.05:
            make_new_roll = True # 5% chance per tick to realize a mistake if the track is solid
            
        if make_new_roll:
            misid_chance = 0.0
            if best_sen != "IR":
                if best_rank == 3: misid_chance = 0.02
                elif best_rank == 2: misid_chance = 0.15
                elif best_rank == 1: misid_chance = 0.35
                
                # Distance penalty (up to +20% at long ranges)
                misid_chance += min(0.20, (best_dist / 150.0) * 0.15)
                
                # ECM / Jamming severely impacts IFF interrogation
                if target.is_jamming: misid_chance += 0.25
                
                # Friendly IFF transponder cuts misidentification chance by 90%
                if actual_side == observer_side and getattr(target, 'iff_active', False):
                    misid_chance *= 0.10
                    
            if random.random() < misid_chance:
                # IFF Failed!
                if random.random() < 0.40:
                    p_side = opp_side  # Dangerous mis-ID (Fratricide risk!)
                else:
                    p_side = "UNKNOWN" # Safe mis-ID (System just can't tell)
            else:
                p_side = actual_side
        else:
            p_side = existing.perceived_side if existing else "UNKNOWN"
        # ──────────────────────────────────────────────────────────────────────

        # Calculate Positional Error (Latency & Sensor Accuracy)
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
            contact.error_angle = (contact.error_angle + random.uniform(-0.5, 0.5)) % 360
            dlat = (math.cos(math.radians(contact.error_angle)) * error_km) / 111.32
            dlon = (math.sin(math.radians(contact.error_angle)) * error_km) / (111.32 * max(0.0001, math.cos(math.radians(target.lat))))
            
            ideal_lat = target.lat + dlat
            ideal_lon = target.lon + dlon
            
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