# sensor.py — physics-based multi-spectrum sensor model

from __future__ import annotations

import math
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
    lat:            float
    lon:            float
    altitude_ft:    float
    classification: str                
    unit_type:      Optional[str]      
    side:           Optional[str]      
    last_update:    float 
    sensor_type:    str = "NONE" # "RADAR", "ESM", or "IR"

def classify_detection(sensor_unit: "Unit", target: "Unit", dist_km: float) -> tuple[str, str]:
    """Returns (Classification, Sensor_Used) across all EM spectrums."""
    
    if not check_line_of_sight(sensor_unit.lat, sensor_unit.lon, sensor_unit.altitude_ft, 
                               target.lat, target.lon, target.altitude_ft):
        return "NONE", "NONE"

    best_cls = "NONE"
    best_sen = "NONE"
    rank = {"NONE": 0, "FAINT": 1, "PROBABLE": 2, "CONFIRMED": 3}

    # 1. ESM (Electronic Support Measures) - Passive sniffing of target's active radar
    if getattr(target, 'radar_active', False) and target.platform.radar_range_km > 0:
        esm_range = sensor_unit.platform.esm_range_km
        if dist_km <= esm_range:
            best_cls = "PROBABLE" # ESM gives bearing and ID, but poor exact ranging
            best_sen = "ESM"

    # 2. IR / FLIR / Optical - Passive thermal and visual
    ir_range = sensor_unit.platform.ir_range_km
    if dist_km <= ir_range:
        if rank["CONFIRMED"] > rank[best_cls]:
            best_cls = "CONFIRMED"
            best_sen = "IR"

    # 3. Active Radar
    if getattr(sensor_unit, 'radar_active', True) and sensor_unit.platform.radar_range_km > 0:
        rcs_ratio  = max(target.platform.rcs_m2, 0.01) / RCS_REFERENCE_M2
        R_rcs      = (sensor_unit.platform.radar_range_km * sensor_unit.performance_mult) * (rcs_ratio ** 0.25)
        
        ecm_penalty = 0.0
        if target.is_jamming and dist_km > BURNTHROUGH_RANGE_KM:
            ecm_penalty = target.platform.ecm_rating * ECM_SCALE
            
        R_effective = R_rcs * max(0.0, 1.0 - ecm_penalty)

        if R_effective > 0.0 and dist_km <= R_effective * FAINT_BAND:
            fraction = dist_km / R_effective       
            cls = "CONFIRMED" if fraction <= CONFIRM_BAND else "PROBABLE" if fraction <= PROBABLE_BAND else "FAINT"
            
            if rank[cls] > rank[best_cls]:
                best_cls = cls
                best_sen = "RADAR"

    return best_cls, best_sen


def update_local_contacts(sensor_units: list["Unit"], target_units: list["Unit"], 
                          local_contacts: dict[str, Contact], game_time: float) -> None:
    """Updates the internal databank of a specific unit or closely-linked local group."""
    _RANK = {"NONE": 0, "FAINT": 1, "PROBABLE": 2, "CONFIRMED": 3}
    refreshed: set[str] = set()

    for target in target_units:
        if not target.alive: continue

        best_cls  = "NONE"
        best_sen  = "NONE"
        best_rank = 0

        for sensor in sensor_units:
            if not sensor.alive: continue
            dist = haversine(sensor.lat, sensor.lon, target.lat, target.lon)
            cls, sen = classify_detection(sensor, target, dist)
            
            if _RANK[cls] > best_rank:
                best_rank = _RANK[cls]
                best_cls  = cls
                best_sen  = sen

        if best_cls == "NONE": continue  

        refreshed.add(target.uid)
        unit_type = target.platform.unit_type if best_rank >= 2 else None
        side      = target.side               if best_rank >= 3 else None

        contact = local_contacts.get(target.uid)
        if contact is None:
            local_contacts[target.uid] = Contact(
                uid=target.uid, lat=target.lat, lon=target.lon, altitude_ft=target.altitude_ft,
                classification=best_cls, unit_type=unit_type, side=side, last_update=game_time, sensor_type=best_sen
            )
        else:
            contact.lat, contact.lon, contact.altitude_ft = target.lat, target.lon, target.altitude_ft
            contact.last_update, contact.sensor_type = game_time, best_sen
            if best_rank > _RANK[contact.classification]:
                contact.classification, contact.unit_type, contact.side = best_cls, unit_type, side

    # Purge old
    expired = [uid for uid, c in local_contacts.items() if uid not in refreshed and (game_time - c.last_update) > CONTACT_TIMEOUT_S]
    for uid in expired: del local_contacts[uid]