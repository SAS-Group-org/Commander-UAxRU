# scenario.py — data models + DB/scenario load & save
#
# Responsibilities:
#   • WeaponDef / PlatformDef  — read-only data loaded from JSON
#   • Unit                     — runtime mutable state for a platform
#   • Missile                  — runtime mutable state for an in-flight weapon
#   • Database                 — loads data/weapons.json + data/units.json
#   • load_scenario()          — instantiates Units from a scenario JSON file
#   • save_scenario()          — serialises current state back to JSON

from __future__ import annotations

import json
import math
import os

# Directory that contains this file — used to build absolute paths to data/
_HERE = os.path.dirname(os.path.abspath(__file__))
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import pygame

from constants import MIN_PK, MAX_PK, MISSILE_TRAIL_LEN
from geo import haversine, bearing


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


@dataclass(frozen=True)
class PlatformDef:
    key:               str
    display_name:      str
    unit_type:         str
    speed_kmh:         float
    ceiling_ft:        int
    ecm_rating:        float
    radar_range_km:    float
    radar_type:        str
    radar_modes:       tuple[str, ...]
    default_loadout:   dict[str, int]
    available_weapons: tuple[str, ...]  # all weapon keys this platform can carry
    fleet_count:       int              # real-world active aircraft count
    player_side:       str              # "Blue" | "Red" | "Any"


# ── Runtime classes ──────────────────────────────────────────────────────────

class Missile:
    """An in-flight weapon travelling toward a target Unit."""

    def __init__(self, lat: float, lon: float,
                 target: "Unit", side: str,
                 weapon_def: WeaponDef):
        self.lat        = lat
        self.lon        = lon
        self.target     = target
        self.side       = side
        self.wdef       = weapon_def
        self.active     = True
        self.status     = "IN_FLIGHT"   # IN_FLIGHT | HIT | MISSED
        self.launch_dist = haversine(lat, lon, target.lat, target.lon)
        # Trail: deque of (lat, lon) for visual history
        self.trail: deque[tuple[float, float]] = deque(maxlen=MISSILE_TRAIL_LEN)

    # ------------------------------------------------------------------ update
    def update(self, sim_delta: float) -> None:
        """Advance missile by sim_delta real-seconds worth of sim-time."""
        if not self.active:
            return
        if not self.target.alive:
            self.active = False
            self.status = "MISSED"
            return

        self.trail.append((self.lat, self.lon))

        speed_kms   = self.wdef.speed_kmh / 3600.0   # km per sim-second
        move_dist   = speed_kms * sim_delta
        dist        = haversine(self.lat, self.lon, self.target.lat, self.target.lon)

        if dist <= move_dist:
            # ── Terminal phase: roll for hit ─────────────────────────────────
            dist_penalty = (self.launch_dist / 50.0) * 0.10
            pk = self.wdef.base_pk - dist_penalty - self.target.platform.ecm_rating
            pk = max(MIN_PK, min(MAX_PK, pk))

            if random.random() <= pk:
                self.target.alive  = False
                self.status        = "HIT"
            else:
                self.status        = "MISSED"

            self.active = False
            self.lat, self.lon = self.target.lat, self.target.lon
        else:
            # ── Move toward target ───────────────────────────────────────────
            ratio    = move_dist / dist
            dlat     = self.target.lat - self.lat
            dlon     = self.target.lon - self.lon
            self.lat += dlat * ratio
            self.lon += dlon * ratio

    # ------------------------------------------------------------------ util
    def estimated_pk(self) -> float:
        dist        = haversine(self.lat, self.lon, self.target.lat, self.target.lon)
        dist_penalty = (dist / 50.0) * 0.10
        pk = self.wdef.base_pk - dist_penalty - self.target.platform.ecm_rating
        return max(MIN_PK, min(MAX_PK, pk))


class Unit:
    """A single platform on the map (aircraft, ship, etc.)."""

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
        self.loadout    = dict(loadout)   # weapon_key → quantity remaining
        self.image_path = image_path

        self.waypoints: list[tuple[float, float]] = []
        self.heading    = 0.0
        self.selected   = False
        self.alive      = True
        self.is_detected = False
        self.flash_frames = 0             # non-zero while drawing hit flash
        self.selected_weapon: Optional[str] = None  # preferred weapon key for firing

        # Home position — Red units return here after completing their patrol
        self.home_lat: float = lat
        self.home_lon: float = lon

        # AI state (Red units only)
        # "patrol"    → following assigned waypoints
        # "returning" → heading back to home_lat/home_lon after last waypoint
        self.ai_state: str   = "patrol"
        self.ai_fire_cooldown: float = 0.0   # sim-seconds until next AI shot

        # Image surface loaded lazily by the renderer
        self._surface: Optional[pygame.Surface] = None

    # ---------------------------------------------------------------- movement
    def add_waypoint(self, lat: float, lon: float) -> None:
        self.waypoints.append((lat, lon))
        self._recalc_heading()

    def clear_waypoints(self) -> None:
        self.waypoints.clear()

    def _recalc_heading(self) -> None:
        if self.waypoints:
            self.heading = bearing(self.lat, self.lon, *self.waypoints[0])

    def update(self, sim_delta: float) -> None:
        """Advance unit movement by sim_delta sim-seconds."""
        if not self.alive or not self.waypoints:
            return

        speed_kms    = self.platform.speed_kmh / 3600.0
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

    # ---------------------------------------------------------------- combat
    def has_ammo(self, weapon_key: str) -> bool:
        return self.loadout.get(weapon_key, 0) > 0

    def expend_round(self, weapon_key: str) -> bool:
        if self.has_ammo(weapon_key):
            self.loadout[weapon_key] -= 1
            return True
        return False

    def best_bvr_weapon(self, db: "Database") -> Optional[str]:
        """Return key of best loaded BVR weapon (non-gun, longest range)."""
        best_key   = None
        best_range = 0.0
        for wkey, qty in self.loadout.items():
            if qty <= 0:
                continue
            wdef = db.weapons.get(wkey)
            if wdef and not wdef.is_gun and wdef.range_km > best_range:
                best_key   = wkey
                best_range = wdef.range_km
        return best_key

    # ---------------------------------------------------------------- hit-flash
    def trigger_flash(self, frames: int = 12) -> None:
        self.flash_frames = frames

    def tick_flash(self) -> None:
        if self.flash_frames > 0:
            self.flash_frames -= 1

    # ---------------------------------------------------------------- is_clicked
    def is_clicked(self, screen_pos: tuple[int, int],
                   sx: float, sy: float, radius: int = 16) -> bool:
        """True if screen_pos is within radius pixels of the unit's screen pos."""
        return math.hypot(sx - screen_pos[0], sy - screen_pos[1]) <= radius

    # ---------------------------------------------------------------- repr
    def __repr__(self) -> str:
        return (f"<Unit {self.callsign} ({self.side}) "
                f"lat={self.lat:.2f} lon={self.lon:.2f} alive={self.alive}>")


# ── Embedded databases (no external files required) ─────────────────────────
# These can be overridden by passing custom paths to Database(), but the
# defaults are always available even if the data/ folder is missing.

_WEAPONS_DATA = {
    # ── Eastern BVR ──────────────────────────────────────────────────────────
    "R-27R":   {"display_name": "R-27R Alamo-A",      "seeker": "SARH",   "range_km": 73,  "min_range_km": 3,   "speed_kmh": 3000, "base_pk": 0.82, "is_gun": False, "description": "Semi-active radar homing BVR missile"},
    "R-27T":   {"display_name": "R-27T Alamo-B",      "seeker": "IR",     "range_km": 70,  "min_range_km": 3,   "speed_kmh": 3000, "base_pk": 0.80, "is_gun": False, "description": "Infrared-homing variant of the Alamo"},
    "R-77":    {"display_name": "R-77 Adder",         "seeker": "ARH",    "range_km": 110, "min_range_km": 3,   "speed_kmh": 3600, "base_pk": 0.84, "is_gun": False, "description": "Active radar homing BVR missile"},
    "R-73":    {"display_name": "R-73 Archer",        "seeker": "IR",     "range_km": 30,  "min_range_km": 0.5, "speed_kmh": 2500, "base_pk": 0.88, "is_gun": False, "description": "High-agility short-range IR missile"},
    "R-60":    {"display_name": "R-60 Aphid",         "seeker": "IR",     "range_km": 8,   "min_range_km": 0.3, "speed_kmh": 2200, "base_pk": 0.72, "is_gun": False, "description": "Short-range IR dogfight missile (older)"},
    # ── Western BVR ──────────────────────────────────────────────────────────
    "AIM-120C":{"display_name": "AIM-120C AMRAAM",    "seeker": "ARH",    "range_km": 105, "min_range_km": 3,   "speed_kmh": 3600, "base_pk": 0.85, "is_gun": False, "description": "NATO standard active radar BVR missile"},
    "AIM-9X":  {"display_name": "AIM-9X Sidewinder",  "seeker": "IR",     "range_km": 35,  "min_range_km": 0.5, "speed_kmh": 2700, "base_pk": 0.90, "is_gun": False, "description": "High off-boresight IR dogfight missile"},
    "AIM-9M":  {"display_name": "AIM-9M Sidewinder",  "seeker": "IR",     "range_km": 18,  "min_range_km": 0.5, "speed_kmh": 2500, "base_pk": 0.82, "is_gun": False, "description": "Older IR dogfight missile, widely used"},
    "MICA-EM": {"display_name": "MICA EM",            "seeker": "ARH",    "range_km": 80,  "min_range_km": 0.5, "speed_kmh": 4000, "base_pk": 0.86, "is_gun": False, "description": "French active radar BVR/WVR missile"},
    "MICA-IR": {"display_name": "MICA IR",            "seeker": "IR",     "range_km": 60,  "min_range_km": 0.5, "speed_kmh": 4000, "base_pk": 0.87, "is_gun": False, "description": "French IR BVR/WVR missile"},
    # ── Ground attack (treated as medium-range for sim purposes) ─────────────
    "Kh-25ML": {"display_name": "Kh-25ML (laser AGM)","seeker": "LASER",  "range_km": 25,  "min_range_km": 2,   "speed_kmh": 1800, "base_pk": 0.78, "is_gun": False, "description": "Laser-guided air-to-ground missile"},
    "S-8":     {"display_name": "S-8 Rockets (pod)",  "seeker": "CANNON", "range_km": 2,   "min_range_km": 0.2, "speed_kmh": 1200, "base_pk": 0.60, "is_gun": True,  "description": "Unguided rocket pod, short-range saturation"},
    "Shturm":  {"display_name": "9M114 Shturm ATGM",  "seeker": "SACLOS", "range_km": 5,   "min_range_km": 0.4, "speed_kmh": 600,  "base_pk": 0.75, "is_gun": False, "description": "Semi-active ATGM, Mi-24 primary weapon"},
    # ── Guns ─────────────────────────────────────────────────────────────────
    "GSh-30-1":{"display_name": "GSh-30-1 (30mm)",    "seeker": "CANNON", "range_km": 0.8, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.65, "is_gun": True,  "description": "30mm single-barrel aircraft cannon"},
    "GSh-30-2":{"display_name": "GSh-30-2 (twin 30mm)","seeker":"CANNON", "range_km": 1.2, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.68, "is_gun": True,  "description": "Twin-barrel 30mm cannon (Su-25)"},
    "GSh-6-23":{"display_name": "GSh-6-23 (23mm)",    "seeker": "CANNON", "range_km": 0.8, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.62, "is_gun": True,  "description": "6-barrel 23mm rotary cannon (Su-24)"},
    "M61A1":   {"display_name": "M61A1 Vulcan (20mm)", "seeker": "CANNON", "range_km": 0.8, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.65, "is_gun": True,  "description": "20mm six-barrel rotary cannon"},
    "DEFA-554":{"display_name": "DEFA 554 (30mm)",    "seeker": "CANNON", "range_km": 0.8, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.64, "is_gun": True,  "description": "30mm revolver cannon (Mirage 2000)"},
    "Yak-B":   {"display_name": "Yak-B (12.7mm)",     "seeker": "CANNON", "range_km": 0.6, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.55, "is_gun": True,  "description": "12.7mm four-barrel rotary (Mi-24 chin gun)"},    # ── Ground weapons ───────────────────────────────────────────────────────
    "GUN_125":     {"display_name": "125mm APFSDS",         "seeker": "CANNON", "range_km": 4.0, "min_range_km": 0.1, "speed_kmh": 0, "base_pk": 0.80, "is_gun": True,  "description": "125mm smoothbore tank gun (T-64/72/80)"},
    "GUN_120NATO": {"display_name": "120mm NATO APFSDS",    "seeker": "CANNON", "range_km": 4.5, "min_range_km": 0.1, "speed_kmh": 0, "base_pk": 0.82, "is_gun": True,  "description": "120mm NATO smoothbore gun (Leopard 2, M1 Abrams)"},
    "GUN_120UK":   {"display_name": "120mm L30A1 (rifled)", "seeker": "CANNON", "range_km": 4.5, "min_range_km": 0.1, "speed_kmh": 0, "base_pk": 0.82, "is_gun": True,  "description": "120mm rifled gun (Challenger 2)"},
    "GUN_105":     {"display_name": "105mm APFSDS",         "seeker": "CANNON", "range_km": 3.0, "min_range_km": 0.1, "speed_kmh": 0, "base_pk": 0.76, "is_gun": True,  "description": "105mm L7/M68 rifled gun (Leopard 1, M113-derived)"},
    "AUTOCANNON_30": {"display_name": "30mm Autocannon",   "seeker": "CANNON", "range_km": 2.5, "min_range_km": 0.1, "speed_kmh": 0, "base_pk": 0.70, "is_gun": True,  "description": "30mm 2A42/Mk 44 autocannon (BMP-2, Marder, CV90)"},
    "AUTOCANNON_25": {"display_name": "25mm M242 Bushmaster","seeker": "CANNON","range_km": 2.0, "min_range_km": 0.1, "speed_kmh": 0, "base_pk": 0.68, "is_gun": True,  "description": "25mm chain gun (Bradley M2/M3)"},
    "ATGM_Konkurs": {"display_name": "9M113 Konkurs ATGM", "seeker": "SACLOS", "range_km": 4.0, "min_range_km": 0.5, "speed_kmh": 200, "base_pk": 0.78, "is_gun": False, "description": "Wire-guided ATGM (BMP-2, 9P148)"},
    "ATGM_TOW":    {"display_name": "BGM-71 TOW ATGM",     "seeker": "SACLOS", "range_km": 3.7, "min_range_km": 0.3, "speed_kmh": 300, "base_pk": 0.80, "is_gun": False, "description": "Wire-guided TOW missile (Bradley, M113)"},
    "ATGM_Stugna": {"display_name": "Stugna-P ATGM",       "seeker": "LASER",  "range_km": 5.5, "min_range_km": 0.1, "speed_kmh": 400, "base_pk": 0.82, "is_gun": False, "description": "Ukrainian laser-guided ATGM (AMX-10 RC, BMP-1U)"},
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
    },
    "Su-27UA": {
        "display_name": "Su-27 Flanker-B", "type": "fighter",
        "speed_kmh": 2500, "ceiling_ft": 59000, "ecm_rating": 0.25,
        "radar": {"type": "N001 Mech", "range_km": 200, "modes": ["air","surface"]},
        "default_loadout": {"R-27R": 4, "R-27T": 2, "R-73": 4, "GSh-30-1": 1},
        "available_weapons": ["R-27R","R-27T","R-73","AIM-9M","GSh-30-1"],
        "fleet_count": 23, "player_side": "Blue",
    },
    "F-16AM": {
        "display_name": "F-16AM Fighting Falcon (MLU)", "type": "fighter",
        "speed_kmh": 2150, "ceiling_ft": 50000, "ecm_rating": 0.22,
        "radar": {"type": "AN/APG-66(V)2", "range_km": 130, "modes": ["air","surface"]},
        "default_loadout": {"AIM-120C": 4, "AIM-9X": 2, "M61A1": 1},
        "available_weapons": ["AIM-120C","AIM-9X","AIM-9M","M61A1"],
        "fleet_count": 23, "player_side": "Blue",
    },
    "F-16UA": {
        "display_name": "F-16 Fighting Falcon (Block 52)", "type": "fighter",
        "speed_kmh": 2150, "ceiling_ft": 50000, "ecm_rating": 0.18,
        "radar": {"type": "AN/APG-68(V)9", "range_km": 148, "modes": ["air","surface"]},
        "default_loadout": {"AIM-120C": 6, "AIM-9X": 2, "M61A1": 1},
        "available_weapons": ["AIM-120C","AIM-9X","AIM-9M","M61A1"],
        "fleet_count": 20, "player_side": "Blue",
    },
    "Su-25UA": {
        "display_name": "Su-25 Frogfoot (CAS)", "type": "attacker",
        "speed_kmh": 950,  "ceiling_ft": 23000, "ecm_rating": 0.15,
        "radar": {"type": "Klen-PS (laser)", "range_km": 20, "modes": ["surface"]},
        "default_loadout": {"R-60": 2, "S-8": 2, "GSh-30-2": 1},
        "available_weapons": ["R-60","S-8","Kh-25ML","GSh-30-2"],
        "fleet_count": 19, "player_side": "Blue",
    },
    "Su-24M": {
        "display_name": "Su-24M Fencer-D (Strike)", "type": "attacker",
        "speed_kmh": 1700, "ceiling_ft": 36000, "ecm_rating": 0.18,
        "radar": {"type": "Orion-A", "range_km": 50, "modes": ["surface"]},
        "default_loadout": {"Kh-25ML": 4, "R-60": 2, "GSh-6-23": 1},
        "available_weapons": ["Kh-25ML","R-60","GSh-6-23"],
        "fleet_count": 13, "player_side": "Blue",
    },
    "Mirage2000-5F": {
        "display_name": "Mirage 2000-5F", "type": "fighter",
        "speed_kmh": 2530, "ceiling_ft": 59000, "ecm_rating": 0.28,
        "radar": {"type": "RDY-2", "range_km": 185, "modes": ["air","surface"]},
        "default_loadout": {"MICA-EM": 4, "MICA-IR": 2, "DEFA-554": 1},
        "available_weapons": ["MICA-EM","MICA-IR","AIM-9M","DEFA-554"],
        "fleet_count": 6, "player_side": "Blue",
    },
    # ══ UKRAINE ARMY AVIATION (Blue) ═════════════════════════════════════════
    "Mi-8UA": {
        "display_name": "Mi-8 Hip (Armed)", "type": "helicopter",
        "speed_kmh": 260,  "ceiling_ft": 14800, "ecm_rating": 0.05,
        "radar": {"type": "None", "range_km": 15, "modes": ["surface"]},
        "default_loadout": {"S-8": 4},
        "available_weapons": ["S-8"],
        "fleet_count": 73, "player_side": "Blue",
    },
    "Mi-24V": {
        "display_name": "Mi-24V Hind-E (Attack)", "type": "helicopter",
        "speed_kmh": 320,  "ceiling_ft": 14800, "ecm_rating": 0.10,
        "radar": {"type": "None", "range_km": 20, "modes": ["surface"]},
        "default_loadout": {"Shturm": 4, "S-8": 2, "Yak-B": 1},
        "available_weapons": ["Shturm","S-8","R-60","Yak-B"],
        "fleet_count": 38, "player_side": "Blue",
    },
    "Mi-2UA": {
        "display_name": "Mi-2 Hoplite (Light)", "type": "helicopter",
        "speed_kmh": 210,  "ceiling_ft": 13100, "ecm_rating": 0.03,
        "radar": {"type": "None", "range_km": 10, "modes": ["surface"]},
        "default_loadout": {"S-8": 2},
        "available_weapons": ["S-8"],
        "fleet_count": 12, "player_side": "Blue",
    },
    # ══ UKRAINE NAVY AVIATION (Blue) ═════════════════════════════════════════
    "Ka-27": {
        "display_name": "Ka-27 Helix (ASW)", "type": "helicopter",
        "speed_kmh": 270,  "ceiling_ft": 12500, "ecm_rating": 0.05,
        "radar": {"type": "OGAS/VGS-3", "range_km": 25, "modes": ["surface"]},
        "default_loadout": {"S-8": 2},
        "available_weapons": ["S-8"],
        "fleet_count": 4, "player_side": "Blue",
    },
    "Mi-14": {
        "display_name": "Mi-14 Haze (ASW/SAR)", "type": "helicopter",
        "speed_kmh": 230,  "ceiling_ft": 11500, "ecm_rating": 0.05,
        "radar": {"type": "Search radar", "range_km": 30, "modes": ["surface"]},
        "default_loadout": {"S-8": 2},
        "available_weapons": ["S-8"],
        "fleet_count": 4, "player_side": "Blue",
    },
    "SeaKing": {
        "display_name": "Sea King (SAR/ASW)", "type": "helicopter",
        "speed_kmh": 230,  "ceiling_ft": 10000, "ecm_rating": 0.08,
        "radar": {"type": "ARI-5955", "range_km": 35, "modes": ["surface"]},
        "default_loadout": {"S-8": 2},
        "available_weapons": ["S-8"],
        "fleet_count": 3, "player_side": "Blue",
    },
    # ══ RUSSIA AIR FORCE (Red) ════════════════════════════════════════════════
    "Su-27S": {
        "display_name": "Su-27S Flanker-B", "type": "fighter",
        "speed_kmh": 2500, "ceiling_ft": 59000, "ecm_rating": 0.25,
        "radar": {"type": "N001 Mech", "range_km": 240, "modes": ["air","surface"]},
        "default_loadout": {"R-27R": 4, "R-27T": 2, "R-73": 4, "GSh-30-1": 1},
        "available_weapons": ["R-27R","R-27T","R-73","GSh-30-1"],
        "fleet_count": 0, "player_side": "Red",
    },
    "Su-35S": {
        "display_name": "Su-35S Flanker-E", "type": "fighter",
        "speed_kmh": 2500, "ceiling_ft": 59000, "ecm_rating": 0.30,
        "radar": {"type": "Irbis-E", "range_km": 300, "modes": ["air","surface"]},
        "default_loadout": {"R-77": 4, "R-27T": 2, "R-73": 6, "GSh-30-1": 1},
        "available_weapons": ["R-77","R-27R","R-27T","R-73","GSh-30-1"],
        "fleet_count": 0, "player_side": "Red",
    },
    "MiG-29A": {
        "display_name": "MiG-29A Fulcrum-A", "type": "fighter",
        "speed_kmh": 2400, "ceiling_ft": 57000, "ecm_rating": 0.20,
        "radar": {"type": "N019 Sapfir", "range_km": 180, "modes": ["air"]},
        "default_loadout": {"R-27R": 2, "R-27T": 2, "R-73": 4, "GSh-30-1": 1},
        "available_weapons": ["R-27R","R-27T","R-73","GSh-30-1"],
        "fleet_count": 0, "player_side": "Red",
    },
    "Su-30SM": {
        "display_name": "Su-30SM Flanker-H", "type": "fighter",
        "speed_kmh": 2125, "ceiling_ft": 56700, "ecm_rating": 0.28,
        "radar": {"type": "N011M BARS", "range_km": 280, "modes": ["air","surface"]},
        "default_loadout": {"R-77": 4, "R-73": 4, "GSh-30-1": 1},
        "available_weapons": ["R-77","R-27R","R-27T","R-73","GSh-30-1"],
        "fleet_count": 0, "player_side": "Red",
    },
    # ══ RUSSIA GROUND FORCES (Red) ═══════════════════════════════════════════
    # ── MBTs ─────────────────────────────────────────────────────────────────
    "T-90R": {
        "display_name": "T-90A / T-90M", "type": "tank",
        "speed_kmh": 65, "ceiling_ft": 0, "ecm_rating": 0.10,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"GUN_125": 40, "ATGM_Konkurs": 4},
        "available_weapons": ["GUN_125", "ATGM_Konkurs"],
        "fleet_count": 30, "player_side": "Red",
    },
    "T-72R": {
        "display_name": "T-72B1 / T-72B3", "type": "tank",
        "speed_kmh": 60, "ceiling_ft": 0, "ecm_rating": 0.06,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"GUN_125": 40, "ATGM_Konkurs": 4},
        "available_weapons": ["GUN_125", "ATGM_Konkurs"],
        "fleet_count": 520, "player_side": "Red",
    },
    "T-80R": {
        "display_name": "T-80BV / T-80BVM / T-80U", "type": "tank",
        "speed_kmh": 70, "ceiling_ft": 0, "ecm_rating": 0.08,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"GUN_125": 38, "ATGM_Konkurs": 4},
        "available_weapons": ["GUN_125", "ATGM_Konkurs"],
        "fleet_count": 88, "player_side": "Red",
    },
    "T-64R": {
        "display_name": "T-64BV (captured)", "type": "tank",
        "speed_kmh": 60, "ceiling_ft": 0, "ecm_rating": 0.06,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"GUN_125": 38, "ATGM_Stugna": 4},
        "available_weapons": ["GUN_125", "ATGM_Stugna"],
        "fleet_count": 220, "player_side": "Red",
    },
    "T-62R": {
        "display_name": "T-62M / T-62MV", "type": "tank",
        "speed_kmh": 50, "ceiling_ft": 0, "ecm_rating": 0.03,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"GUN_105": 40},
        "available_weapons": ["GUN_105"],
        "fleet_count": 50, "player_side": "Red",
    },
    "T-55R": {
        "display_name": "T-55 M-55S (Russian)", "type": "tank",
        "speed_kmh": 50, "ceiling_ft": 0, "ecm_rating": 0.03,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"GUN_105": 43},
        "available_weapons": ["GUN_105"],
        "fleet_count": 26, "player_side": "Red",
    },
    # ── IFVs ──────────────────────────────────────────────────────────────────
    "BMP-2R": {
        "display_name": "BMP-2 (Russian)", "type": "ifv",
        "speed_kmh": 65, "ceiling_ft": 0, "ecm_rating": 0.03,
        "radar": {"type": "Optical sight", "range_km": 4, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 500, "ATGM_Konkurs": 4},
        "available_weapons": ["AUTOCANNON_30", "ATGM_Konkurs"],
        "fleet_count": 150, "player_side": "Red",
    },
    "BMP-3R": {
        "display_name": "BMP-3", "type": "ifv",
        "speed_kmh": 70, "ceiling_ft": 0, "ecm_rating": 0.05,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"GUN_105": 40, "AUTOCANNON_30": 500, "ATGM_Konkurs": 4},
        "available_weapons": ["GUN_105", "AUTOCANNON_30", "ATGM_Konkurs"],
        "fleet_count": 45, "player_side": "Red",
    },
    "BMP-1R": {
        "display_name": "BMP-1 (Russian)", "type": "ifv",
        "speed_kmh": 65, "ceiling_ft": 0, "ecm_rating": 0.03,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 300, "ATGM_Konkurs": 3},
        "available_weapons": ["AUTOCANNON_30", "ATGM_Konkurs"],
        "fleet_count": 250, "player_side": "Red",
    },
    "BMD-2R": {
        "display_name": "BMD-2 (Airborne)", "type": "ifv",
        "speed_kmh": 60, "ceiling_ft": 0, "ecm_rating": 0.03,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 300, "ATGM_Konkurs": 3},
        "available_weapons": ["AUTOCANNON_30", "ATGM_Konkurs"],
        "fleet_count": 20, "player_side": "Red",
    },
    # ── APCs ──────────────────────────────────────────────────────────────────
    "BTR-80R": {
        "display_name": "BTR-80 / BTR-82A (Russian)", "type": "apc",
        "speed_kmh": 80, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 300},
        "available_weapons": ["AUTOCANNON_30"],
        "fleet_count": 302, "player_side": "Red",
    },
    "BTR-70R": {
        "display_name": "BTR-70 / BTR-70M", "type": "apc",
        "speed_kmh": 80, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_25": 300},
        "available_weapons": ["AUTOCANNON_25"],
        "fleet_count": 217, "player_side": "Red",
    },
    "MTLBR": {
        "display_name": "MT-LB / MT-LBu (Russian)", "type": "apc",
        "speed_kmh": 62, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_25": 200},
        "available_weapons": ["AUTOCANNON_25"],
        "fleet_count": 125, "player_side": "Red",
    },
    # ── Recon ─────────────────────────────────────────────────────────────────
    "BRDM2R": {
        "display_name": "BRDM-2 / BRDM-2T (Russian)", "type": "recon",
        "speed_kmh": 95, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical + IR", "range_km": 6, "modes": ["surface"]},
        "default_loadout": {"ATGM_Konkurs": 6},
        "available_weapons": ["ATGM_Konkurs"],
        "fleet_count": 120, "player_side": "Red",
    },
    # ── Tank Destroyers ───────────────────────────────────────────────────────
    "9P148R": {
        "display_name": "9P148 Konkurs ATGM (Russian)", "type": "tank_destroyer",
        "speed_kmh": 100, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical sight", "range_km": 4, "modes": ["surface"]},
        "default_loadout": {"ATGM_Konkurs": 20},
        "available_weapons": ["ATGM_Konkurs"],
        "fleet_count": 7, "player_side": "Red",
    },

    # ══ UKRAINE GROUND FORCES (Blue) ══════════════════════════════════════════
    # ── Main Battle Tanks ─────────────────────────────────────────────────────
    "T-72":         {
        "display_name": "T-72 (various)", "type": "tank",
        "speed_kmh": 60, "ceiling_ft": 0, "ecm_rating": 0.05,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"GUN_125": 40, "ATGM_Konkurs": 4},
        "available_weapons": ["GUN_125", "ATGM_Konkurs"],
        "fleet_count": 520, "player_side": "Blue",
    },
    "T-64":         {
        "display_name": "T-64BV / Bulat", "type": "tank",
        "speed_kmh": 60, "ceiling_ft": 0, "ecm_rating": 0.08,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"GUN_125": 38, "ATGM_Stugna": 4},
        "available_weapons": ["GUN_125", "ATGM_Stugna"],
        "fleet_count": 220, "player_side": "Blue",
    },
    "T-80":         {
        "display_name": "T-80BV / BVM", "type": "tank",
        "speed_kmh": 70, "ceiling_ft": 0, "ecm_rating": 0.08,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"GUN_125": 38, "ATGM_Konkurs": 4},
        "available_weapons": ["GUN_125", "ATGM_Konkurs"],
        "fleet_count": 88, "player_side": "Blue",
    },
    "Leopard1":     {
        "display_name": "Leopard 1A5", "type": "tank",
        "speed_kmh": 65, "ceiling_ft": 0, "ecm_rating": 0.05,
        "radar": {"type": "Thermal sight", "range_km": 6, "modes": ["surface"]},
        "default_loadout": {"GUN_105": 55, "ATGM_Konkurs": 2},
        "available_weapons": ["GUN_105", "ATGM_Konkurs"],
        "fleet_count": 103, "player_side": "Blue",
    },
    "Leopard2":     {
        "display_name": "Leopard 2A4/A6 / Strv 122", "type": "tank",
        "speed_kmh": 72, "ceiling_ft": 0, "ecm_rating": 0.10,
        "radar": {"type": "Hunter-killer sight", "range_km": 7, "modes": ["surface"]},
        "default_loadout": {"GUN_120NATO": 42, "ATGM_TOW": 2},
        "available_weapons": ["GUN_120NATO", "ATGM_TOW"],
        "fleet_count": 60, "player_side": "Blue",
    },
    "Challenger2":  {
        "display_name": "Challenger 2", "type": "tank",
        "speed_kmh": 59, "ceiling_ft": 0, "ecm_rating": 0.10,
        "radar": {"type": "TOGS II thermal", "range_km": 7, "modes": ["surface"]},
        "default_loadout": {"GUN_120UK": 47, "ATGM_TOW": 2},
        "available_weapons": ["GUN_120UK", "ATGM_TOW"],
        "fleet_count": 13, "player_side": "Blue",
    },
    "M1Abrams":     {
        "display_name": "M1A1 Abrams", "type": "tank",
        "speed_kmh": 68, "ceiling_ft": 0, "ecm_rating": 0.10,
        "radar": {"type": "Hunter-killer sight", "range_km": 7, "modes": ["surface"]},
        "default_loadout": {"GUN_120NATO": 40, "ATGM_TOW": 2},
        "available_weapons": ["GUN_120NATO", "ATGM_TOW"],
        "fleet_count": 25, "player_side": "Blue",
    },
    "T-55":         {
        "display_name": "T-55 M-55S", "type": "tank",
        "speed_kmh": 50, "ceiling_ft": 0, "ecm_rating": 0.03,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"GUN_105": 43},
        "available_weapons": ["GUN_105"],
        "fleet_count": 26, "player_side": "Blue",
    },
    # ── Infantry Fighting Vehicles ────────────────────────────────────────────
    "BMP-1":        {
        "display_name": "BMP-1 (various)", "type": "ifv",
        "speed_kmh": 65, "ceiling_ft": 0, "ecm_rating": 0.03,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 300, "ATGM_Konkurs": 3},
        "available_weapons": ["AUTOCANNON_30", "ATGM_Konkurs"],
        "fleet_count": 250, "player_side": "Blue",
    },
    "BMP-2":        {
        "display_name": "BMP-2", "type": "ifv",
        "speed_kmh": 65, "ceiling_ft": 0, "ecm_rating": 0.03,
        "radar": {"type": "Optical sight", "range_km": 4, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 500, "ATGM_Konkurs": 4},
        "available_weapons": ["AUTOCANNON_30", "ATGM_Konkurs"],
        "fleet_count": 150, "player_side": "Blue",
    },
    "Bradley":      {
        "display_name": "M2 Bradley", "type": "ifv",
        "speed_kmh": 66, "ceiling_ft": 0, "ecm_rating": 0.06,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_25": 900, "ATGM_TOW": 7},
        "available_weapons": ["AUTOCANNON_25", "ATGM_TOW"],
        "fleet_count": 350, "player_side": "Blue",
    },
    "Marder":       {
        "display_name": "Marder 1A3", "type": "ifv",
        "speed_kmh": 75, "ceiling_ft": 0, "ecm_rating": 0.05,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 1000, "ATGM_TOW": 4},
        "available_weapons": ["AUTOCANNON_30", "ATGM_TOW"],
        "fleet_count": 140, "player_side": "Blue",
    },
    "CV90":         {
        "display_name": "CV9040", "type": "ifv",
        "speed_kmh": 70, "ceiling_ft": 0, "ecm_rating": 0.06,
        "radar": {"type": "Thermal sight", "range_km": 5, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 1000, "ATGM_Konkurs": 4},
        "available_weapons": ["AUTOCANNON_30", "ATGM_Konkurs"],
        "fleet_count": 48, "player_side": "Blue",
    },
    "YPR-765":      {
        "display_name": "YPR-765 PRAT", "type": "ifv",
        "speed_kmh": 60, "ceiling_ft": 0, "ecm_rating": 0.03,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_25": 400, "ATGM_TOW": 2},
        "available_weapons": ["AUTOCANNON_25", "ATGM_TOW"],
        "fleet_count": 353, "player_side": "Blue",
    },
    # ── Armoured Personnel Carriers ───────────────────────────────────────────
    "M113":         {
        "display_name": "M113 APC (various)", "type": "apc",
        "speed_kmh": 64, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical sight", "range_km": 2, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_25": 200},
        "available_weapons": ["AUTOCANNON_25"],
        "fleet_count": 510, "player_side": "Blue",
    },
    "Stryker":      {
        "display_name": "Stryker M1126", "type": "apc",
        "speed_kmh": 96, "ceiling_ft": 0, "ecm_rating": 0.05,
        "radar": {"type": "Optical sight", "range_km": 4, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 200, "ATGM_TOW": 2},
        "available_weapons": ["AUTOCANNON_30", "ATGM_TOW"],
        "fleet_count": 400, "player_side": "Blue",
    },
    "BTR-80":       {
        "display_name": "BTR-80 / BTR-82A", "type": "apc",
        "speed_kmh": 80, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 300},
        "available_weapons": ["AUTOCANNON_30"],
        "fleet_count": 302, "player_side": "Blue",
    },
    "BTR-70":       {
        "display_name": "BTR-70 / BTR-70M", "type": "apc",
        "speed_kmh": 80, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_25": 300},
        "available_weapons": ["AUTOCANNON_25"],
        "fleet_count": 217, "player_side": "Blue",
    },
    "VAB":          {
        "display_name": "VAB APC", "type": "apc",
        "speed_kmh": 92, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_25": 200},
        "available_weapons": ["AUTOCANNON_25"],
        "fleet_count": 250, "player_side": "Blue",
    },
    "M1117":        {
        "display_name": "M1117 Guardian ASV", "type": "apc",
        "speed_kmh": 100, "ceiling_ft": 0, "ecm_rating": 0.03,
        "radar": {"type": "Optical sight", "range_km": 3, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_25": 200},
        "available_weapons": ["AUTOCANNON_25"],
        "fleet_count": 400, "player_side": "Blue",
    },
    # ── Reconnaissance ────────────────────────────────────────────────────────
    "BRDM-2":       {
        "display_name": "BRDM-2 Recon", "type": "recon",
        "speed_kmh": 95, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical + IR", "range_km": 6, "modes": ["surface"]},
        "default_loadout": {"ATGM_Konkurs": 6},
        "available_weapons": ["ATGM_Konkurs"],
        "fleet_count": 120, "player_side": "Blue",
    },
    "BRM-1":        {
        "display_name": "BRM-1K Recon IFV", "type": "recon",
        "speed_kmh": 65, "ceiling_ft": 0, "ecm_rating": 0.03,
        "radar": {"type": "PSNR-5 battlefield radar", "range_km": 8, "modes": ["surface"]},
        "default_loadout": {"AUTOCANNON_30": 300, "ATGM_Konkurs": 4},
        "available_weapons": ["AUTOCANNON_30", "ATGM_Konkurs"],
        "fleet_count": 50, "player_side": "Blue",
    },
    # ── Tank Destroyers ───────────────────────────────────────────────────────
    "AMX10RC":      {
        "display_name": "AMX-10 RC", "type": "tank_destroyer",
        "speed_kmh": 85, "ceiling_ft": 0, "ecm_rating": 0.05,
        "radar": {"type": "HL-70 sight", "range_km": 6, "modes": ["surface"]},
        "default_loadout": {"GUN_105": 38, "ATGM_Stugna": 4},
        "available_weapons": ["GUN_105", "ATGM_Stugna"],
        "fleet_count": 35, "player_side": "Blue",
    },
    "9P148":        {
        "display_name": "9P148 Konkurs ATGM carrier", "type": "tank_destroyer",
        "speed_kmh": 100, "ceiling_ft": 0, "ecm_rating": 0.02,
        "radar": {"type": "Optical sight", "range_km": 4, "modes": ["surface"]},
        "default_loadout": {"ATGM_Konkurs": 20},
        "available_weapons": ["ATGM_Konkurs"],
        "fleet_count": 7, "player_side": "Blue",
    },

}


# ── Database ─────────────────────────────────────────────────────────────────

class Database:
    """Loads weapon and platform definitions.

    Data is embedded directly in this file so no external data/ folder is
    required.  Passing explicit *_path arguments will override the defaults
    with a JSON file on disk (useful for modding / expansion packs).
    """

    def __init__(self,
                 weapons_path:  str = None,
                 units_path:    str = None):
        self.weapons:   dict[str, WeaponDef]   = {}
        self.platforms: dict[str, PlatformDef] = {}

        # ── Weapons ──────────────────────────────────────────────────────────
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
            )

        # ── Platforms ─────────────────────────────────────────────────────────
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
                radar_range_km    = d["radar"]["range_km"],
                radar_type        = d["radar"]["type"],
                radar_modes       = tuple(d["radar"]["modes"]),
                default_loadout   = d["default_loadout"],
                available_weapons = tuple(d.get("available_weapons",
                                          list(d["default_loadout"].keys()))),
                fleet_count       = d.get("fleet_count", 0),
                player_side       = d.get("player_side", "Any"),
            )


# ── Scenario load / save ──────────────────────────────────────────────────────

def load_scenario(path: str, db: Database) -> tuple[list[Unit], dict]:
    """Parse a scenario JSON file, return (units, meta) where meta has
    start_lat / start_lon / start_zoom / name / description."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    units: list[Unit] = []
    for ud in data.get("units", []):
        platform_key = ud["platform"]
        platform     = db.platforms.get(platform_key)
        if platform is None:
            print(f"[WARN] Unknown platform '{platform_key}', skipping.")
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
    """Serialise the current scenario state to a JSON file."""
    units_data = []
    for u in units:
        units_data.append({
            "id":         u.uid,
            "platform":   u.platform.key,
            "callsign":   u.callsign,
            "side":       u.side,
            "lat":        round(u.lat, 6),
            "lon":        round(u.lon, 6),
            "image_path": u.image_path,
            "loadout":    u.loadout,
            "waypoints":  [[round(lat, 6), round(lon, 6)]
                           for lat, lon in u.waypoints],
        })

    payload = {
        **meta,
        "game_time_seconds": round(game_time, 1),
        "units": units_data,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)