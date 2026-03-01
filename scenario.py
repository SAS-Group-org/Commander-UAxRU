# scenario.py — data models + DB/scenario load & save

from __future__ import annotations

import json
import math
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import pygame

from constants import MIN_PK, MAX_PK, MISSILE_TRAIL_LEN, CHAFF_PK_PENALTY, FLARE_PK_PENALTY
from geo import haversine, bearing, slant_range_km


# ── Data-layer dataclasses (immutable after load) ────────────────────────────

@dataclass(frozen=True)
class WeaponDef:
    key:          str
    display_name: str
    seeker:       str
    range_km:     float
    min_range_km: float
    speed_kmh:    float
    base_pk:      float
    is_gun:       bool
    description:  str
    domain:       str     
    damage:       float   
    reload_time_s: float  
    eccm:         float   


@dataclass(frozen=True)
class PlatformDef:
    key:               str
    display_name:      str
    unit_type:         str
    speed_kmh:         float
    ceiling_ft:        int
    ecm_rating:        float
    chaff_capacity:    int
    flare_capacity:    int
    fuel_capacity_kg:  float
    fuel_burn_rate_kg_h: float
    radar_range_km:    float
    radar_type:        str
    radar_modes:       tuple[str, ...]
    default_loadout:   dict[str, int]
    available_weapons: tuple[str, ...]
    fleet_count:       int              
    player_side:       str              
    rcs_m2:            float            
    cruise_alt_ft:     float            


@dataclass
class Mission:
    name: str
    mission_type: str        # e.g., "CAP", "STRIKE", "PATROL", "RTB"
    target_lat: float
    target_lon: float
    radius_km: float
    altitude_ft: float
    rtb_fuel_pct: float      # E.g. 0.25 = RTB at 25% fuel


# ── Runtime classes ──────────────────────────────────────────────────────────

class Missile:
    def __init__(self, lat: float, lon: float, alt_ft: float,
                 target: "Unit", side: str,
                 weapon_def: WeaponDef):
        self.lat        = lat
        self.lon        = lon
        self.altitude_ft = alt_ft
        self.target     = target
        self.side       = side
        self.wdef       = weapon_def
        self.active     = True
        self.status     = "IN_FLIGHT"
        self.launch_dist = slant_range_km(lat, lon, alt_ft, target.lat, target.lon, target.altitude_ft)
        self.trail: deque[tuple[float, float]] = deque(maxlen=MISSILE_TRAIL_LEN)
        
        self.eff_speed_kmh = weapon_def.speed_kmh if weapon_def.speed_kmh > 0 else 5000.0

    def update(self, sim_delta: float) -> None:
        if not self.active: return
        if not self.target.alive:
            self.active = False
            self.status = "MISSED"
            return

        self.trail.append((self.lat, self.lon))

        speed_kms   = self.eff_speed_kmh / 3600.0
        move_dist   = speed_kms * sim_delta
        dist        = slant_range_km(self.lat, self.lon, self.altitude_ft, self.target.lat, self.target.lon, self.target.altitude_ft)

        if dist <= move_dist:
            dist_penalty = (self.launch_dist / 50.0) * 0.10
            pk = self.wdef.base_pk - dist_penalty 

            if self.target.is_jamming and self.target.platform.ecm_rating > 0:
                if self.wdef.seeker in ("ARH", "SARH"):
                    ecm_effect = max(0.0, self.target.platform.ecm_rating - self.wdef.eccm)
                    pk -= ecm_effect

            if self.wdef.seeker in ("ARH", "SARH") and self.target.chaff > 0:
                self.target.chaff -= 1
                pk -= CHAFF_PK_PENALTY
            elif self.wdef.seeker == "IR" and self.target.flare > 0:
                self.target.flare -= 1
                pk -= FLARE_PK_PENALTY

            pk = max(MIN_PK, min(MAX_PK, pk))

            if random.random() <= pk:
                self.target.take_damage(self.wdef.damage)
                self.status = "HIT"
            else:
                self.status = "MISSED"

            self.active = False
            self.lat, self.lon, self.altitude_ft = self.target.lat, self.target.lon, self.target.altitude_ft
        else:
            ratio    = move_dist / dist
            dlat     = self.target.lat - self.lat
            dlon     = self.target.lon - self.lon
            dalt     = self.target.altitude_ft - self.altitude_ft
            self.lat += dlat * ratio
            self.lon += dlon * ratio
            self.altitude_ft += dalt * ratio

    def estimated_pk(self) -> float:
        dist        = slant_range_km(self.lat, self.lon, self.altitude_ft, self.target.lat, self.target.lon, self.target.altitude_ft)
        dist_penalty = (dist / 50.0) * 0.10
        pk = self.wdef.base_pk - dist_penalty 
        
        ecm_effect = max(0.0, self.target.platform.ecm_rating - self.wdef.eccm)
        pk -= ecm_effect
        
        return max(MIN_PK, min(MAX_PK, pk))


class Unit:
    def __init__(self, uid: str, callsign: str, lat: float, lon: float,
                 side: str, platform: PlatformDef,
                 loadout: dict[str, int],
                 image_path: Optional[str] = None):
        self.uid        = uid
        self.callsign   = callsign
        self.lat        = lat
        self.lon        = lon
        self.side       = side
        self.platform   = platform
        self.loadout    = dict(loadout)
        self.image_path = image_path
        self.fuel_kg    = platform.fuel_capacity_kg

        self.hp: float = 1.0
        self.damage_state: str = "OK"

        self.waypoints: list[tuple[float, float]] = []
        self.heading    = 0.0
        self.selected   = False
        self.is_detected = False
        self.flash_frames = 0
        self.selected_weapon: Optional[str] = None
        self.auto_engage = True if platform.unit_type in ("tank", "ifv", "apc", "recon", "tank_destroyer", "sam") else False
        
        # Doctrine & Mission
        self.roe: str = "FREE" if side == "Red" else "TIGHT"  # FREE, TIGHT, HOLD
        self.mission: Optional[Mission] = None

        self.is_jamming: bool = False
        self.chaff: int = platform.chaff_capacity
        self.flare: int = platform.flare_capacity

        self.weapon_cooldowns: dict[str, float] = {k: 0.0 for k in self.loadout.keys()}

        self.altitude_ft: float = platform.cruise_alt_ft
        self.target_altitude_ft: float = self.altitude_ft

        self.home_lat: float = lat
        self.home_lon: float = lon

        self.ai_fire_cooldown: float = 0.0

        self._surface: Optional[pygame.Surface] = None

    @property
    def alive(self) -> bool:
        return self.hp > 0.0

    @property
    def performance_mult(self) -> float:
        if self.damage_state == "OK": return 1.0
        if self.damage_state == "LIGHT": return 0.8
        if self.damage_state == "MODERATE": return 0.6
        if self.damage_state == "HEAVY": return 0.4
        return 0.0

    def take_damage(self, amount: float) -> None:
        if not self.alive: return
        self.hp = max(0.0, self.hp - amount)
        
        if self.hp <= 0.0:
            self.damage_state = "KILLED"
            self.is_jamming = False 
        elif self.hp <= 0.25:
            self.damage_state = "HEAVY"
        elif self.hp <= 0.50:
            self.damage_state = "MODERATE"
        elif self.hp <= 0.75:
            self.damage_state = "LIGHT"
        else:
            self.damage_state = "OK"

    def add_waypoint(self, lat: float, lon: float) -> None:
        self.waypoints.append((lat, lon))
        self._recalc_heading()

    def clear_waypoints(self) -> None:
        self.waypoints.clear()

    def _recalc_heading(self) -> None:
        if self.waypoints:
            self.heading = bearing(self.lat, self.lon, *self.waypoints[0])

    def update(self, sim_delta: float) -> None:
        if not self.alive: return
        
        for k in self.weapon_cooldowns:
            if self.weapon_cooldowns[k] > 0:
                self.weapon_cooldowns[k] = max(0.0, self.weapon_cooldowns[k] - sim_delta)
        
        if self.altitude_ft != self.target_altitude_ft:
            climb_rate_fps = 166.67 * self.performance_mult
            alt_diff = self.target_altitude_ft - self.altitude_ft
            step = climb_rate_fps * sim_delta
            if abs(alt_diff) <= step:
                self.altitude_ft = self.target_altitude_ft
            else:
                self.altitude_ft += math.copysign(step, alt_diff)

        if self.waypoints:
            speed_kms    = (self.platform.speed_kmh * self.performance_mult) / 3600.0
            dist_budget  = speed_kms * sim_delta

            while dist_budget > 0 and self.waypoints:
                tlat, tlon   = self.waypoints[0]
                dlat         = tlat - self.lat
                dlon         = tlon - self.lon
                lat_km       = dlat * 111.32
                lon_km       = dlon * 111.32 * math.cos(math.radians(self.lat))
                dist_to_wp   = math.hypot(lat_km, lon_km)

                if dist_to_wp <= dist_budget:
                    self.lat, self.lon = tlat, tlon
                    dist_budget -= dist_to_wp
                    self.waypoints.pop(0)
                    self._recalc_heading()
                else:
                    ratio      = dist_budget / dist_to_wp
                    self.lat  += dlat * ratio
                    self.lon  += dlon * ratio
                    dist_budget = 0
                    self._recalc_heading()

        if self.fuel_kg > 0:
            burn_per_sec = self.platform.fuel_burn_rate_kg_h / 3600.0
            self.fuel_kg -= burn_per_sec * sim_delta
            if self.fuel_kg <= 0:
                self.fuel_kg = 0
                self.take_damage(999.0) 

    def has_ammo(self, weapon_key: str) -> bool:
        return self.loadout.get(weapon_key, 0) > 0

    def expend_round(self, weapon_key: str) -> bool:
        if self.has_ammo(weapon_key):
            self.loadout[weapon_key] -= 1
            return True
        return False

    def best_bvr_weapon(self, db: "Database") -> Optional[str]:
        best_key   = None
        best_range = 0.0
        for wkey, qty in self.loadout.items():
            if qty <= 0: continue
            wdef = db.weapons.get(wkey)
            if wdef and not wdef.is_gun and wdef.range_km > best_range:
                best_key   = wkey
                best_range = wdef.range_km
        return best_key

    def trigger_flash(self, frames: int = 12) -> None:
        self.flash_frames = frames

    def tick_flash(self) -> None:
        if self.flash_frames > 0: self.flash_frames -= 1

    def is_clicked(self, screen_pos: tuple[int, int],
                   sx: float, sy: float, radius: int = 16) -> bool:
        return math.hypot(sx - screen_pos[0], sy - screen_pos[1]) <= radius


# ── Embedded databases ───────────────────────────────────────────────────────

_WEAPONS_DATA = {
    "R-27R":   {"display_name": "R-27R Alamo-A",      "seeker": "SARH",   "range_km": 73,  "min_range_km": 3,   "speed_kmh": 3000, "base_pk": 0.82, "is_gun": False, "domain": "air", "damage": 0.6, "eccm": 0.1, "description": "Semi-active radar homing BVR missile"},
    "R-27T":   {"display_name": "R-27T Alamo-B",      "seeker": "IR",     "range_km": 70,  "min_range_km": 3,   "speed_kmh": 3000, "base_pk": 0.80, "is_gun": False, "domain": "air", "damage": 0.6, "eccm": 0.1, "description": "Infrared-homing variant of the Alamo"},
    "R-77":    {"display_name": "R-77 Adder",         "seeker": "ARH",    "range_km": 110, "min_range_km": 3,   "speed_kmh": 3600, "base_pk": 0.84, "is_gun": False, "domain": "air", "damage": 0.7, "eccm": 0.2, "description": "Active radar homing BVR missile"},
    "R-73":    {"display_name": "R-73 Archer",        "seeker": "IR",     "range_km": 30,  "min_range_km": 0.5, "speed_kmh": 2500, "base_pk": 0.88, "is_gun": False, "domain": "air", "damage": 0.5, "eccm": 0.15,"description": "High-agility short-range IR missile"},
    "R-60":    {"display_name": "R-60 Aphid",         "seeker": "IR",     "range_km": 8,   "min_range_km": 0.3, "speed_kmh": 2200, "base_pk": 0.72, "is_gun": False, "domain": "air", "damage": 0.4, "eccm": 0.05,"description": "Short-range IR dogfight missile (older)"},
    "AIM-120C":{"display_name": "AIM-120C AMRAAM",    "seeker": "ARH",    "range_km": 105, "min_range_km": 3,   "speed_kmh": 3600, "base_pk": 0.85, "is_gun": False, "domain": "air", "damage": 0.7, "eccm": 0.2, "description": "NATO standard active radar BVR missile"},
    "AIM-9X":  {"display_name": "AIM-9X Sidewinder",  "seeker": "IR",     "range_km": 35,  "min_range_km": 0.5, "speed_kmh": 2700, "base_pk": 0.90, "is_gun": False, "domain": "air", "damage": 0.6, "eccm": 0.2, "description": "High off-boresight IR dogfight missile"},
    "AIM-9M":  {"display_name": "AIM-9M Sidewinder",  "seeker": "IR",     "range_km": 18,  "min_range_km": 0.5, "speed_kmh": 2500, "base_pk": 0.82, "is_gun": False, "domain": "air", "damage": 0.5, "eccm": 0.1, "description": "Older IR dogfight missile, widely used"},
    "MICA-EM": {"display_name": "MICA EM",            "seeker": "ARH",    "range_km": 80,  "min_range_km": 0.5, "speed_kmh": 4000, "base_pk": 0.86, "is_gun": False, "domain": "air", "damage": 0.6, "eccm": 0.2, "description": "French active radar BVR/WVR missile"},
    "MICA-IR": {"display_name": "MICA IR",            "seeker": "IR",     "range_km": 60,  "min_range_km": 0.5, "speed_kmh": 4000, "base_pk": 0.87, "is_gun": False, "domain": "air", "damage": 0.6, "eccm": 0.2, "description": "French IR BVR/WVR missile"},
    
    "48N6":    {"display_name": "48N6E (S-400)",      "seeker": "SARH",   "range_km": 250, "min_range_km": 5,   "speed_kmh": 7000, "base_pk": 0.85, "is_gun": False, "domain": "air", "damage": 1.0, "eccm": 0.2, "description": "Long-range heavy SAM"},
    "9M317":   {"display_name": "9M317 (Buk-M2)",     "seeker": "SARH",   "range_km": 45,  "min_range_km": 3,   "speed_kmh": 4000, "base_pk": 0.80, "is_gun": False, "domain": "air", "damage": 0.8, "eccm": 0.15,"description": "Medium-range tactical SAM"},
    "9M331":   {"display_name": "9M331 (Tor-M1)",     "seeker": "ARH",    "range_km": 15,  "min_range_km": 1,   "speed_kmh": 2800, "base_pk": 0.85, "is_gun": False, "domain": "air", "damage": 0.6, "eccm": 0.15,"description": "Short-range point defense SAM"},
    "PAC-3":   {"display_name": "MIM-104F PAC-3",     "seeker": "ARH",    "range_km": 100, "min_range_km": 3,   "speed_kmh": 5000, "base_pk": 0.90, "is_gun": False, "domain": "air", "damage": 1.0, "eccm": 0.25,"description": "High-velocity hit-to-kill SAM"},
    "AIM-120_SAM": {"display_name": "AIM-120 (NASAMS)","seeker": "ARH",   "range_km": 30,  "min_range_km": 2,   "speed_kmh": 3500, "base_pk": 0.88, "is_gun": False, "domain": "air", "damage": 0.7, "eccm": 0.2, "description": "Ground-launched AMRAAM"},
    "IRIS-T":  {"display_name": "IRIS-T SLM",         "seeker": "IR",     "range_km": 40,  "min_range_km": 1,   "speed_kmh": 3500, "base_pk": 0.92, "is_gun": False, "domain": "air", "damage": 0.7, "eccm": 0.2, "description": "Highly agile imaging IR SAM"},
    "Gepard_35": {"display_name": "35mm Oerlikon KDA", "seeker": "CANNON","range_km": 4.0, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.70, "is_gun": True,  "domain": "both","damage": 0.2, "eccm": 0.1, "description": "Twin 35mm SPAAG"},
    
    "Kh-25ML": {"display_name": "Kh-25ML (laser AGM)","seeker": "LASER",  "range_km": 25,  "min_range_km": 2,   "speed_kmh": 1800, "base_pk": 0.78, "is_gun": False, "domain": "ground", "damage": 0.8, "eccm": 0.1, "description": "Laser-guided air-to-ground missile"},
    "S-8":     {"display_name": "S-8 Rockets (pod)",  "seeker": "CANNON", "range_km": 2,   "min_range_km": 0.2, "speed_kmh": 1200, "base_pk": 0.60, "is_gun": True,  "domain": "ground", "damage": 0.4, "eccm": 0.1, "description": "Unguided rocket pod, short-range saturation"},
    "Shturm":  {"display_name": "9M114 Shturm ATGM",  "seeker": "SACLOS", "range_km": 5,   "min_range_km": 0.4, "speed_kmh": 600,  "base_pk": 0.75, "is_gun": False, "domain": "ground", "damage": 0.7, "eccm": 0.1, "description": "Semi-active ATGM, Mi-24 primary weapon"},
    
    "GSh-30-1":{"display_name": "GSh-30-1 (30mm)",    "seeker": "CANNON", "range_km": 0.8, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.65, "is_gun": True,  "domain": "both", "damage": 0.2, "eccm": 0.1, "description": "30mm single-barrel aircraft cannon"},
    "GSh-30-2":{"display_name": "GSh-30-2 (twin 30mm)","seeker":"CANNON", "range_km": 1.2, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.68, "is_gun": True,  "domain": "both", "damage": 0.25,"eccm": 0.1, "description": "Twin-barrel 30mm cannon (Su-25)"},
    "GSh-6-23":{"display_name": "GSh-6-23 (23mm)",    "seeker": "CANNON", "range_km": 0.8, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.62, "is_gun": True,  "domain": "both", "damage": 0.15,"eccm": 0.1, "description": "6-barrel 23mm rotary cannon (Su-24)"},
    "M61A1":   {"display_name": "M61A1 Vulcan (20mm)", "seeker": "CANNON", "range_km": 0.8, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.65, "is_gun": True,  "domain": "both", "damage": 0.15,"eccm": 0.1, "description": "20mm six-barrel rotary cannon"},
    "DEFA-554":{"display_name": "DEFA 554 (30mm)",    "seeker": "CANNON", "range_km": 0.8, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.64, "is_gun": True,  "domain": "both", "damage": 0.2, "eccm": 0.1, "description": "30mm revolver cannon (Mirage 2000)"},
    "Yak-B":   {"display_name": "Yak-B (12.7mm)",     "seeker": "CANNON", "range_km": 0.6, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.55, "is_gun": True,  "domain": "both", "damage": 0.1, "eccm": 0.1, "description": "12.7mm four-barrel rotary (Mi-24 chin gun)"},
    
    "GUN_125":     {"display_name": "125mm APFSDS",         "seeker": "CANNON", "range_km": 4.0, "min_range_km": 0.1, "speed_kmh": 0, "base_pk": 0.80, "is_gun": True,  "domain": "ground", "damage": 0.8, "eccm": 0.1, "description": "125mm smoothbore tank gun (T-64/72/80)"},
    "GUN_120NATO": {"display_name": "120mm NATO APFSDS",    "seeker": "CANNON", "range_km": 4.5, "min_range_km": 0.1, "speed_kmh": 0, "base_pk": 0.82, "is_gun": True,  "domain": "ground", "damage": 0.8, "eccm": 0.1, "description": "120mm NATO smoothbore gun (Leopard 2, M1 Abrams)"},
    "GUN_120UK":   {"display_name": "120mm L30A1 (rifled)", "seeker": "CANNON", "range_km": 4.5, "min_range_km": 0.1, "speed_kmh": 0, "base_pk": 0.82, "is_gun": True,  "domain": "ground", "damage": 0.8, "eccm": 0.1, "description": "120mm rifled gun (Challenger 2)"},
    "GUN_105":     {"display_name": "105mm APFSDS",         "seeker": "CANNON", "range_km": 3.0, "min_range_km": 0.1, "speed_kmh": 0, "base_pk": 0.76, "is_gun": True,  "domain": "ground", "damage": 0.7, "eccm": 0.1, "description": "105mm L7/M68 rifled gun (Leopard 1, M113-derived)"},
    "AUTOCANNON_30": {"display_name": "30mm Autocannon",    "seeker": "CANNON", "range_km": 2.5, "min_range_km": 0.1, "speed_kmh": 0, "base_pk": 0.70, "is_gun": True,  "domain": "ground", "damage": 0.25,"eccm": 0.1, "description": "30mm 2A42/Mk 44 autocannon (BMP-2, Marder, CV90)"},
    "AUTOCANNON_25": {"display_name": "25mm M242 Bushmaster","seeker": "CANNON","range_km": 2.0, "min_range_km": 0.1, "speed_kmh": 0, "base_pk": 0.68, "is_gun": True,  "domain": "ground", "damage": 0.2, "eccm": 0.1, "description": "25mm chain gun (Bradley M2/M3)"},
    "ATGM_Konkurs": {"display_name": "9M113 Konkurs ATGM",  "seeker": "SACLOS", "range_km": 4.0, "min_range_km": 0.5, "speed_kmh": 200, "base_pk": 0.78, "is_gun": False, "domain": "ground", "damage": 0.8, "eccm": 0.1, "description": "Wire-guided ATGM (BMP-2, 9P148)"},
    "ATGM_TOW":    {"display_name": "BGM-71 TOW ATGM",      "seeker": "SACLOS", "range_km": 3.7, "min_range_km": 0.3, "speed_kmh": 300, "base_pk": 0.80, "is_gun": False, "domain": "ground", "damage": 0.8, "eccm": 0.1, "description": "Wire-guided TOW missile (Bradley, M113)"},
    "ATGM_Stugna": {"display_name": "Stugna-P ATGM",        "seeker": "LASER",  "range_km": 5.5, "min_range_km": 0.1, "speed_kmh": 400, "base_pk": 0.82, "is_gun": False, "domain": "ground", "damage": 0.85,"eccm": 0.1, "description": "Ukrainian laser-guided ATGM (AMX-10 RC, BMP-1U)"},
}

_PLATFORMS_DATA = {
    # ══ UKRAINE AIR FORCE (Blue) ═════════════════════════════════════════════
    "MiG-29UA": {
        "display_name": "MiG-29 Fulcrum", "type": "fighter",
        "speed_kmh": 2400, "ceiling_ft": 57000, "ecm_rating": 0.20,
        "radar": {"type": "N019 Sapfir", "range_km": 150, "modes": ["air"]},
        "default_loadout": {"R-27R": 2, "R-27T": 2, "R-73": 4, "GSh-30-1": 1},
        "available_weapons": ["R-27R","R-27T","R-73","AIM-9M","GSh-30-1"],
        "fleet_count": 45, "player_side": "Blue",
        "rcs_m2": 5.0, "cruise_alt_ft": 30000,
    },
    "Su-27UA": {
        "display_name": "Su-27 Flanker-B", "type": "fighter",
        "speed_kmh": 2500, "ceiling_ft": 59000, "ecm_rating": 0.25,
        "radar": {"type": "N001 Mech", "range_km": 200, "modes": ["air","surface"]},
        "default_loadout": {"R-27R": 4, "R-27T": 2, "R-73": 4, "GSh-30-1": 1},
        "available_weapons": ["R-27R","R-27T","R-73","AIM-9M","GSh-30-1"],
        "fleet_count": 23, "player_side": "Blue",
        "rcs_m2": 5.0, "cruise_alt_ft": 30000,
    },
    "F-16AM": {
        "display_name": "F-16AM Fighting Falcon (MLU)", "type": "fighter",
        "speed_kmh": 2150, "ceiling_ft": 50000, "ecm_rating": 0.22,
        "radar": {"type": "AN/APG-66(V)2", "range_km": 130, "modes": ["air","surface"]},
        "default_loadout": {"AIM-120C": 4, "AIM-9X": 2, "M61A1": 1},
        "available_weapons": ["AIM-120C","AIM-9X","AIM-9M","M61A1"],
        "fleet_count": 23, "player_side": "Blue",
        "rcs_m2": 5.0, "cruise_alt_ft": 30000,
    },
    "F-16UA": {
        "display_name": "F-16 Fighting Falcon (Block 52)", "type": "fighter",
        "speed_kmh": 2150, "ceiling_ft": 50000, "ecm_rating": 0.18,
        "radar": {"type": "AN/APG-68(V)9", "range_km": 148, "modes": ["air","surface"]},
        "default_loadout": {"AIM-120C": 6, "AIM-9X": 2, "M61A1": 1},
        "available_weapons": ["AIM-120C","AIM-9X","AIM-9M","M61A1"],
        "fleet_count": 20, "player_side": "Blue",
        "rcs_m2": 5.0, "cruise_alt_ft": 30000,
    },
    "Su-25UA": {
        "display_name": "Su-25 Frogfoot (CAS)", "type": "attacker",
        "speed_kmh": 950,  "ceiling_ft": 23000, "ecm_rating": 0.15,
        "radar": {"type": "Klen-PS (laser)", "range_km": 20, "modes": ["surface"]},
        "default_loadout": {"R-60": 2, "S-8": 2, "GSh-30-2": 1},
        "available_weapons": ["R-60","S-8","Kh-25ML","GSh-30-2"],
        "fleet_count": 19, "player_side": "Blue",
        "rcs_m2": 8.0, "cruise_alt_ft": 12000,
    },
    "Su-24M": {
        "display_name": "Su-24M Fencer-D (Strike)", "type": "attacker",
        "speed_kmh": 1700, "ceiling_ft": 36000, "ecm_rating": 0.18,
        "radar": {"type": "Orion-A", "range_km": 50, "modes": ["surface"]},
        "default_loadout": {"Kh-25ML": 4, "R-60": 2, "GSh-6-23": 1},
        "available_weapons": ["Kh-25ML","R-60","GSh-6-23"],
        "fleet_count": 13, "player_side": "Blue",
        "rcs_m2": 8.0, "cruise_alt_ft": 12000,
    },
    "Mirage2000-5F": {
        "display_name": "Mirage 2000-5F", "type": "fighter",
        "speed_kmh": 2530, "ceiling_ft": 59000, "ecm_rating": 0.28,
        "radar": {"type": "RDY-2", "range_km": 185, "modes": ["air","surface"]},
        "default_loadout": {"MICA-EM": 4, "MICA-IR": 2, "DEFA-554": 1},
        "available_weapons": ["MICA-EM","MICA-IR","AIM-9M","DEFA-554"],
        "fleet_count": 6, "player_side": "Blue",
        "rcs_m2": 5.0, "cruise_alt_ft": 30000,
    },
    # ══ UKRAINE ARMY AVIATION (Blue) ═════════════════════════════════════════
    "Mi-8UA": {
        "display_name": "Mi-8 Hip (Armed)", "type": "helicopter",
        "speed_kmh": 260,  "ceiling_ft": 14800, "ecm_rating": 0.05,
        "radar": {"type": "None", "range_km": 15, "modes": ["surface"]},
        "default_loadout": {"S-8": 4},
        "available_weapons": ["S-8"],
        "fleet_count": 73, "player_side": "Blue",
        "rcs_m2": 10.0, "cruise_alt_ft": 1500,
    },
    "Mi-24V": {
        "display_name": "Mi-24V Hind-E (Attack)", "type": "helicopter",
        "speed_kmh": 320,  "ceiling_ft": 14800, "ecm_rating": 0.10,
        "radar": {"type": "None", "range_km": 20, "modes": ["surface"]},
        "default_loadout": {"Shturm": 4, "S-8": 2, "Yak-B": 1},
        "available_weapons": ["Shturm","S-8","R-60","Yak-B"],
        "fleet_count": 38, "player_side": "Blue",
        "rcs_m2": 10.0, "cruise_alt_ft": 1500,
    },
    "Mi-2UA": {
        "display_name": "Mi-2 Hoplite (Light)", "type": "helicopter",
        "speed_kmh": 210,  "ceiling_ft": 13100, "ecm_rating": 0.03,
        "radar": {"type": "None", "range_km": 10, "modes": ["surface"]},
        "default_loadout": {"S-8": 2},
        "available_weapons": ["S-8"],
        "fleet_count": 12, "player_side": "Blue",
        "rcs_m2": 10.0, "cruise_alt_ft": 1500,
    },
    # ══ RUSSIA AIR FORCE (Red) ════════════════════════════════════════════════
    "Su-27S": {
        "display_name": "Su-27S Flanker-B", "type": "fighter",
        "speed_kmh": 2500, "ceiling_ft": 59000, "ecm_rating": 0.25,
        "radar": {"type": "N001 Mech", "range_km": 240, "modes": ["air","surface"]},
        "default_loadout": {"R-27R": 4, "R-27T": 2, "R-73": 4, "GSh-30-1": 1},
        "available_weapons": ["R-27R","R-27T","R-73","GSh-30-1"],
        "fleet_count": 0, "player_side": "Red",
        "rcs_m2": 5.0, "cruise_alt_ft": 30000,
    },
    "Su-35S": {
        "display_name": "Su-35S Flanker-E", "type": "fighter",
        "speed_kmh": 2500, "ceiling_ft": 59000, "ecm_rating": 0.30,
        "radar": {"type": "Irbis-E", "range_km": 300, "modes": ["air","surface"]},
        "default_loadout": {"R-77": 4, "R-27T": 2, "R-73": 6, "GSh-30-1": 1},
        "available_weapons": ["R-77","R-27R","R-27T","R-73","GSh-30-1"],
        "fleet_count": 0, "player_side": "Red",
        "rcs_m2": 5.0, "cruise_alt_ft": 30000,
    },
    "MiG-29A": {
        "display_name": "MiG-29A Fulcrum-A", "type": "fighter",
        "speed_kmh": 2400, "ceiling_ft": 57000, "ecm_rating": 0.20,
        "radar": {"type": "N019 Sapfir", "range_km": 180, "modes": ["air"]},
        "default_loadout": {"R-27R": 2, "R-27T": 2, "R-73": 4, "GSh-30-1": 1},
        "available_weapons": ["R-27R","R-27T","R-73","GSh-30-1"],
        "fleet_count": 0, "player_side": "Red",
        "rcs_m2": 5.0, "cruise_alt_ft": 30000,
    },
    "Su-30SM": {
        "display_name": "Su-30SM Flanker-H", "type": "fighter",
        "speed_kmh": 2125, "ceiling_ft": 56700, "ecm_rating": 0.28,
        "radar": {"type": "N011M BARS", "range_km": 280, "modes": ["air","surface"]},
        "default_loadout": {"R-77": 4, "R-73": 4, "GSh-30-1": 1},
        "available_weapons": ["R-77","R-27R","R-27T","R-73","GSh-30-1"],
        "fleet_count": 0, "player_side": "Red",
        "rcs_m2": 5.0, "cruise_alt_ft": 30000,
    },

    # ══ AIR DEFENSE SYSTEMS (SAMs) ═══════════════════════════════════════════
    "S-400": {
        "display_name": "S-400 Triumf (SA-21)", "type": "sam",
        "speed_kmh": 40, "ceiling_ft": 0, "ecm_rating": 0.10,
        "radar": {"type": "91N6E Big Bird", "range_km": 400, "modes": ["air"]},
        "default_loadout": {"48N6": 16},
        "available_weapons": ["48N6"],
        "fleet_count": 10, "player_side": "Red",
        "rcs_m2": 25.0, "cruise_alt_ft": 0,
    },
    "Buk-M2": {
        "display_name": "Buk-M2 (SA-17)", "type": "sam",
        "speed_kmh": 65, "ceiling_ft": 0, "ecm_rating": 0.05,
        "radar": {"type": "9S36", "range_km": 120, "modes": ["air"]},
        "default_loadout": {"9M317": 4},
        "available_weapons": ["9M317"],
        "fleet_count": 40, "player_side": "Red",
        "rcs_m2": 15.0, "cruise_alt_ft": 0,
    },
    "Tor-M1": {
        "display_name": "Tor-M1 (SA-15)", "type": "sam",
        "speed_kmh": 65, "ceiling_ft": 0, "ecm_rating": 0.05,
        "radar": {"type": "Scrum Half", "range_km": 25, "modes": ["air"]},
        "default_loadout": {"9M331": 8},
        "available_weapons": ["9M331"],
        "fleet_count": 80, "player_side": "Red",
        "rcs_m2": 15.0, "cruise_alt_ft": 0,
    },
    "Patriot": {
        "display_name": "MIM-104 Patriot PAC-3", "type": "sam",
        "speed_kmh": 40, "ceiling_ft": 0, "ecm_rating": 0.10,
        "radar": {"type": "AN/MPQ-65", "range_km": 160, "modes": ["air"]},
        "default_loadout": {"PAC-3": 16},
        "available_weapons": ["PAC-3"],
        "fleet_count": 3, "player_side": "Blue",
        "rcs_m2": 25.0, "cruise_alt_ft": 0,
    },
    "NASAMS": {
        "display_name": "NASAMS II", "type": "sam",
        "speed_kmh": 40, "ceiling_ft": 0, "ecm_rating": 0.05,
        "radar": {"type": "AN/MPQ-64F1 Sentinel", "range_km": 75, "modes": ["air"]},
        "default_loadout": {"AIM-120_SAM": 6},
        "available_weapons": ["AIM-120_SAM"],
        "fleet_count": 6, "player_side": "Blue",
        "rcs_m2": 15.0, "cruise_alt_ft": 0,
    },
    "IRIS-T_SLM": {
        "display_name": "IRIS-T SLM", "type": "sam",
        "speed_kmh": 40, "ceiling_ft": 0, "ecm_rating": 0.05,
        "radar": {"type": "TRML-4D", "range_km": 250, "modes": ["air"]},
        "default_loadout": {"IRIS-T": 8},
        "available_weapons": ["IRIS-T"],
        "fleet_count": 4, "player_side": "Blue",
        "rcs_m2": 15.0, "cruise_alt_ft": 0,
    },
    "Gepard": {
        "display_name": "Flakpanzer Gepard SPAAG", "type": "sam",
        "speed_kmh": 65, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "S-Band Search", "range_km": 15, "modes": ["air"]},
        "default_loadout": {"Gepard_35": 40},
        "available_weapons": ["Gepard_35"],
        "fleet_count": 40, "player_side": "Blue",
        "rcs_m2": 15.0, "cruise_alt_ft": 0,
    },

    # ══ RUSSIA GROUND FORCES (Red) ═══════════════════════════════════════════
    "T-90R": {
        "display_name": "T-90A / T-90M", "type": "tank",
        "speed_kmh": 65, "ceiling_ft": 0, "ecm_rating": 0.10,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"GUN_125": 40, "ATGM_Konkurs": 4},
        "available_weapons": ["GUN_125", "ATGM_Konkurs"],
        "fleet_count": 30, "player_side": "Red",
        "rcs_m2": 20.0, "cruise_alt_ft": 0,
    },
    "T-72R": {
        "display_name": "T-72B1 / T-72B3", "type": "tank",
        "speed_kmh": 60, "ceiling_ft": 0, "ecm_rating": 0.06,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"GUN_125": 40, "ATGM_Konkurs": 4},
        "available_weapons": ["GUN_125", "ATGM_Konkurs"],
        "fleet_count": 520, "player_side": "Red",
        "rcs_m2": 20.0, "cruise_alt_ft": 0,
    },
    "BMP-2R": {
        "display_name": "BMP-2 (Russian)", "type": "ifv",
        "speed_kmh": 65, "ceiling_ft": 0, "ecm_rating": 0.03,
        "radar": {"type": "Optical sight", "range_km": 4, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 500, "ATGM_Konkurs": 4},
        "available_weapons": ["AUTOCANNON_30", "ATGM_Konkurs"],
        "fleet_count": 150, "player_side": "Red",
        "rcs_m2": 12.0, "cruise_alt_ft": 0,
    },
    "BTR-80R": {
        "display_name": "BTR-80 / BTR-82A (Russian)", "type": "apc",
        "speed_kmh": 80, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 300},
        "available_weapons": ["AUTOCANNON_30"],
        "fleet_count": 302, "player_side": "Red",
        "rcs_m2": 10.0, "cruise_alt_ft": 0,
    },
    "BRDM2R": {
        "display_name": "BRDM-2 / BRDM-2T (Russian)", "type": "recon",
        "speed_kmh": 95, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical + IR", "range_km": 6, "modes": ["surface"]},
        "default_loadout": {"ATGM_Konkurs": 6},
        "available_weapons": ["ATGM_Konkurs"],
        "fleet_count": 120, "player_side": "Red",
        "rcs_m2": 6.0, "cruise_alt_ft": 0,
    },
    "9P148R": {
        "display_name": "9P148 Konkurs ATGM (Russian)", "type": "tank_destroyer",
        "speed_kmh": 100, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical sight", "range_km": 4, "modes": ["surface"]},
        "default_loadout": {"ATGM_Konkurs": 20},
        "available_weapons": ["ATGM_Konkurs"],
        "fleet_count": 7, "player_side": "Red",
        "rcs_m2": 8.0, "cruise_alt_ft": 0,
    },

    # ══ UKRAINE GROUND FORCES (Blue) ══════════════════════════════════════════
    "T-72": {
        "display_name": "T-72 (various)", "type": "tank",
        "speed_kmh": 60, "ceiling_ft": 0, "ecm_rating": 0.05,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"GUN_125": 40, "ATGM_Konkurs": 4},
        "available_weapons": ["GUN_125", "ATGM_Konkurs"],
        "fleet_count": 520, "player_side": "Blue",
        "rcs_m2": 20.0, "cruise_alt_ft": 0,
    },
    "Leopard2": {
        "display_name": "Leopard 2A4/A6 / Strv 122", "type": "tank",
        "speed_kmh": 72, "ceiling_ft": 0, "ecm_rating": 0.10,
        "radar": {"type": "Hunter-killer sight", "range_km": 7, "modes": ["surface"]},
        "default_loadout": {"GUN_120NATO": 42, "ATGM_TOW": 2},
        "available_weapons": ["GUN_120NATO", "ATGM_TOW"],
        "fleet_count": 60, "player_side": "Blue",
        "rcs_m2": 20.0, "cruise_alt_ft": 0,
    },
    "M1Abrams": {
        "display_name": "M1A1 Abrams", "type": "tank",
        "speed_kmh": 68, "ceiling_ft": 0, "ecm_rating": 0.10,
        "radar": {"type": "Hunter-killer sight", "range_km": 7, "modes": ["surface"]},
        "default_loadout": {"GUN_120NATO": 40, "ATGM_TOW": 2},
        "available_weapons": ["GUN_120NATO", "ATGM_TOW"],
        "fleet_count": 25, "player_side": "Blue",
        "rcs_m2": 20.0, "cruise_alt_ft": 0,
    },
    "Bradley": {
        "display_name": "M2 Bradley", "type": "ifv",
        "speed_kmh": 66, "ceiling_ft": 0, "ecm_rating": 0.06,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_25": 900, "ATGM_TOW": 7},
        "available_weapons": ["AUTOCANNON_25", "ATGM_TOW"],
        "fleet_count": 350, "player_side": "Blue",
        "rcs_m2": 12.0, "cruise_alt_ft": 0,
    },
    "Stryker": {
        "display_name": "Stryker M1126", "type": "apc",
        "speed_kmh": 96, "ceiling_ft": 0, "ecm_rating": 0.05,
        "radar": {"type": "Optical sight", "range_km": 4, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 200, "ATGM_TOW": 2},
        "available_weapons": ["AUTOCANNON_30", "ATGM_TOW"],
        "fleet_count": 400, "player_side": "Blue",
        "rcs_m2": 10.0, "cruise_alt_ft": 0,
    },
}

# ── Database ─────────────────────────────────────────────────────────────────

class Database:
    def __init__(self,
                 weapons_path:  str = None,
                 units_path:    str = None):
        self.weapons:   dict[str, WeaponDef]   = {}
        self.platforms: dict[str, PlatformDef] = {}

        if weapons_path and os.path.exists(weapons_path):
            with open(weapons_path, encoding="utf-8") as fh:
                raw_weapons = json.load(fh)
            print(f"[DB] Loaded weapons from {weapons_path}")
        else:
            raw_weapons = _WEAPONS_DATA

        for key, d in raw_weapons.items():
            self.weapons[key] = WeaponDef(
                key          = key,
                display_name = d["display_name"],
                seeker       = d["seeker"],
                range_km     = d["range_km"],
                min_range_km = d["min_range_km"],
                speed_kmh    = d["speed_kmh"],
                base_pk      = d["base_pk"],
                is_gun       = d["is_gun"],
                description  = d["description"],
                domain       = d.get("domain", "both"),
                damage       = float(d.get("damage", 0.6)),
                reload_time_s = float(d.get("reload_time_s", 0.5 if d.get("is_gun") else 3.0)),
                eccm         = float(d.get("eccm", 0.1)),
            )

        if units_path and os.path.exists(units_path):
            with open(units_path, encoding="utf-8") as fh:
                raw_platforms = json.load(fh)
            print(f"[DB] Loaded platforms from {units_path}")
        else:
            raw_platforms = _PLATFORMS_DATA

        for key, d in raw_platforms.items():
            self.platforms[key] = PlatformDef(
                key               = key,
                display_name      = d["display_name"],
                unit_type         = d["type"],
                speed_kmh         = d["speed_kmh"],
                ceiling_ft        = d["ceiling_ft"],
                ecm_rating        = d["ecm_rating"],
                chaff_capacity    = int(d.get("chaff_capacity", 30 if d["type"] in ("fighter", "attacker", "helicopter") else 0)),
                flare_capacity    = int(d.get("flare_capacity", 30 if d["type"] in ("fighter", "attacker", "helicopter") else 0)),
                fuel_capacity_kg  = d.get("fuel_capacity_kg", 5000.0),    
                fuel_burn_rate_kg_h = d.get("fuel_burn_rate_kg_h", 1500.0),
                radar_range_km    = d["radar"]["range_km"],
                radar_type        = d["radar"]["type"],
                radar_modes       = tuple(d["radar"]["modes"]),
                default_loadout   = d["default_loadout"],
                available_weapons = tuple(d.get("available_weapons",
                                          list(d["default_loadout"].keys()))),
                fleet_count       = d.get("fleet_count", 0),
                player_side       = d.get("player_side", "Any"),
                rcs_m2            = float(d.get("rcs_m2", 5.0)),
                cruise_alt_ft     = float(d.get("cruise_alt_ft", 0)),
            )


# ── Scenario load / save ──────────────────────────────────────────────────────

def load_scenario(path: str, db: Database) -> tuple[list[Unit], dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    units: list[Unit] = []
    for ud in data.get("units", []):
        platform_key = ud["platform"]
        platform     = db.platforms.get(platform_key)
        if platform is None:
            continue

        loadout = ud.get("loadout", platform.default_loadout)

        unit = Unit(
            uid        = ud["id"],
            callsign   = ud["callsign"],
            lat        = ud["lat"],
            lon        = ud["lon"],
            side       = ud["side"],
            platform   = platform,
            loadout    = loadout,
            image_path = ud.get("image_path"),
        )
        
        unit.roe = ud.get("roe", "FREE" if unit.side == "Red" else "TIGHT")
        mdata = ud.get("mission")
        if mdata:
            unit.mission = Mission(
                name=mdata["name"],
                mission_type=mdata["type"],
                target_lat=mdata["lat"],
                target_lon=mdata["lon"],
                radius_km=mdata["radius"],
                altitude_ft=mdata["alt"],
                rtb_fuel_pct=mdata["rtb_fuel"]
            )
            
        for wp in ud.get("waypoints", []):
            unit.add_waypoint(wp[0], wp[1])

        units.append(unit)

    meta = {
        "name":        data.get("name",        "Unnamed Scenario"),
        "description": data.get("description", ""),
        "start_lat":   data.get("start_lat",   50.0),
        "start_lon":   data.get("start_lon",   30.0),
        "start_zoom":  data.get("start_zoom",  7),
    }
    return units, meta

def save_scenario(path: str, units: list[Unit], meta: dict,
                  game_time: float = 0.0) -> None:
    units_data = []
    for u in units:
        entry = {
            "id":         u.uid,
            "platform":   u.platform.key,
            "callsign":   u.callsign,
            "side":       u.side,
            "lat":        round(u.lat, 6),
            "lon":        round(u.lon, 6),
            "image_path": u.image_path,
            "loadout":    u.loadout,
            "roe":        u.roe,
            "waypoints":  [[round(lat, 6), round(lon, 6)]
                           for lat, lon in u.waypoints],
        }
        if u.mission:
            entry["mission"] = {
                "name": u.mission.name,
                "type": u.mission.mission_type,
                "lat": round(u.mission.target_lat, 6),
                "lon": round(u.mission.target_lon, 6),
                "radius": round(u.mission.radius_km, 2),
                "alt": round(u.mission.altitude_ft, 2),
                "rtb_fuel": round(u.mission.rtb_fuel_pct, 2)
            }
        units_data.append(entry)

    payload = {
        **meta,
        "game_time_seconds": round(game_time, 1),
        "units": units_data,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

def save_deployment(path: str, units: list[Unit]) -> None:
    units_data = []
    for u in units:
        if u.side != "Blue": continue
        entry = {
            "id":         u.uid,
            "platform":   u.platform.key,
            "callsign":   u.callsign,
            "side":       u.side,
            "lat":        round(u.lat, 6),
            "lon":        round(u.lon, 6),
            "image_path": u.image_path,
            "loadout":    u.loadout,
            "roe":        u.roe,
            "waypoints":  [[round(lat, 6), round(lon, 6)]
                           for lat, lon in u.waypoints],
        }
        if u.mission:
            entry["mission"] = {
                "name": u.mission.name,
                "type": u.mission.mission_type,
                "lat": round(u.mission.target_lat, 6),
                "lon": round(u.mission.target_lon, 6),
                "radius": round(u.mission.radius_km, 2),
                "alt": round(u.mission.altitude_ft, 2),
                "rtb_fuel": round(u.mission.rtb_fuel_pct, 2)
            }
        units_data.append(entry)
        
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"deployment": units_data}, fh, indent=2)

def load_deployment(path: str, db: Database) -> list[Unit]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    
    units: list[Unit] = []
    for ud in data.get("deployment", []):
        platform_key = ud["platform"]
        platform     = db.platforms.get(platform_key)
        if platform is None:
            continue

        loadout = ud.get("loadout", platform.default_loadout)

        unit = Unit(
            uid        = ud["id"], 
            callsign   = ud["callsign"],
            lat        = ud["lat"],
            lon        = ud["lon"],
            side       = ud["side"],
            platform   = platform,
            loadout    = loadout,
            image_path = ud.get("image_path"),
        )
        
        unit.roe = ud.get("roe", "TIGHT")
        mdata = ud.get("mission")
        if mdata:
            unit.mission = Mission(
                name=mdata["name"],
                mission_type=mdata["type"],
                target_lat=mdata["lat"],
                target_lon=mdata["lon"],
                radius_km=mdata["radius"],
                altitude_ft=mdata["alt"],
                rtb_fuel_pct=mdata["rtb_fuel"]
            )
            
        for wp in ud.get("waypoints", []):
            unit.add_waypoint(wp[0], wp[1])
        units.append(unit)
    return units