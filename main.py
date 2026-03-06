#!/usr/bin/env python3
# main.py — entry point

from __future__ import annotations

import sys
import os
import json
import pathlib
import tkinter as tk
from tkinter import filedialog
import math

import pygame
import pygame_gui

from constants import (
    WINDOW_WIDTH_DEFAULT, WINDOW_HEIGHT_DEFAULT,
    BOTTOM_PANEL_FRACTION, BOTTOM_PANEL_MIN_HEIGHT, FPS, TIME_SPEEDS,
)
from geo import lat_lon_to_pixel, pixel_to_lat_lon, world_to_screen
from renderer import Renderer
from scenario import Database, Unit, Mission, GameEvent, load_scenario, save_scenario, save_deployment, load_deployment
from simulation import SimulationEngine
from ui import GameUI

_HERE         = pathlib.Path(__file__).parent
SCENARIO_PATH = str(_HERE / "data" / "scenarios" / "ukraine_russia.json")
SAVE_PATH     = str(_HERE / "data" / "scenarios" / "ukraine_russia_save.json")
BGM_PATH      = str(_HERE / "assets" / "cc_style_bgm.mp3") 

_UID_COUNTER = 0

def _next_uid(prefix: str = "u") -> str:
    global _UID_COUNTER
    _UID_COUNTER += 1
    return f"{prefix}_{_UID_COUNTER:04d}"

def map_area_height(win_h: int) -> int:
    panel_h = max(BOTTOM_PANEL_MIN_HEIGHT, int(win_h * BOTTOM_PANEL_FRACTION))
    return max(200, win_h - panel_h)

class CameraState:
    def __init__(self, lat, lon, zoom, win_w, win_h):
        self.lat   = lat
        self.lon   = lon
        self.zoom  = zoom
        self.win_w = win_w
        self.win_h = win_h

    @property
    def map_h(self) -> int:
        return map_area_height(self.win_h)

    @property
    def pixel_xy(self):
        return lat_lon_to_pixel(self.lat, self.lon, self.zoom)

    def pan(self, dx, dy):
        px, py = self.pixel_xy
        self.lat, self.lon = pixel_to_lat_lon(px - dx, py - dy, self.zoom)

    def zoom_by(self, delta: int, sx: float, sy: float) -> None:
        target_zoom = max(4, min(12, self.zoom + delta))
        if target_zoom == self.zoom: 
            return
            
        target_lat, target_lon = self.screen_to_world(sx, sy)
        self.zoom = target_zoom
        tx, ty = lat_lon_to_pixel(target_lat, target_lon, self.zoom)
        
        cam_px = tx - sx + self.win_w / 2
        cam_py = ty - sy + self.map_h / 2
        
        self.lat, self.lon = pixel_to_lat_lon(cam_px, cam_py, self.zoom)

    def screen_to_world(self, sx, sy):
        px, py = self.pixel_xy
        return pixel_to_lat_lon(sx + px - self.win_w / 2, sy + py - self.map_h  / 2, self.zoom)

    def world_to_screen(self, lat, lon):
        px, py = self.pixel_xy
        return world_to_screen(lat, lon, px, py, self.zoom, self.win_w, self.map_h)

import random as _random
from geo import haversine

def _is_water(lat: float, lon: float) -> bool:
    if 45.3 < lat < 47.05 and 34.8 < lon < 38.5: return True
    if lat < 46.2 and lon < 32.8: return True
    if lat < 45.0 and lon < 33.4: return True
    if lat < 44.38: return True
    if lat < 44.8 and lon > 34.5: return True
    if lat < 45.0 and lon > 35.3: return True
    if 45.8 < lat < 46.2 and 34.0 < lon < 35.0: return True
    return False

_AO_BOUNDS = {
    "Luhansk":   {"lat": (48.5, 49.5), "lon": (38.0, 40.0)},
    "Donetsk":   {"lat": (47.6, 48.8), "lon": (37.0, 39.5)},
    "Zaporizhia":{"lat": (47.1, 47.9), "lon": (35.5, 37.8)},
}

_CLUSTERS = [
    {"name": "LUHANSK CITY",      "lat": 48.57, "lon": 39.34, "spread": 0.12, "mix": {"tank":3,"ifv":4,"apc":3,"recon":2,"tank_destroyer":1, "sam":2}},
    {"name": "SEVERODONETSK",     "lat": 48.95, "lon": 38.49, "spread": 0.10, "mix": {"tank":2,"ifv":3,"apc":4,"recon":1,"tank_destroyer":1, "sam":1}},
    {"name": "STAROBILSK",        "lat": 49.27, "lon": 38.92, "spread": 0.12, "mix": {"tank":3,"ifv":2,"apc":3,"recon":2,"tank_destroyer":2, "sam":1}},
    {"name": "ROVENKY",           "lat": 48.09, "lon": 39.37, "spread": 0.10, "mix": {"tank":2,"ifv":3,"apc":2,"recon":2,"tank_destroyer":1, "sam":1}},
    {"name": "DONETSK CITY",      "lat": 47.99, "lon": 37.80, "spread": 0.15, "mix": {"tank":4,"ifv":5,"apc":3,"recon":2,"tank_destroyer":2, "sam":2}},
    {"name": "MARIUPOL",          "lat": 47.10, "lon": 37.55, "spread": 0.12, "mix": {"tank":3,"ifv":3,"apc":4,"recon":1,"tank_destroyer":1, "sam":1}},
    {"name": "HORLIVKA",          "lat": 48.33, "lon": 38.06, "spread": 0.10, "mix": {"tank":3,"ifv":4,"apc":2,"recon":2,"tank_destroyer":1, "sam":1}},
    {"name": "VOLNOVAKHA",        "lat": 47.60, "lon": 37.50, "spread": 0.10, "mix": {"tank":2,"ifv":3,"apc":3,"recon":1,"tank_destroyer":1, "sam":1}},
    {"name": "TOKMAK",            "lat": 47.25, "lon": 35.71, "spread": 0.10, "mix": {"tank":2,"ifv":2,"apc":3,"recon":2,"tank_destroyer":1, "sam":2}},
    {"name": "MILLEROVO AIR",     "lat": 48.93, "lon": 40.39, "spread": 0.05, "mix": {"airbase":1, "fighter":4,"attacker":2, "sam":2}},
    {"name": "MOROZOVSK AIR",     "lat": 48.35, "lon": 41.83, "spread": 0.05, "mix": {"airbase":1, "fighter":4, "sam":2, "awacs":1}},
    {"name": "CRIMEA CENTRAL",    "lat": 45.28, "lon": 34.03, "spread": 0.15, "mix": {"tank":4,"ifv":5,"apc":4,"recon":2,"sam":3}},
    {"name": "SEVASTOPOL NAVAL",  "lat": 44.61, "lon": 33.52, "spread": 0.05, "mix": {"sam":2, "airbase":1, "fighter":2}},
    {"name": "DZHANKOI AIR",      "lat": 45.70, "lon": 34.42, "spread": 0.08, "mix": {"airbase":1, "helicopter":4, "sam":2, "tank": 2}},
    {"name": "BELBEK AIR",        "lat": 44.68, "lon": 33.56, "spread": 0.05, "mix": {"airbase":1, "fighter":3, "sam":1}},
]

_RED_GROUND_POOLS: dict[str, list[str]] = {
    "tank":           ["T-72R","T-72R","T-72R","T-80R","T-80R","T-64R","T-90R","T-62R","T-55R"],
    "ifv":            ["BMP-2R","BMP-2R","BMP-2R","BMP-1R","BMP-1R","BMP-3R","BMD-2R"],
    "apc":            ["BTR-80R","BTR-80R","BTR-70R","BTR-70R","MTLBR","MTLBR"],
    "recon":          ["BRDM2R","BRDM2R","BRM-1"],
    "tank_destroyer": ["9P148R","9P148R"],
    "fighter":        ["Su-35S","Su-35S","Su-30SM","MiG-29A"],
    "attacker":       ["Su-25UA"], 
    "helicopter":     ["Mi-24V", "Mi-8UA"],
    "sam":            ["S-400", "Buk-M2", "Buk-M2", "Tor-M1", "Tor-M1"],
    "airbase":        ["AirbaseR"],
    "awacs":          ["A-50U_Mainstay"],
}

_GROUND_CALLSIGNS: dict[str, list[str]] = {
    "tank":           ["HAMMER","ANVIL","IRON","STEEL","ARMOR","FIST","CLAW","BLADE"],
    "ifv":            ["WOLF","LYNX","FOX","VIPER","COBRA","SHARK"],
    "apc":            ["MULE","BISON","OX","RAM","BULL"],
    "recon":          ["SCOUT","HAWK","SHADOW","GHOST"],
    "tank_destroyer": ["HUNTER","RAPTOR","LANCE"],
    "sam":            ["SPIKE", "SHIELD", "DOME", "SPEAR", "ARROW"],
    "fighter":        ["BANDIT","FALCON","EAGLE","CROW"],
    "attacker":       ["FROG","SNAKE","JACKAL"],
    "helicopter":     ["HIND","BEAR","SWIFT"],
    "airbase":        ["STRATEGIC"],
    "awacs":          ["MAINSTAY", "EYE", "WATCHER"],
}

_GROUND_SIDE_NUMBERS: dict[str, int] = {} 

def _gen_red_callsign(unit_type: str) -> str:
    names = _GROUND_CALLSIGNS.get(unit_type, ["UNIT"])
    prefix = _random.choice(names)
    n = _GROUND_SIDE_NUMBERS.get(prefix, 0) + 1
    _GROUND_SIDE_NUMBERS[prefix] = n
    return f"{prefix} {n}"

def _generate_scenario(db: "Database") -> dict:
    _GROUND_SIDE_NUMBERS.clear()
    rng = _random.Random()   

    units = []
    uid   = 0

    for cluster in _CLUSTERS:
        clat, clon = cluster["lat"], cluster["lon"]
        spread     = cluster["spread"]

        for utype, count in cluster["mix"].items():
            pool = _RED_GROUND_POOLS.get(utype, [])
            if not pool: continue

            actual = max(1, count + rng.randint(-1, 1)) if utype != "airbase" else count
            flight_leader_uid = ""
            
            for _ in range(actual):
                uid += 1
                platform_key = rng.choice(pool)
                if platform_key not in db.platforms: continue
                plat = db.platforms[platform_key]

                lat, lon = clat, clon
                if utype != "airbase":
                    for _attempt in range(50):
                        test_lat = clat + rng.gauss(0, spread)
                        test_lon = clon + rng.gauss(0, spread * 1.4)
                        if plat.unit_type in ("tank", "ifv", "apc", "recon", "tank_destroyer", "sam", "artillery"):
                            if _is_water(test_lat, test_lon): continue 
                        lat, lon = test_lat, test_lon
                        break

                callsign = _gen_red_callsign(utype)
                entry: dict = {
                    "id":         f"red_{uid:03d}",
                    "platform":   platform_key,
                    "callsign":   callsign,
                    "side":       "Red",
                    "lat":        round(lat, 5),
                    "lon":        round(lon, 5),
                    "image_path": "assets/red_jet.png",
                    "drunkness":  rng.choices([1, 2, 3, 4, 5], weights=[20, 30, 25, 15, 10])[0],
                    "corruption": rng.choices([1, 2, 3, 4, 5], weights=[10, 30, 30, 20, 10])[0],
                    "loadout":    dict(plat.default_loadout),
                    "waypoints":  [],
                }

                if utype in ("fighter", "attacker", "helicopter", "awacs"):
                    if _ == 0:
                        flight_leader_uid = entry["id"]
                    else:
                        entry["leader_uid"] = flight_leader_uid
                        entry["formation_slot"] = _

                    entry["mission"] = {
                        "name": f"Patrol {callsign}",
                        "type": "CAP" if utype in ("fighter", "awacs") else "STRIKE",
                        "lat": lat + rng.uniform(-0.5, 0.5),
                        "lon": lon + rng.uniform(-0.5, 0.5),
                        "radius": 40.0,
                        "alt": 30000.0 if utype in ("fighter", "awacs") else 15000.0,
                        "rtb_fuel": 0.25
                    }
                units.append(entry)

    red_bases = [u for u in units if db.platforms[u["platform"]].unit_type == "airbase"]
    for u in units:
        if db.platforms[u["platform"]].unit_type in ("fighter", "attacker", "helicopter", "awacs"):
            if red_bases:
                closest = min(red_bases, key=lambda b: haversine(b["lat"], b["lon"], u["lat"], u["lon"]))
                u["home_uid"] = closest["id"]
                u["lat"], u["lon"] = closest["lat"], closest["lon"]
                u["duty_state"] = "ACTIVE"

    events = [
        {
            "id": "ev_01",
            "condition_type": "TIME",
            "condition_val": "180",
            "action_type": "LOG",
            "action_val": "Intel indicates massive Russian resupply convoy en route to Melitopol.",
            "triggered": False
        }
    ]

    return {
        "name":        "Operation East Wind — Donbas 2024",
        "description": "Russian forces are entrenched across Donbas and Luhansk.",
        "start_lat":   48.5,
        "start_lon":   38.5,
        "start_zoom":  8,
        "units":       units,
        "events":      events,
    }

def _write_default_scenario(path: str, db: "Database") -> None:
    scenario = _generate_scenario(db)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(scenario, fh, indent=2)

def _auto_deploy_blue(db: Database, sim: SimulationEngine, placement_counts: dict[str, int]) -> None:
    blue_clusters = [
        {"name": "STAROKOSTIANTYNIV", "lat": 49.74, "lon": 27.27, "mix": {"AirbaseB": 1, "Patriot": 1, "F-16A": 4, "Su-24M": 2}},
        {"name": "LVIV", "lat": 49.83, "lon": 24.02, "mix": {"AirbaseB": 1, "IRIS-T_SLM": 1, "MiG-29UA": 4}},
        {"name": "KYIV", "lat": 50.45, "lon": 30.52, "mix": {"AirbaseB": 1, "Patriot": 2, "NASAMS": 2, "F-16C": 4, "M142_HIMARS": 2, "E-3G_Sentry": 1}},
        {"name": "ODESA", "lat": 46.48, "lon": 30.72, "mix": {"AirbaseB": 1, "Patriot": 1, "MiG-29UA": 2, "Gepard": 2, "M777": 4}},
        {"name": "ZHYTOMYR", "lat": 50.25, "lon": 28.65, "mix": {"AirbaseB": 1, "Su-27UA": 2, "Mi-24V": 2, "IRIS-T_SLM": 1, "M270_MLRS": 1}},
        {"name": "DNIPRO", "lat": 48.46, "lon": 35.04, "mix": {"Leopard2": 2, "Bradley": 4, "Gepard": 2, "PzH2000": 3}},
        {"name": "KHERSON", "lat": 46.63, "lon": 32.61, "mix": {"M1Abrams": 2, "Stryker": 3, "Gepard": 1, "T-72": 2, "CAESAR": 2}}
    ]
    
    rng = _random.Random()
    
    for cluster in blue_clusters:
        clat, clon = cluster["lat"], cluster["lon"]
        base_uid = ""
        
        if "AirbaseB" in cluster["mix"]:
            count = cluster["mix"]["AirbaseB"]
            for _ in range(count):
                n = placement_counts.get("AirbaseB", 0) + 1
                placement_counts["AirbaseB"] = n
                callsign = _callsign_for("AirbaseB", n)
                unit = _make_blue_unit("AirbaseB", clat, clon, db, callsign)
                if unit:
                    sim.units.append(unit)
                    base_uid = unit.uid
        
        for utype, count in cluster["mix"].items():
            if utype == "AirbaseB": continue
            plat = db.platforms.get(utype)
            if not plat: continue
            
            flight_leader_uid = ""
            for _ in range(count):
                lat = clat + rng.gauss(0, 0.05)
                lon = clon + rng.gauss(0, 0.05)
                
                if plat.unit_type in ("tank", "ifv", "apc", "recon", "tank_destroyer", "sam", "artillery"):
                    if _is_water(lat, lon): lat, lon = clat, clon 
                if plat.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
                    lat, lon = clat, clon 
                    
                n = placement_counts.get(utype, 0) + 1
                placement_counts[utype] = n
                callsign = _callsign_for(utype, n)
                
                unit = _make_blue_unit(utype, lat, lon, db, callsign)
                if unit:
                    if plat.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
                        if _ == 0:
                            flight_leader_uid = unit.uid
                        else:
                            unit.leader_uid = flight_leader_uid
                            unit.formation_slot = _
                            
                    if plat.unit_type in ("fighter", "attacker", "helicopter", "awacs") and base_uid:
                        unit.home_uid = base_uid
                        unit.duty_state = "READY"
                        unit.altitude_ft = 0
                    sim.units.append(unit)
                    
    sim.log("Blue forces automatically deployed across Western/Central Ukraine.")


def _pick_unit(screen_pos, cam: CameraState, units: list[Unit],
               blue_only: bool = False,
               show_all_enemies: bool = False,
               app_mode: str = "combat") -> Unit | None:
    for unit in reversed(units):
        if not unit.alive: continue
        if app_mode == "combat" and unit.duty_state != "ACTIVE": continue
        if blue_only and unit.side != "Blue": continue
        if unit.side == "Red" and not unit.is_detected and not show_all_enemies: continue
        sx, sy = cam.world_to_screen(unit.lat, unit.lon)
        if unit.is_clicked(screen_pos, sx, sy):
            return unit
    return None

def _make_blue_unit(platform_key: str, lat: float, lon: float, db: Database, callsign: str) -> Unit | None:
    plat = db.platforms.get(platform_key)
    if plat is None: return None
    return Unit(
        uid        = _next_uid("blue"),
        callsign   = callsign,
        lat        = lat,
        lon        = lon,
        side       = "Blue",
        platform   = plat,
        loadout    = dict(plat.default_loadout),
        image_path = "assets/blue_jet.png",
        drunkness  = 1, 
        corruption = 1
    )

def _callsign_for(platform_key: str, index: int) -> str:
    prefixes = {
        "MiG-29UA":     "GHOST", "Su-27UA":      "PHANTOM", "F-16AM":       "VIPER", "F-16A":        "FALCON",
        "F-16C":        "FALCON", "F-16UA":       "FALCON", "Su-25UA":      "WARTHOG", "Su-24M":       "SWORD", 
        "Mirage2000-5F":"ANGEL", "Mi-8UA":       "BEAR", "Mi-24V":       "HIND", "Mi-2UA":       "SWIFT", 
        "Ka-27":        "SHARK", "Mi-14":        "HAZE", "SeaKing":      "KING", "AirbaseB":     "ALPHA BASE", 
        "CarrierB":     "NIMITZ", "Bayraktar_TB2": "DRONE", "E-3G_Sentry": "SENTRY", "A-50U_Mainstay": "MAINSTAY"
    }
    return f"{prefixes.get(platform_key, 'UNIT')} {index}"

def _handle_right_click(screen_pos, cam: CameraState,
                         sim: SimulationEngine,
                         selected: Unit, db: Database,
                         ui: GameUI,
                         show_all_enemies: bool) -> None:
    enemy = None
    for unit in reversed(sim.units):
        if unit.side == selected.side: continue
        if not unit.is_detected and not show_all_enemies: continue
        sx, sy = cam.world_to_screen(unit.lat, unit.lon)
        if unit.is_clicked(screen_pos, sx, sy):
            enemy = unit
            break

    if enemy:
        wkey = selected.selected_weapon or selected.best_weapon_for(db, enemy)
        if wkey:
            salvo_mode = ui.salvo_mode
            if salvo_mode == "1": count, doc = 1, "salvo"
            elif salvo_mode == "2": count, doc = 2, "salvo"
            elif salvo_mode == "4": count, doc = 4, "salvo"
            elif salvo_mode == "SLS": count, doc = 999, "SLS"
            else: count, doc = 1, "salvo"
            
            sim.queue_salvo(selected, enemy, wkey, count, doc)
            ui.rebuild_weapon_buttons(selected, sim)
        else:
            sim.log(f"{selected.callsign}: no weapons available.")
    else:
        lat, lon = cam.screen_to_world(screen_pos[0], screen_pos[1])
        
        if selected.platform.unit_type in ("tank", "ifv", "apc", "recon", "tank_destroyer", "sam", "artillery"):
            if _is_water(lat, lon):
                sim.log(f"{selected.callsign}: COMMAND REJECTED. Cannot route ground units over water.")
                return
                
        selected.add_waypoint(lat, lon, -1.0)
        sim.log(f"{selected.callsign}: waypoint → ({lat:.2f}°, {lon:.2f}°).")


def main() -> None:
    pygame.init()
    pygame.mixer.init() 
    
    win_w, win_h = WINDOW_WIDTH_DEFAULT, WINDOW_HEIGHT_DEFAULT
    window = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)
    pygame.display.set_caption("Command: Ukraine–Russia  |  Tactical Simulator")
    clock  = pygame.time.Clock()

    bgm_enabled = False
    bgm_loaded = False
    try:
        if os.path.exists(BGM_PATH):
            pygame.mixer.music.load(BGM_PATH)
            pygame.mixer.music.set_volume(0.4) 
            pygame.mixer.music.play(loops=-1) 
            bgm_enabled = True
            bgm_loaded = True
        else:
            print(f"Notice: Background music not found at {BGM_PATH}. Skipping playback.")
    except pygame.error as e:
        print(f"Warning: Could not play background music: {e}")

    db = Database(
        weapons_path = str(_HERE / "weapons.json"),
        units_path   = str(_HERE / "units.json"),
    )
    _write_default_scenario(SCENARIO_PATH, db)
    
    all_units, meta, events = load_scenario(SCENARIO_PATH, db)
    red_units  = [u for u in all_units if u.side == "Red"]
    blue_units = [u for u in all_units if u.side == "Blue"]

    placement_counts: dict[str, int] = {}

    sim      = SimulationEngine(list(red_units) + list(blue_units), db, events)
    sim.set_compression(0)   

    renderer = Renderer(window)
    ui       = GameUI(window, win_w, win_h, db)
    cam      = CameraState(meta["start_lat"], meta["start_lon"],
                           meta["start_zoom"], win_w, win_h)

    app_mode:          str             = "setup"   
    placing_type:      str | None      = None      
    placing_remaining: int             = 0         
    selected_unit:     Unit | None     = None
    assigning_mission: str | None      = None
    assigning_package_state: dict | None = None
    assigning_package_tot: float       = 0.0
    
    assigning_package_waypoints: list[tuple[float, float, float]] = []
    
    is_dragging:       bool            = False
    drag_start_pos:    tuple[int, int] | None = None
    
    show_all_enemies:  bool            = False
    game_over_triggered: bool          = False
    
    show_air_labels:   bool            = True
    show_ground_labels:bool            = True
    show_radar_rings:  bool            = True

    running = True
    while running:
        real_delta  = clock.tick(FPS) / 1000.0
        cam_px, cam_py = cam.pixel_xy
        cur_map_h   = cam.map_h

        for event in pygame.event.get():
            if event.type == pygame.QUIT: 
                running = False

            elif event.type == pygame.VIDEORESIZE:
                win_w, win_h = event.w, event.h
                window = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)
                renderer.update_surface(window)
                ui.resize(window, win_w, win_h)
                cam.win_w, cam.win_h = win_w, win_h
                sim.time_compression = TIME_SPEEDS[ui.active_speed_idx]
                sim.paused = (sim.time_compression == 0)
                
            elif event.type == pygame.WINDOWRESIZED:
                win_w, win_h = event.x, event.y
                window = pygame.display.get_surface()
                renderer.update_surface(window)
                ui.resize(window, win_w, win_h)
                cam.win_w, cam.win_h = win_w, win_h
                sim.time_compression = TIME_SPEEDS[ui.active_speed_idx]
                sim.paused = (sim.time_compression == 0)

            action = ui.process_events(event)
            
            if action:
                if action.get("type") == "open_pkg_window" and selected_unit:
                    ui._create_strike_package_window(selected_unit, sim)
                elif action.get("type") == "prep_launch_package":
                    assigning_package_state = action["state"]
                    assigning_package_tot = action.get("tot_mins", 0.0)
                    assigning_package_waypoints.clear()
                    sim.log(f"Right-click to add waypoints. Left-click to set final target & launch. ESC to cancel. (ToT Offset: {assigning_package_tot}m)")
                elif action.get("type") == "add_package_waypoint":
                    assigning_package_waypoints.append((action["lat"], action["lon"], action["alt"]))
                    sim.log(f"Waypoint added at {int(action['alt'])} ft.")
                elif action.get("type") == "speed_change":
                    sim.set_compression(TIME_SPEEDS[action["speed_idx"]])
                elif action.get("type") == "toggle_air_labels":
                    show_air_labels = not show_air_labels
                elif action.get("type") == "toggle_ground_labels":
                    show_ground_labels = not show_ground_labels
                elif action.get("type") == "toggle_radar_rings":
                    show_radar_rings = not show_radar_rings
                elif action.get("type") == "toggle_bgm":
                    if bgm_loaded:
                        bgm_enabled = not bgm_enabled
                        if bgm_enabled: pygame.mixer.music.unpause()
                        else: pygame.mixer.music.pause()
                elif action.get("type") == "set_volume":
                    if bgm_loaded: pygame.mixer.music.set_volume(action["value"])
                elif action.get("type") == "place_unit":
                    placing_type      = action["platform_key"]
                    placing_remaining = action.get("quantity", 1)
                elif action.get("type") == "place_unit_no_selection": 
                    pass   
                elif action.get("type") == "auto_deploy_blue":
                    _auto_deploy_blue(db, sim, placement_counts)
                elif action.get("type") == "save_deployment":
                    root = tk.Tk()
                    root.withdraw()
                    path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")], title="Save Blue Deployment")
                    if path: save_deployment(path, [u for u in sim.units if u.side == "Blue"])
                    root.destroy()
                elif action.get("type") == "load_deployment":
                    root = tk.Tk()
                    root.withdraw()
                    path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")], title="Load Blue Deployment")
                    if path and os.path.exists(path):
                        loaded_blues = load_deployment(path, db)
                        sim.units.extend(loaded_blues)
                        sim.log(f"Deployed {len(loaded_blues)} reinforcement units.")
                    root.destroy()
                elif action.get("type") == "remove_selected":
                    if selected_unit and selected_unit.side == "Blue":
                        selected_unit.take_damage(999.0)
                        sim.units = [u for u in sim.units if u.alive]
                        selected_unit = None
                elif action.get("type") == "clear_blue":
                    sim.units = [u for u in sim.units if u.side == "Red"]
                    selected_unit, placing_type, placing_remaining = None, None, 0
                elif action.get("type") == "start_sim":
                    app_mode = "combat"
                    ui.set_mode("combat")
                    sim.set_compression(TIME_SPEEDS[ui.active_speed_idx])
                    sim.log(f"Simulation {'resumed' if sim.game_time > 0 else 'started'} — {len(sim.blue_units())} Blue, {len(sim.red_units())} Red")
                elif action.get("type") == "enter_setup":
                    app_mode = "setup"
                    ui.set_mode("setup")
                    sim.set_compression(0)
                    sim.log("Simulation paused for reinforcements.")
                elif action.get("type") == "end_game":
                    sim.game_over_reason = "Manual end by player."
                    sim.log("*** MISSION TERMINATED BY COMMANDER ***")
                    sim.set_compression(0)
                    game_over_triggered = True
                    aar_data = sim.generate_aar()
                    ui.create_aar_window(aar_data)
                elif action.get("type") == "restart_scenario":
                    fresh_units, meta, events = load_scenario(SCENARIO_PATH, db)
                    sim.units = fresh_units
                    sim.events = events
                    sim.missiles.clear()
                    sim.explosions.clear()
                    sim.salvos.clear()
                    sim.game_time = 0.0
                    sim.event_log.clear()
                    sim.game_over_reason = None
                    sim.score_blue = 0
                    sim.score_red = 0
                    sim.aar_log.clear()
                    sim.blue_contacts.clear()
                    sim.set_compression(0)
                    app_mode, selected_unit, placing_type, placing_remaining, assigning_mission, game_over_triggered = "setup", None, None, 0, None, False
                    assigning_package_tot = 0.0
                    assigning_package_waypoints.clear()
                    ui.set_mode("setup")
                    sim.log("Scenario restarted.")
                elif action.get("type") == "toggle_fow": show_all_enemies = not show_all_enemies
                elif action.get("type") == "toggle_auto_engage" and selected_unit:
                    selected_unit.auto_engage = not getattr(selected_unit, 'auto_engage', False)
                elif action.get("type") == "toggle_roe" and selected_unit:
                    roes = ["FREE", "TIGHT", "HOLD"]
                    selected_unit.roe = roes[(roes.index(selected_unit.roe) + 1) % 3]
                elif action.get("type") == "cycle_emcon" and selected_unit:
                    states = ["SILENT", "SEARCH_ONLY", "ACTIVE", "BLINDING"]
                    cur = getattr(selected_unit, 'emcon_state', "ACTIVE")
                    selected_unit.set_emcon(states[(states.index(cur) + 1) % len(states)])
                elif action.get("type") == "cycle_wra_rng" and selected_unit:
                    pcts = [0.90, 0.75, 0.50, 0.25]
                    selected_unit.wra_range_pct = pcts[(pcts.index(selected_unit.wra_range_pct) + 1) % len(pcts)]
                elif action.get("type") == "cycle_wra_qty" and selected_unit:
                    qtys = [1, 2, 4]
                    selected_unit.wra_qty = qtys[(qtys.index(selected_unit.wra_qty) + 1) % len(qtys)]
                elif action.get("type") == "cycle_formation" and selected_unit:
                    forms = ["WEDGE", "LINE", "TRAIL"]
                    selected_unit.formation = forms[(forms.index(selected_unit.formation) + 1) % len(forms)]
                elif action.get("type") == "cycle_doctrine" and selected_unit:
                    docs = ["STANDARD", "AMBUSH_COVER"]
                    selected_unit.flight_doctrine = docs[(docs.index(selected_unit.flight_doctrine) + 1) % len(docs)]
                elif action.get("type") == "select_parked":
                    p_unit = sim.get_unit_by_uid(action["uid"])
                    if p_unit:
                        if selected_unit: selected_unit.selected = False
                        selected_unit = p_unit
                        selected_unit.selected = True
                        if not selected_unit.mission:
                            base = sim.get_unit_by_uid(selected_unit.home_uid)
                            lat, lon = (base.lat, base.lon) if base else (selected_unit.lat, selected_unit.lon)
                            selected_unit.mission = Mission(f"{selected_unit.callsign} Alpha", "CAP", lat, lon, 30.0, selected_unit.platform.cruise_alt_ft, 0.25)
                        ui.rebuild_weapon_buttons(selected_unit, sim)
                elif action.get("type") == "cycle_mission" and selected_unit:
                    msns = ["CAP", "STRIKE", "SEAD"]
                    cur = selected_unit.mission.mission_type if selected_unit.mission else "CAP"
                    if selected_unit.mission: selected_unit.mission.mission_type = msns[(msns.index(cur) + 1) % len(msns)]
                elif action.get("type") == "cycle_loadout" and selected_unit:
                    selected_unit.cycle_loadout(db)
                    ui.rebuild_weapon_buttons(selected_unit, sim)
                elif action.get("type") == "launch_unit" and selected_unit:
                    if selected_unit.duty_state == "READY":
                        selected_unit.duty_state = "ACTIVE"
                        selected_unit.altitude_ft = selected_unit.platform.cruise_alt_ft
                        selected_unit.target_altitude_ft = selected_unit.platform.cruise_alt_ft
                        selected_unit.clear_waypoints()
                        sim.log(f"{selected_unit.callsign}: Launched for {selected_unit.mission.mission_type if selected_unit.mission else 'Patrol'}.")
                        ui.rebuild_weapon_buttons(selected_unit, sim)
                elif action.get("type") == "assign_cap" and selected_unit: assigning_mission = "CAP"
                elif action.get("type") == "clear_mission" and selected_unit:
                    selected_unit.mission = None
                    selected_unit.clear_waypoints()
                    selected_unit.leader_uid = "" 
                elif action.get("type") == "weapon_select" and selected_unit:
                    wkey = action["weapon_key"]
                    selected_unit.selected_weapon = None if selected_unit.selected_weapon == wkey else wkey
                    ui.rebuild_weapon_buttons(selected_unit, sim)
                elif action.get("type") == "change_alt" and selected_unit:
                    if selected_unit.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
                        clamped_alt = max(0.0, min(float(selected_unit.platform.ceiling_ft), selected_unit.target_altitude_ft + action["delta"]))
                        selected_unit.target_altitude_ft = clamped_alt
                
                continue

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    placing_type, placing_remaining, assigning_mission, assigning_package_state = None, 0, None, None
                    assigning_package_tot = 0.0
                    assigning_package_waypoints.clear()
                    
                    if getattr(ui, "_wp_alt_window", None):
                        ui._wp_alt_window.kill()
                        ui._wp_alt_window = None
                        ui._pending_wp_coords = None
                        
                    if selected_unit:
                        selected_unit.selected = False
                        selected_unit = None
                        ui.rebuild_weapon_buttons(None, sim)
                elif event.key == pygame.K_DELETE and selected_unit:
                    selected_unit.clear_waypoints()
                    selected_unit.leader_uid = "" 
                elif event.key == pygame.K_s and (event.mod & pygame.KMOD_CTRL) and app_mode == "combat":
                    save_scenario(SAVE_PATH, sim.units, meta, sim.events, sim.game_time)
                elif event.key == pygame.K_SPACE and app_mode == "combat":
                    sim.set_compression(0 if not sim.paused else 1)
                elif (event.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5) and app_mode == "combat"):
                    idx = event.key - pygame.K_1
                    if 0 <= idx < len(TIME_SPEEDS): sim.set_compression(TIME_SPEEDS[idx])
                continue   

            if event.type == pygame.MOUSEBUTTONDOWN:
                if ui.is_mouse_over_ui(event.pos): continue  

                if event.button == 1:
                    if placing_type and app_mode == "setup":
                        lat, lon = cam.screen_to_world(*event.pos)
                        plat = db.platforms.get(placing_type)
                        if not plat: continue
                        
                        if plat.unit_type in ("tank", "ifv", "apc", "recon", "tank_destroyer", "sam", "artillery"):
                            if _is_water(lat, lon): continue

                        home_uid = ""
                        if plat.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
                            bases = [u for u in sim.units if u.side == "Blue" and u.platform.unit_type == "airbase"]
                            if not bases: continue
                            closest = min(bases, key=lambda b: haversine(b.lat, b.lon, lat, lon))
                            if haversine(closest.lat, closest.lon, lat, lon) > 100.0: continue
                            lat, lon, home_uid = closest.lat, closest.lon, closest.uid

                        n = placement_counts.get(placing_type, 0) + 1
                        placement_counts[placing_type] = n
                        unit = _make_blue_unit(placing_type, lat, lon, db, _callsign_for(placing_type, n))
                        
                        if unit: 
                            unit.home_uid = home_uid
                            if home_uid:
                                unit.duty_state = "READY"
                                unit.altitude_ft = 0
                            sim.units.append(unit)
                            
                        placing_remaining -= 1
                        if placing_remaining <= 0: placing_type = None  
                        continue
                        
                    if assigning_mission and selected_unit:
                        lat, lon = cam.screen_to_world(*event.pos)
                        selected_unit.mission = Mission(f"{assigning_mission} Alpha", assigning_mission, lat, lon, 30.0, selected_unit.platform.cruise_alt_ft, 0.25)
                        selected_unit.clear_waypoints()
                        assigning_mission = None
                        if selected_unit.duty_state == "READY":
                            selected_unit.duty_state = "ACTIVE"
                            selected_unit.altitude_ft = selected_unit.platform.cruise_alt_ft
                        continue

                    if assigning_package_state:
                        lat, lon = cam.screen_to_world(*event.pos)
                        pkg_id = f"PKG_{int(sim.game_time)}_{_random.randint(100,999)}"
                        count_launched = 0
                        
                        target_tot = sim.game_time + (assigning_package_tot * 60.0) if assigning_package_tot > 0 else 0.0
                        
                        for uid, state in assigning_package_state.items():
                            if state["included"]:
                                u = sim.get_unit_by_uid(uid)
                                if u and u.duty_state == "READY":
                                    u.set_loadout_role(db, state["loadout"])
                                    
                                    unit_tot = target_tot
                                    if target_tot > 0 and state['role'] == "SEAD":
                                        unit_tot = max(sim.game_time + 10.0, target_tot - 60.0)
                                        
                                    u.mission = Mission(f"{pkg_id} {state['role']}", state["role"], lat, lon, 30.0, u.platform.cruise_alt_ft, 0.25, time_on_target=unit_tot, package_id=pkg_id)
                                    u.duty_state = "ACTIVE"
                                    u.auto_engage = True  # FIX: Auto-enable engagement for launched package units
                                    u.altitude_ft = u.platform.cruise_alt_ft
                                    u.target_altitude_ft = u.platform.cruise_alt_ft
                                    u.clear_waypoints()
                                    
                                    for wlat, wlon, walt in assigning_package_waypoints:
                                        u.add_waypoint(wlat, wlon, walt)
                                        
                                    u.add_waypoint(lat, lon, -1.0)
                                    count_launched += 1
                                    
                        ui.rebuild_weapon_buttons(selected_unit, sim)
                        sim.log(f"Strike Package '{pkg_id}' launched ({count_launched} aircraft).")
                        assigning_package_state = None
                        assigning_package_tot = 0.0
                        assigning_package_waypoints.clear()
                        continue

                    hit = _pick_unit(event.pos, cam, sim.units, show_all_enemies=show_all_enemies, app_mode=app_mode)
                    if hit:
                        if selected_unit: selected_unit.selected = False
                        selected_unit = hit
                        selected_unit.selected = True
                        assigning_mission = None
                        assigning_package_state = None
                        assigning_package_tot = 0.0
                        ui.rebuild_weapon_buttons(selected_unit, sim)
                    else:
                        is_dragging = True
                        drag_start_pos = event.pos

                elif event.button == 3:
                    if app_mode == "setup":
                        hit = _pick_unit(event.pos, cam, sim.units, blue_only=True, app_mode=app_mode)
                        if hit:
                            hit.take_damage(999.0)
                            sim.units = [u for u in sim.units if u.alive]
                            if selected_unit == hit:
                                selected_unit = None
                                ui.rebuild_weapon_buttons(None, sim)
                    elif selected_unit and app_mode == "combat":
                        if assigning_mission: 
                            assigning_mission = None
                        elif assigning_package_state: 
                            lat, lon = cam.screen_to_world(*event.pos)
                            ui.create_waypoint_alt_window(lat, lon)
                        elif selected_unit.duty_state == "READY":
                            lat, lon = cam.screen_to_world(*event.pos)
                            if not selected_unit.mission: 
                                selected_unit.mission = Mission("Configured Mission", "CAP", lat, lon, 30.0, selected_unit.platform.cruise_alt_ft, 0.25)
                            else: 
                                selected_unit.mission.target_lat, selected_unit.mission.target_lon = lat, lon
                        else:
                            if selected_unit.duty_state == "READY":
                                selected_unit.duty_state = "ACTIVE"
                                selected_unit.altitude_ft = selected_unit.platform.cruise_alt_ft
                            _handle_right_click(event.pos, cam, sim, selected_unit, db, ui, show_all_enemies)

                elif event.button in (4, 5):
                    mx, my = event.pos
                    cam.zoom_by(1 if event.button == 4 else -1, mx, my)

            elif event.type == pygame.MOUSEBUTTONUP:
                if ui.is_mouse_over_ui(event.pos):
                    is_dragging = False
                    drag_start_pos = None
                    continue
                    
                if event.button == 1: 
                    if is_dragging and drag_start_pos:
                        dist = math.hypot(event.pos[0] - drag_start_pos[0], event.pos[1] - drag_start_pos[1])
                        if dist < 5.0:
                            if selected_unit:
                                selected_unit.selected = False
                                selected_unit = None
                                ui.rebuild_weapon_buttons(None, sim)
                    is_dragging = False
                    drag_start_pos = None

            elif event.type == pygame.MOUSEMOTION:
                if is_dragging: cam.pan(event.rel[0], event.rel[1])

            elif event.type == pygame.MOUSEWHEEL:
                mx, my = pygame.mouse.get_pos()
                if not ui.is_mouse_over_ui((mx, my)):
                    cam.zoom_by(event.y, mx, my)

        if app_mode == "combat":
            sim.update(real_delta)
            if selected_unit and not selected_unit.alive:
                selected_unit = None
                ui.rebuild_weapon_buttons(None, sim)
                
            if not game_over_triggered:
                result = sim.is_game_over()
                if result:
                    sim.log(f"*** {result.upper()} ***")
                    sim.set_compression(0)
                    game_over_triggered = True
                    aar_data = sim.generate_aar()
                    ui.create_aar_window(aar_data)

        cursor_type = placing_type
        if assigning_mission: cursor_type = assigning_mission
        if assigning_package_state: cursor_type = "STRIKE PACKAGE TARGET"

        renderer.draw_frame(cam_px, cam_py, cam.zoom,
                            sim.units, sim.missiles,
                            cam.win_w, cam.map_h,
                            blue_contacts=sim.blue_contacts,
                            explosions=sim.explosions,
                            placing_type=cursor_type,
                            placing_remaining=placing_remaining,
                            mouse_pos=pygame.mouse.get_pos() if cursor_type else None,
                            show_all_enemies=show_all_enemies,
                            show_air_labels=show_air_labels,
                            show_ground_labels=show_ground_labels,
                            show_radar_rings=show_radar_rings,
                            package_waypoints=assigning_package_waypoints)

        ui.update(real_delta, sim, selected_unit, placing_type, placing_remaining, show_all_enemies,
                  blue_contacts=sim.blue_contacts, show_air_labels=show_air_labels, show_ground_labels=show_ground_labels,
                  show_radar_rings=show_radar_rings, bgm_enabled=bgm_enabled)
        ui.draw()
        pygame.display.flip()

    pygame.quit()
    sys.exit(0)

if __name__ == "__main__":
    main()