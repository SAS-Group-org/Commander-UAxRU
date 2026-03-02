# simulation.py — real-time simulation engine

from __future__ import annotations
import math
import random
from collections import deque
from typing import Optional

from geo import haversine, slant_range_km, bearing
from scenario import Database, Missile, Unit, WeaponDef, Mission
from sensor import Contact, update_contacts

_MAX_LOG = 60
_HOME_ARRIVAL_KM       = 2.0    
_AI_COOLDOWN_MISSILE   = 45.0   
_AI_COOLDOWN_GUN       = 8.0    
_AI_ENGAGE_FRAC        = 0.90   

# Energy Maneuvering Constants
_G_LIMIT_BLEED_FACTOR  = 0.05   
_MIN_EVASION_SPEED_KMH = 350.0  

_GROUND_TYPES = {"tank", "ifv", "apc", "recon", "tank_destroyer", "sam", "airbase", "artillery"}

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

    def get_unit_by_uid(self, uid: str) -> Optional[Unit]:
        """Fixed missing attribute error."""
        for u in self.units:
            if u.uid == uid: return u
        return None

    def set_compression(self, factor: int) -> None:
        """Fixed missing attribute error."""
        self.time_compression = factor
        self.paused = (factor == 0)
        self.log("PAUSED" if self.paused else f"Time compression → {factor}×")

    def log(self, msg: str) -> None:
        self.event_log.append(f"[{self._fmt_time(self.game_time)}] {msg}")

    def update(self, real_delta: float) -> None:
        if self.paused or self.time_compression == 0:
            return

        sim_delta = real_delta * self.time_compression
        self.game_time += sim_delta

        self._move_units(sim_delta)
        self._process_unit_missions(sim_delta)
        self._unit_defensive_ai(sim_delta)
        self._red_ai(sim_delta)
        self._blue_ai(sim_delta)
        self._process_salvos(sim_delta)
        self._move_missiles(sim_delta)
        self._resolve_missile_outcomes()
        self._purge_dead()
        self._update_contacts()
        self._tick_flashes()

    def _unit_defensive_ai(self, sim_delta: float) -> None:
        """CMO-style Defensive AI: Handles break turns, speed bleed, and CMs."""
        
        for u in self.units:
            if not u.alive or u.platform.unit_type not in ("fighter", "attacker", "helicopter"):
                continue

            # 1. Threat Detection (15km reaction window)
            incoming = [m for m in self.missiles if m.active and m.target == u]
            
            if incoming:
                u.is_evading = True
                u.last_evasion_time = self.game_time
                
                threat = min(incoming, key=lambda m: slant_range_km(u.lat, u.lon, u.altitude_ft, m.lat, m.lon, m.altitude_ft))
                dist = slant_range_km(u.lat, u.lon, u.altitude_ft, threat.lat, threat.lon, threat.altitude_ft)

                # 2. Seeker-Specific Countermeasures
                if dist < 15.0:
                    if threat.wdef.seeker in ("ARH", "SARH") and u.chaff > 0:
                        if random.random() < (0.25 * sim_delta):
                            u.chaff -= 1
                            if u.side == "Blue": self.log(f"{u.callsign}: Chaff away!")
                    elif threat.wdef.seeker == "IR" and u.flare > 0:
                        if random.random() < (0.35 * sim_delta):
                            u.flare -= 1
                            if u.side == "Blue": self.log(f"{u.callsign}: Flaring!")

                # 3. Defensive Break Turn & Speed Bleed
                current_spd = u.platform.speed_kmh * u.performance_mult
                if current_spd > _MIN_EVASION_SPEED_KMH:
                    threat_brg = bearing(u.lat, u.lon, threat.lat, threat.lon)
                    u.heading = (threat_brg + 90) % 360 
                    
                    # Simulated speed loss from high G maneuvering
                    speed_loss = current_spd * _G_LIMIT_BLEED_FACTOR * sim_delta
                
                # 4. Defensive Descent
                if u.altitude_ft > 2000:
                    u.target_altitude_ft = max(1000, u.altitude_ft - 5000)

                if u.waypoints and dist < 8.0:
                    u.clear_waypoints() 

            else:
                # 5. Mission Recovery (after 8s clear skies)
                if u.is_evading and (self.game_time - u.last_evasion_time) > 8.0:
                    u.is_evading = False
                    if u.mission:
                        u.target_altitude_ft = u.mission.altitude_ft
                    if u.side == "Blue": self.log(f"{u.callsign}: Threat cleared, resuming profile.")

    def queue_salvo(self, shooter: Unit, target: Unit, weapon_key: str, count: int, doctrine: str) -> None:
        wdef = self.db.weapons.get(weapon_key)
        if not wdef: return
        target_is_air = target.platform.unit_type in ("fighter", "attacker", "helicopter")
        if wdef.domain == "air" and not target_is_air: return
        if wdef.domain == "ground" and target_is_air: return
        self.salvos.append(SalvoMission(shooter, target, weapon_key, count, doctrine))

    def _process_salvos(self, sim_delta: float) -> None:
        active_salvos = []
        for s in self.salvos:
            if not s.shooter.alive or not s.target.alive or s.count <= 0: continue
            s.active_missiles = [m for m in s.active_missiles if m.active]
            if s.doctrine == "SLS" and len(s.active_missiles) > 0:
                active_salvos.append(s)
                continue
            if s.shooter.weapon_cooldowns.get(s.weapon_key, 0.0) <= 0:
                wdef = self.db.weapons[s.weapon_key]
                dist = slant_range_km(s.shooter.lat, s.shooter.lon, s.shooter.altitude_ft, s.target.lat, s.target.lon, s.target.altitude_ft)
                if dist > wdef.range_km or dist < wdef.min_range_km:
                    active_salvos.append(s)
                    continue
                if s.shooter.expend_round(s.weapon_key):
                    m = Missile(s.shooter.lat, s.shooter.lon, s.shooter.altitude_ft, s.target, s.shooter.side, wdef)
                    self.missiles.append(m)
                    s.active_missiles.append(m)
                    s.count -= 1
                    s.shooter.weapon_cooldowns[s.weapon_key] = wdef.reload_time_s
                    if s.count > 0: active_salvos.append(s)
            else:
                active_salvos.append(s)
        self.salvos = active_salvos

    def _move_units(self, sim_delta: float) -> None:
        for u in self.units:
            u.update(sim_delta)
            if u.duty_state != "ACTIVE" and u.platform.unit_type in ("fighter", "attacker", "helicopter"):
                base = self.get_unit_by_uid(u.home_uid)
                if base: u.lat, u.lon = base.lat, base.lon

    def _process_unit_missions(self, sim_delta: float) -> None:
        for u in self.units:
            if not u.alive or not u.mission or u.duty_state != "ACTIVE": continue
            if u.mission.mission_type == "RTB":
                base = self.get_unit_by_uid(u.home_uid)
                if base:
                    dist_home = haversine(u.lat, u.lon, base.lat, base.lon)
                    if dist_home > _HOME_ARRIVAL_KM:
                        if not u.waypoints: u.add_waypoint(base.lat, base.lon)
                    else:
                        u.duty_state = "REARMING"
                        u.duty_timer = u.platform.rearm_time_s
                        u.mission = None
            elif not u.waypoints:
                angle = random.uniform(0, 360)
                dist = random.uniform(0, u.mission.radius_km)
                dlat = (math.cos(math.radians(angle)) * dist) / 111.32
                dlon = (math.sin(math.radians(angle)) * dist) / (111.32 * math.cos(math.radians(u.mission.target_lat)))
                u.add_waypoint(u.mission.target_lat + dlat, u.mission.target_lon + dlon)

    def _move_missiles(self, sim_delta: float) -> None:
        for m in self.missiles: m.update(sim_delta)

    def _blue_ai(self, sim_delta: float) -> None:
        red_units = [u for u in self.units if u.side == "Red" and u.alive]
        for blue in self.units:
            if blue.side == "Blue" and blue.alive and getattr(blue, 'auto_engage', False):
                self._auto_engage_shooter(blue, red_units, self.blue_contacts)

    def _red_ai(self, sim_delta: float) -> None:
        blue_units = [u for u in self.units if u.side == "Blue" and u.alive]
        for red in self.units:
            if red.side == "Red" and red.alive:
                self._auto_engage_shooter(red, blue_units, self._red_contacts)

    def _auto_engage_shooter(self, shooter: Unit, targets: list[Unit], contacts: dict[str, Contact]) -> None:
        if shooter.roe == "HOLD": return
        for host in targets:
            contact = contacts.get(host.uid)
            if not contact or contact.classification == "FAINT": continue
            wkey = shooter.best_weapon_for(self.db, host)
            if wkey:
                wdef = self.db.weapons[wkey]
                dist = slant_range_km(shooter.lat, shooter.lon, shooter.altitude_ft, host.lat, host.lon, host.altitude_ft)
                if dist < wdef.range_km * _AI_ENGAGE_FRAC:
                    self.queue_salvo(shooter, host, wkey, 1, "salvo")
                    break

    def _resolve_missile_outcomes(self) -> None:
        for m in self.missiles:
            if not m.active:
                if m.status == "HIT":
                    m.target.trigger_flash()
                    if not m.target.alive: self.log(f"SPLASH {m.target.callsign}!")
                else:
                    self.log(f"{m.target.callsign} evaded.")

    def _update_contacts(self) -> None:
        blue_active = [u for u in self.units if u.side == "Blue" and u.alive]
        red_active  = [u for u in self.units if u.side == "Red"  and u.alive]
        update_contacts(blue_active, red_active, self.blue_contacts, self.game_time)
        update_contacts(red_active, blue_active, self._red_contacts, self.game_time)

    def _tick_flashes(self) -> None:
        for u in self.units: u.tick_flash()

    def _purge_dead(self) -> None:
        self.missiles = [m for m in self.missiles if m.active]
        self.units    = [u for u in self.units    if u.alive]

    def blue_units(self) -> list[Unit]: return [u for u in self.units if u.side == "Blue"]
    def red_units(self) -> list[Unit]: return [u for u in self.units if u.side == "Red"]

    def is_game_over(self) -> Optional[str]:
        blues = any(u.side == "Blue" for u in self.units)
        reds = any(u.side == "Red" for u in self.units)
        if not blues: return "Red wins"
        if not reds: return "Blue wins"
        return None

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        h, m = divmod(int(seconds), 3600)
        m, s = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"