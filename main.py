#!/usr/bin/env python3
# main.py — entry point
#
# App modes:
#   "setup"  — player places Blue units on the map from a roster panel
#   "combat" — real-time simulation with weapon selection
#
# Controls (setup):
#   Select unit type → Place on Map → left-click map to position
#   Right-click placed Blue unit → remove it
#   ESC → cancel placement
#   ▶ START SIMULATION → enter combat mode
#
# Controls (combat):
#   Left-click unit     → select
#   Right-click enemy   → fire selected/best weapon
#   Right-click map     → add waypoint
#   Armaments panel     → click weapon to make it preferred
#   DEL                 → clear waypoints of selected unit
#   SPACE               → pause/resume
#   1-5                 → time compression
#   Ctrl+S              → save scenario

from __future__ import annotations

import sys
import os
import json
import pathlib

import pygame
import pygame_gui

from constants import (
    WINDOW_WIDTH_DEFAULT, WINDOW_HEIGHT_DEFAULT,
    BOTTOM_PANEL_HEIGHT, FPS, TIME_SPEEDS,
)
from geo import lat_lon_to_pixel, pixel_to_lat_lon, world_to_screen
from renderer import Renderer
from scenario import Database, Unit, load_scenario, save_scenario
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


# ── Camera ────────────────────────────────────────────────────────────────────

def map_area_height(win_h: int) -> int:
    return max(200, win_h - BOTTOM_PANEL_HEIGHT)


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


# ── Default scenario ─────────────────────────────────────────────────────────

_DEFAULT_SCENARIO = {
    "name":        "Ukraine Air Superiority — Spring 2024",
    "description": "Deploy Ukrainian forces, then engage advancing Russian fighters.",
    "start_lat":   49.0,
    "start_lon":   32.0,
    "start_zoom":  7,
    # No Blue units pre-placed — player deploys in setup mode
    "units": [
        {"id": "red_1",  "platform": "Su-35S",  "callsign": "BANDIT 1", "side": "Red",
         "lat": 49.50, "lon": 36.20, "image_path": "assets/red_jet.png",
         "loadout": {"R-77": 4, "R-27T": 2, "R-73": 6, "GSh-30-1": 1},
         "waypoints": [[49.80, 32.50], [50.00, 30.00]]},
        {"id": "red_2",  "platform": "MiG-29A", "callsign": "BANDIT 2", "side": "Red",
         "lat": 48.80, "lon": 36.80, "image_path": "assets/red_jet.png",
         "loadout": {"R-27R": 2, "R-27T": 2, "R-73": 4, "GSh-30-1": 1},
         "waypoints": [[49.00, 33.00], [49.20, 30.50]]},
        {"id": "red_3",  "platform": "Su-30SM", "callsign": "BANDIT 3", "side": "Red",
         "lat": 50.20, "lon": 37.50, "image_path": "assets/red_jet.png",
         "loadout": {"R-77": 4, "R-73": 4, "GSh-30-1": 1},
         "waypoints": [[50.00, 34.00], [49.80, 31.50]]},
    ],
}


def _write_default_scenario(path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_DEFAULT_SCENARIO, fh, indent=2)
    print(f"[INFO] Default scenario written to {path}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pick_unit(screen_pos, cam: CameraState, units: list[Unit],
               blue_only: bool = False) -> Unit | None:
    for unit in units:
        if not unit.alive:
            continue
        if blue_only and unit.side != "Blue":
            continue
        if unit.side == "Red" and not unit.is_detected:
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
        "MiG-29UA":     "GHOST",
        "Su-27UA":      "PHANTOM",
        "F-16AM":       "VIPER",
        "F-16UA":       "FALCON",
        "Su-25UA":      "WARTHOG",
        "Su-24M":       "SWORD",
        "Mirage2000-5F":"ANGEL",
        "Mi-8UA":       "BEAR",
        "Mi-24V":       "HIND",
        "Mi-2UA":       "SWIFT",
        "Ka-27":        "SHARK",
        "Mi-14":        "HAZE",
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

    # ── Data ──────────────────────────────────────────────────────────────────
    db = Database()
    if not os.path.exists(SCENARIO_PATH):
        os.makedirs(os.path.dirname(SCENARIO_PATH), exist_ok=True)
        _write_default_scenario(SCENARIO_PATH)
    all_units, meta = load_scenario(SCENARIO_PATH, db)
    red_units  = [u for u in all_units if u.side == "Red"]
    blue_units = [u for u in all_units if u.side == "Blue"]

    # Track per-platform placement count for auto-callsigns
    placement_counts: dict[str, int] = {}

    # ── Subsystems ───────────────────────────────────────────────────────────
    # Start with a minimal sim (Red units only) so renderer works in setup mode
    sim      = SimulationEngine(list(red_units) + list(blue_units), db)
    sim.set_compression(0)   # paused until simulation starts

    renderer = Renderer(window)
    ui       = GameUI(window, win_w, win_h, db)
    cam      = CameraState(meta["start_lat"], meta["start_lon"],
                           meta["start_zoom"], win_w, win_h)

    # ── App state ────────────────────────────────────────────────────────────
    app_mode:       str             = "setup"   # "setup" | "combat"
    placing_type:   str | None      = None      # platform key being placed
    selected_unit:  Unit | None     = None
    is_dragging:    bool            = False

    # ── Game loop ─────────────────────────────────────────────────────────────
    running = True
    while running:
        real_delta  = clock.tick(FPS) / 1000.0
        cam_px, cam_py = cam.pixel_xy
        cur_map_h   = cam.map_h

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            # ── Resize ───────────────────────────────────────────────────────
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

            # ── UI events ────────────────────────────────────────────────────
            action = ui.process_events(event)

            if action.get("type") == "speed_change":
                sim.set_compression(TIME_SPEEDS[action["speed_idx"]])

            elif action.get("type") == "place_unit":
                placing_type = action["platform_key"]

            elif action.get("type") == "place_unit_no_selection":
                pass   # nothing selected in roster — ignore

            elif action.get("type") == "remove_selected":
                if selected_unit and selected_unit.side == "Blue":
                    selected_unit.alive = False
                    sim.units = [u for u in sim.units if u.alive]
                    selected_unit = None

            elif action.get("type") == "clear_blue":
                sim.units = [u for u in sim.units if u.side == "Red"]
                selected_unit = None
                placing_type  = None

            elif action.get("type") == "start_sim":
                app_mode = "combat"
                ui.set_mode("combat")
                sim.set_compression(TIME_SPEEDS[ui.active_speed_idx])
                sim.log(f"Simulation started — {len(sim.blue_units())} Blue, "
                        f"{len(sim.red_units())} Red")

            elif action.get("type") == "weapon_select" and selected_unit:
                wkey = action["weapon_key"]
                # Toggle: clicking the already-selected weapon deselects it
                if selected_unit.selected_weapon == wkey:
                    selected_unit.selected_weapon = None
                else:
                    selected_unit.selected_weapon = wkey
                # Rebuild buttons to reflect new selection
                ui.rebuild_weapon_buttons(selected_unit)

            # ── Keyboard ─────────────────────────────────────────────────────
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if placing_type:
                        placing_type = None
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
                elif (event.key in (pygame.K_1, pygame.K_2, pygame.K_3,
                                    pygame.K_4, pygame.K_5)
                      and app_mode == "combat"):
                    idx = event.key - pygame.K_1
                    if 0 <= idx < len(TIME_SPEEDS):
                        sim.set_compression(TIME_SPEEDS[idx])
                continue   # keyboard events fully handled

            # ── Mouse (map area only) ─────────────────────────────────────────
            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.pos[1] >= cur_map_h:
                    continue  # inside panel

                if event.button == 1:
                    # ── Placement mode ───────────────────────────────────────
                    if placing_type and app_mode == "setup":
                        lat, lon = cam.screen_to_world(*event.pos)
                        n = placement_counts.get(placing_type, 0) + 1
                        placement_counts[placing_type] = n
                        callsign = _callsign_for(placing_type, n)
                        unit = _make_blue_unit(placing_type, lat, lon,
                                               db, callsign)
                        if unit:
                            sim.units.append(unit)
                        placing_type = None   # one placement per click
                        continue

                    # ── Unit selection ───────────────────────────────────────
                    hit = _pick_unit(event.pos, cam, sim.units)
                    if hit:
                        if selected_unit:
                            selected_unit.selected = False
                        selected_unit = hit
                        selected_unit.selected = True
                        if app_mode == "combat":
                            sim.log(f"Selected {hit.callsign} "
                                    f"({hit.platform.display_name})")
                        ui.rebuild_weapon_buttons(selected_unit)
                    else:
                        if selected_unit:
                            selected_unit.selected = False
                            selected_unit = None
                            ui.rebuild_weapon_buttons(None)
                        is_dragging = True

                elif event.button == 3:
                    if app_mode == "setup":
                        # Right-click in setup removes a Blue unit
                        hit = _pick_unit(event.pos, cam, sim.units,
                                         blue_only=True)
                        if hit:
                            hit.alive = False
                            sim.units = [u for u in sim.units if u.alive]
                            if selected_unit == hit:
                                selected_unit = None
                                ui.rebuild_weapon_buttons(None)
                    elif selected_unit and app_mode == "combat":
                        _handle_right_click(event.pos, cam, sim,
                                            selected_unit, db, ui)

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

        # ── Sim tick ─────────────────────────────────────────────────────────
        if app_mode == "combat":
            sim.update(real_delta)

            if selected_unit and not selected_unit.alive:
                selected_unit = None
                ui.rebuild_weapon_buttons(None)

            result = sim.is_game_over()
            if result:
                sim.log(f"*** {result.upper()} ***")
                sim.set_compression(0)

        # ── Render ───────────────────────────────────────────────────────────
        renderer.draw_frame(cam_px, cam_py, cam.zoom,
                            sim.units, sim.missiles,
                            cam.win_w, cam.map_h,
                            placing_type=placing_type,
                            mouse_pos=pygame.mouse.get_pos()
                                       if placing_type else None)

        ui.update(real_delta, sim, selected_unit, placing_type)
        ui.draw()
        pygame.display.flip()

    pygame.quit()
    sys.exit(0)


# ── Combat input helpers ──────────────────────────────────────────────────────

def _handle_right_click(screen_pos, cam: CameraState,
                         sim: SimulationEngine,
                         selected: Unit, db: Database,
                         ui: GameUI) -> None:
    # Check if we clicked on an enemy unit
    enemy = None
    for unit in sim.units:
        if unit.side == selected.side or not unit.is_detected:
            continue
        sx, sy = cam.world_to_screen(unit.lat, unit.lon)
        if unit.is_clicked(screen_pos, sx, sy):
            enemy = unit
            break

    if enemy:
        # Use selected weapon if set, otherwise auto-pick best BVR
        wkey = selected.selected_weapon or selected.best_bvr_weapon(db)
        if wkey:
            sim.fire_weapon(selected, enemy, wkey)
            # Refresh weapon buttons (ammo changed)
            ui.rebuild_weapon_buttons(selected)
        else:
            sim.log(f"{selected.callsign}: no weapons available.")
    else:
        lat, lon = cam.screen_to_world(screen_pos[0], screen_pos[1])
        selected.add_waypoint(lat, lon)
        sim.log(f"{selected.callsign}: waypoint → ({lat:.2f}°, {lon:.2f}°).")


if __name__ == "__main__":
    main()