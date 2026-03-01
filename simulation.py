# simulation.py — real-time simulation engine

from __future__ import annotations

import math
import random
from collections import deque
from typing import Optional

from geo import haversine, slant_range_km
from scenario import Database, Missile, Unit, WeaponDef
from sensor import Contact, update_contacts

_MAX_LOG = 60

_HOME_ARRIVAL_KM       = 2.0    
_AI_COOLDOWN_MISSILE   = 45.0   
_AI_COOLDOWN_GUN       = 8.0    
_AI_ENGAGE_FRAC        = 0.90   

_GROUND_TYPES = {"tank", "ifv", "apc", "recon", "tank_destroyer", "sam"}

class SalvoMission:
    def __init__(self, shooter: Unit, target: Unit, weapon_key: str, count: int, doctrine: str):
        self.shooter = shooter
        self.target = target
        self.weapon_key = weapon_key
        self.count = count
        self.doctrine = doctrine
        self.active_missiles: list[Missile] = []


class SimulationEngine:
    def __init__(self, units: list[Unit], db: Database):
        self.units:    list[Unit]    = units
        self.missiles: list[Missile] = []
        self.salvos:   list[SalvoMission] = []
        self.db:       Database      = db

        self.game_time:        float = 0.0
        self.time_compression: int   = 1
        self.paused:           bool  = False

        self.event_log: deque[str] = deque(maxlen=_MAX_LOG)
        self.blue_contacts: dict[str, Contact] = {}
        self._red_contacts:  dict[str, Contact] = {}
        self.log(f"Scenario loaded — {len(units)} units ready.")

    def log(self, msg: str) -> None:
        self.event_log.append(f"[{self._fmt_time(self.game_time)}] {msg}")

    def set_compression(self, factor: int) -> None:
        self.time_compression = factor
        self.paused = (factor == 0)
        self.log("PAUSED" if self.paused else f"Time compression → {factor}×")

    def update(self, real_delta: float) -> None:
        if self.paused or self.time_compression == 0:
            return

        sim_delta = real_delta * self.time_compression
        self.game_time += sim_delta

        self._move_units(sim_delta)
        self._process_unit_missions(sim_delta)
        self._red_ai(sim_delta)
        self._blue_ai(sim_delta)
        self._process_salvos(sim_delta)
        self._move_missiles(sim_delta)
        self._resolve_missile_outcomes()
        self._purge_dead()
        self._update_contacts()
        self._tick_flashes()

    def queue_salvo(self, shooter: Unit, target: Unit, weapon_key: str, count: int, doctrine: str) -> None:
        wdef = self.db.weapons.get(weapon_key)
        if not wdef: return
        
        target_is_air = target.platform.unit_type in ("fighter", "attacker", "helicopter")
        if wdef.domain == "air" and not target_is_air:
            self.log(f"{shooter.callsign}: {wdef.display_name} cannot target ground units.")
            return
        if wdef.domain == "ground" and target_is_air:
            self.log(f"{shooter.callsign}: {wdef.display_name} cannot target air units.")
            return
            
        self.salvos.append(SalvoMission(shooter, target, weapon_key, count, doctrine))

    def _process_salvos(self, sim_delta: float) -> None:
        active_salvos = []
        for s in self.salvos:
            if not s.shooter.alive or not s.target.alive or s.count <= 0:
                continue
            if s.shooter.loadout.get(s.weapon_key, 0) <= 0:
                continue

            s.active_missiles = [m for m in s.active_missiles if m.active]

            if s.doctrine == "SLS" and len(s.active_missiles) > 0:
                active_salvos.append(s)
                continue
            
            if s.shooter.weapon_cooldowns.get(s.weapon_key, 0.0) <= 0:
                wdef = self.db.weapons[s.weapon_key]
                dist = slant_range_km(s.shooter.lat, s.shooter.lon, s.shooter.altitude_ft, 
                                      s.target.lat, s.target.lon, s.target.altitude_ft)
                
                if dist > wdef.range_km or dist < wdef.min_range_km:
                    active_salvos.append(s)
                    continue

                if s.shooter.expend_round(s.weapon_key):
                    m = Missile(s.shooter.lat, s.shooter.lon, s.shooter.altitude_ft, s.target, s.shooter.side, wdef)
                    self.missiles.append(m)
                    s.active_missiles.append(m)
                    s.count -= 1
                    s.shooter.weapon_cooldowns[s.weapon_key] = wdef.reload_time_s

                    est_pk = m.estimated_pk()
                    fox = {"SARH": "Fox 1", "IR": "Fox 2", "ARH": "Fox 3", "CANNON": "Guns", "SACLOS": "ATGM", "LASER": "ATGM"}.get(wdef.seeker, "Fox 3")
                    prefix = "⚠ " if s.shooter.side == "Red" else ""
                    doc_str = "SLS" if s.doctrine == "SLS" else f"Salvo {s.count} left"
                    self.log(f"{prefix}{s.shooter.callsign}: {fox}! {wdef.display_name} → {s.target.callsign} ({doc_str}) Pk={int(est_pk*100)}%")
                    
                    if s.count > 0:
                        active_salvos.append(s)
            else:
                active_salvos.append(s)
                
        self.salvos = active_salvos

    def _move_units(self, sim_delta: float) -> None:
        for u in self.units:
            was_alive = u.alive
            had_waypoints = bool(u.waypoints)
            
            u.update(sim_delta)
            
            if had_waypoints and not u.waypoints and u.alive and not u.mission:
                self.log(f"{u.callsign} reached destination.")
                
            if was_alive and not u.alive and u.fuel_kg <= 0:
                reason = "crashed" if u.platform.unit_type in ["fighter", "attacker", "helicopter"] else "abandoned"
                self.log(f"MAYDAY: {u.callsign} out of fuel and {reason}!")

    def _process_unit_missions(self, sim_delta: float) -> None:
        for u in self.units:
            if not u.alive or not u.mission:
                continue

            if u.platform.fuel_capacity_kg > 0:
                fuel_pct = u.fuel_kg / u.platform.fuel_capacity_kg
                if fuel_pct <= u.mission.rtb_fuel_pct and u.mission.mission_type != "RTB":
                    u.mission.mission_type = "RTB"
                    u.clear_waypoints()
                    self.log(f"{u.callsign}: bingo fuel, returning to base.")

            if u.mission.mission_type == "RTB":
                dist_home = haversine(u.lat, u.lon, u.home_lat, u.home_lon)
                if not u.waypoints and dist_home > _HOME_ARRIVAL_KM:
                    u.add_waypoint(u.home_lat, u.home_lon)
                elif dist_home <= _HOME_ARRIVAL_KM:
                    if u.waypoints: u.clear_waypoints()

            elif u.mission.mission_type in ("CAP", "PATROL", "STRIKE", "SEAD", "ASW"):
                u.target_altitude_ft = u.mission.altitude_ft
                if not u.waypoints:
                    angle = random.uniform(0, 360)
                    dist = random.uniform(0, u.mission.radius_km)
                    dlat = (math.cos(math.radians(angle)) * dist) / 111.32
                    dlon = (math.sin(math.radians(angle)) * dist) / (111.32 * math.cos(math.radians(u.mission.target_lat)))
                    u.add_waypoint(u.mission.target_lat + dlat, u.mission.target_lon + dlon)

    def _move_missiles(self, sim_delta: float) -> None:
        for m in self.missiles:
            m.update(sim_delta)

    def _blue_ai(self, sim_delta: float) -> None:
        red_units = [u for u in self.units if u.side == "Red" and u.alive]
        if not red_units: return

        for blue in self.units:
            if blue.side != "Blue" or not blue.alive: continue
            
            if blue.platform.ecm_rating > 0 and len(self.blue_contacts) > 0:
                blue.is_jamming = True
            
            if not getattr(blue, 'auto_engage', False): continue
            
            is_ground = blue.platform.unit_type in _GROUND_TYPES
            
            if blue.ai_fire_cooldown > 0: blue.ai_fire_cooldown = max(0.0, blue.ai_fire_cooldown - sim_delta)
            if not hasattr(blue, "ai_gun_cooldown"): blue.ai_gun_cooldown = 0.0  
            if blue.ai_gun_cooldown > 0: blue.ai_gun_cooldown = max(0.0, blue.ai_gun_cooldown - sim_delta) 
            
            self._auto_engage_shooter(blue, red_units, self.blue_contacts, is_ground)

    def _red_ai(self, sim_delta: float) -> None:
        blue_units = [u for u in self.units if u.side == "Blue" and u.alive]
        if not blue_units: return

        for red in self.units:
            if red.side != "Red" or not red.alive: continue

            if red.platform.ecm_rating > 0 and len(self._red_contacts) > 0:
                red.is_jamming = True

            is_ground = red.platform.unit_type in _GROUND_TYPES

            if red.ai_fire_cooldown > 0: red.ai_fire_cooldown = max(0.0, red.ai_fire_cooldown - sim_delta)
            if not hasattr(red, "ai_gun_cooldown"): red.ai_gun_cooldown = 0.0  
            if red.ai_gun_cooldown > 0: red.ai_gun_cooldown = max(0.0, red.ai_gun_cooldown - sim_delta) 

            self._auto_engage_shooter(red, blue_units, self._red_contacts, is_ground)

    def _auto_engage_shooter(self, shooter: Unit, hostile_targets: list[Unit], contacts: dict[str, Contact], is_ground: bool) -> None:
        if shooter.roe == "HOLD": return

        gun_ready     = (shooter.ai_gun_cooldown <= 0)       
        missile_ready = (shooter.ai_fire_cooldown <= 0)

        if not gun_ready and not missile_ready: return  

        candidates: list[tuple[str, WeaponDef]] = []
        for wkey, qty in shooter.loadout.items():
            if qty <= 0: continue
            wdef = self.db.weapons.get(wkey)
            if wdef is None: continue
            if wdef.is_gun and not gun_ready: continue
            if not wdef.is_gun and not missile_ready: continue
            if not is_ground and wdef.is_gun: continue
            candidates.append((wkey, wdef))

        if not candidates: return
        candidates.sort(key=lambda x: -x[1].range_km)

        for wkey, wdef in candidates:
            if shooter.weapon_cooldowns.get(wkey, 0.0) > 0: continue

            target      = None
            target_dist = float("inf")
            engage_range = wdef.range_km * _AI_ENGAGE_FRAC

            for host in hostile_targets:
                contact = contacts.get(host.uid)
                if not contact: continue
                
                # Enforce ROE Doctrine
                if shooter.roe == "TIGHT" and contact.classification != "CONFIRMED":
                    continue
                if shooter.roe == "FREE" and contact.classification not in ("PROBABLE", "CONFIRMED"):
                    continue

                target_is_air = host.platform.unit_type in ("fighter", "attacker", "helicopter")
                if wdef.domain == "air" and not target_is_air: continue
                if wdef.domain == "ground" and target_is_air: continue
                
                dist = slant_range_km(shooter.lat, shooter.lon, shooter.altitude_ft, host.lat, host.lon, host.altitude_ft)
                if dist > engage_range or dist < wdef.min_range_km: continue
                if dist < target_dist:
                    target      = host
                    target_dist = dist

            if target is None: continue   

            self.queue_salvo(shooter, target, wkey, count=1, doctrine="salvo")

            if wdef.is_gun: shooter.ai_gun_cooldown = _AI_COOLDOWN_GUN  
            else: shooter.ai_fire_cooldown = _AI_COOLDOWN_MISSILE
            return   

    def _resolve_missile_outcomes(self) -> None:
        for m in self.missiles:
            if not m.active and m.status in ("HIT", "MISSED"):
                if m.status == "HIT":
                    if m.target.alive: 
                        m.target.trigger_flash()
                        self.log(f"HIT! {m.target.callsign} damaged by {m.wdef.display_name} (State: {m.target.damage_state}).")
                    else:
                        m.target.trigger_flash()
                        kill_word = "SPLASH" if m.target.platform.unit_type in ("fighter", "attacker", "helicopter") else "SHACK"
                        self.log(f"{kill_word}! {m.target.callsign} destroyed by {m.wdef.display_name}.")
                else:
                    self.log(f"{m.target.callsign} survived {m.wdef.display_name}.")

    def _update_contacts(self) -> None:
        blue_alive = [u for u in self.units if u.side == "Blue" and u.alive]
        red_alive  = [u for u in self.units if u.side == "Red"  and u.alive]

        update_contacts(blue_alive, red_alive, self.blue_contacts, self.game_time)
        update_contacts(red_alive, blue_alive, self._red_contacts, self.game_time)

        red_uid_set  = set(self.blue_contacts.keys())
        blue_uid_set = set(self._red_contacts.keys())
        for u in red_alive: u.is_detected = u.uid in red_uid_set
        for u in blue_alive: u.is_detected = u.uid in blue_uid_set

    def _tick_flashes(self) -> None:
        for u in self.units: u.tick_flash()

    def _purge_dead(self) -> None:
        self.missiles = [m for m in self.missiles if m.active]
        self.units    = [u for u in self.units    if u.alive]

    def blue_units(self) -> list[Unit]: return [u for u in self.units if u.side == "Blue"]
    def red_units(self) -> list[Unit]: return [u for u in self.units if u.side == "Red"]

    def is_game_over(self) -> Optional[str]:
        blues_alive = any(u.alive for u in self.units if u.side == "Blue")
        reds_alive  = any(u.alive for u in self.units if u.side == "Red")
        if not blues_alive and not reds_alive: return "Draw"
        if not blues_alive: return "Red wins"
        if not reds_alive: return "Blue wins"
        return None

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"