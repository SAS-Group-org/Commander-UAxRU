# sensor.py — physics-based sensor model

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from constants import BURNTHROUGH_RANGE_KM

if TYPE_CHECKING:
    from scenario import PlatformDef, Unit

# ── Physical constants ────────────────────────────────────────────────────────

RCS_REFERENCE_M2: float = 5.0
HORIZON_COEFF: float = 4.12
GROUND_SENSOR_HEIGHT_M: float = 5.0

# ECM effectiveness scalar applied to ecm_rating when reducing detection range.
ECM_SCALE: float = 0.60

FAINT_BAND:    float = 1.00
PROBABLE_BAND: float = 0.75
CONFIRM_BAND:  float = 0.50

CONTACT_TIMEOUT_S: float = 30.0


# ── Contact dataclass ─────────────────────────────────────────────────────────

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

    @property
    def label(self) -> str:
        if self.classification == "CONFIRMED":
            return f"[{self.side}] {self.unit_type}"
        if self.classification == "PROBABLE":
            return f"? {self.unit_type}"
        return "?"


# ── Core detection function ───────────────────────────────────────────────────

def classify_detection(sensor_unit: "Unit",
                       target: "Unit",
                       dist_km: float) -> str:

    # 1. Radar horizon (earth curvature) 
    sensor_alt_m = max(sensor_unit.altitude_ft * 0.3048, GROUND_SENSOR_HEIGHT_M)
    target_alt_m = max(target.altitude_ft * 0.3048, GROUND_SENSOR_HEIGHT_M)
    horizon_km   = HORIZON_COEFF * (math.sqrt(sensor_alt_m) + math.sqrt(target_alt_m))
    
    if dist_km > horizon_km:
        return "NONE"

    # 2. RCS-adjusted detection range & Damage Penalty
    rcs_ratio  = max(target.platform.rcs_m2, 0.01) / RCS_REFERENCE_M2
    R_rcs      = (sensor_unit.platform.radar_range_km * sensor_unit.performance_mult) * (rcs_ratio ** 0.25)

    # 3. Active ECM Jamming Reduction
    ecm_penalty = 0.0
    if target.is_jamming and dist_km > BURNTHROUGH_RANGE_KM:
        ecm_penalty = target.platform.ecm_rating * ECM_SCALE
        
    R_effective = R_rcs * max(0.0, 1.0 - ecm_penalty)

    if R_effective <= 0.0 or dist_km > R_effective * FAINT_BAND:
        return "NONE"

    # 4. Classification bands
    fraction = dist_km / R_effective       

    if fraction > PROBABLE_BAND:
        return "FAINT"
    if fraction > CONFIRM_BAND:
        return "PROBABLE"
    return "CONFIRMED"


# ── Multi-sensor scan ─────────────────────────────────────────────────────────

def update_contacts(detecting_units: list["Unit"],
                    target_units: list["Unit"],
                    existing_contacts: dict[str, Contact],
                    game_time: float) -> dict[str, Contact]:
    from geo import haversine

    _RANK = {"NONE": 0, "FAINT": 1, "PROBABLE": 2, "CONFIRMED": 3}
    refreshed: set[str] = set()

    for target in target_units:
        if not target.alive:
            continue

        best_cls  = "NONE"
        best_rank = 0

        for sensor_unit in detecting_units:
            if not sensor_unit.alive:
                continue

            dist = haversine(sensor_unit.lat, sensor_unit.lon,
                             target.lat, target.lon)
            
            cls  = classify_detection(sensor_unit, target, dist)
            
            rank = _RANK[cls]
            if rank > best_rank:
                best_rank = rank
                best_cls  = cls

        if best_cls == "NONE":
            continue  

        refreshed.add(target.uid)

        unit_type = target.platform.unit_type if best_rank >= 2 else None
        side      = target.side               if best_rank >= 3 else None

        contact = existing_contacts.get(target.uid)
        if contact is None:
            existing_contacts[target.uid] = Contact(
                uid            = target.uid,
                lat            = target.lat,
                lon            = target.lon,
                altitude_ft    = target.altitude_ft,
                classification = best_cls,
                unit_type      = unit_type,
                side           = side,
                last_update    = game_time,
            )
        else:
            contact.lat            = target.lat
            contact.lon            = target.lon
            contact.altitude_ft    = target.altitude_ft
            contact.last_update    = game_time
            if best_rank > _RANK[contact.classification]:
                contact.classification = best_cls
                contact.unit_type      = unit_type
                contact.side           = side

    expired = [uid for uid, c in existing_contacts.items()
               if uid not in refreshed
               and (game_time - c.last_update) > CONTACT_TIMEOUT_S]
    for uid in expired:
        del existing_contacts[uid]

    return existing_contacts