# renderer.py — all pygame rendering, no game logic

from __future__ import annotations

import math
import os
from typing import Optional

import pygame

from constants import (
    TILE_SIZE,
    BLUE_UNIT_COLOR, RED_UNIT_COLOR, SELECTED_COLOR,
    WAYPOINT_COLOR, ROUTE_LINE_COLOR,
    RADAR_RING_COLOR,
    MISSILE_BLUE_COLOR, MISSILE_RED_COLOR, TRAIL_COLOR,
    CONTACT_FAINT_COLOR, CONTACT_PROBABLE_COLOR, CONTACT_CONFIRM_COLOR,
)
from geo import lat_lon_to_pixel, world_to_screen
from map_tiles import get_tile
from scenario import Missile, Unit
from sensor import Contact


class Renderer:
    def __init__(self, surface: pygame.Surface):
        self.surface     = surface
        self._img_cache: dict[str, pygame.Surface] = {}
        self._font_sm    = pygame.font.SysFont(None, 15)
        self._font_med   = pygame.font.SysFont(None, 18)

    def update_surface(self, surface: pygame.Surface) -> None:
        self.surface = surface

    def draw_frame(self,
                   cam_px: float, cam_py: float, zoom: int,
                   units: list[Unit],
                   missiles: list[Missile],
                   win_w: int, map_h: int,
                   blue_contacts: dict[str, Contact] | None = None,
                   placing_type: str | None = None,
                   placing_remaining: int = 0,
                   mouse_pos: tuple[int, int] | None = None,
                   show_all_enemies: bool = False) -> None:
        contacts = blue_contacts or {}
        self.surface.fill((18, 26, 34))
        self._draw_tiles(cam_px, cam_py, zoom, win_w, map_h)
        
        # Pass show_all_enemies to radar and routes
        self._draw_radar_rings(cam_px, cam_py, zoom, units, win_w, map_h, show_all_enemies)
        self._draw_routes(cam_px, cam_py, zoom, units, win_w, map_h, show_all_enemies)
        
        self._draw_missiles(cam_px, cam_py, zoom, missiles, win_w, map_h)
        self._draw_units(cam_px, cam_py, zoom, units, win_w, map_h,
                         contacts, show_all_enemies)
        self._draw_contacts(cam_px, cam_py, zoom, contacts, units,
                            win_w, map_h, show_all_enemies)
        if placing_type and mouse_pos and mouse_pos[1] < map_h:
            self._draw_placement_cursor(mouse_pos, placing_type, placing_remaining)

    def _draw_tiles(self, cam_px, cam_py, zoom, win_w, map_h) -> None:
        tl_x = cam_px - win_w / 2
        tl_y = cam_py - map_h / 2
        sx = int(tl_x // TILE_SIZE)
        sy = int(tl_y // TILE_SIZE)
        ex = sx + (win_w  // TILE_SIZE) + 2
        ey = sy + (map_h  // TILE_SIZE) + 2

        for tx in range(sx, ex):
            for ty in range(sy, ey):
                bx = int(tx * TILE_SIZE - tl_x)
                by = int(ty * TILE_SIZE - tl_y)
                surf = get_tile(zoom, tx, ty)
                if surf:
                    self.surface.blit(surf, (bx, by))
                else:
                    pygame.draw.rect(self.surface, (28, 38, 48), (bx, by, TILE_SIZE, TILE_SIZE))
                    pygame.draw.rect(self.surface, (40, 54, 68), (bx, by, TILE_SIZE, TILE_SIZE), 1)

    def _draw_radar_rings(self, cam_px, cam_py, zoom, units, win_w, map_h, show_all_enemies: bool) -> None:
        for unit in units:
            if not unit.alive: continue
            
            # Hide Red team radar if Fog of War is ON
            if unit.side == "Red" and not show_all_enemies: 
                continue         
                
            sx, sy = world_to_screen(unit.lat, unit.lon, cam_px, cam_py, zoom, win_w, map_h)
            lat2 = unit.lat + (unit.platform.radar_range_km / 111.32)
            _, py1 = lat_lon_to_pixel(unit.lat, unit.lon, zoom)
            _, py2 = lat_lon_to_pixel(lat2,     unit.lon, zoom)
            radius = int(abs(py1 - py2))
            
            if 2 <= radius <= 4000:
                # Use standard cyan for Blue, and a dark red for Red radars
                color = RADAR_RING_COLOR if unit.side == "Blue" else (160, 50, 50)
                pygame.draw.circle(self.surface, color, (int(sx), int(sy)), radius, 1)

    def _draw_routes(self, cam_px, cam_py, zoom, units, win_w, map_h, show_all_enemies: bool) -> None:
        for unit in units:
            if not unit.alive or not unit.waypoints: continue
            
            # Hide Red team routes if Fog of War is ON
            if unit.side == "Red" and not show_all_enemies:
                continue
                
            sx, sy = world_to_screen(unit.lat, unit.lon, cam_px, cam_py, zoom, win_w, map_h)
            points = [(sx, sy)]
            for wlat, wlon in unit.waypoints:
                wx, wy = world_to_screen(wlat, wlon, cam_px, cam_py, zoom, win_w, map_h)
                points.append((wx, wy))
                pygame.draw.circle(self.surface, WAYPOINT_COLOR, (int(wx), int(wy)), 3)
            if len(points) > 1:
                pygame.draw.lines(self.surface, ROUTE_LINE_COLOR, False, points, 1)

    def _draw_missiles(self, cam_px, cam_py, zoom, missiles, win_w, map_h) -> None:
        for m in missiles:
            if not m.active: continue
            color = MISSILE_BLUE_COLOR if m.side == "Blue" else MISSILE_RED_COLOR
            trail = list(m.trail)
            n     = len(trail)
            for i, (tlat, tlon) in enumerate(trail):
                tx, ty = world_to_screen(tlat, tlon, cam_px, cam_py, zoom, win_w, map_h)
                alpha  = int(255 * (i + 1) / max(n, 1))
                radius = max(1, int(3 * (i + 1) / max(n, 1)))
                s = pygame.Surface((radius * 2 + 1, radius * 2 + 1), pygame.SRCALPHA)
                pygame.draw.circle(s, (*TRAIL_COLOR, alpha), (radius, radius), radius)
                self.surface.blit(s, (int(tx) - radius, int(ty) - radius))
            mx, my = world_to_screen(m.lat, m.lon, cam_px, cam_py, zoom, win_w, map_h)
            pygame.draw.circle(self.surface, color, (int(mx), int(my)), 3)

    def _draw_units(self, cam_px, cam_py, zoom, units, win_w, map_h,
                   contacts: dict, show_all_enemies: bool) -> None:
        for unit in units:
            if not unit.alive: continue
            if unit.side == "Red" and not show_all_enemies: continue

            sx, sy = world_to_screen(unit.lat, unit.lon, cam_px, cam_py, zoom, win_w, map_h)

            if not (-80 < sx < win_w + 80 and -80 < sy < map_h + 80): continue

            base_color = (BLUE_UNIT_COLOR if unit.side == "Blue" else RED_UNIT_COLOR)
            if unit.flash_frames > 0: base_color = (255, 255, 255)
            color = SELECTED_COLOR if unit.selected else base_color

            if unit.selected: pygame.draw.circle(self.surface, SELECTED_COLOR, (int(sx), int(sy)), 18, 2)

            surf = self._get_unit_surface(unit)
            if surf:
                rotated = pygame.transform.rotate(surf, -unit.heading)
                rect    = rotated.get_rect(center=(int(sx), int(sy)))
                self.surface.blit(rotated, rect.topleft)
            else:
                utype = unit.platform.unit_type
                if utype == "tank": self._draw_tank_symbol(sx, sy, unit.heading, color)
                elif utype == "ifv": self._draw_ifv_symbol(sx, sy, color)
                elif utype == "apc": self._draw_apc_symbol(sx, sy, color)
                elif utype == "recon": self._draw_recon_symbol(sx, sy, color)
                elif utype == "tank_destroyer": self._draw_td_symbol(sx, sy, unit.heading, color)
                elif utype == "sam": self._draw_sam_symbol(sx, sy, color)
                else: self._draw_jet_polygon(sx, sy, unit.heading, color)

            det_tag = " ◆" if unit.is_detected and unit.side == "Blue" else ""
            label   = self._font_sm.render(f"{unit.callsign}{det_tag}", True, (0, 0, 0))
            self.surface.blit(label, (int(sx) + 13, int(sy) - 10))
            
            type_label = self._font_sm.render(unit.platform.display_name, True, (0, 0, 0))
            self.surface.blit(type_label, (int(sx) + 13, int(sy) + 2))

    def _get_unit_surface(self, unit: Unit) -> Optional[pygame.Surface]:
        path = unit.image_path
        if not path: return None
        if path not in self._img_cache:
            if os.path.exists(path):
                try:
                    img = pygame.image.load(path).convert_alpha()
                    self._img_cache[path] = pygame.transform.smoothscale(img, (32, 32))
                except pygame.error: self._img_cache[path] = None   # type: ignore[assignment]
            else: self._img_cache[path] = None       # type: ignore[assignment]
        return self._img_cache.get(path)

    def _draw_jet_polygon(self, sx, sy, heading, color) -> None:
        pts = [(0, -12), (8, 6), (2, 4), (2, 12), (-2, 12), (-2, 4), (-8, 6)]
        rad = math.radians(heading)
        cos_h, sin_h = math.cos(rad), math.sin(rad)
        rotated = [(sx + px * cos_h - py * sin_h, sy + px * sin_h + py * cos_h) for px, py in pts]
        pygame.draw.polygon(self.surface, color, rotated, 2)

    def _draw_tank_symbol(self, sx, sy, heading, color) -> None:
        hw, hh = 10, 7
        body = pygame.Surface((hw*2, hh*2), pygame.SRCALPHA)
        pygame.draw.rect(body, color, (0, 0, hw*2, hh*2))
        pygame.draw.rect(body, (0,0,0), (0, 0, hw*2, hh*2), 1)
        rad = math.radians(heading)
        rot = pygame.transform.rotate(body, -heading)
        rect = rot.get_rect(center=(int(sx), int(sy)))
        self.surface.blit(rot, rect.topleft)
        blen = 14
        ex = sx + blen * math.sin(rad)
        ey = sy - blen * math.cos(rad)
        pygame.draw.line(self.surface, color, (int(sx), int(sy)), (int(ex), int(ey)), 2)

    def _draw_ifv_symbol(self, sx, sy, color) -> None:
        hw, hh = 9, 6
        pygame.draw.rect(self.surface, color, (int(sx)-hw, int(sy)-hh, hw*2, hh*2), 2)
        pygame.draw.circle(self.surface, color, (int(sx), int(sy)), 4)

    def _draw_apc_symbol(self, sx, sy, color) -> None:
        hw, hh = 9, 6
        pygame.draw.rect(self.surface, color, (int(sx)-hw, int(sy)-hh, hw*2, hh*2), 2)

    def _draw_recon_symbol(self, sx, sy, color) -> None:
        pts = [(sx, sy-10), (sx+9, sy), (sx, sy+10), (sx-9, sy)]
        pygame.draw.polygon(self.surface, color, pts, 2)

    def _draw_td_symbol(self, sx, sy, heading, color) -> None:
        rad = math.radians(heading)
        pts = [
            (sx + 12*math.sin(rad),         sy - 12*math.cos(rad)),
            (sx + 8*math.sin(rad+2.3),       sy -  8*math.cos(rad+2.3)),
            (sx + 8*math.sin(rad-2.3),       sy -  8*math.cos(rad-2.3)),
        ]
        pygame.draw.polygon(self.surface, color, pts, 2)
        blen = 14
        ex = sx + blen*math.sin(rad)
        ey = sy - blen*math.cos(rad)
        pygame.draw.line(self.surface, color, (int(sx),int(sy)), (int(ex),int(ey)), 2)

    def _draw_sam_symbol(self, sx, sy, color) -> None:
        """Rectangle with a dome at the bottom (NATO Air Defense Symbol)."""
        hw, hh = 10, 7
        pygame.draw.rect(self.surface, color, (int(sx)-hw, int(sy)-hh, hw*2, hh*2), 2)
        rect = pygame.Rect(int(sx)-5, int(sy)+hh-5, 10, 10)
        pygame.draw.arc(self.surface, color, rect, 0, math.pi, 2)

    def _draw_contacts(self, cam_px, cam_py, zoom,
                       contacts: dict, units: list,
                       win_w: int, map_h: int,
                       show_all_enemies: bool) -> None:
        if show_all_enemies: return

        uid_to_unit = {u.uid: u for u in units if u.side == "Red"}

        for uid, contact in contacts.items():
            sx, sy = world_to_screen(contact.lat, contact.lon, cam_px, cam_py, zoom, win_w, map_h)
            if not (-80 < sx < win_w + 80 and -80 < sy < map_h + 80): continue

            cls = contact.classification

            if cls == "FAINT":
                pygame.draw.circle(self.surface, CONTACT_FAINT_COLOR, (int(sx), int(sy)), 5, 1)
                lbl = self._font_sm.render("?", True, CONTACT_FAINT_COLOR)
                self.surface.blit(lbl, (int(sx) + 7, int(sy) - 6))

            elif cls == "PROBABLE":
                color = CONTACT_PROBABLE_COLOR
                utype = contact.unit_type or "unknown"
                if utype in ("fighter", "attacker", "helicopter"):
                    pts = [(sx, sy - 9), (sx + 8, sy + 5), (sx - 8, sy + 5)]
                    pygame.draw.polygon(self.surface, color, pts, 2)
                else:
                    pts = [(sx, sy - 9), (sx + 9, sy), (sx, sy + 9), (sx - 9, sy)]
                    pygame.draw.polygon(self.surface, color, pts, 2)
                lbl = self._font_sm.render(f"? {utype}", True, color)
                self.surface.blit(lbl, (int(sx) + 12, int(sy) - 6))

            else:  
                unit = uid_to_unit.get(uid)
                if unit is None: continue  
                color = CONTACT_CONFIRM_COLOR
                utype = unit.platform.unit_type

                if unit.flash_frames > 0: color = (255, 255, 255)

                surf = self._get_unit_surface(unit)
                if surf:
                    rotated = pygame.transform.rotate(surf, -unit.heading)
                    rect    = rotated.get_rect(center=(int(sx), int(sy)))
                    self.surface.blit(rotated, rect.topleft)
                else:
                    if utype == "tank": self._draw_tank_symbol(sx, sy, unit.heading, color)
                    elif utype == "ifv": self._draw_ifv_symbol(sx, sy, color)
                    elif utype == "apc": self._draw_apc_symbol(sx, sy, color)
                    elif utype == "recon": self._draw_recon_symbol(sx, sy, color)
                    elif utype == "tank_destroyer": self._draw_td_symbol(sx, sy, unit.heading, color)
                    elif utype == "sam": self._draw_sam_symbol(sx, sy, color)
                    else: self._draw_jet_polygon(sx, sy, unit.heading, color)

                callsign_lbl = self._font_sm.render(unit.callsign, True, (0, 0, 0))
                type_lbl = self._font_sm.render(unit.platform.display_name, True, (0, 0, 0))
                self.surface.blit(callsign_lbl, (int(sx) + 13, int(sy) - 10))
                self.surface.blit(type_lbl,     (int(sx) + 13, int(sy) + 2))

    def _draw_placement_cursor(self, mouse_pos: tuple[int, int],
                                placing_type: str,
                                placing_remaining: int = 0) -> None:
        mx, my = mouse_pos
        size   = 16
        col    = (80, 220, 120)
        pygame.draw.line(self.surface, col, (mx - size, my), (mx + size, my), 2)
        pygame.draw.line(self.surface, col, (mx, my - size), (mx, my + size), 2)
        pygame.draw.circle(self.surface, col, (mx, my), size, 1)
        rem_txt = f" ({placing_remaining} left)" if placing_remaining > 1 else ""
        label = self._font_sm.render(f"Place: {placing_type}{rem_txt}", True, col)
        self.surface.blit(label, (mx + size + 4, my - 8))