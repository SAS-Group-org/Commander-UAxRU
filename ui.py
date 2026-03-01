# ui.py — GameUI (setup mode + combat mode)

from __future__ import annotations
from typing import Optional

import pygame
import pygame_gui
from pygame_gui.elements import (
    UIButton, UIPanel, UITextBox, UISelectionList, UILabel,
    UITextEntryLine,
)

from constants import BOTTOM_PANEL_FRACTION, BOTTOM_PANEL_MIN_HEIGHT, TIME_SPEEDS, TIME_SPEED_LABELS, DEFAULT_SPEED_IDX
from scenario import Database, Unit
from simulation import SimulationEngine

_PAD     = 6
_BTN_H   = 32
_BTN_PAD = 4
_WEAP_H  = 30   # height of each weapon button


class GameUI:
    def __init__(self, surface: pygame.Surface, win_w: int, win_h: int,
                 db: Database):
        self._win        = surface
        self._win_w      = win_w
        self._win_h      = win_h
        self._db         = db
        self._mode       = "setup"        # "setup" | "combat"
        self._speed_idx  = DEFAULT_SPEED_IDX
        self._last_log_len = 0

        # Roster data (setup mode)
        self._roster_items: list[str] = []   # display strings
        self._roster_keys:  list[str] = []   # platform keys

        # Widget references (rebuilt on mode change / resize)
        self.manager:       pygame_gui.UIManager = None  # type: ignore
        self._panel:        UIPanel   = None             # type: ignore
        
        # Setup widgets
        self._roster_list:  UISelectionList = None       # type: ignore
        self._setup_info:   UITextBox       = None       # type: ignore
        self._place_btn:    UIButton        = None       # type: ignore
        self._remove_btn:   UIButton        = None       # type: ignore
        self._clear_btn:    UIButton        = None       # type: ignore
        self._start_btn:    UIButton        = None       # type: ignore
        self._qty_entry:    UITextEntryLine = None       # type: ignore
        
        # Combat widgets
        self._nav_box:      UITextBox      = None        # type: ignore
        self._log_box:      UITextBox      = None        # type: ignore
        self._fow_btn:      UIButton       = None        # type: ignore
        self._speed_btns:   list[UIButton] = []
        self._weap_btns:    list[UIButton] = []
        self._weap_keys:    list[str]      = []

        self._build_roster_data()
        self._build()

    # ── Roster ────────────────────────────────────────────────────────────────

    _CATEGORIES = [
        ("─── FIXED-WING ───",   ("fighter", "attacker")),
        ("─── ROTARY WING ───",  ("helicopter",)),
        ("─── ARMOR (MBT) ───",  ("tank",)),
        ("─── IFV ───",           ("ifv",)),
        ("─── APC ───",           ("apc",)),
        ("─── RECON ───",         ("recon",)),
        ("─── TANK DESTROY ───",  ("tank_destroyer",)),
    ]
    _DIVIDER_PREFIX = "───" 

    def _build_roster_data(self) -> None:
        self._roster_items.clear()
        self._roster_keys.clear()

        blue = {key: p for key, p in self._db.platforms.items()
                if p.player_side == "Blue"}

        for header, types in self._CATEGORIES:
            group = sorted(
                [(k, p) for k, p in blue.items() if p.unit_type in types],
                key=lambda x: -x[1].fleet_count,
            )
            if not group:
                continue
            self._roster_items.append(header)
            self._roster_keys.append(header)   
            for key, p in group:
                label = f"  {p.display_name}  ×{p.fleet_count}"
                self._roster_items.append(label)
                self._roster_keys.append(key)

    # ── Build ─────────────────────────────────────────────────────────────────

    def _col_widths(self) -> tuple[int, int, int]:
        avail = self._win_w - _PAD * 4
        c1 = max(280, int(avail * 0.35))
        c2 = max(220, int(avail * 0.27))
        c3 = max(180, avail - c1 - c2)
        return c1, c2, c3

    def _build(self) -> None:
        self.manager = pygame_gui.UIManager((self._win_w, self._win_h))
        self.manager.preload_fonts([
            {"name": "noto_sans", "point_size": 13,
             "style": "bold", "antialiased": "1"},
        ])
        self._speed_btns = []
        self._weap_btns  = []
        self._weap_keys  = []

        # Calculate dynamic scaling height based on the window size
        panel_h = max(BOTTOM_PANEL_MIN_HEIGHT, int(self._win_h * BOTTOM_PANEL_FRACTION))
        panel_y = self._win_h - panel_h
        
        self._panel = UIPanel(
            relative_rect=pygame.Rect(0, panel_y, self._win_w, panel_h),
            manager=self.manager,
        )

        if self._mode == "setup":
            self._build_setup(panel_h)
        else:
            self._build_combat(panel_h)

        self._last_log_len = -1   

    def _build_setup(self, panel_h: int) -> None:
        roster_w  = max(300, int(self._win_w * 0.62))
        ctrl_x    = roster_w + _PAD * 2
        ctrl_w    = self._win_w - ctrl_x - _PAD

        self._roster_list = UISelectionList(
            relative_rect=pygame.Rect(_PAD, _PAD,
                                      roster_w, panel_h - _PAD * 2),
            item_list=self._roster_items,
            manager=self.manager,
            container=self._panel,
        )

        # We need slightly less info height to fit the FOW button
        info_h = panel_h - (_BTN_H + _BTN_PAD) * 5 - _PAD * 3
        self._setup_info = UITextBox(
            html_text=(
                "<b>UNIT DEPLOYMENT</b><br>"
                "Select a unit type, set quantity,<br>"
                "then click <b>Place on Map</b>.<br>"
                "Each left-click places one unit.<br>"
                "Right-click a placed unit to remove."
            ),
            relative_rect=pygame.Rect(ctrl_x, _PAD, ctrl_w, info_h),
            manager=self.manager,
            container=self._panel,
        )

        btn_y = info_h + _PAD * 2

        _LBL_W   = 28                          
        _ENTRY_W = 52                          
        _GAP     = _PAD
        place_w  = ctrl_w - _LBL_W - _ENTRY_W - _GAP * 2

        UILabel(
            relative_rect=pygame.Rect(ctrl_x, btn_y, _LBL_W, _BTN_H),
            text="Qty:",
            manager=self.manager,
            container=self._panel,
        )
        self._qty_entry = UITextEntryLine(
            relative_rect=pygame.Rect(
                ctrl_x + _LBL_W + _GAP, btn_y, _ENTRY_W, _BTN_H
            ),
            manager=self.manager,
            container=self._panel,
        )
        self._qty_entry.set_text("1")
        self._qty_entry.set_allowed_characters("numbers")

        self._place_btn = UIButton(
            relative_rect=pygame.Rect(
                ctrl_x + _LBL_W + _ENTRY_W + _GAP * 2, btn_y, place_w, _BTN_H
            ),
            text="Place on Map",
            manager=self.manager,
            container=self._panel,
        )
        btn_y += _BTN_H + _BTN_PAD

        # Added FOW toggle to the setup mode buttons
        for label, attr in [
            ("Remove Selected",     "_remove_btn"),
            ("Clear All Blue",      "_clear_btn"),
            ("FOG OF WAR: ON",      "_fow_btn"),
            ("▶  START SIMULATION", "_start_btn"),
        ]:
            btn = UIButton(
                relative_rect=pygame.Rect(ctrl_x, btn_y, ctrl_w, _BTN_H),
                text=label,
                manager=self.manager,
                container=self._panel,
            )
            setattr(self, attr, btn)
            btn_y += _BTN_H + _BTN_PAD

    def _build_combat(self, panel_h: int) -> None:
        c1, c2, c3 = self._col_widths()
        col1_x = _PAD
        col2_x = col1_x + c1 + _PAD
        col3_x = col2_x + c2 + _PAD

        self._nav_box = UITextBox(
            html_text="<b>STANDBY</b>",
            relative_rect=pygame.Rect(col1_x, _PAD, c1, panel_h - _PAD * 2),
            manager=self.manager,
            container=self._panel,
        )

        UILabel(
            relative_rect=pygame.Rect(col2_x, _PAD, c2, 20),
            text="ARMAMENTS  (click to select)",
            manager=self.manager,
            container=self._panel,
        )

        n       = len(TIME_SPEED_LABELS)
        btn_w   = max(44, (c3 - _BTN_PAD * (n - 1)) // n)
        for i, label in enumerate(TIME_SPEED_LABELS):
            bx = col3_x + i * (btn_w + _BTN_PAD)
            self._speed_btns.append(UIButton(
                relative_rect=pygame.Rect(bx, _PAD, btn_w, _BTN_H),
                text=label,
                manager=self.manager,
                container=self._panel,
            ))

        fow_y = _PAD + _BTN_H + _BTN_PAD
        self._fow_btn = UIButton(
            relative_rect=pygame.Rect(col3_x, fow_y, c3, _BTN_H),
            text="FOG OF WAR: ON",
            manager=self.manager,
            container=self._panel,
        )

        log_y = fow_y + _BTN_H + _BTN_PAD
        self._log_box = UITextBox(
            html_text="<b>EVENT LOG</b>",
            relative_rect=pygame.Rect(
                col3_x, log_y,
                c3, panel_h - log_y - _PAD,
            ),
            manager=self.manager,
            container=self._panel,
        )

    # ── Dynamic weapon buttons ────────────────────────────────────────────────

    def rebuild_weapon_buttons(self, unit: Optional[Unit]) -> None:
        for btn in self._weap_btns:
            btn.kill()
        self._weap_btns.clear()
        self._weap_keys.clear()

        if unit is None or self._mode != "combat":
            return

        _, c2, _ = self._col_widths()
        c1, _, _ = self._col_widths()
        col2_x   = _PAD + c1 + _PAD
        start_y  = _PAD + 22   

        for i, (wkey, qty) in enumerate(unit.loadout.items()):
            wdef     = self._db.weapons.get(wkey)
            name     = wdef.display_name if wdef else wkey
            rng_str  = f"  {wdef.range_km:.0f}km" if wdef and not wdef.is_gun else ""
            is_sel   = (unit.selected_weapon == wkey)
            prefix   = "► " if is_sel else "   "
            color_tag = '<font color="#FFEE44">' if is_sel else '<font color="#AACCAA">' if qty > 0 else '<font color="#AA6666">'
            label    = f"{prefix}{qty}×  {name}{rng_str}"

            btn = UIButton(
                relative_rect=pygame.Rect(
                    col2_x, start_y + i * (_WEAP_H + _BTN_PAD),
                    c2, _WEAP_H
                ),
                text=label,
                manager=self.manager,
                container=self._panel,
            )
            self._weap_btns.append(btn)
            self._weap_keys.append(wkey)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _parse_qty(self) -> int:
        if self._qty_entry is None:
            return 1
        try:
            n = int(self._qty_entry.get_text())
        except (ValueError, TypeError):
            n = 1
        return max(1, min(20, n))

    # ── Mode switch ───────────────────────────────────────────────────────────

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._build()

    def resize(self, surface: pygame.Surface, w: int, h: int) -> None:
        self._win   = surface
        self._win_w = w
        self._win_h = h
        self._build()

    # ── Events ────────────────────────────────────────────────────────────────

    def process_events(self, event: pygame.event.Event) -> dict:
        self.manager.process_events(event)

        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            
            # Common Buttons
            if event.ui_element == self._fow_btn:
                return {"type": "toggle_fow"}
                
            # Setup buttons
            if self._mode == "setup":
                if event.ui_element == self._place_btn:
                    sel = (self._roster_list.get_single_selection()
                           if self._roster_list else None)
                    if sel and sel in self._roster_items:
                        key = self._roster_keys[self._roster_items.index(sel)]
                        if key.startswith(self._DIVIDER_PREFIX):
                            return {"type": "place_unit_no_selection"}
                        qty = self._parse_qty()
                        return {"type": "place_unit",
                                "platform_key": key, "quantity": qty}
                    return {"type": "place_unit_no_selection"}
                if event.ui_element == self._remove_btn:
                    return {"type": "remove_selected"}
                if event.ui_element == self._clear_btn:
                    return {"type": "clear_blue"}
                if event.ui_element == self._start_btn:
                    return {"type": "start_sim"}

            # Combat speed buttons
            for i, btn in enumerate(self._speed_btns):
                if event.ui_element == btn:
                    self._speed_idx = i
                    return {"type": "speed_change", "speed_idx": i}
            
            # Weapon select buttons
            for i, btn in enumerate(self._weap_btns):
                if event.ui_element == btn:
                    return {"type": "weapon_select",
                            "weapon_key": self._weap_keys[i]}

        return {}

    # ── Per-frame update ──────────────────────────────────────────────────────

    def update(self, time_delta: float,
               sim: Optional[SimulationEngine],
               selected: Optional[Unit],
               placing_type: Optional[str] = None,
               placing_remaining: int = 0,
               show_all_enemies: bool = False) -> None:

        if self._mode == "setup":
            if self._setup_info:
                if placing_type:
                    p = self._db.platforms.get(placing_type)
                    pname = p.display_name if p else placing_type
                    self._setup_info.set_text(
                        f"<b>PLACING:</b> {pname}<br>"
                        f"<b>{placing_remaining} remaining</b><br>"
                        f"Left-click map to place unit.<br>"
                        f"Press ESC to cancel."
                    )
                else:
                    placed = len(sim.blue_units()) if sim else 0
                    sel_str = ""
                    if self._roster_list:
                        s = self._roster_list.get_single_selection()
                        if s and s in self._roster_items:
                            key = self._roster_keys[self._roster_items.index(s)]
                            p   = self._db.platforms.get(key)
                            if p:
                                type_labels = {
                                    "fighter":"Fighter","attacker":"Attack",
                                    "helicopter":"Helicopter","tank":"MBT",
                                    "ifv":"IFV","apc":"APC","recon":"Recon",
                                    "tank_destroyer":"Tank Destroyer",
                                }
                                tl = type_labels.get(p.unit_type, p.unit_type.upper())
                                spd_lbl = "km/h" if p.unit_type not in ("tank","ifv","apc","recon","tank_destroyer") else "km/h (road)"
                                sel_str = (
                                    f"<b>Selected:</b> {p.display_name}<br>"
                                    f"<b>Type:</b> {tl}  ×{p.fleet_count} in service<br>"
                                    f"Spd {p.speed_kmh} {spd_lbl}  "
                                    f"Detect {p.radar_range_km} km<br>"
                                    f"ECM {int(p.ecm_rating*100)}%<br>"
                                    f"<br>"
                                )
                    self._setup_info.set_text(
                        f"<b>DEPLOYMENT PHASE</b><br>"
                        f"Blue units placed: <b>{placed}</b><br><br>"
                        + sel_str +
                        "Select type → Place on Map<br>"
                        "Right-click unit to remove"
                    )

        else:  # combat mode
            if sim is None:
                return

            if selected and selected.alive:
                p = selected.platform
                wp = len(selected.waypoints)
                
                # Calculate fuel percentage and color code it
                fuel_pct = (selected.fuel_kg / p.fuel_capacity_kg) * 100 if p.fuel_capacity_kg > 0 else 0
                fuel_col = "#FF4444" if fuel_pct < 20 else "#FFAA00" if fuel_pct < 50 else "#FFFFFF"

                self._nav_box.set_text(
                    f"<b>{selected.callsign}</b>  [{selected.side}]<br>"
                    f"<b>Type:</b> {p.display_name}<br>"
                    f"<b>Spd:</b> {p.speed_kmh:,} km/h  "
                    f"<b>Ceil:</b> {p.ceiling_ft:,} ft<br>"
                    f"<b>Fuel:</b> <font color='{fuel_col}'>{int(fuel_pct)}%</font> ({int(selected.fuel_kg)} kg)<br>"
                    f"<b>Radar:</b> {p.radar_type}  {p.radar_range_km} km<br>"
                    f"<b>HDG:</b> {selected.heading:05.1f}°  "
                    f"<b>ECM:</b> {int(p.ecm_rating*100)}%<br>"
                    f"<b>Pos:</b> {selected.lat:.3f}°N  {selected.lon:.3f}°E<br>"
                    f"<b>Route:</b> {wp} wp{'s' if wp!=1 else ''}"
                )
            else:
                t  = SimulationEngine._fmt_time(sim.game_time)
                cx = "PAUSED" if sim.paused else f"{sim.time_compression}×"
                self._nav_box.set_text(
                    f"<b>TACTICAL DISPLAY</b><br>"
                    f"<b>Time:</b> {t}  <b>Speed:</b> {cx}<br>"
                    f"<b>Blue:</b> {len(sim.blue_units())} units  "
                    f"<b>Red:</b> {len(sim.red_units())} units<br>"
                    f"<b>Missiles:</b> {len(sim.missiles)} in flight<br><br>"
                    f"Left-click unit to select<br>"
                    f"Right-click enemy to fire<br>"
                    f"Right-click map to waypoint"
                )

            if len(sim.event_log) != self._last_log_len:
                self._last_log_len = len(sim.event_log)
                recent = list(reversed(list(sim.event_log)[-6:]))
                self._log_box.set_text("<br>".join(
                    f'<font color="#90D090">› {e}</font>' for e in recent
                ))

        # Update FOW Button Text (Common to both modes)
        if self._fow_btn:
            self._fow_btn.set_text(f"FOG OF WAR: {'OFF' if show_all_enemies else 'ON'}")

        self.manager.update(time_delta)

    def draw(self) -> None:
        self.manager.draw_ui(self._win)

    @property
    def active_speed_idx(self) -> int:
        return self._speed_idx

    @property
    def mode(self) -> str:
        return self._mode

    def get_roster_selection(self) -> Optional[str]:
        if self._roster_list is None:
            return None
        sel = self._roster_list.get_single_selection()
        if sel and sel in self._roster_items:
            return self._roster_keys[self._roster_items.index(sel)]
        return None