# scenario.py — data models + DB/scenario load & save

from __future__ import annotations
import json
import math
import os
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
import pygame

from constants import MIN_PK, MAX_PK, MISSILE_TRAIL_LEN, CHAFF_PK_PENALTY, FLARE_PK_PENALTY
from geo import haversine, bearing, slant_range_km

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
    rearm_time_s:      float            

@dataclass
class Mission:
    name: str
    mission_type: str
    target_lat: float
    target_lon: float
    radius_km: float
    altitude_ft: float
    rtb_fuel_pct: float

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
        dist         = slant_range_km(self.lat, self.lon, self.altitude_ft, self.target.lat, self.target.lon, self.target.altitude_ft)
        dist_penalty = (dist / 50.0) * 0.10
        pk = self.wdef.base_pk - dist_penalty
        if self.target.is_jamming and self.target.platform.ecm_rating > 0:
            if self.wdef.seeker in ("ARH", "SARH"):
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
        self.auto_engage = True if platform.unit_type in ("tank", "ifv", "apc", "recon", "tank_destroyer", "sam", "artillery") else False
        
        self.roe: str = "FREE" if side == "Red" else "TIGHT" 
        self.mission: Optional[Mission] = None
        
        self.home_uid: str = ""
        self.duty_state: str = "ACTIVE"  
        self.duty_timer: float = 0.0

        self.is_jamming: bool = False
        self.radar_active: bool = True
        self.chaff: int = platform.chaff_capacity
        self.flare: int = platform.flare_capacity

        # Evasion State
        self.is_evading: bool = False
        self.last_evasion_time: float = 0.0

        self.weapon_cooldowns: dict[str, float] = {k: 0.0 for k in self.loadout.keys()}
        self.altitude_ft: float = platform.cruise_alt_ft
        self.target_altitude_ft: float = self.altitude_ft

        self.home_lat: float = lat
        self.home_lon: float = lon
        self.ai_fire_cooldown: float = 0.0
        self.ai_gun_cooldown:  float = 0.0

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
            self.radar_active = False
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
        
        if self.duty_state == "REARMING":
            self.duty_timer -= sim_delta
            if self.duty_timer <= 0:
                self.duty_state = "READY"
                self.duty_timer = 0.0
                
        if self.platform.unit_type in ("fighter", "attacker", "helicopter") and self.duty_state != "ACTIVE":
            return
        
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

        if self.fuel_kg > 0 and self.platform.unit_type in ("fighter", "attacker", "helicopter"):
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

    def best_weapon_for(self, db: "Database", target: "Unit") -> Optional[str]:
        target_is_air = target.platform.unit_type in ("fighter", "attacker", "helicopter")
        best_key   = None
        best_range = -1.0
        for wkey, qty in self.loadout.items():
            if qty <= 0: continue
            wdef = db.weapons.get(wkey)
            if not wdef: continue
            if wdef.domain == "air" and not target_is_air: continue
            if wdef.domain == "ground" and target_is_air: continue
            if wdef.range_km > best_range:
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


# ── Database ─────────────────────────────────────────────────────────────────

class Database:
    def __init__(self,
                 weapons_path:  str = None,
                 units_path:    str = None):
        self.weapons:   dict[str, WeaponDef]   = {}
        self.platforms: dict[str, PlatformDef] = {}

        # Load from external JSON
        raw_weapons = {}
        if weapons_path and os.path.exists(weapons_path):
            with open(weapons_path, encoding="utf-8") as fh:
                raw_weapons = json.load(fh)
        else:
            print(f"[WARNING] Weapons file not found or path is empty: {weapons_path}")

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

        # Load from external JSON
        raw_platforms = {}
        if units_path and os.path.exists(units_path):
            with open(units_path, encoding="utf-8") as fh:
                raw_platforms = json.load(fh)
        else:
            print(f"[WARNING] Units file not found or path is empty: {units_path}")

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
                rearm_time_s      = float(d.get("rearm_time_s", 120.0)),
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
        unit.radar_active = ud.get("radar_active", True)
        unit.home_uid = ud.get("home_uid", "")
        unit.duty_state = ud.get("duty_state", "ACTIVE")
        unit.duty_timer = ud.get("duty_timer", 0.0)

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
            "radar_active": u.radar_active,
            "home_uid":   u.home_uid,
            "duty_state": u.duty_state,
            "duty_timer": round(u.duty_timer, 1),
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
            "radar_active": u.radar_active,
            "home_uid":   u.home_uid,
            "duty_state": u.duty_state,
            "duty_timer": round(u.duty_timer, 1),
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
        unit.radar_active = ud.get("radar_active", True)
        unit.home_uid = ud.get("home_uid", "")
        unit.duty_state = ud.get("duty_state", "ACTIVE")
        unit.duty_timer = ud.get("duty_timer", 0.0)

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