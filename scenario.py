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
    "Yak-B":   {"display_name": "Yak-B (12.7mm)",     "seeker": "CANNON", "range_km": 0.6, "min_range_km": 0.1, "speed_kmh": 0,    "base_pk": 0.55, "is_gun": True,  "description": "12.7mm four-barrel rotary (Mi-24 chin gun)"},
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