# simulation.py — real-time simulation engine

from __future__ import annotations
import math
import random
from collections import deque
from typing import Optional

from geo import haversine, slant_range_km, bearing, check_line_of_sight
from scenario import Database, Missile, Unit, WeaponDef, Mission
from sensor import Contact, update_local_contacts

_MAX_LOG = 60
_HOME_ARRIVAL_KM       = 2.0    
_AI_COOLDOWN_MISSILE   = 45.0   
_AI_COOLDOWN_GUN       = 8.0    
_AI_ENGAGE_FRAC        = 0.90   

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
        
        # The Master Command Datalinks
        self.blue_network: dict[str, Contact] = {}
        self.red_network:  dict[str, Contact] = {}
        
        # Legacy property to not crash ui.py, but now maps to network
        self.blue_contacts = self.blue_network 
        
        self.log(f"Scenario loaded — {len(units)} units ready.")

    def get_unit_by_uid(self, uid: str) -> Optional[Unit]:
        for u in self.units:
            if u.uid == uid: return u
        return None

    def set_compression(self, factor: int) -> None:
        self.time_compression = factor
        self.paused = (factor == 0)
        self.log("PAUSED" if self.paused else f"Time compression → {factor}×")

    def log(self, msg: str) -> None:
        self.event_log.append(f"[{self._fmt_time(self.game_time)}] {msg}")

    def update(self, real_delta: float) -> None:
        if self.paused or self.time_compression == 0: return

        sim_delta = real_delta * self.time_compression
        self.game_time += sim_delta

        self._move_units(sim_delta)
        self._process_unit_missions(sim_delta)
        self._unit_defensive_ai(sim_delta)
        self._update_contacts() # Datalink update MUST happen before AI logic
        self._red_ai(sim_delta)
        self._blue_ai(sim_delta)
        self._process_salvos(sim_delta)
        self._move_missiles(sim_delta)
        self._resolve_missile_outcomes()
        self._purge_dead()
        self._tick_flashes()

    def _unit_defensive_ai(self, sim_delta: float) -> None:
        for u in self.units:
            if not u.alive or u.platform.unit_type not in ("fighter", "attacker", "helicopter", "awacs"):
                continue

            incoming = [m for m in self.missiles if m.active and m.target == u]
            
            if incoming:
                u.is_evading = True
                u.last_evasion_time = self.game_time
                
                if getattr(u.platform, 'ecm_rating', 0.0) > 0 and not u.is_jamming:
                    u.is_jamming = True
                    if u.side == "Blue": self.log(f"{u.callsign}: Jammer active!")
                
                for threat in incoming:
                    dist = slant_range_km(u.lat, u.lon, u.altitude_ft, threat.lat, threat.lon, threat.altitude_ft)
                    if dist < 15.0:
                        if threat.wdef.seeker in ("ARH", "SARH") and u.chaff > 0:
                            if random.random() < (0.25 * sim_delta): u.chaff -= 1
                        elif threat.wdef.seeker == "IR" and u.flare > 0:
                            if random.random() < (0.35 * sim_delta): u.flare -= 1

                closest_threat = min(incoming, key=lambda m: slant_range_km(u.lat, u.lon, u.altitude_ft, m.lat, m.lon, m.altitude_ft))
                closest_dist = slant_range_km(u.lat, u.lon, u.altitude_ft, closest_threat.lat, closest_threat.lon, closest_threat.altitude_ft)

                current_spd = u.platform.speed_kmh * u.performance_mult
                if current_spd > _MIN_EVASION_SPEED_KMH:
                    threat_brg = bearing(u.lat, u.lon, closest_threat.lat, closest_threat.lon)
                    u.heading = (threat_brg + 90) % 360 
                
                if u.altitude_ft > 2000:
                    u.target_altitude_ft = max(1000, u.altitude_ft - 5000)

                if u.waypoints and closest_dist < 8.0:
                    u.clear_waypoints() 

            else:
                if u.is_evading and (self.game_time - u.last_evasion_time) > 8.0:
                    u.is_evading = False
                    if u.mission: u.target_altitude_ft = u.mission.altitude_ft

    def queue_salvo(self, shooter: Unit, target: Unit, weapon_key: str, count: int, doctrine: str) -> None:
        wdef = self.db.weapons.get(weapon_key)
        if not wdef: return
        target_is_air = target.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs")
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
                    m = Missile(s.shooter, s.target, wdef)
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
            if u.duty_state != "ACTIVE" and u.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
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
        for blue in self.units:
            if blue.side == "Blue" and blue.alive and getattr(blue, 'auto_engage', False):
                # AI targets based on its own dynamically merged sensor scope
                targets = [self.get_unit_by_uid(uid) for uid in blue.merged_contacts.keys()]
                valid_targets = [t for t in targets if t is not None and t.alive]
                self._auto_engage_shooter(blue, valid_targets, blue.merged_contacts)

    def _red_ai(self, sim_delta: float) -> None:
        for red in self.units:
            if red.side == "Red" and red.alive:
                targets = [self.get_unit_by_uid(uid) for uid in red.merged_contacts.keys()]
                valid_targets = [t for t in targets if t is not None and t.alive]
                self._auto_engage_shooter(red, valid_targets, red.merged_contacts)

    def _auto_engage_shooter(self, shooter: Unit, targets: list[Unit], contacts: dict[str, Contact]) -> None:
        if shooter.roe == "HOLD": return
        
        engaged_uids = set()
        for m in self.missiles:
            if m.active and m.side == shooter.side: engaged_uids.add(m.target.uid)
        for s in self.salvos:
            if s.shooter.side == shooter.side and s.count > 0: engaged_uids.add(s.target.uid)
        
        if shooter.mission and shooter.mission.mission_type == "SEAD":
            valid_targets = []
            for host in targets:
                if host.uid in engaged_uids: continue  
                contact = contacts.get(host.uid)
                if not contact or contact.classification == "FAINT": continue
                score = 0
                if host.platform.unit_type in ("sam", "airbase"): score += 100
                if getattr(host, 'radar_active', False): score += 50
                valid_targets.append((score, host))
            valid_targets.sort(key=lambda x: x[0], reverse=True)
            targets_to_check = [t[1] for t in valid_targets]
        else:
            targets_to_check = [t for t in targets if t.uid not in engaged_uids]  
            
        for host in targets_to_check:
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
                    if m.target.alive: m.target.trigger_flash()
                    if not m.target.alive: self.log(f"SPLASH {m.target.callsign}!")
                elif m.status == "MISSED":
                    if m.target.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
                        self.log(f"{m.target.callsign} evaded.")
                    else:
                        self.log(f"Missile missed {m.target.callsign}.")

    def _update_contacts(self) -> None:
        """The Datalink Architecture"""
        _RANK = {"NONE": 0, "FAINT": 1, "PROBABLE": 2, "CONFIRMED": 3}
        
        blue_active = [u for u in self.units if u.side == "Blue" and u.alive]
        red_active  = [u for u in self.units if u.side == "Red"  and u.alive]
        
        # 1. Update individual local scopes
        for b in blue_active: update_local_contacts([b], red_active, b.local_contacts, self.game_time)
        for r in red_active:  update_local_contacts([r], blue_active, r.local_contacts, self.game_time)

        # 2. Aggregate Master Networks (AWACS and Ground Stations upload tracks here)
        self.blue_network.clear()
        self.red_network.clear()
        
        blue_c2 = [u for u in blue_active if u.platform.unit_type in ("awacs", "airbase")]
        red_c2  = [u for u in red_active if u.platform.unit_type in ("awacs", "airbase")]
        
        for u in self.units:
            c2_nodes = blue_c2 if u.side == "Blue" else red_c2
            master_net = self.blue_network if u.side == "Blue" else self.red_network
            
            # Connection Check: Are we AWACS, or do we have LoS to an AWACS/Base?
            connected = False
            if u.platform.unit_type in ("awacs", "airbase"):
                connected = True
            else:
                for c2 in c2_nodes:
                    if check_line_of_sight(u.lat, u.lon, u.altitude_ft, c2.lat, c2.lon, c2.altitude_ft):
                        connected = True
                        break
            
            u.datalink_active = connected
            
            # If connected, upload local tracks to the network
            if connected:
                for uid, local_c in u.local_contacts.items():
                    net_c = master_net.get(uid)
                    if net_c is None or _RANK[local_c.classification] > _RANK[net_c.classification]:
                        master_net[uid] = local_c

        # 3. Download the Network back to connected units
        for u in self.units:
            master_net = self.blue_network if u.side == "Blue" else self.red_network
            
            # Every unit's "AI Brain" merged dictionary starts with what it can see locally
            u.merged_contacts = dict(u.local_contacts)
            
            # If datalink is up, append/overwrite with the master network
            if u.datalink_active:
                for uid, net_c in master_net.items():
                    local_c = u.merged_contacts.get(uid)
                    if local_c is None or _RANK[net_c.classification] > _RANK[local_c.classification]:
                        u.merged_contacts[uid] = net_c

        # 4. Global UI hack (Ensures the player sees what the Blue network sees)
        for r in red_active:
            r.is_detected = (r.uid in self.blue_network)

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