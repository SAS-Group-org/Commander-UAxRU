#!/usr/bin/env python3
# main.py — entry point

from __future__ import annotations

import sys
import os
import json
import pathlib
import tkinter as tk
from tkinter import filedialog

import pygame
import pygame_gui

from constants import (
    WINDOW_WIDTH_DEFAULT, WINDOW_HEIGHT_DEFAULT,
    BOTTOM_PANEL_FRACTION, BOTTOM_PANEL_MIN_HEIGHT, FPS, TIME_SPEEDS,
)
from geo import lat_lon_to_pixel, pixel_to_lat_lon, world_to_screen
from renderer import Renderer
from scenario import Database, Unit, Mission, load_scenario, save_scenario, save_deployment, load_deployment
from simulation import SimulationEngine
from ui import GameUI

_HERE         = pathlib.Path(__file__).parent
SCENARIO_PATH = str(_HERE / "data" / "scenarios" / "ukraine_russia.json")
SAVE_PATH     = str(_HERE / "data" / "scenarios" / "ukraine_russia_save.json")

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

    def zoom_by(self, delta):
        self.zoom = max(4, min(12, self.zoom + delta))

    def screen_to_world(self, sx, sy):
        px, py = self.pixel_xy
        return pixel_to_lat_lon(
            sx + px - self.win_w / 2,
            sy + py - self.map_h  / 2,
            self.zoom,
        )

    def world_to_screen(self, lat, lon):
        px, py = self.pixel_xy
        return world_to_screen(lat, lon, px, py, self.zoom,
                               self.win_w, self.map_h)


# ── Scenario generation ───────────────────────────────────────────────────────

import random as _random

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
    {"name": "MILLEROVO AIR",     "lat": 48.93, "lon": 40.39, "spread": 0.05, "mix": {"fighter":2,"attacker":1, "sam":2}},
    {"name": "MOROZOVSK AIR",     "lat": 48.35, "lon": 41.83, "spread": 0.05, "mix": {"fighter":2, "sam":2}},
]

_RED_GROUND_POOLS: dict[str, list[str]] = {
    "tank":           ["T-72R","T-72R","T-72R","T-80R","T-80R","T-64R","T-90R","T-62R","T-55R"],
    "ifv":            ["BMP-2R","BMP-2R","BMP-2R","BMP-1R","BMP-1R","BMP-3R","BMD-2R"],
    "apc":            ["BTR-80R","BTR-80R","BTR-70R","BTR-70R","MTLBR","MTLBR"],
    "recon":          ["BRDM2R","BRDM2R","BRM-1"],
    "tank_destroyer": ["9P148R","9P148R"],
    "fighter":        ["Su-35S","Su-35S","Su-30SM","MiG-29A"],
    "attacker":       ["Su-25UA"], 
    "sam":            ["S-400", "Buk-M2", "Buk-M2", "Tor-M1", "Tor-M1"],
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
        clat, clon   = cluster["lat"], cluster["lon"]
        spread       = cluster["spread"]

        for utype, count in cluster["mix"].items():
            pool = _RED_GROUND_POOLS.get(utype, [])
            if not pool: 
                continue

            actual = max(1, count + rng.randint(-1, 1))   
            for _ in range(actual):
                uid += 1
                platform_key = rng.choice(pool)
                if platform_key not in db.platforms: 
                    continue
                plat = db.platforms[platform_key]

                lat = clat + rng.gauss(0, spread)
                lon = clon + rng.gauss(0, spread * 1.4)

                callsign = _gen_red_callsign(utype)
                loadout  = dict(plat.default_loadout)

                entry: dict = {
                    "id":         f"red_{uid:03d}",
                    "platform":   platform_key,
                    "callsign":   callsign,
                    "side":       "Red",
                    "lat":        round(lat, 5),
                    "lon":        round(lon, 5),
                    "image_path": "assets/red_jet.png",
                    "loadout":    loadout,
                    "waypoints":  [],
                }

                if utype in ("fighter", "attacker"):
                    entry["mission"] = {
                        "name": f"Patrol {callsign}",
                        "type": "CAP" if utype=="fighter" else "STRIKE",
                        "lat": lat + rng.uniform(-0.5, 0.5),
                        "lon": lon + rng.uniform(-0.5, 0.5),
                        "radius": 40.0,
                        "alt": 30000.0 if utype=="fighter" else 15000.0,
                        "rtb_fuel": 0.25
                    }

                units.append(entry)

    red_count    = len(units)
    ground_count = sum(1 for u in units if db.platforms.get(u["platform"], type("P",(object,),{"unit_type":""})()).unit_type in ("tank","ifv","apc","recon","tank_destroyer", "sam"))
    air_count    = red_count - ground_count

    print(f"[GEN] Red force: {red_count} total ({ground_count} ground, {air_count} air)")

    return {
        "name":        "Operation East Wind — Donbas 2024",
        "description": "Russian forces are entrenched across Donbas and Luhansk.",
        "start_lat":   48.5,
        "start_lon":   38.5,
        "start_zoom":  8,
        "units":       units,
    }

def _write_default_scenario(path: str, db: "Database") -> None:
    scenario = _generate_scenario(db)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(scenario, fh, indent=2)
    print(f"[INFO] Scenario written → {path}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pick_unit(screen_pos, cam: CameraState, units: list[Unit],
               blue_only: bool = False,
               show_all_enemies: bool = False) -> Unit | None:
    for unit in units:
        if not unit.alive: 
            continue
        if blue_only and unit.side != "Blue": 
            continue
        if unit.side == "Red" and not unit.is_detected and not show_all_enemies: 
            continue
        sx, sy = cam.world_to_screen(unit.lat, unit.lon)
        if unit.is_clicked(screen_pos, sx, sy):
            return unit
    return None

def _make_blue_unit(platform_key: str, lat: float, lon: float,
                    db: Database, callsign: str) -> Unit | None:
    plat = db.platforms.get(platform_key)
    if plat is None: 
        return None
    return Unit(
        uid        = _next_uid("blue"),
        callsign   = callsign,
        lat        = lat,
        lon        = lon,
        side       = "Blue",
        platform   = plat,
        loadout    = dict(plat.default_loadout),
        image_path = "assets/blue_jet.png",
    )

def _callsign_for(platform_key: str, index: int) -> str:
    prefixes = {
        "MiG-29UA":     "GHOST", "Su-27UA":      "PHANTOM", "F-16AM":       "VIPER", "F-16UA":       "FALCON",
        "Su-25UA":      "WARTHOG", "Su-24M":       "SWORD", "Mirage2000-5F":"ANGEL", "Mi-8UA":       "BEAR",
        "Mi-24V":       "HIND", "Mi-2UA":       "SWIFT", "Ka-27":        "SHARK", "Mi-14":        "HAZE",
        "SeaKing":      "KING",
    }
    prefix = prefixes.get(platform_key, "UNIT")
    return f"{prefix} {index}"


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    pygame.init()
    win_w, win_h = WINDOW_WIDTH_DEFAULT, WINDOW_HEIGHT_DEFAULT
    window = pygame.display.set_mode((win_w, win_h), pygame.RESIZABLE)
    pygame.display.set_caption("Command: Ukraine–Russia  |  Tactical Simulator")
    clock  = pygame.time.Clock()

    db = Database()
    _write_default_scenario(SCENARIO_PATH, db)
    
    all_units, meta = load_scenario(SCENARIO_PATH, db)
    red_units  = [u for u in all_units if u.side == "Red"]
    blue_units = [u for u in all_units if u.side == "Blue"]

    placement_counts: dict[str, int] = {}

    sim      = SimulationEngine(list(red_units) + list(blue_units), db)
    sim.set_compression(0)   

    renderer = Renderer(window)
    ui       = GameUI(window, win_w, win_h, db)
    cam      = CameraState(meta["start_lat"], meta["start_lon"],
                           meta["start_zoom"], win_w, win_h)

    app_mode:       str             = "setup"   
    placing_type:      str | None      = None      
    placing_remaining: int             = 0         
    selected_unit:     Unit | None     = None
    assigning_mission: str | None      = None
    is_dragging:       bool            = False
    show_all_enemies:  bool            = False

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

            if action.get("type") == "speed_change":
                sim.set_compression(TIME_SPEEDS[action["speed_idx"]])
            elif action.get("type") == "place_unit":
                placing_type      = action["platform_key"]
                placing_remaining = action.get("quantity", 1)
            elif action.get("type") == "place_unit_no_selection": 
                pass   
            elif action.get("type") == "save_deployment":
                root = tk.Tk()
                root.withdraw()
                path = filedialog.asksaveasfilename(
                    defaultextension=".json",
                    filetypes=[("JSON files", "*.json")],
                    title="Save Blue Deployment"
                )
                if path:
                    blue_arr = [u for u in sim.units if u.side == "Blue"]
                    save_deployment(path, blue_arr)
                root.destroy()
            elif action.get("type") == "load_deployment":
                root = tk.Tk()
                root.withdraw()
                path = filedialog.askopenfilename(
                    filetypes=[("JSON files", "*.json")],
                    title="Load Blue Deployment"
                )
                if path and os.path.exists(path):
                    loaded_blues = load_deployment(path, db)
                    sim.units = [u for u in sim.units if u.side == "Red"]
                    sim.units.extend(loaded_blues)
                root.destroy()
            elif action.get("type") == "remove_selected":
                if selected_unit and selected_unit.side == "Blue":
                    selected_unit.take_damage(999.0)
                    sim.units = [u for u in sim.units if u.alive]
                    selected_unit = None
            elif action.get("type") == "clear_blue":
                sim.units = [u for u in sim.units if u.side == "Red"]
                selected_unit     = None
                placing_type      = None
                placing_remaining = 0
            elif action.get("type") == "start_sim":
                app_mode = "combat"
                ui.set_mode("combat")
                sim.set_compression(TIME_SPEEDS[ui.active_speed_idx])
                sim.log(f"Simulation started — {len(sim.blue_units())} Blue, {len(sim.red_units())} Red")
            elif action.get("type") == "toggle_fow":
                show_all_enemies = not show_all_enemies
            elif action.get("type") == "toggle_auto_engage" and selected_unit:
                selected_unit.auto_engage = not getattr(selected_unit, 'auto_engage', False)
                if app_mode == "combat":
                    sim.log(f"{selected_unit.callsign}: Auto-Engage set to {'ON' if selected_unit.auto_engage else 'OFF'}.")
            elif action.get("type") == "toggle_ecm" and selected_unit:
                selected_unit.is_jamming = not getattr(selected_unit, 'is_jamming', False)
                if app_mode == "combat":
                    state_str = "ACTIVE" if selected_unit.is_jamming else "PASSIVE"
                    sim.log(f"{selected_unit.callsign}: ECM set to {state_str}.")
            elif action.get("type") == "toggle_roe" and selected_unit:
                roes = ["FREE", "TIGHT", "HOLD"]
                idx = roes.index(selected_unit.roe)
                selected_unit.roe = roes[(idx + 1) % 3]
            elif action.get("type") == "assign_cap" and selected_unit:
                assigning_mission = "CAP"
            elif action.get("type") == "clear_mission" and selected_unit:
                selected_unit.mission = None
                selected_unit.clear_waypoints()
                if app_mode == "combat":
                    sim.log(f"{selected_unit.callsign}: Mission cleared.")
            elif action.get("type") == "weapon_select" and selected_unit:
                wkey = action["weapon_key"]
                if selected_unit.selected_weapon == wkey: 
                    selected_unit.selected_weapon = None
                else: 
                    selected_unit.selected_weapon = wkey
                ui.rebuild_weapon_buttons(selected_unit)
            elif action.get("type") == "change_alt" and selected_unit:
                if selected_unit.platform.unit_type in ("fighter", "attacker", "helicopter"):
                    new_alt = selected_unit.target_altitude_ft + action["delta"]
                    clamped_alt = max(0.0, min(float(selected_unit.platform.ceiling_ft), new_alt))
                    selected_unit.target_altitude_ft = clamped_alt
                    if app_mode == "combat":
                        sim.log(f"{selected_unit.callsign}: altitude target set to {int(clamped_alt):,} ft.")

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if placing_type:
                        placing_type      = None
                        placing_remaining = 0
                    elif assigning_mission:
                        assigning_mission = None
                    elif selected_unit:
                        selected_unit.selected = False
                        selected_unit = None
                elif event.key == pygame.K_DELETE and selected_unit:
                    selected_unit.clear_waypoints()
                    if app_mode == "combat": 
                        sim.log(f"{selected_unit.callsign}: route cleared.")
                elif event.key == pygame.K_s and (event.mod & pygame.KMOD_CTRL):
                    if app_mode == "combat":
                        save_scenario(SAVE_PATH, sim.units, meta, sim.game_time)
                        sim.log(f"Scenario saved → {SAVE_PATH}")
                elif event.key == pygame.K_SPACE and app_mode == "combat":
                    sim.set_compression(0 if not sim.paused else 1)
                elif (event.key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5) and app_mode == "combat"):
                    idx = event.key - pygame.K_1
                    if 0 <= idx < len(TIME_SPEEDS): 
                        sim.set_compression(TIME_SPEEDS[idx])
                continue   

            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.pos[1] >= cur_map_h: 
                    continue  

                if event.button == 1:
                    if placing_type and app_mode == "setup":
                        lat, lon = cam.screen_to_world(*event.pos)
                        n = placement_counts.get(placing_type, 0) + 1
                        placement_counts[placing_type] = n
                        callsign = _callsign_for(placing_type, n)
                        unit = _make_blue_unit(placing_type, lat, lon, db, callsign)
                        if unit: 
                            sim.units.append(unit)
                        placing_remaining -= 1
                        if placing_remaining <= 0:
                            placing_type      = None  
                            placing_remaining = 0
                        continue
                        
                    if assigning_mission and selected_unit:
                        lat, lon = cam.screen_to_world(*event.pos)
                        selected_unit.mission = Mission(
                            name=f"{assigning_mission} Alpha", 
                            mission_type=assigning_mission,
                            target_lat=lat, 
                            target_lon=lon,
                            radius_km=30.0,
                            altitude_ft=selected_unit.platform.cruise_alt_ft,
                            rtb_fuel_pct=0.25
                        )
                        selected_unit.clear_waypoints()
                        assigning_mission = None
                        if app_mode == "combat": 
                            sim.log(f"{selected_unit.callsign}: {selected_unit.mission.mission_type} mission established.")
                        continue

                    hit = _pick_unit(event.pos, cam, sim.units, show_all_enemies=show_all_enemies)
                    if hit:
                        if selected_unit: 
                            selected_unit.selected = False
                        selected_unit = hit
                        selected_unit.selected = True
                        assigning_mission = None
                        if app_mode == "combat": 
                            sim.log(f"Selected {hit.callsign} ({hit.platform.display_name})")
                        ui.rebuild_weapon_buttons(selected_unit)
                    else:
                        if selected_unit:
                            selected_unit.selected = False
                            selected_unit = None
                            ui.rebuild_weapon_buttons(None)
                        is_dragging = True

                elif event.button == 3:
                    if app_mode == "setup":
                        hit = _pick_unit(event.pos, cam, sim.units, blue_only=True)
                        if hit:
                            hit.take_damage(999.0)
                            sim.units = [u for u in sim.units if u.alive]
                            if selected_unit == hit:
                                selected_unit = None
                                ui.rebuild_weapon_buttons(None)
                    elif selected_unit and app_mode == "combat":
                        if assigning_mission:
                            assigning_mission = None
                        else:
                            _handle_right_click(event.pos, cam, sim, selected_unit, db, ui, show_all_enemies)

                elif event.button in (4, 5):
                    cam.zoom_by(1 if event.button == 4 else -1)

            elif event.type == pygame.MOUSEBUTTONUP:
                if event.button == 1: 
                    is_dragging = False
                    
            elif event.type == pygame.MOUSEMOTION:
                if is_dragging: 
                    cam.pan(event.rel[0], event.rel[1])
                    
            elif event.type == pygame.MOUSEWHEEL:
                cam.zoom_by(event.y)

        if app_mode == "combat":
            sim.update(real_delta)

            if selected_unit and not selected_unit.alive:
                selected_unit = None
                ui.rebuild_weapon_buttons(None)

            result = sim.is_game_over()
            if result:
                sim.log(f"*** {result.upper()} ***")
                sim.set_compression(0)

        renderer.draw_frame(cam_px, cam_py, cam.zoom,
                            sim.units, sim.missiles,
                            cam.win_w, cam.map_h,
                            blue_contacts=sim.blue_contacts,
                            placing_type=assigning_mission if assigning_mission else placing_type,
                            placing_remaining=placing_remaining,
                            mouse_pos=pygame.mouse.get_pos() if (placing_type or assigning_mission) else None,
                            show_all_enemies=show_all_enemies)

        ui.update(real_delta, sim, selected_unit, placing_type, placing_remaining, show_all_enemies,
                  blue_contacts=sim.blue_contacts)
        ui.draw()
        pygame.display.flip()

    pygame.quit()
    sys.exit(0)


def _handle_right_click(screen_pos, cam: CameraState,
                         sim: SimulationEngine,
                         selected: Unit, db: Database,
                         ui: GameUI,
                         show_all_enemies: bool) -> None:
    enemy = None
    for unit in sim.units:
        if unit.side == selected.side: 
            continue
        if not unit.is_detected and not show_all_enemies: 
            continue
        sx, sy = cam.world_to_screen(unit.lat, unit.lon)
        if unit.is_clicked(screen_pos, sx, sy):
            enemy = unit
            break

    if enemy:
        wkey = selected.selected_weapon or selected.best_bvr_weapon(db)
        if wkey:
            salvo_mode = ui.salvo_mode
            if salvo_mode == "1": 
                count, doc = 1, "salvo"
            elif salvo_mode == "2": 
                count, doc = 2, "salvo"
            elif salvo_mode == "4": 
                count, doc = 4, "salvo"
            elif salvo_mode == "SLS": 
                count, doc = 999, "SLS"
            else: 
                count, doc = 1, "salvo"
            
            sim.queue_salvo(selected, enemy, wkey, count, doc)
            ui.rebuild_weapon_buttons(selected)
        else:
            sim.log(f"{selected.callsign}: no weapons available.")
    else:
        lat, lon = cam.screen_to_world(screen_pos[0], screen_pos[1])
        selected.add_waypoint(lat, lon)
        sim.log(f"{selected.callsign}: waypoint → ({lat:.2f}°, {lon:.2f}°).")

if __name__ == "__main__":
    main()
    