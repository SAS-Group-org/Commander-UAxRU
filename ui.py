# ui.py — GameUI (setup mode + combat mode)

from __future__ import annotations
from typing import Optional

import pygame
import pygame_gui
from pygame_gui.elements import (
    UIButton, UIPanel, UITextBox, UISelectionList, UILabel,
    UITextEntryLine, UIWindow, UIHorizontalSlider, UIScrollingContainer
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
        self._last_parked_count = -1
        self._bgm_volume = 0.4

        self._roster_items: list[str] = []   
        self._roster_keys:  list[str] = []   

        self.manager:       pygame_gui.UIManager = None  # type: ignore
        self._panel:        UIPanel   = None             # type: ignore
        
        self._roster_list:  UISelectionList = None       # type: ignore
        self._setup_info:   UITextBox       = None       # type: ignore
        self._place_btn:    UIButton        = None       # type: ignore
        self._remove_btn:   UIButton        = None       # type: ignore
        self._clear_btn:    UIButton        = None       # type: ignore
        self._auto_deploy_btn: UIButton     = None       # type: ignore
        self._save_deploy_btn: UIButton     = None       # type: ignore
        self._load_deploy_btn: UIButton     = None       # type: ignore
        self._start_btn:    UIButton        = None       # type: ignore
        self._qty_entry:    UITextEntryLine = None       # type: ignore
        
        self._nav_box:      UITextBox      = None        # type: ignore
        self._log_box:      UITextBox      = None        # type: ignore
        
        self._settings_btn:    UIButton           = None # type: ignore
        self._settings_window: UIWindow           = None # type: ignore
        self._fow_btn:         UIButton           = None # type: ignore
        self._air_lbl_btn:     UIButton           = None # type: ignore
        self._gnd_lbl_btn:     UIButton           = None # type: ignore
        self._radar_rings_btn: UIButton           = None # type: ignore
        self._bgm_btn:         UIButton           = None # type: ignore
        self._bgm_vol_slider:  UIHorizontalSlider = None # type: ignore
        
        self._strike_pkg_btn:  UIButton           = None # type: ignore
        self._pkg_window:      UIWindow           = None # type: ignore
        self._pkg_launch_btn:  UIButton           = None # type: ignore
        self._pkg_tot_entry:   UITextEntryLine    = None # type: ignore
        self._pkg_ui_map:      dict               = {}   
        self._pkg_state:       dict               = {}   
        
        self._wp_alt_window:   UIWindow           = None # type: ignore
        self._wp_alt_entry:    UITextEntryLine    = None # type: ignore
        self._wp_alt_ok_btn:   UIButton           = None # type: ignore
        self._pending_wp_coords: tuple[float, float] | None = None

        self.aar_window:       UIWindow           = None # type: ignore
        self.aar_restart_btn:  UIButton           = None # type: ignore
        
        self._reinforce_btn: UIButton      = None        # type: ignore
        self._restart_btn:   UIButton      = None        # type: ignore
        
        self._auto_engage_btn: UIButton    = None        # type: ignore
        self._roe_btn:         UIButton    = None        # type: ignore
        self._emcon_btn:       UIButton    = None        # type: ignore
        
        self._wra_rng_btn:     UIButton    = None        # type: ignore
        self._wra_qty_btn:     UIButton    = None        # type: ignore
        
        self._assign_cap_btn:  UIButton    = None        # type: ignore
        self._clear_msn_btn:   UIButton    = None        # type: ignore
        self._form_btn:        UIButton    = None        # type: ignore
        self._doc_btn:         UIButton    = None        # type: ignore
        
        self._cycle_msn_btn:   UIButton    = None        # type: ignore
        self._cycle_ldt_btn:   UIButton    = None        # type: ignore
        self._launch_btn:      UIButton    = None        # type: ignore

        self._climb_5k_btn:    UIButton    = None        # type: ignore
        self._climb_1k_btn:    UIButton    = None        # type: ignore
        self._climb_500_btn:   UIButton    = None        # type: ignore
        self._dive_5k_btn:     UIButton    = None        # type: ignore
        self._dive_1k_btn:     UIButton    = None        # type: ignore
        self._dive_500_btn:    UIButton    = None        # type: ignore
        
        self._speed_btns:   list[UIButton] = []
        self._weap_btns:    list[UIButton] = []
        self._weap_keys:    list[str]      = []
        self._weap_scroll_container: UIScrollingContainer = None # type: ignore
        
        self.salvo_mode = "1"
        self._salvo_modes = ["1", "2", "4", "SLS"]
        self._salvo_btns:   list[UIButton] = []

        self._build_roster_data()
        self._build()

    _CATEGORIES = [
        ("AWACS & C2",        ("awacs",)),
        ("FIXED-WING",        ("fighter", "attacker")),
        ("ROTARY WING",       ("helicopter",)),
        ("LOGISTICS",         ("airbase",)),
        ("AIR DEFENSE",       ("sam",)),
        ("ARTILLERY",         ("artillery",)),
        ("ARMOR (MBT)",       ("tank",)),
        ("INFANTRY FV",       ("ifv",)),
        ("ARMORED PC",        ("apc",)),
        ("RECON",             ("recon",)),
        ("TANK DESTROYERS",   ("tank_destroyer",)),
    ]

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
            
            self._roster_items.append(f"▼ {header}")
            self._roster_keys.append(f"HEADER:{header}")   
            
            for key, p in group:
                label = f"      • {p.display_name} (×{p.fleet_count})"
                self._roster_items.append(label)
                self._roster_keys.append(key)

    def _col_widths(self) -> tuple[int, int, int]:
        avail = self._win_w - _PAD * 4
        if avail < 860:
            return int(avail * 0.36), int(avail * 0.41), int(avail * 0.23)
        
        c1 = max(310, int(avail * 0.30))
        c2 = max(350, int(avail * 0.40))
        c3 = max(180, avail - c1 - c2)
        return c1, c2, c3

    def _build(self) -> None:
        self._settings_window = None
        self._pkg_window = None
        self._wp_alt_window = None
        self.aar_window = None
        self._pkg_ui_map.clear()
        self._pkg_state.clear()
        self._pkg_tot_entry = None
        self._weap_scroll_container = None
        
        self.manager = pygame_gui.UIManager((self._win_w, self._win_h), "theme.json")
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

        info_h = max(20, panel_h - (_BTN_H + _BTN_PAD) * 6 - _PAD * 3)
        self._setup_info = UITextBox(
            html_text=(
                "<b>UNIT DEPLOYMENT</b><br>"
                "Select a unit type, set quantity,<br>"
                "then click <b>PLACE ON MAP</b>.<br>"
                "Each left-click places one unit.<br>"
                "Right-click a placed unit to remove."
            ),
            relative_rect=pygame.Rect(ctrl_x, _PAD, ctrl_w, info_h),
            manager=self.manager, container=self._panel,
        )

        btn_y = _PAD + info_h + _PAD
        _LBL_W, _ENTRY_W, _GAP = 45, 52, _PAD
        place_w  = ctrl_w - _LBL_W - _ENTRY_W - _GAP * 2

        UILabel(relative_rect=pygame.Rect(ctrl_x, btn_y, _LBL_W, _BTN_H), text="QTY:", manager=self.manager, container=self._panel)
        self._qty_entry = UITextEntryLine(relative_rect=pygame.Rect(ctrl_x + _LBL_W + _GAP, btn_y, _ENTRY_W, _BTN_H), manager=self.manager, container=self._panel)
        self._qty_entry.set_text("1")
        self._qty_entry.set_allowed_characters("numbers")

        self._place_btn = UIButton(relative_rect=pygame.Rect(ctrl_x + _LBL_W + _ENTRY_W + _GAP * 2, btn_y, place_w, _BTN_H), text="PLACE ON MAP", manager=self.manager, container=self._panel)
        btn_y += _BTN_H + _BTN_PAD

        btn_w_half = (ctrl_w - _BTN_PAD) // 2

        self._remove_btn = UIButton(relative_rect=pygame.Rect(ctrl_x, btn_y, btn_w_half, _BTN_H), text="REMOVE SELECTED", manager=self.manager, container=self._panel)
        self._clear_btn = UIButton(relative_rect=pygame.Rect(ctrl_x + btn_w_half + _BTN_PAD, btn_y, btn_w_half, _BTN_H), text="CLEAR BLUE", manager=self.manager, container=self._panel)
        btn_y += _BTN_H + _BTN_PAD

        self._save_deploy_btn = UIButton(relative_rect=pygame.Rect(ctrl_x, btn_y, btn_w_half, _BTN_H), text="SAVE DEPLOYMENT", manager=self.manager, container=self._panel)
        self._load_deploy_btn = UIButton(relative_rect=pygame.Rect(ctrl_x + btn_w_half + _BTN_PAD, btn_y, btn_w_half, _BTN_H), text="LOAD DEPLOYMENT", manager=self.manager, container=self._panel)
        btn_y += _BTN_H + _BTN_PAD

        self._auto_deploy_btn = UIButton(relative_rect=pygame.Rect(ctrl_x, btn_y, btn_w_half, _BTN_H), text="AUTO DEPLOY BLUE", manager=self.manager, container=self._panel)
        self._settings_btn = UIButton(relative_rect=pygame.Rect(ctrl_x + btn_w_half + _BTN_PAD, btn_y, btn_w_half, _BTN_H), text="SETTINGS", manager=self.manager, container=self._panel)
        btn_y += _BTN_H + _BTN_PAD

        self._start_btn = UIButton(relative_rect=pygame.Rect(ctrl_x, btn_y, ctrl_w, _BTN_H), text="▶ START SIMULATION", manager=self.manager, container=self._panel)

    def _build_combat(self, panel_h: int) -> None:
        c1, c2, c3 = self._col_widths()
        col1_x, col2_x, col3_x = _PAD, _PAD + c1 + _PAD, _PAD + c1 + _PAD + c2 + _PAD

        row6_y = panel_h - _PAD - _BTN_H
        row5_y = row6_y - _BTN_PAD - _BTN_H
        row4_y = row5_y - _BTN_PAD - _BTN_H
        row3_y = row4_y - _BTN_PAD - _BTN_H
        row2_y = row3_y - _BTN_PAD - _BTN_H
        row1_y = row2_y - _BTN_PAD - _BTN_H
        nav_h  = row1_y - (_PAD * 2)

        self._nav_box = UITextBox(
            html_text="<b>STANDBY</b>",
            relative_rect=pygame.Rect(col1_x, _PAD, c1, nav_h),
            manager=self.manager, container=self._panel,
        )
        
        btn_w_third = int((c1 - _BTN_PAD * 2) / 3) - 1
        btn_w_half  = int((c1 - _BTN_PAD) / 2)
        btn_w_qtr   = int((c1 - _BTN_PAD * 3) / 4)

        self._climb_5k_btn = UIButton(relative_rect=pygame.Rect(col1_x, row1_y, btn_w_third, _BTN_H), text="▲ +5K FT", tool_tip_text="Climb 5,000 feet", manager=self.manager, container=self._panel)
        self._climb_1k_btn = UIButton(relative_rect=pygame.Rect(col1_x + btn_w_third + _BTN_PAD, row1_y, btn_w_third, _BTN_H), text="▲ +1K FT", tool_tip_text="Climb 1,000 feet", manager=self.manager, container=self._panel)
        self._climb_500_btn = UIButton(relative_rect=pygame.Rect(col1_x + (btn_w_third + _BTN_PAD)*2, row1_y, btn_w_third, _BTN_H), text="▲ +500 FT", tool_tip_text="Climb 500 feet", manager=self.manager, container=self._panel)

        self._dive_5k_btn = UIButton(relative_rect=pygame.Rect(col1_x, row2_y, btn_w_third, _BTN_H), text="▼ -5K FT", tool_tip_text="Dive 5,000 feet", manager=self.manager, container=self._panel)
        self._dive_1k_btn = UIButton(relative_rect=pygame.Rect(col1_x + btn_w_third + _BTN_PAD, row2_y, btn_w_third, _BTN_H), text="▼ -1K FT", tool_tip_text="Dive 1,000 feet", manager=self.manager, container=self._panel)
        self._dive_500_btn = UIButton(relative_rect=pygame.Rect(col1_x + (btn_w_third + _BTN_PAD)*2, row2_y, btn_w_third, _BTN_H), text="▼ -500 FT", tool_tip_text="Dive 500 feet", manager=self.manager, container=self._panel)

        self._auto_engage_btn = UIButton(relative_rect=pygame.Rect(col1_x, row3_y, btn_w_third, _BTN_H), text="AUTO", manager=self.manager, container=self._panel)
        self._roe_btn         = UIButton(relative_rect=pygame.Rect(col1_x + btn_w_third + _BTN_PAD, row3_y, btn_w_third, _BTN_H), text="ROE", manager=self.manager, container=self._panel)
        self._emcon_btn       = UIButton(relative_rect=pygame.Rect(col1_x + (btn_w_third + _BTN_PAD)*2, row3_y, btn_w_third, _BTN_H), text="EMCON", manager=self.manager, container=self._panel)

        self._wra_rng_btn     = UIButton(relative_rect=pygame.Rect(col1_x, row4_y, btn_w_half, _BTN_H), text="WRA RNG: 90%", tool_tip_text="AI Weapon Release Range", manager=self.manager, container=self._panel)
        self._wra_qty_btn     = UIButton(relative_rect=pygame.Rect(col1_x + btn_w_half + _BTN_PAD, row4_y, btn_w_half, _BTN_H), text="WRA QTY: 1", tool_tip_text="AI Weapon Release Quantity", manager=self.manager, container=self._panel)

        self._assign_cap_btn = UIButton(relative_rect=pygame.Rect(col1_x, row5_y, btn_w_qtr, _BTN_H), text="CAP", tool_tip_text="Assign CAP", manager=self.manager, container=self._panel)
        self._clear_msn_btn  = UIButton(relative_rect=pygame.Rect(col1_x + btn_w_qtr + _BTN_PAD, row5_y, btn_w_qtr, _BTN_H), text="CLR", tool_tip_text="Clear Mission", manager=self.manager, container=self._panel)
        self._form_btn       = UIButton(relative_rect=pygame.Rect(col1_x + (btn_w_qtr + _BTN_PAD)*2, row5_y, btn_w_qtr, _BTN_H), text="WEDGE", tool_tip_text="Flight Formation", manager=self.manager, container=self._panel)
        self._doc_btn        = UIButton(relative_rect=pygame.Rect(col1_x + (btn_w_qtr + _BTN_PAD)*3, row5_y, btn_w_qtr, _BTN_H), text="STD", tool_tip_text="Flight Doctrine", manager=self.manager, container=self._panel)

        self._cycle_msn_btn = UIButton(relative_rect=pygame.Rect(col1_x, row6_y, btn_w_third, _BTN_H), text="MISSION: CAP", manager=self.manager, container=self._panel)
        self._cycle_ldt_btn = UIButton(relative_rect=pygame.Rect(col1_x + btn_w_third + _BTN_PAD, row6_y, btn_w_third, _BTN_H), text="LOADOUT: DEF", manager=self.manager, container=self._panel)
        self._launch_btn    = UIButton(relative_rect=pygame.Rect(col1_x + (btn_w_third + _BTN_PAD)*2, row6_y, btn_w_third, _BTN_H), text="LAUNCH", manager=self.manager, container=self._panel)

        self._auto_engage_btn.hide()
        self._roe_btn.hide()
        self._emcon_btn.hide()
        self._wra_rng_btn.hide()
        self._wra_qty_btn.hide()
        self._assign_cap_btn.hide()
        self._clear_msn_btn.hide()
        self._form_btn.hide()
        self._doc_btn.hide()
        self._cycle_msn_btn.hide()
        self._cycle_ldt_btn.hide()
        self._launch_btn.hide()

        UILabel(relative_rect=pygame.Rect(col2_x, _PAD, c2, 20), text="ARMAMENTS / HANGAR", manager=self.manager, container=self._panel)

        salvo_y = _PAD + 22
        btn_w_salvo = (c2 - _BTN_PAD * 3) // 4
        for i, smode in enumerate(self._salvo_modes):
            bx = col2_x + i * (btn_w_salvo + _BTN_PAD)
            btn = UIButton(relative_rect=pygame.Rect(bx, salvo_y, btn_w_salvo, _BTN_H), 
                           text=smode, manager=self.manager, container=self._panel)
            self._salvo_btns.append(btn)
        self.refresh_salvo_buttons()
        
        self._strike_pkg_btn = UIButton(relative_rect=pygame.Rect(col2_x, salvo_y, c2, _BTN_H), text="CREATE STRIKE PACKAGE", manager=self.manager, container=self._panel)
        self._strike_pkg_btn.hide()

        # FIX: The dedicated scrolling container for dynamic weapons/hangar lists
        start_y = salvo_y + _BTN_H + _BTN_PAD
        self._weap_scroll_container = UIScrollingContainer(
            relative_rect=pygame.Rect(col2_x, start_y, c2, panel_h - start_y - _PAD),
            manager=self.manager,
            container=self._panel
        )

        n       = len(TIME_SPEED_LABELS)
        btn_w   = max(44, (c3 - _BTN_PAD * (n - 1)) // n)
        for i, label in enumerate(TIME_SPEED_LABELS):
            bx = col3_x + i * (btn_w + _BTN_PAD)
            self._speed_btns.append(UIButton(
                relative_rect=pygame.Rect(bx, _PAD, btn_w, _BTN_H),
                text=label.upper(), manager=self.manager, container=self._panel,
            ))

        btn_y = _PAD + _BTN_H + _BTN_PAD
        btn_w_half_col3 = (c3 - _BTN_PAD) // 2
        
        self._reinforce_btn = UIButton(relative_rect=pygame.Rect(col3_x, btn_y, btn_w_half_col3, _BTN_H), text="REINFORCE", tool_tip_text="Pause and open deployment menu", manager=self.manager, container=self._panel)
        self._restart_btn = UIButton(relative_rect=pygame.Rect(col3_x + btn_w_half_col3 + _BTN_PAD, btn_y, btn_w_half_col3, _BTN_H), text="END GAME", tool_tip_text="End scenario and view AAR", manager=self.manager, container=self._panel)

        set_y = btn_y + _BTN_H + _BTN_PAD
        self._settings_btn = UIButton(relative_rect=pygame.Rect(col3_x, set_y, c3, _BTN_H), text="SETTINGS", manager=self.manager, container=self._panel)

        log_y = set_y + _BTN_H + _BTN_PAD
        self._log_box = UITextBox(
            html_text="<b>EVENT LOG</b>",
            relative_rect=pygame.Rect(col3_x, log_y, c3, panel_h - log_y - _PAD),
            manager=self.manager, container=self._panel,
        )

    def _create_settings_window(self) -> None:
        if getattr(self, "_settings_window", None) is not None:
            return
            
        w, h = 340, 340
        x = (self._win_w - w) // 2
        y = (self._win_h - h) // 2
        
        self._settings_window = UIWindow(
            rect=pygame.Rect(x, y, w, h),
            manager=self.manager,
            window_display_title="GAME SETTINGS",
            object_id="#settings_window"
        )
        
        lbl_w = 180
        btn_w = 60
        btn_x = w - btn_w - 40 
        by = 10
        row_pad = 42
        
        UILabel(relative_rect=pygame.Rect(10, by, lbl_w, 30), text="AIR LABELS", manager=self.manager, container=self._settings_window, object_id="#settings_label")
        self._air_lbl_btn = UIButton(relative_rect=pygame.Rect(btn_x, by, btn_w, 30), text="ON", manager=self.manager, container=self._settings_window, object_id="#toggle_btn")
        by += row_pad
        
        UILabel(relative_rect=pygame.Rect(10, by, lbl_w, 30), text="GROUND LABELS", manager=self.manager, container=self._settings_window, object_id="#settings_label")
        self._gnd_lbl_btn = UIButton(relative_rect=pygame.Rect(btn_x, by, btn_w, 30), text="ON", manager=self.manager, container=self._settings_window, object_id="#toggle_btn")
        by += row_pad
        
        UILabel(relative_rect=pygame.Rect(10, by, lbl_w, 30), text="FOG OF WAR", manager=self.manager, container=self._settings_window, object_id="#settings_label")
        self._fow_btn = UIButton(relative_rect=pygame.Rect(btn_x, by, btn_w, 30), text="ON", manager=self.manager, container=self._settings_window, object_id="#toggle_btn")
        by += row_pad
        
        UILabel(relative_rect=pygame.Rect(10, by, lbl_w, 30), text="RADAR CIRCLES", manager=self.manager, container=self._settings_window, object_id="#settings_label")
        self._radar_rings_btn = UIButton(relative_rect=pygame.Rect(btn_x, by, btn_w, 30), text="ON", manager=self.manager, container=self._settings_window, object_id="#toggle_btn")
        by += row_pad
        
        UILabel(relative_rect=pygame.Rect(10, by, lbl_w, 30), text="MUSIC ON / OFF", manager=self.manager, container=self._settings_window, object_id="#settings_label")
        self._bgm_btn = UIButton(relative_rect=pygame.Rect(btn_x, by, btn_w, 30), text="ON", manager=self.manager, container=self._settings_window, object_id="#toggle_btn")
        by += row_pad
        
        UILabel(relative_rect=pygame.Rect(10, by, lbl_w, 30), text="MUSIC VOLUME", manager=self.manager, container=self._settings_window, object_id="#settings_label")
        self._bgm_vol_slider = UIHorizontalSlider(
            relative_rect=pygame.Rect(btn_x - 50, by + 5, btn_w + 50, 20),
            start_value=self._bgm_volume,
            value_range=(0.0, 1.0),
            manager=self.manager,
            container=self._settings_window
        )

    def _create_strike_package_window(self, base_unit: Unit, sim: SimulationEngine) -> None:
        if getattr(self, "_pkg_window", None) is not None:
            return
            
        w, h = 580, 460
        x = (self._win_w - w) // 2
        y = (self._win_h - h) // 2
        
        self._pkg_window = UIWindow(
            rect=pygame.Rect(x, y, w, h),
            manager=self.manager,
            window_display_title="STRIKE PACKAGE PLANNER"
        )
        
        self._pkg_ui_map.clear()
        self._pkg_state.clear()
        self._pkg_tot_entry = None
        
        parked = [u for u in sim.units if u.home_uid == base_unit.uid and u.duty_state == "READY" and u.alive]
        
        if not parked:
            UILabel(relative_rect=pygame.Rect(10, 50, w-50, 30), text="NO READY AIRCRAFT AT THIS BASE", manager=self.manager, container=self._pkg_window)
            return

        scroll_rect = pygame.Rect(10, 10, w - 50, h - 130)
        scroll_container = UIScrollingContainer(relative_rect=scroll_rect, manager=self.manager, container=self._pkg_window)
        
        lbl_w = 160
        inc_w = 80
        msn_w = 110
        ldt_w = 140
        
        for i, u in enumerate(parked):
            self._pkg_state[u.uid] = {"included": False, "role": "STRIKE", "loadout": "DEFAULT"}
            row_y = i * 40
            
            UILabel(relative_rect=pygame.Rect(5, row_y, lbl_w, 32), text=f"{u.callsign[:12]} ({u.platform.unit_type[:3].upper()})", manager=self.manager, container=scroll_container)
            
            btn_inc = UIButton(relative_rect=pygame.Rect(5 + lbl_w, row_y, inc_w, 32), text="[ ] INC", manager=self.manager, container=scroll_container)
            btn_msn = UIButton(relative_rect=pygame.Rect(5 + lbl_w + inc_w + _PAD, row_y, msn_w, 32), text="MSN: STRIKE", manager=self.manager, container=scroll_container)
            btn_ldt = UIButton(relative_rect=pygame.Rect(5 + lbl_w + inc_w + msn_w + _PAD*2, row_y, ldt_w, 32), text="LDT: DEFAULT", manager=self.manager, container=scroll_container)
            
            self._pkg_ui_map[btn_inc] = (u.uid, "toggle_incl")
            self._pkg_ui_map[btn_msn] = (u.uid, "cycle_role")
            self._pkg_ui_map[btn_ldt] = (u.uid, "cycle_ldt")
            
        scroll_container.set_scrollable_area_dimensions((w - 70, len(parked) * 40))

        UILabel(relative_rect=pygame.Rect(10, h - 110, 320, 30), text="Time on Target (mins from now, 0 = ASAP):", manager=self.manager, container=self._pkg_window)
        self._pkg_tot_entry = UITextEntryLine(relative_rect=pygame.Rect(340, h - 110, 60, 30), manager=self.manager, container=self._pkg_window)
        self._pkg_tot_entry.set_text("0")
        self._pkg_tot_entry.set_allowed_characters("numbers")

        self._pkg_launch_btn = UIButton(
            relative_rect=pygame.Rect(10, h - 70, w - 50, 40),
            text="SET TARGET & LAUNCH PACKAGE",
            manager=self.manager,
            container=self._pkg_window
        )
        
    def create_waypoint_alt_window(self, lat: float, lon: float) -> None:
        if getattr(self, "_wp_alt_window", None) is not None:
            self._wp_alt_window.kill()
            
        w, h = 250, 150
        x = (self._win_w - w) // 2
        y = (self._win_h - h) // 2
        
        self._wp_alt_window = UIWindow(
            rect=pygame.Rect(x, y, w, h),
            manager=self.manager,
            window_display_title="WAYPOINT ALTITUDE",
            object_id="#wp_window"
        )
        
        self._pending_wp_coords = (lat, lon)
        
        UILabel(relative_rect=pygame.Rect(10, 10, 200, 30), text="Altitude (ft):", manager=self.manager, container=self._wp_alt_window)
        self._wp_alt_entry = UITextEntryLine(relative_rect=pygame.Rect(10, 40, 200, 30), manager=self.manager, container=self._wp_alt_window)
        self._wp_alt_entry.set_text("15000")
        self._wp_alt_entry.set_allowed_characters("numbers")
        
        self._wp_alt_ok_btn = UIButton(relative_rect=pygame.Rect(10, 80, 200, 30), text="ADD WAYPOINT", manager=self.manager, container=self._wp_alt_window)

    def create_aar_window(self, aar_data: dict) -> None:
        if getattr(self, "aar_window", None) is not None:
            return
            
        # FIX: Enlarged the AAR window so casualty reports do not get squished
        w, h = 800, 600
        x = (self._win_w - w) // 2
        y = (self._win_h - h) // 2

        window_rect = pygame.Rect((x, y), (w, h))

        self.aar_window = UIWindow(
            rect=window_rect,
            manager=self.manager,
            window_display_title="AFTER ACTION REPORT",
            resizable=False
        )

        winner = aar_data.get('winner', 'Draw')
        color = "#55AAFF" if winner == "Blue" else "#FF5555" if winner == "Red" else "#AAAAAA"
        
        summary_html = (
            f"<font size='5' color='{color}'><b>VICTORY: {winner.upper()}</b></font><br><br>"
            f"<font size='4'>"
            f"<b>Duration:</b> {aar_data.get('duration', '00:00:00')}<br>"
            f"<b>Blue Score:</b> {aar_data.get('score_blue', 0)} pts<br>"
            f"<b>Red Score:</b> {aar_data.get('score_red', 0)} pts"
            f"</font>"
        )

        UITextBox(
            html_text=summary_html,
            relative_rect=pygame.Rect((15, 15), (w - 60, 130)),
            manager=self.manager,
            container=self.aar_window
        )

        log_entries = aar_data.get("kill_log", [])
        log_html = "<br>".join(log_entries) if log_entries else "<i>No casualties recorded.</i>"
        
        # UITextBox natively scrolls when HTML text exceeds its height
        UITextBox(
            html_text=f"<font size='3'>{log_html}</font>",
            relative_rect=pygame.Rect((15, 155), (w - 60, h - 240)),
            manager=self.manager,
            container=self.aar_window
        )

        self.aar_restart_btn = UIButton(
            relative_rect=pygame.Rect(((w - 150) // 2, h - 70), (150, 40)),
            text="RESTART SCENARIO",
            manager=self.manager,
            container=self.aar_window,
            object_id="#aar_restart_btn"
        )

    def refresh_salvo_buttons(self) -> None:
        for i, btn in enumerate(self._salvo_btns):
            mode = self._salvo_modes[i]
            if mode == self.salvo_mode:
                btn.set_text(f"► {mode}")
            else:
                btn.set_text(mode)

    def rebuild_weapon_buttons(self, unit: Optional[Unit], sim: Optional[SimulationEngine] = None) -> None:
        for btn in self._weap_btns: btn.kill()
        self._weap_btns.clear()
        self._weap_keys.clear()

        if unit is None or self._mode != "combat":
            if getattr(self, "_weap_scroll_container", None):
                self._weap_scroll_container.set_scrollable_area_dimensions((10, 10))
            return

        _, c2, _ = self._col_widths()
        scroll_w = c2 - 24 # Reserve space for the scrollbar

        btn_idx = 0

        if unit.platform.unit_type == "airbase" and sim is not None:
            parked = [u for u in sim.units if u.home_uid == unit.uid and u.duty_state != "ACTIVE" and u.alive]
            for p_unit in parked:
                state_str = "READY" if p_unit.duty_state == "READY" else f"REARM {int(p_unit.duty_timer)}S"
                label = f"✈ {p_unit.callsign.upper()} ({state_str})"
                tt_text = f"<b>{p_unit.platform.display_name}</b><br>Role: {p_unit.platform.unit_type.upper()}<br>Loadout: {p_unit.loadout}"
                
                # FIX: Anchoring buttons inside the scroll container, starting at y=0 relative to the container
                btn = UIButton(relative_rect=pygame.Rect(0, btn_idx * (_WEAP_H + _BTN_PAD), scroll_w, _WEAP_H), 
                               text=label, tool_tip_text=tt_text, manager=self.manager, container=self._weap_scroll_container)
                if p_unit.duty_state != "READY":
                    btn.disable()
                self._weap_btns.append(btn)
                self._weap_keys.append(f"SELECT:{p_unit.uid}")
                btn_idx += 1
        else:
            for wkey, qty in unit.loadout.items():
                wdef     = self._db.weapons.get(wkey)
                name     = wdef.display_name if wdef else wkey
                rng_str  = f" ({wdef.range_km:.0f}KM)" if wdef and not wdef.is_gun else ""
                desc_str = f" - {wdef.description}" if wdef and wdef.description else ""
                
                is_sel   = (unit.selected_weapon == wkey)
                prefix   = "► " if is_sel else "   "
                label    = f"{prefix}{qty}× {name.upper()}{rng_str}{desc_str.upper()}"
                
                tt_text  = f"<b>{name}</b><br>{wdef.description}<br>Domain: {wdef.domain.upper()}<br>Speed: {wdef.speed_kmh} km/h" if wdef else ""
                
                btn = UIButton(relative_rect=pygame.Rect(0, btn_idx * (_WEAP_H + _BTN_PAD), scroll_w, _WEAP_H), 
                               text=label, tool_tip_text=tt_text, manager=self.manager, container=self._weap_scroll_container)
                self._weap_btns.append(btn)
                self._weap_keys.append(wkey)
                btn_idx += 1

        if getattr(self, "_weap_scroll_container", None):
            total_h = btn_idx * (_WEAP_H + _BTN_PAD)
            self._weap_scroll_container.set_scrollable_area_dimensions((scroll_w, max(10, total_h)))

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
        
        if event.type == pygame_gui.UI_WINDOW_CLOSE:
            if event.ui_element == getattr(self, "_settings_window", None):
                self._settings_window = None
                self._air_lbl_btn = None
                self._gnd_lbl_btn = None
                self._fow_btn = None
                self._radar_rings_btn = None
                self._bgm_btn = None
                self._bgm_vol_slider = None
            if event.ui_element == getattr(self, "_pkg_window", None):
                self._pkg_window = None
                self._pkg_ui_map.clear()
                self._pkg_tot_entry = None
            if event.ui_element == getattr(self, "aar_window", None):
                self.aar_window = None
                self.aar_restart_btn = None
            if event.ui_element == getattr(self, "_wp_alt_window", None):
                self._wp_alt_window = None
                self._pending_wp_coords = None
                
        if event.type == pygame_gui.UI_HORIZONTAL_SLIDER_MOVED:
            if event.ui_element == getattr(self, "_bgm_vol_slider", None):
                self._bgm_volume = event.value
                return {"type": "set_volume", "value": event.value}

        if event.type == pygame_gui.UI_BUTTON_PRESSED:
            if getattr(self, "aar_restart_btn", None) and event.ui_element == self.aar_restart_btn:
                self.aar_window.kill()
                self.aar_window = None
                return {"type": "restart_scenario"}
                
            if getattr(self, "_wp_alt_ok_btn", None) and event.ui_element == self._wp_alt_ok_btn:
                alt_str = self._wp_alt_entry.get_text()
                try: alt = float(alt_str)
                except ValueError: alt = 15000.0
                
                lat, lon = self._pending_wp_coords
                self._wp_alt_window.kill()
                self._wp_alt_window = None
                self._pending_wp_coords = None
                return {"type": "add_package_waypoint", "lat": lat, "lon": lon, "alt": alt}

            if event.ui_element in self._pkg_ui_map:
                uid, action = self._pkg_ui_map[event.ui_element]
                state = self._pkg_state[uid]
                if action == "toggle_incl":
                    state["included"] = not state["included"]
                    event.ui_element.set_text("[X] INC" if state["included"] else "[ ] INC")
                elif action == "cycle_role":
                    roles = ["CAP", "STRIKE", "SEAD"]
                    state["role"] = roles[(roles.index(state["role"]) + 1) % len(roles)]
                    event.ui_element.set_text(f"MSN: {state['role']}")
                elif action == "cycle_ldt":
                    ldts = ["DEFAULT", "A2A", "A2G", "SEAD"]
                    state["loadout"] = ldts[(ldts.index(state["loadout"]) + 1) % len(ldts)]
                    event.ui_element.set_text(f"LDT: {state['loadout']}")
                return {} 

            if getattr(self, "_pkg_launch_btn", None) and event.ui_element == self._pkg_launch_btn:
                if any(s["included"] for s in self._pkg_state.values()):
                    tot_str = self._pkg_tot_entry.get_text() if self._pkg_tot_entry else "0"
                    try: tot_mins = float(tot_str)
                    except ValueError: tot_mins = 0.0
                    
                    pkg_state_copy = dict(self._pkg_state)
                    self._pkg_window.kill()
                    self._pkg_window = None
                    self._pkg_ui_map.clear()
                    self._pkg_tot_entry = None
                    return {"type": "prep_launch_package", "state": pkg_state_copy, "tot_mins": tot_mins}
                return {} 

            if getattr(self, "_strike_pkg_btn", None) and event.ui_element == self._strike_pkg_btn:
                return {"type": "open_pkg_window"}

            if getattr(self, "_settings_btn", None) and event.ui_element == self._settings_btn:
                self._create_settings_window()
                return {}
            
            if event.ui_element == getattr(self, "_bgm_btn", None): return {"type": "toggle_bgm"}
            if event.ui_element == getattr(self, "_radar_rings_btn", None): return {"type": "toggle_radar_rings"}
            if event.ui_element == getattr(self, "_fow_btn", None): return {"type": "toggle_fow"}
            if event.ui_element == getattr(self, "_air_lbl_btn", None): return {"type": "toggle_air_labels"}
            if event.ui_element == getattr(self, "_gnd_lbl_btn", None): return {"type": "toggle_ground_labels"}
            
            if event.ui_element == getattr(self, "_auto_engage_btn", None): return {"type": "toggle_auto_engage"}
            if event.ui_element == getattr(self, "_roe_btn", None): return {"type": "toggle_roe"}
            if event.ui_element == getattr(self, "_emcon_btn", None): return {"type": "cycle_emcon"}
            if getattr(self, "_wra_rng_btn", None) and event.ui_element == self._wra_rng_btn: return {"type": "cycle_wra_rng"}
            if getattr(self, "_wra_qty_btn", None) and event.ui_element == self._wra_qty_btn: return {"type": "cycle_wra_qty"}
            
            if getattr(self, "_form_btn", None) and event.ui_element == self._form_btn: return {"type": "cycle_formation"}
            if getattr(self, "_doc_btn", None) and event.ui_element == self._doc_btn: return {"type": "cycle_doctrine"}
            if event.ui_element == getattr(self, "_assign_cap_btn", None): return {"type": "assign_cap"}
            if event.ui_element == getattr(self, "_clear_msn_btn", None): return {"type": "clear_mission"}
            
            if event.ui_element == getattr(self, "_cycle_msn_btn", None): return {"type": "cycle_mission"}
            if event.ui_element == getattr(self, "_cycle_ldt_btn", None): return {"type": "cycle_loadout"}
            if event.ui_element == getattr(self, "_launch_btn", None): return {"type": "launch_unit"}
            
            if self._mode == "setup":
                if event.ui_element == getattr(self, "_auto_deploy_btn", None): return {"type": "auto_deploy_blue"}
                if event.ui_element == getattr(self, "_save_deploy_btn", None): return {"type": "save_deployment"}
                if event.ui_element == getattr(self, "_load_deploy_btn", None): return {"type": "load_deployment"}
                if event.ui_element == getattr(self, "_place_btn", None):
                    sel = (self._roster_list.get_single_selection() if self._roster_list else None)
                    if sel and sel in self._roster_items:
                        key = self._roster_keys[self._roster_items.index(sel)]
                        if key.startswith("HEADER:"): return {"type": "place_unit_no_selection"}
                        return {"type": "place_unit", "platform_key": key, "quantity": self._parse_qty()}
                    return {"type": "place_unit_no_selection"}
                if event.ui_element == getattr(self, "_remove_btn", None): return {"type": "remove_selected"}
                if event.ui_element == getattr(self, "_clear_btn", None): return {"type": "clear_blue"}
                if event.ui_element == getattr(self, "_start_btn", None): return {"type": "start_sim"}

            if self._mode == "combat":
                if getattr(self, "_reinforce_btn", None) and event.ui_element == self._reinforce_btn: return {"type": "enter_setup"}
                if getattr(self, "_restart_btn", None) and event.ui_element == self._restart_btn: return {"type": "end_game"}

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
                    if event.ui_element == btn:
                        key = self._weap_keys[i]
                        if key.startswith("SELECT:"):
                            return {"type": "select_parked", "uid": key.split(":")[1]}
                        else:
                            return {"type": "weapon_select", "weapon_key": key}
        return {}

    def is_mouse_over_ui(self, pos: tuple[int, int]) -> bool:
        """Check if the mouse cursor is currently over any active Pygame GUI elements."""
        panel_h = max(BOTTOM_PANEL_MIN_HEIGHT, int(self._win_h * BOTTOM_PANEL_FRACTION))
        if pos[1] >= self._win_h - panel_h:
            return True
            
        windows = [self._pkg_window, self._wp_alt_window, self._settings_window, self.aar_window]
        for w in windows:
            if w is not None and w.rect.collidepoint(pos):
                return True
        return False

    def update(self, time_delta: float, sim: Optional[SimulationEngine], selected: Optional[Unit], 
               placing_type: Optional[str] = None, placing_remaining: int = 0, 
               show_all_enemies: bool = False, blue_contacts: dict | None = None,
               show_air_labels: bool = True, show_ground_labels: bool = True,
               show_radar_rings: bool = True, bgm_enabled: bool = True) -> None:
        
        if getattr(self, "_bgm_btn", None): self._bgm_btn.set_text("ON" if bgm_enabled else "OFF")
        if getattr(self, "_radar_rings_btn", None): self._radar_rings_btn.set_text("ON" if show_radar_rings else "OFF")
        if getattr(self, "_air_lbl_btn", None): self._air_lbl_btn.set_text("ON" if show_air_labels else "OFF")
        if getattr(self, "_gnd_lbl_btn", None): self._gnd_lbl_btn.set_text("ON" if show_ground_labels else "OFF")
        if getattr(self, "_fow_btn", None): self._fow_btn.set_text("OFF" if show_all_enemies else "ON")

        if self._mode == "setup":
            if self._setup_info:
                if placing_type:
                    p = self._db.platforms.get(placing_type)
                    pname = p.display_name.upper() if p else placing_type.upper()
                    self._setup_info.set_text(f"<b>PLACING:</b> {pname}<br><b>{placing_remaining} REMAINING</b><br>Left-click map to place unit.<br>Press ESC to cancel.")
                else:
                    placed = len(sim.blue_units()) if sim else 0
                    sel_str = ""
                    if self._roster_list:
                        s = self._roster_list.get_single_selection()
                        if s and s in self._roster_items:
                            key = self._roster_keys[self._roster_items.index(s)]
                            if not key.startswith("HEADER:"):
                                p   = self._db.platforms.get(key)
                                if p:
                                    type_labels = {
                                        "fighter":"Fighter", "attacker":"Attack", "helicopter":"Helicopter", "awacs": "AWACS / C2",
                                        "tank":"MBT", "ifv":"IFV", "apc":"APC", "recon":"Recon",
                                        "tank_destroyer":"Tank Destroyer", "sam":"Air Defense",
                                        "airbase":"Logistics Node", "artillery":"Artillery"
                                    }
                                    tl = type_labels.get(p.unit_type, p.unit_type.upper())
                                    spd_lbl = "km/h" if p.unit_type not in ("tank","ifv","apc","recon","tank_destroyer","sam","airbase","artillery") else "km/h (road)"
                                    sel_str = f"<b>Selected:</b> {p.display_name}<br><b>Type:</b> {tl}  ×{p.fleet_count} in service<br>Spd {p.speed_kmh} {spd_lbl}  Detect {p.radar_range_km} km<br>ECM {int(p.ecm_rating*100)}%<br><br>"
                    self._setup_info.set_text(f"<b>DEPLOYMENT PHASE</b><br>Blue units placed: <b>{placed}</b><br><br>{sel_str}Select type → PLACE ON MAP<br>Right-click unit to remove")
                    
            if sim and sim.game_time > 0 and getattr(self, "_start_btn", None):
                self._start_btn.set_text("▶ RESUME SIMULATION")
            elif getattr(self, "_start_btn", None):
                self._start_btn.set_text("▶ START SIMULATION")
        else:  
            if sim is None: return

            if selected and selected.alive:
                p = selected.platform
                wp = len(selected.waypoints)

                is_blue_air = selected.side == "Blue" and p.unit_type in ("fighter", "attacker", "helicopter", "awacs")
                is_blue_armed = selected.side == "Blue" and len(p.available_weapons) > 0
                is_parked   = is_blue_air and selected.duty_state == "READY"
                is_flying   = is_blue_air and selected.duty_state == "ACTIVE"
                
                alt_btns = [
                    getattr(self, "_climb_5k_btn", None), getattr(self, "_climb_1k_btn", None), getattr(self, "_climb_500_btn", None),
                    getattr(self, "_dive_5k_btn", None), getattr(self, "_dive_1k_btn", None), getattr(self, "_dive_500_btn", None)
                ]
                for btn in alt_btns:
                    if btn: btn.show() if is_flying else btn.hide()
                    
                if is_parked:
                    self._cycle_msn_btn.show()
                    self._cycle_ldt_btn.show()
                    self._launch_btn.show()
                    self._assign_cap_btn.hide()
                    self._clear_msn_btn.hide()
                    self._form_btn.hide()
                    self._doc_btn.hide()
                    msn_str = selected.mission.mission_type if selected.mission else "NONE"
                    self._cycle_msn_btn.set_text(f"MISSION: {msn_str}")
                    ldt_str = getattr(selected, "current_loadout_role", "DEFAULT")
                    self._cycle_ldt_btn.set_text(f"LOADOUT: {ldt_str}")
                elif is_flying:
                    self._cycle_msn_btn.hide()
                    self._cycle_ldt_btn.hide()
                    self._launch_btn.hide()
                    self._assign_cap_btn.show()
                    self._clear_msn_btn.show()
                    
                    if not getattr(selected, 'leader_uid', ""):
                        self._form_btn.show()
                        self._doc_btn.show()
                        self._form_btn.set_text(getattr(selected, 'formation', 'WEDGE')[:5])
                        self._doc_btn.set_text("AMBUSH" if getattr(selected, 'flight_doctrine', 'STANDARD') == "AMBUSH_COVER" else "STD")
                    else:
                        self._form_btn.hide()
                        self._doc_btn.hide()
                else:
                    self._cycle_msn_btn.hide()
                    self._cycle_ldt_btn.hide()
                    self._launch_btn.hide()
                    self._assign_cap_btn.hide()
                    self._clear_msn_btn.hide()
                    self._form_btn.hide()
                    self._doc_btn.hide()

                if getattr(self, "_auto_engage_btn", None):
                    if is_blue_armed:
                        self._auto_engage_btn.show()
                        self._roe_btn.show()
                        self._auto_engage_btn.set_text(f"AUTO: {'ON' if getattr(selected, 'auto_engage', False) else 'OFF'}")
                        self._roe_btn.set_text(f"ROE: {selected.roe}")
                    else:
                        self._auto_engage_btn.hide()
                        self._roe_btn.hide()
                        
                if getattr(self, "_wra_rng_btn", None):
                    if is_blue_armed:
                        self._wra_rng_btn.show()
                        self._wra_qty_btn.show()
                        self._wra_rng_btn.set_text(f"WRA RNG: {int(getattr(selected, 'wra_range_pct', 0.90)*100)}%")
                        self._wra_qty_btn.set_text(f"WRA QTY: {getattr(selected, 'wra_qty', 1)}")
                    else:
                        self._wra_rng_btn.hide()
                        self._wra_qty_btn.hide()
                        
                if getattr(self, "_emcon_btn", None):
                    if selected.side == "Blue":
                        self._emcon_btn.show()
                        lbl = {"SILENT":"EMC: SILENT", "SEARCH_ONLY":"EMC: SEARCH", "ACTIVE":"EMC: ACTIVE", "BLINDING":"EMC: BLIND"}.get(getattr(selected, 'emcon_state', "ACTIVE"), "EMC: ACTIVE")
                        self._emcon_btn.set_text(lbl)
                    else:
                        self._emcon_btn.hide()
                        
                if selected.platform.unit_type == "airbase" and selected.side == "Blue":
                    for btn in self._salvo_btns: btn.hide()
                    if getattr(self, "_strike_pkg_btn", None): self._strike_pkg_btn.show()
                    
                    parked = [u for u in sim.units if u.home_uid == selected.uid and u.duty_state != "ACTIVE" and u.alive]
                    parked_count = len(parked)
                    if parked_count != getattr(self, "_last_parked_count", -1):
                        self.rebuild_weapon_buttons(selected, sim)
                        self._last_parked_count = parked_count
                    
                    for i, key in enumerate(self._weap_keys):
                        if key.startswith("SELECT:"):
                            p_uid = key.split(":")[1]
                            p_unit = sim.get_unit_by_uid(p_uid)
                            if p_unit:
                                btn = self._weap_btns[i]
                                if p_unit.duty_state == "READY":
                                    btn.set_text(f"✈ {p_unit.callsign.upper()} (READY)")
                                    btn.enable()
                                else:
                                    btn.set_text(f"⏳ {p_unit.callsign.upper()} (REARM {int(p_unit.duty_timer)}S)")
                                    btn.disable()
                else:
                    for btn in self._salvo_btns: btn.show()
                    if getattr(self, "_strike_pkg_btn", None): self._strike_pkg_btn.hide()
                    self._last_parked_count = -1

                alt_display = f"{int(selected.altitude_ft):,} ft"
                if int(selected.target_altitude_ft) != int(selected.altitude_ft):
                    alt_display += f" <font color='#FFAA00'>(→{int(selected.target_altitude_ft):,})</font>"

                fuel_pct = (selected.fuel_kg / p.fuel_capacity_kg) * 100 if p.fuel_capacity_kg > 0 else 0
                fuel_col = "#FF4444" if fuel_pct < 20 else "#FFAA00" if fuel_pct < 50 else "#FFFFFF"
                
                hp_pct = int(selected.hp * 100)
                hp_col = "#FF4444" if hp_pct <= 25 else "#FFAA00" if hp_pct <= 50 else "#FFFF00" if hp_pct <= 75 else "#44FF44"

                def sys_col(state: str) -> str: return "#44FF44" if state=="OK" else "#FFAA00" if state=="DEGRADED" else "#FF4444"
                sr_stat  = selected.systems.get('search_radar', 'OK')
                fcr_stat = selected.systems.get('fc_radar', 'OK')
                m_stat   = selected.systems.get('mobility', 'OK')
                w_stat   = selected.systems.get('weapons', 'OK')
                
                sys_str = (
                    f"<b>Subsystems:</b> "
                    f"SR <font color='{sys_col(sr_stat)}'>{sr_stat[:3]}</font> | "
                    f"FCR <font color='{sys_col(fcr_stat)}'>{fcr_stat[:3]}</font> | "
                    f"MOB <font color='{sys_col(m_stat)}'>{m_stat[:3]}</font> | "
                    f"WPN <font color='{sys_col(w_stat)}'>{w_stat[:3]}</font>"
                )
                
                fire_str = f" <font color='#FF4444'>[🔥 FIRE {int(selected.fire_intensity*100)}%]</font>" if getattr(selected, 'fire_intensity', 0.0) > 0 else ""

                if p.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
                    if selected.duty_state == "REARMING":
                        state_str = f"<b>Status:</b> <font color='#FFAA00'>REARMING ({int(selected.duty_timer)}s)</font>"
                    elif selected.duty_state == "READY":
                        state_str = f"<b>Status:</b> <font color='#44FF44'>READY (Pre-flight)</font>"
                    else:
                        g_load = getattr(selected, 'current_g_load', 1.0)
                        g_col = "#FF4444" if g_load > 6.0 else "#FFAA00" if g_load > 2.0 else "#FFFFFF"
                        evade_tag = " <font color='#FF4444'><b>[EVADING]</b></font>" if getattr(selected, 'is_evading', False) else ""
                        crank_tag = " <font color='#55AAFF'><b>[CRANKING]</b></font>" if getattr(selected, 'is_cranking', False) else ""
                        state_str = f"<b>Spd:</b> {int(selected.current_speed_kmh):,} km/h  <b>Alt:</b> {alt_display}  <b>Load:</b> <font color='{g_col}'>{g_load:.1f}G</font>{evade_tag}{crank_tag}"
                else:
                    state_str = f"<b>Spd:</b> {p.speed_kmh:,} km/h  <b>Alt:</b> {alt_display}"

                contacts = blue_contacts or {}
                contact = contacts.get(selected.uid) if selected.side == "Red" else None
                
                cond_str = ""
                if selected.side == "Red":
                    cond_str = f"<br><b>Morale:</b> <font color='#FFAA00'>{selected.drunkness_label}</font> | <b>Logistics:</b> <font color='#FFAA00'>{selected.corruption_label}</font>"

                if selected.side == "Red" and contact is not None:
                    cls = contact.classification
                    cls_col = {"FAINT": "#888888", "PROBABLE": "#DCA03C", "CONFIRMED": "#FF4444"}.get(cls, "#FFFFFF")
                    
                    err_str = f" <font color='#FFAA00'>(Err: {contact.pos_error_km:.1f}km)</font>" if contact.pos_error_km > 0.5 else ""
                    
                    if cls == "FAINT":
                        self._nav_box.set_text(f"<b>CONTACT</b>  <font color='{cls_col}'>{cls}</font><br><b>Pos:</b> {contact.est_lat:.3f}°N  {contact.est_lon:.3f}°E{err_str}<br><b>Type:</b> unknown<br><b>IFF:</b> {contact.perceived_side}")
                    elif cls == "PROBABLE":
                        self._nav_box.set_text(f"<b>CONTACT</b>  <font color='{cls_col}'>{cls}</font><br><b>Pos:</b> {contact.est_lat:.3f}°N  {contact.est_lon:.3f}°E{err_str}<br><b>Type:</b> {contact.unit_type or 'unknown'}<br><b>Alt:</b> {int(contact.altitude_ft):,} ft<br><b>IFF:</b> {contact.perceived_side}")
                    else:  
                        msn_str = f"<b>Mission:</b> {selected.mission.mission_type}" if selected.mission else "<b>Mission:</b> NONE"
                        self._nav_box.set_text(f"<b>CONTACT — {selected.callsign}</b>{fire_str}  <font color='{cls_col}'>{cls}</font><br><b>Type:</b> {p.display_name}<br><b>HP:</b> <font color='{hp_col}'>{hp_pct}% ({selected.damage_state})</font> {msn_str}{cond_str}<br><b>Pos:</b> {contact.est_lat:.3f}°N  {contact.est_lon:.3f}°E{err_str}<br>{state_str}<br><b>RCS:</b> {p.rcs_m2} m²  <b>ECM:</b> {int(p.ecm_rating*100)}% ({'ACTIVE' if selected.is_jamming else 'PASSIVE'})<br><b>IFF:</b> {contact.perceived_side}")
                elif selected.side == "Blue":
                    msn_str = f"<b>Mission:</b> {selected.mission.mission_type} ({selected.mission.name})" if selected.mission else "<b>Mission:</b> NONE"
                    
                    if selected.mission and selected.mission.time_on_target > 0:
                        tot_str = f" <b>ToT:</b> {SimulationEngine._fmt_time(selected.mission.time_on_target)}"
                    else:
                        tot_str = ""
                        
                    fuel_str = f"<b>Fuel:</b> <font color='{fuel_col}'>{int(fuel_pct)}%</font> ({int(selected.fuel_kg)} kg)" if p.unit_type not in ("airbase", "artillery", "sam", "tank", "ifv", "apc", "recon", "tank_destroyer") else ""
                    
                    link_color = "#44FF44" if getattr(selected, 'datalink_active', True) else "#FF4444"
                    link_text = "LINK-16: ON" if getattr(selected, 'datalink_active', True) else "DATALINK OFFLINE"
                    
                    self._nav_box.set_text(f"<b>{selected.callsign}</b>{fire_str}  [Blue]<br><b>Type:</b> {p.display_name}<br><b>HP:</b> <font color='{hp_col}'>{hp_pct}% ({selected.damage_state})</font>  {fuel_str}<br>{sys_str}<br>{msn_str}{tot_str}<br>{state_str}<br><b>Radar:</b> {p.radar_range_km} km ({'ON' if getattr(selected, 'search_radar_active', True) else 'OFF'})  <b>ECM:</b> {int(p.ecm_rating*100)}% ({'ACTIVE' if selected.is_jamming else 'PASSIVE'})<br><b><font color='{link_color}'>{link_text}</font></b><br><b>HDG:</b> {selected.heading:05.1f}°  <b>Pos:</b> {selected.lat:.3f}°N  {selected.lon:.3f}°E<br><b>Route:</b> {wp} wp{'s' if wp!=1 else ''}")
                else:
                    self._nav_box.set_text(f"<b>{selected.callsign}</b>  [Red]<br><b>Type:</b> {p.display_name}{cond_str}<br><b>Not currently tracked</b>")
            else:
                alt_btns = [
                    getattr(self, "_climb_5k_btn", None), getattr(self, "_climb_1k_btn", None), getattr(self, "_climb_500_btn", None),
                    getattr(self, "_dive_5k_btn", None), getattr(self, "_dive_1k_btn", None), getattr(self, "_dive_500_btn", None)
                ]
                for btn in alt_btns:
                    if btn: btn.hide()
                    
                if getattr(self, "_auto_engage_btn", None): self._auto_engage_btn.hide()
                if getattr(self, "_roe_btn", None): self._roe_btn.hide()
                if getattr(self, "_emcon_btn", None): self._emcon_btn.hide()
                if getattr(self, "_wra_rng_btn", None): self._wra_rng_btn.hide()
                if getattr(self, "_wra_qty_btn", None): self._wra_qty_btn.hide()
                if getattr(self, "_assign_cap_btn", None): self._assign_cap_btn.hide()
                if getattr(self, "_clear_msn_btn", None): self._clear_msn_btn.hide()
                if getattr(self, "_form_btn", None): self._form_btn.hide()
                if getattr(self, "_doc_btn", None): self._doc_btn.hide()
                if getattr(self, "_cycle_msn_btn", None): self._cycle_msn_btn.hide()
                if getattr(self, "_cycle_ldt_btn", None): self._cycle_ldt_btn.hide()
                if getattr(self, "_launch_btn", None): self._launch_btn.hide()
                if getattr(self, "_strike_pkg_btn", None): self._strike_pkg_btn.hide()
                self._last_parked_count = -1
                
                t  = SimulationEngine._fmt_time(sim.game_time)
                cx = "PAUSED" if sim.paused else f"{sim.time_compression}×"
                self._nav_box.set_text(f"<b>TACTICAL DISPLAY</b><br><b>Time:</b> {t}  <b>Speed:</b> {cx}<br><b>Blue:</b> {len(sim.blue_units())} units  <b>Red:</b> {len(sim.red_units())} units<br><b>Missiles:</b> {len(sim.missiles)} in flight<br><br>Left-click unit to select<br>Right-click enemy to fire<br>Right-click map to waypoint")

            if len(sim.event_log) != self._last_log_len:
                self._last_log_len = len(sim.event_log)
                recent = list(reversed(list(sim.event_log)[-6:]))
                self._log_box.set_text("<br>".join(f'<font color="#90D090">› {e}</font>' for e in recent))

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