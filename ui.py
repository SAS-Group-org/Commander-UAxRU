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
_WEAP_H  = 30   

class GameUI:
    def __init__(self, surface: pygame.Surface, win_w: int, win_h: int,
                 db: Database):
        self._win        = surface
        self._win_w      = win_w
        self._win_h      = win_h
        self._db         = db
        self._mode       = "setup"        
        self._speed_idx  = DEFAULT_SPEED_IDX
        self._last_log_len = 0

        self._roster_items: list[str] = []   
        self._roster_keys:  list[str] = []   

        self.manager:       pygame_gui.UIManager = None  # type: ignore
        self._panel:        UIPanel   = None             # type: ignore
        
        self._roster_list:  UISelectionList = None       # type: ignore
        self._setup_info:   UITextBox       = None       # type: ignore
        self._place_btn:    UIButton        = None       # type: ignore
        self._remove_btn:   UIButton        = None       # type: ignore
        self._clear_btn:    UIButton        = None       # type: ignore
        self._save_deploy_btn: UIButton     = None       # type: ignore
        self._load_deploy_btn: UIButton     = None       # type: ignore
        self._start_btn:    UIButton        = None       # type: ignore
        self._qty_entry:    UITextEntryLine = None       # type: ignore
        
        self._nav_box:      UITextBox      = None        # type: ignore
        self._log_box:      UITextBox      = None        # type: ignore
        self._fow_btn:      UIButton       = None        # type: ignore
        
        self._auto_engage_btn: UIButton    = None        # type: ignore
        self._roe_btn:      UIButton       = None        # type: ignore
        self._ecm_btn:      UIButton       = None        # type: ignore
        
        self._assign_cap_btn: UIButton     = None        # type: ignore
        self._clear_msn_btn:  UIButton     = None        # type: ignore

        self._climb_5k_btn: UIButton       = None        # type: ignore
        self._climb_1k_btn: UIButton       = None        # type: ignore
        self._climb_500_btn:UIButton       = None        # type: ignore
        self._dive_5k_btn:  UIButton       = None        # type: ignore
        self._dive_1k_btn:  UIButton       = None        # type: ignore
        self._dive_500_btn: UIButton       = None        # type: ignore
        
        self._speed_btns:   list[UIButton] = []
        self._weap_btns:    list[UIButton] = []
        self._weap_keys:    list[str]      = []
        
        self.salvo_mode = "1"
        self._salvo_modes = ["1", "2", "4", "SLS"]
        self._salvo_btns:   list[UIButton] = []

        self._build_roster_data()
        self._build()

    _CATEGORIES = [
        ("─── FIXED-WING ───",   ("fighter", "attacker")),
        ("─── ROTARY WING ───",  ("helicopter",)),
        ("─── AIR DEFENSE ───",  ("sam",)),
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
            if not group: continue
            self._roster_items.append(header)
            self._roster_keys.append(header)   
            for key, p in group:
                label = f"  {p.display_name}  ×{p.fleet_count}"
                self._roster_items.append(label)
                self._roster_keys.append(key)

    def _col_widths(self) -> tuple[int, int, int]:
        avail = self._win_w - _PAD * 4
        c1 = max(310, int(avail * 0.30))
        c2 = max(350, int(avail * 0.40))
        c3 = max(180, avail - c1 - c2)
        return c1, c2, c3

    def _build(self) -> None:
        self.manager = pygame_gui.UIManager((self._win_w, self._win_h))
        self.manager.preload_fonts([{"name": "noto_sans", "point_size": 13, "style": "bold", "antialiased": "1"}])
        self._speed_btns = []
        self._weap_btns  = []
        self._weap_keys  = []
        self._salvo_btns = []

        panel_h = max(BOTTOM_PANEL_MIN_HEIGHT, int(self._win_h * BOTTOM_PANEL_FRACTION))
        panel_y = self._win_h - panel_h
        
        self._panel = UIPanel(
            relative_rect=pygame.Rect(0, panel_y, self._win_w, panel_h),
            manager=self.manager,
        )

        if self._mode == "setup": self._build_setup(panel_h)
        else: self._build_combat(panel_h)

        self._last_log_len = -1   

    def _build_setup(self, panel_h: int) -> None:
        roster_w  = max(300, int(self._win_w * 0.62))
        ctrl_x    = roster_w + _PAD * 2
        ctrl_w    = self._win_w - ctrl_x - _PAD

        self._roster_list = UISelectionList(
            relative_rect=pygame.Rect(_PAD, _PAD, roster_w, panel_h - _PAD * 2),
            item_list=self._roster_items, manager=self.manager, container=self._panel,
        )

        info_h = panel_h - (_BTN_H + _BTN_PAD) * 7 - _PAD * 3
        self._setup_info = UITextBox(
            html_text=(
                "<b>UNIT DEPLOYMENT</b><br>"
                "Select a unit type, set quantity,<br>"
                "then click <b>Place on Map</b>.<br>"
                "Each left-click places one unit.<br>"
                "Right-click a placed unit to remove."
            ),
            relative_rect=pygame.Rect(ctrl_x, _PAD, ctrl_w, info_h),
            manager=self.manager, container=self._panel,
        )

        btn_y = info_h + _PAD * 2
        _LBL_W, _ENTRY_W, _GAP = 28, 52, _PAD
        place_w  = ctrl_w - _LBL_W - _ENTRY_W - _GAP * 2

        UILabel(relative_rect=pygame.Rect(ctrl_x, btn_y, _LBL_W, _BTN_H), text="Qty:", manager=self.manager, container=self._panel)
        self._qty_entry = UITextEntryLine(relative_rect=pygame.Rect(ctrl_x + _LBL_W + _GAP, btn_y, _ENTRY_W, _BTN_H), manager=self.manager, container=self._panel)
        self._qty_entry.set_text("1")
        self._qty_entry.set_allowed_characters("numbers")

        self._place_btn = UIButton(relative_rect=pygame.Rect(ctrl_x + _LBL_W + _ENTRY_W + _GAP * 2, btn_y, place_w, _BTN_H), text="Place on Map", manager=self.manager, container=self._panel)
        btn_y += _BTN_H + _BTN_PAD

        for label, attr in [
            ("Remove Selected",     "_remove_btn"),
            ("Clear All Blue",      "_clear_btn"),
            ("Save Deployment",     "_save_deploy_btn"),
            ("Load Deployment",     "_load_deploy_btn"),
            ("FOG OF WAR: ON",      "_fow_btn"),
            ("▶  START SIMULATION", "_start_btn")
        ]:
            btn = UIButton(relative_rect=pygame.Rect(ctrl_x, btn_y, ctrl_w, _BTN_H), text=label, manager=self.manager, container=self._panel)
            setattr(self, attr, btn)
            btn_y += _BTN_H + _BTN_PAD

    def _build_combat(self, panel_h: int) -> None:
        c1, c2, c3 = self._col_widths()
        col1_x, col2_x, col3_x = _PAD, _PAD + c1 + _PAD, _PAD + c1 + _PAD + c2 + _PAD

        row4_y = panel_h - _PAD - _BTN_H
        row3_y = row4_y - _BTN_PAD - _BTN_H
        row2_y = row3_y - _BTN_PAD - _BTN_H
        row1_y = row2_y - _BTN_PAD - _BTN_H
        nav_h  = row1_y - (_PAD * 2)

        self._nav_box = UITextBox(
            html_text="<b>STANDBY</b>",
            relative_rect=pygame.Rect(col1_x, _PAD, c1, nav_h),
            manager=self.manager, container=self._panel,
        )
        
        btn_w_third = (c1 - _BTN_PAD * 2) // 3
        btn_w_half  = (c1 - _BTN_PAD) // 2

        # Row 1 & 2: Altitude
        self._climb_5k_btn = UIButton(relative_rect=pygame.Rect(col1_x, row1_y, btn_w_third, _BTN_H), text="▲ +5k ft", tool_tip_text="Climb 5,000 feet", manager=self.manager, container=self._panel)
        self._climb_1k_btn = UIButton(relative_rect=pygame.Rect(col1_x + btn_w_third + _BTN_PAD, row1_y, btn_w_third, _BTN_H), text="▲ +1k ft", tool_tip_text="Climb 1,000 feet", manager=self.manager, container=self._panel)
        self._climb_500_btn = UIButton(relative_rect=pygame.Rect(col1_x + (btn_w_third + _BTN_PAD)*2, row1_y, btn_w_third, _BTN_H), text="▲ +500 ft", tool_tip_text="Climb 500 feet", manager=self.manager, container=self._panel)

        self._dive_5k_btn = UIButton(relative_rect=pygame.Rect(col1_x, row2_y, btn_w_third, _BTN_H), text="▼ -5k ft", tool_tip_text="Dive 5,000 feet", manager=self.manager, container=self._panel)
        self._dive_1k_btn = UIButton(relative_rect=pygame.Rect(col1_x + btn_w_third + _BTN_PAD, row2_y, btn_w_third, _BTN_H), text="▼ -1k ft", tool_tip_text="Dive 1,000 feet", manager=self.manager, container=self._panel)
        self._dive_500_btn = UIButton(relative_rect=pygame.Rect(col1_x + (btn_w_third + _BTN_PAD)*2, row2_y, btn_w_third, _BTN_H), text="▼ -500 ft", tool_tip_text="Dive 500 feet", manager=self.manager, container=self._panel)

        # Row 3: Doctrine & EW
        self._auto_engage_btn = UIButton(relative_rect=pygame.Rect(col1_x, row3_y, btn_w_third, _BTN_H), text="AUTO", manager=self.manager, container=self._panel)
        self._roe_btn         = UIButton(relative_rect=pygame.Rect(col1_x + btn_w_third + _BTN_PAD, row3_y, btn_w_third, _BTN_H), text="ROE: TGT", manager=self.manager, container=self._panel)
        self._ecm_btn         = UIButton(relative_rect=pygame.Rect(col1_x + (btn_w_third + _BTN_PAD)*2, row3_y, btn_w_third, _BTN_H), text="ECM: PAS", manager=self.manager, container=self._panel)

        # Row 4: Missions
        self._assign_cap_btn = UIButton(relative_rect=pygame.Rect(col1_x, row4_y, btn_w_half, _BTN_H), text="ASSIGN CAP", tool_tip_text="Click map to set CAP center", manager=self.manager, container=self._panel)
        self._clear_msn_btn  = UIButton(relative_rect=pygame.Rect(col1_x + btn_w_half + _BTN_PAD, row4_y, btn_w_half, _BTN_H), text="CLEAR MSN", manager=self.manager, container=self._panel)
        
        self._auto_engage_btn.hide()
        self._roe_btn.hide()
        self._ecm_btn.hide()
        self._assign_cap_btn.hide()
        self._clear_msn_btn.hide()

        UILabel(relative_rect=pygame.Rect(col2_x, _PAD, c2, 20), text="ARMAMENTS  (click to select)", manager=self.manager, container=self._panel)

        salvo_y = _PAD + 22
        btn_w_salvo = (c2 - _BTN_PAD * 3) // 4
        for i, smode in enumerate(self._salvo_modes):
            bx = col2_x + i * (btn_w_salvo + _BTN_PAD)
            btn = UIButton(relative_rect=pygame.Rect(bx, salvo_y, btn_w_salvo, _BTN_H), 
                           text=smode, manager=self.manager, container=self._panel)
            self._salvo_btns.append(btn)
        self.refresh_salvo_buttons()

        n       = len(TIME_SPEED_LABELS)
        btn_w   = max(44, (c3 - _BTN_PAD * (n - 1)) // n)
        for i, label in enumerate(TIME_SPEED_LABELS):
            bx = col3_x + i * (btn_w + _BTN_PAD)
            self._speed_btns.append(UIButton(
                relative_rect=pygame.Rect(bx, _PAD, btn_w, _BTN_H),
                text=label, manager=self.manager, container=self._panel,
            ))

        fow_y = _PAD + _BTN_H + _BTN_PAD
        self._fow_btn = UIButton(relative_rect=pygame.Rect(col3_x, fow_y, c3, _BTN_H), text="FOG OF WAR: ON", manager=self.manager, container=self._panel)

        log_y = fow_y + _BTN_H + _BTN_PAD
        self._log_box = UITextBox(
            html_text="<b>EVENT LOG</b>",
            relative_rect=pygame.Rect(col3_x, log_y, c3, panel_h - log_y - _PAD),
            manager=self.manager, container=self._panel,
        )

    def refresh_salvo_buttons(self) -> None:
        for i, btn in enumerate(self._salvo_btns):
            mode = self._salvo_modes[i]
            if mode == self.salvo_mode:
                btn.set_text(f"► {mode}")
            else:
                btn.set_text(mode)

    def rebuild_weapon_buttons(self, unit: Optional[Unit]) -> None:
        for btn in self._weap_btns: btn.kill()
        self._weap_btns.clear()
        self._weap_keys.clear()

        if unit is None or self._mode != "combat": return

        _, c2, _ = self._col_widths()
        c1, _, _ = self._col_widths()
        col2_x   = _PAD + c1 + _PAD
        start_y  = _PAD + 22 + _BTN_H + _BTN_PAD  

        for i, (wkey, qty) in enumerate(unit.loadout.items()):
            wdef     = self._db.weapons.get(wkey)
            name     = wdef.display_name if wdef else wkey
            rng_str  = f" ({wdef.range_km:.0f}km)" if wdef and not wdef.is_gun else ""
            desc_str = f" - {wdef.description}" if wdef and wdef.description else ""
            
            is_sel   = (unit.selected_weapon == wkey)
            prefix   = "► " if is_sel else "   "
            label    = f"{prefix}{qty}× {name}{rng_str}{desc_str}"
            
            tt_text  = f"<b>{name}</b><br>{wdef.description}<br>Domain: {wdef.domain.upper()}<br>Speed: {wdef.speed_kmh} km/h" if wdef else ""
            
            btn = UIButton(relative_rect=pygame.Rect(col2_x, start_y + i * (_WEAP_H + _BTN_PAD), c2, _WEAP_H), 
                           text=label, 
                           tool_tip_text=tt_text,
                           manager=self.manager, 
                           container=self._panel)
            self._weap_btns.append(btn)
            self._weap_keys.append(wkey)

    def _parse_qty(self) -> int:
        if self._qty_entry is None: return 1
        try: n = int(self._qty_entry.get_text())
        except (ValueError, TypeError): n = 1
        return max(1, min(20, n))

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._build()

    def resize(self, surface: pygame.Surface, w: int, h: int) -> None:
        self._win   = surface
        self._win_w = w
        self._win_h = h
        self._build()

    def process_events(self, event: pygame.event.Event) -> dict:
        self.manager.process_events(event)

        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            if event.ui_element == self._fow_btn: return {"type": "toggle_fow"}
            if hasattr(self, "_auto_engage_btn") and event.ui_element == self._auto_engage_btn: return {"type": "toggle_auto_engage"}
            if hasattr(self, "_roe_btn") and event.ui_element == self._roe_btn: return {"type": "toggle_roe"}
            if hasattr(self, "_ecm_btn") and event.ui_element == self._ecm_btn: return {"type": "toggle_ecm"}
            if hasattr(self, "_assign_cap_btn") and event.ui_element == self._assign_cap_btn: return {"type": "assign_cap"}
            if hasattr(self, "_clear_msn_btn") and event.ui_element == self._clear_msn_btn: return {"type": "clear_mission"}
            
            if self._mode == "setup":
                if event.ui_element == getattr(self, "_save_deploy_btn", None): return {"type": "save_deployment"}
                if event.ui_element == getattr(self, "_load_deploy_btn", None): return {"type": "load_deployment"}
                if event.ui_element == self._place_btn:
                    sel = (self._roster_list.get_single_selection() if self._roster_list else None)
                    if sel and sel in self._roster_items:
                        key = self._roster_keys[self._roster_items.index(sel)]
                        if key.startswith(self._DIVIDER_PREFIX): return {"type": "place_unit_no_selection"}
                        return {"type": "place_unit", "platform_key": key, "quantity": self._parse_qty()}
                    return {"type": "place_unit_no_selection"}
                if event.ui_element == self._remove_btn: return {"type": "remove_selected"}
                if event.ui_element == self._clear_btn: return {"type": "clear_blue"}
                if event.ui_element == self._start_btn: return {"type": "start_sim"}

            if self._mode == "combat":
                if hasattr(self, "_climb_5k_btn"):
                    if event.ui_element == self._climb_5k_btn: return {"type": "change_alt", "delta": 5000}
                    if event.ui_element == self._climb_1k_btn: return {"type": "change_alt", "delta": 1000}
                    if event.ui_element == self._climb_500_btn: return {"type": "change_alt", "delta": 500}
                    if event.ui_element == self._dive_5k_btn: return {"type": "change_alt", "delta": -5000}
                    if event.ui_element == self._dive_1k_btn: return {"type": "change_alt", "delta": -1000}
                    if event.ui_element == self._dive_500_btn: return {"type": "change_alt", "delta": -500}

                for i, btn in enumerate(self._salvo_btns):
                    if event.ui_element == btn:
                        self.salvo_mode = self._salvo_modes[i]
                        self.refresh_salvo_buttons()
                        return {"type": "salvo_change"}

                for i, btn in enumerate(self._speed_btns):
                    if event.ui_element == btn: return {"type": "speed_change", "speed_idx": i}
                
                for i, btn in enumerate(self._weap_btns):
                    if event.ui_element == btn: return {"type": "weapon_select", "weapon_key": self._weap_keys[i]}
        return {}

    def update(self, time_delta: float, sim: Optional[SimulationEngine], selected: Optional[Unit], placing_type: Optional[str] = None, placing_remaining: int = 0, show_all_enemies: bool = False, blue_contacts: dict | None = None) -> None:
        if self._mode == "setup":
            if self._setup_info:
                if placing_type:
                    p = self._db.platforms.get(placing_type)
                    pname = p.display_name if p else placing_type
                    self._setup_info.set_text(f"<b>PLACING:</b> {pname}<br><b>{placing_remaining} remaining</b><br>Left-click map to place unit.<br>Press ESC to cancel.")
                else:
                    placed = len(sim.blue_units()) if sim else 0
                    sel_str = ""
                    if self._roster_list:
                        s = self._roster_list.get_single_selection()
                        if s and s in self._roster_items:
                            key = self._roster_keys[self._roster_items.index(s)]
                            p   = self._db.platforms.get(key)
                            if p:
                                type_labels = {"fighter":"Fighter","attacker":"Attack","helicopter":"Helicopter","tank":"MBT","ifv":"IFV","apc":"APC","recon":"Recon","tank_destroyer":"Tank Destroyer","sam":"Air Defense"}
                                tl = type_labels.get(p.unit_type, p.unit_type.upper())
                                spd_lbl = "km/h" if p.unit_type not in ("tank","ifv","apc","recon","tank_destroyer","sam") else "km/h (road)"
                                sel_str = f"<b>Selected:</b> {p.display_name}<br><b>Type:</b> {tl}  ×{p.fleet_count} in service<br>Spd {p.speed_kmh} {spd_lbl}  Detect {p.radar_range_km} km<br>ECM {int(p.ecm_rating*100)}%<br><br>"
                    self._setup_info.set_text(f"<b>DEPLOYMENT PHASE</b><br>Blue units placed: <b>{placed}</b><br><br>{sel_str}Select type → Place on Map<br>Right-click unit to remove")
        else:  
            if sim is None: return

            if selected and selected.alive:
                p = selected.platform
                wp = len(selected.waypoints)

                is_blue_air = selected.side == "Blue" and p.unit_type in ("fighter", "attacker", "helicopter")
                alt_btns = [
                    getattr(self, "_climb_5k_btn", None), getattr(self, "_climb_1k_btn", None), getattr(self, "_climb_500_btn", None),
                    getattr(self, "_dive_5k_btn", None), getattr(self, "_dive_1k_btn", None), getattr(self, "_dive_500_btn", None)
                ]
                for btn in alt_btns:
                    if btn: btn.show() if is_blue_air else btn.hide()

                if getattr(self, "_assign_cap_btn", None):
                    self._assign_cap_btn.show() if is_blue_air else self._assign_cap_btn.hide()
                    self._clear_msn_btn.show() if is_blue_air else self._clear_msn_btn.hide()

                is_blue_armed = selected.side == "Blue" and len(p.available_weapons) > 0
                if getattr(self, "_auto_engage_btn", None):
                    if is_blue_armed:
                        self._auto_engage_btn.show()
                        self._roe_btn.show()
                        self._auto_engage_btn.set_text(f"AUTO: {'ON' if getattr(selected, 'auto_engage', False) else 'OFF'}")
                        self._roe_btn.set_text(f"ROE: {selected.roe}")
                    else:
                        self._auto_engage_btn.hide()
                        self._roe_btn.hide()
                        
                has_ecm = selected.side == "Blue" and p.ecm_rating > 0
                if getattr(self, "_ecm_btn", None):
                    if has_ecm:
                        self._ecm_btn.show()
                        self._ecm_btn.set_text(f"ECM: {'ACTV' if selected.is_jamming else 'PASS'}")
                    else:
                        self._ecm_btn.hide()

                alt_display = f"{int(selected.altitude_ft):,} ft"
                if int(selected.target_altitude_ft) != int(selected.altitude_ft):
                    alt_display += f" <font color='#FFAA00'>(→{int(selected.target_altitude_ft):,})</font>"

                fuel_pct = (selected.fuel_kg / p.fuel_capacity_kg) * 100 if p.fuel_capacity_kg > 0 else 0
                fuel_col = "#FF4444" if fuel_pct < 20 else "#FFAA00" if fuel_pct < 50 else "#FFFFFF"
                
                hp_pct = int(selected.hp * 100)
                hp_col = "#FF4444" if hp_pct <= 25 else "#FFAA00" if hp_pct <= 50 else "#FFFF00" if hp_pct <= 75 else "#44FF44"

                contacts = blue_contacts or {}
                contact = contacts.get(selected.uid) if selected.side == "Red" else None

                if selected.side == "Red" and contact is not None:
                    cls = contact.classification
                    cls_col = {"FAINT": "#888888", "PROBABLE": "#DCA03C", "CONFIRMED": "#FF4444"}.get(cls, "#FFFFFF")
                    if cls == "FAINT":
                        self._nav_box.set_text(f"<b>CONTACT</b>  <font color='{cls_col}'>{cls}</font><br><b>Pos:</b> {contact.lat:.3f}°N  {contact.lon:.3f}°E<br><b>Type:</b> unknown<br><b>Side:</b> unknown")
                    elif cls == "PROBABLE":
                        self._nav_box.set_text(f"<b>CONTACT</b>  <font color='{cls_col}'>{cls}</font><br><b>Pos:</b> {contact.lat:.3f}°N  {contact.lon:.3f}°E<br><b>Type:</b> {contact.unit_type or 'unknown'}<br><b>Alt:</b> {int(contact.altitude_ft):,} ft<br><b>Side:</b> unknown")
                    else:  
                        msn_str = f"<b>Mission:</b> {selected.mission.mission_type}" if selected.mission else "<b>Mission:</b> NONE"
                        self._nav_box.set_text(f"<b>CONTACT — {selected.callsign}</b>  <font color='{cls_col}'>{cls}</font><br><b>Type:</b> {p.display_name}<br><b>HP:</b> <font color='{hp_col}'>{hp_pct}% ({selected.damage_state})</font> {msn_str}<br><b>Pos:</b> {selected.lat:.3f}°N  {selected.lon:.3f}°E<br><b>Alt:</b> {alt_display}  <b>Spd:</b> {p.speed_kmh:,} km/h<br><b>RCS:</b> {p.rcs_m2} m²  <b>ECM:</b> {int(p.ecm_rating*100)}% ({'ACTIVE' if selected.is_jamming else 'PASSIVE'})")
                elif selected.side == "Blue":
                    msn_str = f"<b>Mission:</b> {selected.mission.mission_type} ({selected.mission.name})" if selected.mission else "<b>Mission:</b> NONE"
                    self._nav_box.set_text(f"<b>{selected.callsign}</b>  [Blue]<br><b>Type:</b> {p.display_name}<br><b>HP:</b> <font color='{hp_col}'>{hp_pct}% ({selected.damage_state})</font>  <b>Fuel:</b> <font color='{fuel_col}'>{int(fuel_pct)}%</font> ({int(selected.fuel_kg)} kg)<br>{msn_str}<br><b>Spd:</b> {p.speed_kmh:,} km/h  <b>Alt:</b> {alt_display}<br><b>Radar:</b> {p.radar_range_km} km  <b>ECM:</b> {int(p.ecm_rating*100)}% ({'ACTIVE' if selected.is_jamming else 'PASSIVE'})<br><b>Chaff/Flare:</b> {selected.chaff} / {selected.flare}<br><b>HDG:</b> {selected.heading:05.1f}°  <b>Pos:</b> {selected.lat:.3f}°N  {selected.lon:.3f}°E<br><b>Route:</b> {wp} wp{'s' if wp!=1 else ''}")
                else:
                    self._nav_box.set_text(f"<b>{selected.callsign}</b>  [Red]<br><b>Type:</b> {p.display_name}<br><b>Not currently tracked</b>")
            else:
                alt_btns = [
                    getattr(self, "_climb_5k_btn", None), getattr(self, "_climb_1k_btn", None), getattr(self, "_climb_500_btn", None),
                    getattr(self, "_dive_5k_btn", None), getattr(self, "_dive_1k_btn", None), getattr(self, "_dive_500_btn", None)
                ]
                for btn in alt_btns:
                    if btn: btn.hide()
                    
                if getattr(self, "_auto_engage_btn", None): self._auto_engage_btn.hide()
                if getattr(self, "_roe_btn", None): self._roe_btn.hide()
                if getattr(self, "_ecm_btn", None): self._ecm_btn.hide()
                if getattr(self, "_assign_cap_btn", None): self._assign_cap_btn.hide()
                if getattr(self, "_clear_msn_btn", None): self._clear_msn_btn.hide()
                
                t  = SimulationEngine._fmt_time(sim.game_time)
                cx = "PAUSED" if sim.paused else f"{sim.time_compression}×"
                self._nav_box.set_text(f"<b>TACTICAL DISPLAY</b><br><b>Time:</b> {t}  <b>Speed:</b> {cx}<br><b>Blue:</b> {len(sim.blue_units())} units  <b>Red:</b> {len(sim.red_units())} units<br><b>Missiles:</b> {len(sim.missiles)} in flight<br><br>Left-click unit to select<br>Right-click enemy to fire<br>Right-click map to waypoint")

            if len(sim.event_log) != self._last_log_len:
                self._last_log_len = len(sim.event_log)
                recent = list(reversed(list(sim.event_log)[-6:]))
                self._log_box.set_text("<br>".join(f'<font color="#90D090">› {e}</font>' for e in recent))

        if self._fow_btn: self._fow_btn.set_text(f"FOG OF WAR: {'OFF' if show_all_enemies else 'ON'}")

        self.manager.update(time_delta)

    def draw(self) -> None: self.manager.draw_ui(self._win)
    @property
    def active_speed_idx(self) -> int: return self._speed_idx
    @property
    def mode(self) -> str: return self._mode

    def get_roster_selection(self) -> Optional[str]:
        if self._roster_list is None: return None
        sel = self._roster_list.get_single_selection()
        if sel and sel in self._roster_items: return self._roster_keys[self._roster_items.index(sel)]
        return None