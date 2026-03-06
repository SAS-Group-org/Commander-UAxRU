# simulation.py — real-time simulation engine

from __future__ import annotations
import math
import random
from collections import deque
from typing import Optional
from dataclasses import dataclass

from geo import haversine, slant_range_km, bearing, check_line_of_sight
from scenario import Database, Missile, Unit, WeaponDef, Mission, GameEvent
from sensor import Contact, update_local_contacts

_MAX_LOG = 60
_HOME_ARRIVAL_KM       = 2.0    
_AI_COOLDOWN_MISSILE   = 45.0   
_AI_COOLDOWN_GUN       = 8.0    

_G_LIMIT_BLEED_FACTOR  = 0.05   
_MIN_EVASION_SPEED_KMH = 350.0  

_GROUND_TYPES = {"tank", "ifv", "apc", "recon", "tank_destroyer", "sam", "airbase", "artillery"}

@dataclass
class Explosion:
    lat: float
    lon: float
    max_radius_km: float
    life: float = 0.0
    max_life: float = 1.0

class PackageManager:
    def __init__(self, engine: "SimulationEngine"):
        self.engine = engine
        self.update_timer = 5.0

    def update_packages(self, game_time: float, sim_delta: float):
        self.update_timer -= sim_delta
        if self.update_timer > 0: return
        self.update_timer = 5.0
        
        packages = {}
        for u in self.engine.units:
            if u.alive and u.duty_state == "ACTIVE" and u.mission and u.mission.package_id:
                packages.setdefault(u.mission.package_id, []).append(u)
                
        for pid, members in packages.items():
            if len(members) <= 1: continue
            max_tot = max((u.mission.time_on_target for u in members), default=0.0)
            
            if max_tot == 0.0:
                max_eta = 0.0
                for u in members:
                    dist = haversine(u.lat, u.lon, u.mission.target_lat, u.mission.target_lon)
                    spd_kms = (max(300.0, u.platform.speed_kmh * u.performance_mult)) / 3600.0
                    eta = dist / spd_kms
                    if eta > max_eta: max_eta = eta
                
                base_tot = game_time + max_eta + 120.0
                for u in members:
                    if u.mission.mission_type == "SEAD":
                        u.mission.time_on_target = base_tot - 60.0 
                    else:
                        u.mission.time_on_target = base_tot
                self.engine.log(f"Package '{pid}' sequenced. ToT: {self.engine._fmt_time(base_tot)}.")

class SalvoMission:
    def __init__(self, shooter: Unit, target: Unit, weapon_key: str, count: int, doctrine: str):
        self.shooter = shooter
        self.target = target
        self.weapon_key = weapon_key
        self.count = count
        self.doctrine = doctrine
        self.active_missiles: list[Missile] = []

class SimulationEngine:
    def __init__(self, units: list[Unit], db: Database, events: list[GameEvent] = None):
        self.units:    list[Unit]    = units
        self.missiles: list[Missile] = []
        self.salvos:   list[SalvoMission] = []
        self.explosions: list[Explosion] = []
        self.db:       Database      = db
        self.events:   list[GameEvent] = events or []
        
        self.game_time:        float = 0.0
        self.time_compression: int   = 1
        self.paused:           bool  = False
        self.event_log: deque[str] = deque(maxlen=_MAX_LOG)
        self.game_over_reason: Optional[str] = None
        
        self.score_blue: int = 0
        self.score_red: int = 0
        self.aar_log: list[str] = [] 
        
        self.blue_network: dict[str, Contact] = {}
        self.red_network:  dict[str, Contact] = {}
        self.blue_contacts = self.blue_network 
        
        self._units_by_uid: dict[str, Unit] = {u.uid: u for u in self.units}
        self.package_manager = PackageManager(self)
        self.log(f"Scenario loaded — {len(units)} units ready.")

    def get_unit_by_uid(self, uid: str) -> Optional[Unit]:
        if len(self.units) != len(self._units_by_uid):
            self._units_by_uid = {u.uid: u for u in self.units}
        return self._units_by_uid.get(uid)

    def set_compression(self, factor: int) -> None:
        self.time_compression = factor
        self.paused = (factor == 0)
        self.log("PAUSED" if self.paused else f"Time compression → {factor}×")

    def log(self, msg: str) -> None:
        self.event_log.append(f"[{self._fmt_time(self.game_time)}] {msg}")

    def generate_aar(self) -> dict:
        return {
            "duration": self._fmt_time(self.game_time),
            "score_blue": self.score_blue,
            "score_red": self.score_red,
            "winner": "Blue" if self.score_blue > self.score_red else "Red" if self.score_red > self.score_blue else "Draw",
            "kill_log": self.aar_log
        }

    def update(self, real_delta: float) -> None:
        if self.paused or self.time_compression == 0: return

        sim_delta = real_delta * self.time_compression
        self.game_time += sim_delta

        self._move_units(sim_delta)
        self._process_unit_status(sim_delta) 
        self._process_unit_missions(sim_delta)
        self._unit_defensive_ai(sim_delta)
        self._process_point_defense(sim_delta)
        self.package_manager.update_packages(self.game_time, sim_delta)
        
        self._update_contacts()
        self._red_ai(sim_delta)
        self._blue_ai(sim_delta)
        
        self._process_salvos(sim_delta)
        self._move_missiles(sim_delta)
        self._resolve_missile_outcomes()
        
        for exp in self.explosions: exp.life += sim_delta
        self.explosions = [e for e in self.explosions if e.life < e.max_life]
        
        self._process_events()
        self._purge_dead()
        self._tick_flashes()

    def _process_point_defense(self, sim_delta: float) -> None:
        for m in self.missiles:
            if not m.active or m.wdef.is_gun: continue 
            
            dist_to_target = slant_range_km(m.lat, m.lon, m.altitude_ft, m.impact_lat if m.is_ballistic else m.target.lat, m.impact_lon if m.is_ballistic else m.target.lon, m.impact_alt_ft if m.is_ballistic else m.target.altitude_ft)
            
            if dist_to_target > 20.0: continue
            
            defenders = [u for u in self.units if u.alive and u.side != m.side and u.duty_state == "ACTIVE"]
            for defender in defenders:
                pd_weapon_key = None
                for wkey, qty in defender.loadout.items():
                    if qty > 0 and self.db.weapons[wkey].is_point_defense and defender.weapon_cooldowns.get(wkey, 0.0) <= 0:
                        pd_weapon_key = wkey
                        break
                        
                if not pd_weapon_key: continue
                
                pd_wdef = self.db.weapons[pd_weapon_key]
                dist_to_missile = slant_range_km(defender.lat, defender.lon, defender.altitude_ft, m.lat, m.lon, m.altitude_ft)
                
                if pd_wdef.min_range_km <= dist_to_missile <= pd_wdef.range_km:
                    rcs_ratio = (max(0.001, m.wdef.rcs_m2) / 0.10) ** 0.33
                    base_chance = pd_wdef.base_pk * (0.60 * rcs_ratio)
                    
                    spd_mult = 0.05 if pd_wdef.range_km > 30.0 else 0.15
                    speed_penalty = (m.eff_speed_kmh / 1000.0) * spd_mult
                    
                    profile_penalty = 0.0
                    if m.wdef.flight_profile in ("sea_skimming", "terrain_following"):
                        profile_penalty += 0.15
                        if m.wdef.rcs_m2 <= 0.01:
                            profile_penalty += 0.30 
                        
                    if m.is_ballistic and pd_wdef.range_km < 50.0 and not pd_wdef.is_gun:
                        profile_penalty += 0.25

                    intercept_chance = max(0.01, (base_chance * defender.performance_mult) - speed_penalty - profile_penalty)
                    
                    defender.expend_round(pd_weapon_key)
                    defender.weapon_cooldowns[pd_weapon_key] = pd_wdef.reload_time_s
                    
                    if random.random() <= intercept_chance:
                        m.active = False
                        m.status = "INTERCEPTED"
                        self.explosions.append(Explosion(m.lat, m.lon, 0.02))
                        self.log(f"{defender.callsign} INTERCEPTED incoming {m.wdef.display_name} with {pd_wdef.display_name}!")
                        break

    def _process_events(self) -> None:
        for e in self.events:
            if e.triggered: continue
            triggered = False
            if e.condition_type == "TIME":
                if self.game_time >= float(e.condition_val): triggered = True
            elif e.condition_type == "UNIT_DEAD":
                u = self.get_unit_by_uid(e.condition_val)
                if u is None or not u.alive: triggered = True
            elif e.condition_type == "AREA_ENTERED":
                try:
                    lat_str, lon_str, rad_str = e.condition_val.split(",")
                    tgt_lat, tgt_lon, tgt_rad = float(lat_str), float(lon_str), float(rad_str)
                    for u in self.units:
                        if u.alive and u.side == "Blue" and haversine(u.lat, u.lon, tgt_lat, tgt_lon) <= tgt_rad:
                            triggered = True
                            break
                except Exception:
                    pass

            if triggered:
                e.triggered = True
                if e.action_type == "LOG":
                    self.log(f"EVENT: {e.action_val}")
                elif e.action_type == "SCORE":
                    try:
                        side, pts_str = e.action_val.split(":")
                        pts = int(pts_str)
                        if side == "Blue": self.score_blue += pts
                        elif side == "Red": self.score_red += pts
                        self.log(f"EVENT: {side} scored {pts} objective points!")
                    except Exception:
                        self.log(f"EVENT ERROR: Malformed SCORE action_val '{e.action_val}'")
                elif e.action_type == "VICTORY":
                    self.log(f"*** {e.action_val.upper()} WINS BY EVENT OBJECTIVE ***")
                    self.game_over_reason = f"{e.action_val} wins"

    def _process_unit_status(self, sim_delta: float) -> None:
        for u in self.units:
            if not u.alive: continue
            
            was_on_fire = getattr(u, '_was_on_fire', False)
            is_on_fire = getattr(u, 'fire_intensity', 0.0) > 0
            if is_on_fire and not was_on_fire:
                self.log(f"{u.callsign}: ON FIRE!")
            elif was_on_fire and not is_on_fire:
                self.log(f"{u.callsign}: Fire extinguished.")
            u._was_on_fire = is_on_fire
            
            is_ground = u.platform.unit_type in _GROUND_TYPES
            is_air = u.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs")
            
            if is_ground and u.systems["mobility"] == "DESTROYED" and u.waypoints:
                u.clear_waypoints()
                
            if is_air and u.duty_state == "ACTIVE":
                if u.damage_state in ("HEAVY", "MODERATE") or u.systems["mobility"] != "OK" or u.systems["weapons"] == "DESTROYED":
                    if not u.mission or u.mission.mission_type != "RTB":
                        base = self.get_unit_by_uid(u.home_uid)
                        if base:
                            u.mission = Mission("Emergency RTB", "RTB", base.lat, base.lon, 0, u.altitude_ft, 0)
                            u.clear_waypoints()
                            self.log(f"{u.callsign}: Critical damage! Aborting mission, emergency RTB.")

    def _unit_defensive_ai(self, sim_delta: float) -> None:
        for u in self.units:
            if not u.alive: continue
            
            incoming = [m for m in self.missiles if m.active and m.target == u]
            
            if u.platform.unit_type in _GROUND_TYPES:
                if incoming:
                    arms = [m for m in incoming if m.wdef.seeker == "ARM"]
                    if arms:
                        if u.emcon_state in ("ACTIVE", "BLINDING"):
                            u.set_emcon("SEARCH_ONLY")
                            if u.side == "Blue": self.log(f"{u.callsign}: ARM inbound! Dropping Fire Control Radar.")
            
            elif u.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
                if incoming:
                    u.is_evading = True
                    u.last_evasion_time = self.game_time
                    
                    if getattr(u.platform, 'ecm_rating', 0.0) > 0 and not u.is_jamming:
                        u.set_emcon("BLINDING")
                        if u.side == "Blue": self.log(f"{u.callsign}: Threat detected! EMCON BLINDING!")
                    
                    penalty = getattr(u, 'inefficiency_penalty', 0.0)
                    
                    for threat in incoming:
                        dist = slant_range_km(u.lat, u.lon, u.altitude_ft, threat.lat, threat.lon, threat.altitude_ft)
                        if dist < 15.0:
                            if threat.wdef.seeker in ("ARH", "SARH") and u.chaff > 0:
                                if random.random() < (0.25 * sim_delta * (1.0 - penalty)): u.chaff -= 1
                            elif threat.wdef.seeker == "IR" and u.flare > 0:
                                if random.random() < (0.35 * sim_delta * (1.0 - penalty)): u.flare -= 1

                    closest_threat = min(incoming, key=lambda m: slant_range_km(u.lat, u.lon, u.altitude_ft, m.lat, m.lon, m.altitude_ft))
                    
                    threat_brg = bearing(u.lat, u.lon, closest_threat.lat, closest_threat.lon)
                    opt1 = (threat_brg + 90) % 360
                    opt2 = (threat_brg - 90) % 360
                    
                    diff1 = abs((opt1 - u.heading + 360) % 360)
                    diff1 = diff1 if diff1 <= 180 else 360 - diff1
                    diff2 = abs((opt2 - u.heading + 360) % 360)
                    diff2 = diff2 if diff2 <= 180 else 360 - diff2

                    u.target_heading = opt1 if diff1 < diff2 else opt2
                    
                    if u.altitude_ft > 2000:
                        u.target_altitude_ft = max(1000, u.altitude_ft - 5000)

                else:
                    if getattr(u, 'is_evading', False) and (self.game_time - u.last_evasion_time) > 8.0:
                        u.is_evading = False
                        u.set_emcon("ACTIVE")
                        u.target_altitude_ft = u.mission.altitude_ft if u.mission else u.platform.cruise_alt_ft
                        u._recalc_heading()

    def queue_salvo(self, shooter: Unit, target: Unit, weapon_key: str, count: int, doctrine: str) -> None:
        wdef = self.db.weapons.get(weapon_key)
        if not wdef: return
        target_is_air = target.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs")
        
        if wdef.domain == "air" and not target_is_air:
            self.log(f"{shooter.callsign}: Cannot fire air-to-air weapon at a ground target.")
            return
        if wdef.domain == "ground" and target_is_air:
            self.log(f"{shooter.callsign}: Cannot fire air-to-ground weapon at an air target.")
            return
            
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
                
                if dist > wdef.range_km * 1.5:
                    self.log(f"Salvo aborted: {s.target.callsign} is out of range.")
                    continue
                    
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
            u.update(sim_delta, self.game_time)
            
            if getattr(u, 'leader_uid', "") and u.duty_state == "ACTIVE":
                leader = self.get_unit_by_uid(u.leader_uid)
                if leader and leader.alive and leader.duty_state == "ACTIVE":
                    
                    spacing_km = 1.5
                    if leader.formation == "TRAIL":
                        offset_dist = spacing_km * u.formation_slot
                        offset_bearing = (leader.heading + 180) % 360
                    elif leader.formation == "LINE":
                        offset_dist = spacing_km * math.ceil(u.formation_slot / 2.0)
                        offset_bearing = (leader.heading + 90) % 360 if u.formation_slot % 2 != 0 else (leader.heading - 90) % 360
                    else: 
                        offset_dist = spacing_km * u.formation_slot
                        offset_bearing = (leader.heading + 135) % 360 if u.formation_slot % 2 != 0 else (leader.heading - 135) % 360
                    
                    tlat = leader.lat + (math.cos(math.radians(offset_bearing)) * offset_dist) / 111.32
                    tlon = leader.lon + (math.sin(math.radians(offset_bearing)) * offset_dist) / (111.32 * max(0.0001, math.cos(math.radians(leader.lat))))
                    
                    u.waypoints = [(tlat, tlon, -1.0)]
                    
                    dist_to_station = haversine(u.lat, u.lon, tlat, tlon)
                    if dist_to_station > 1.0:
                        u.formation_target_speed = min(u.platform.speed_kmh * u.performance_mult, leader.current_speed_kmh + 300.0)
                    elif dist_to_station < 0.2:
                        u.formation_target_speed = max(350.0, leader.current_speed_kmh - 100.0)
                    else:
                        u.formation_target_speed = leader.current_speed_kmh
                    
                    if leader.flight_doctrine == "AMBUSH_COVER":
                        fighting = getattr(leader, 'is_evading', False) or getattr(leader, 'is_intercepting', False) or any(m.active and m.shooter.uid == leader.uid for m in self.missiles)
                        if fighting:
                            u.target_altitude_ft = leader.target_altitude_ft
                            if u.emcon_state == "SILENT": 
                                u.set_emcon("ACTIVE")
                                if u.side == "Blue": self.log(f"{u.callsign}: Leader engaged! Popping up and going ACTIVE.")
                        else:
                            u.target_altitude_ft = max(1000.0, leader.target_altitude_ft - 5000.0)
                            if u.emcon_state != "SILENT": 
                                u.set_emcon("SILENT")
                    else:
                        u.target_altitude_ft = leader.target_altitude_ft
                    
                    if getattr(leader, 'is_evading', False):
                        u.is_evading = True
                        u.target_heading = leader.target_heading

            if u.duty_state != "ACTIVE" and u.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
                base = self.get_unit_by_uid(u.home_uid)
                if base: u.lat, u.lon = base.lat, base.lon

    def _process_unit_missions(self, sim_delta: float) -> None:
        for u in self.units:
            if not u.alive or not u.mission or u.duty_state != "ACTIVE": continue
            
            if getattr(u, 'is_evading', False): continue
            
            if (u.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs")
                    and u.mission.mission_type != "RTB"
                    and u.platform.fuel_capacity_kg > 0):
                fuel_pct = u.fuel_kg / u.platform.fuel_capacity_kg
                if fuel_pct <= u.mission.rtb_fuel_pct:
                    base = self.get_unit_by_uid(u.home_uid)
                    if base:
                        u.mission = Mission("Bingo RTB", "RTB", base.lat, base.lon,
                                            0, u.altitude_ft, 0)
                        u.clear_waypoints()
                        if u.side == "Blue":
                            self.log(f"{u.callsign}: BINGO FUEL — returning to base.")
            
            if u.mission.mission_type == "RTB":
                base = self.get_unit_by_uid(u.home_uid)
                if base:
                    dist_home = haversine(u.lat, u.lon, base.lat, base.lon)
                    if dist_home > _HOME_ARRIVAL_KM:
                        if not u.waypoints: u.add_waypoint(base.lat, base.lon, -1.0)
                    else:
                        u.duty_state = "REARMING"
                        u.duty_timer = u.platform.rearm_time_s
                        u.mission = None
                        
            elif u.mission.mission_type == "CAP":
                if not getattr(u, 'is_intercepting', False) and not getattr(u, 'leader_uid', ""):
                    if not u.waypoints:
                        enemy_side = "Red" if u.side == "Blue" else "Blue"
                        threat_contacts = [c for c in u.merged_contacts.values()
                                           if c.perceived_side == enemy_side]
                        if threat_contacts:
                            nearest = min(threat_contacts,
                                          key=lambda c: haversine(u.mission.target_lat, u.mission.target_lon,
                                                                   c.est_lat, c.est_lon))
                            threat_brg = bearing(u.mission.target_lat, u.mission.target_lon,
                                                 nearest.est_lat, nearest.est_lon)
                        else:
                            threat_brg = 90.0   
                        ang1 = threat_brg
                        ang2 = (threat_brg + 180) % 360

                        length = 20.0
                        lat1 = u.mission.target_lat + (math.cos(math.radians(ang1)) * length) / 111.32
                        lon1 = u.mission.target_lon + (math.sin(math.radians(ang1)) * length) / (111.32 * max(0.0001, math.cos(math.radians(u.mission.target_lat))))
                        lat2 = u.mission.target_lat + (math.cos(math.radians(ang2)) * length) / 111.32
                        lon2 = u.mission.target_lon + (math.sin(math.radians(ang2)) * length) / (111.32 * max(0.0001, math.cos(math.radians(u.mission.target_lat))))
                        
                        u.waypoints = [(lat1, lon1, -1.0), (lat2, lon2, -1.0)]
                        u._recalc_heading()
                else:
                    enemy_side = "Red" if u.side == "Blue" else "Blue"
                    has_hostiles = any(c.perceived_side == enemy_side for c in u.merged_contacts.values() if c.unit_type in ("fighter", "attacker", "helicopter", "awacs"))
                    if not has_hostiles:
                        u.is_intercepting = False
                        u.waypoints = list(getattr(u, 'saved_waypoints', []))

            elif not u.waypoints and not getattr(u, 'leader_uid', ""):
                angle = random.uniform(0, 360)
                dist = random.uniform(0, u.mission.radius_km)
                dlat = (math.cos(math.radians(angle)) * dist) / 111.32
                dlon = (math.sin(math.radians(angle)) * dist) / (111.32 * math.cos(math.radians(u.mission.target_lat)))
                u.add_waypoint(u.mission.target_lat + dlat, u.mission.target_lon + dlon, -1.0)

    def _move_missiles(self, sim_delta: float) -> None:
        for m in self.missiles: m.update(sim_delta)

    def _blue_ai(self, sim_delta: float) -> None:
        for blue in self.units:
            if blue.side == "Blue" and blue.alive:
                is_active_air = blue.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs") and blue.mission is not None
                if getattr(blue, 'auto_engage', False) or is_active_air:
                    valid_targets = []
                    for uid, c in blue.merged_contacts.items():
                        if c.perceived_side == "Red" or (blue.roe == "FREE" and c.perceived_side == "UNKNOWN"):
                            t = self.get_unit_by_uid(uid)
                            if t and t.alive: valid_targets.append(t)
                    if valid_targets:
                        self._auto_engage_shooter(blue, valid_targets, blue.merged_contacts)

    def _red_ai(self, sim_delta: float) -> None:
        for red in self.units:
            if red.side == "Red" and red.alive:
                is_active_air = red.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs") and red.mission is not None
                if getattr(red, 'auto_engage', False) or is_active_air:
                    valid_targets = []
                    for uid, c in red.merged_contacts.items():
                        if c.perceived_side == "Blue" or (red.roe == "FREE" and c.perceived_side == "UNKNOWN"):
                            t = self.get_unit_by_uid(uid)
                            if t and t.alive: valid_targets.append(t)
                    if valid_targets:
                        self._auto_engage_shooter(red, valid_targets, red.merged_contacts)

    def _auto_engage_shooter(self, shooter: Unit, targets: list[Unit], contacts: dict[str, Contact]) -> None:
        if shooter.roe == "HOLD": return
        
        valid_targets = []
        for host in targets:
            contact = contacts.get(host.uid)
            if not contact or contact.classification == "FAINT": continue
            
            score = host.platform.value_points
            dist = slant_range_km(shooter.lat, shooter.lon, shooter.altitude_ft, contact.est_lat, contact.est_lon, contact.altitude_ft)
            score += max(0, 100 - dist) 
            
            if host.platform.unit_type in ("fighter", "attacker", "helicopter"):
                if dist < 40.0:
                    score += 500 

            if shooter.mission:
                if shooter.mission.mission_type == "SEAD":
                    if host.platform.unit_type in ("sam", "airbase"): score += 1000
                    if getattr(host, 'search_radar_active', False) or getattr(host, 'fc_radar_active', False): score += 800
                elif shooter.mission.mission_type == "STRIKE" and host.platform.unit_type in _GROUND_TYPES:
                    dist_to_obj = haversine(host.lat, host.lon, shooter.mission.target_lat, shooter.mission.target_lon)
                    if dist_to_obj < shooter.mission.radius_km + 20.0:
                        score += 2000 
            
            target_engaged_count = sum(1 for m in self.missiles if m.active and m.side == shooter.side and m.target.uid == host.uid)
            target_engaged_count += sum(1 for s in self.salvos if s.shooter.side == shooter.side and s.target.uid == host.uid and s.count > 0)
            
            if target_engaged_count >= 2:
                continue
                
            score -= (target_engaged_count * 300) 
            
            valid_targets.append((score, host, contact, dist))

        valid_targets.sort(key=lambda x: x[0], reverse=True)
        
        for score, host, contact, dist in valid_targets:
            wkey = shooter.best_weapon_for(self.db, host)
            if wkey:
                wdef = self.db.weapons[wkey]
                
                wra_range = getattr(shooter, 'wra_range_pct', 0.90)
                
                if dist < wdef.range_km * wra_range:
                    
                    if wdef.seeker in ("SARH", "ARH", "CLOS") and not getattr(shooter, 'fc_radar_active', True):
                        arms = [m for m in self.missiles if m.active and m.target == shooter and m.wdef.seeker == "ARM"]
                        if arms:
                            continue 
                        else:
                            shooter.set_emcon("ACTIVE")
                            if shooter.side == "Blue": self.log(f"{shooter.callsign}: Activating FC Radar to engage {host.callsign}!")

                    if shooter.mission and shooter.mission.mission_type == "CAP" and not shooter.is_evading:
                        if not getattr(shooter, 'is_intercepting', False):
                            shooter.is_intercepting = True
                            shooter.saved_waypoints = list(shooter.waypoints)
                        shooter.clear_waypoints()
                        shooter.add_waypoint(contact.est_lat, contact.est_lon, -1.0)
                    
                    qty_to_fire = getattr(shooter, 'wra_qty', 1)
                    actual_qty = min(qty_to_fire, shooter.loadout.get(wkey, 0))
                    
                    if actual_qty > 0:
                        self.queue_salvo(shooter, host, wkey, actual_qty, "salvo")
                    
                        if wdef.seeker in ("SARH", "ARH") and shooter.platform.unit_type in ("fighter", "attacker"):
                            shooter.is_cranking = True
                            shooter.crank_timer = 15.0
                            brg_to_tgt = bearing(shooter.lat, shooter.lon, contact.est_lat, contact.est_lon)
                            
                            diff_right = (brg_to_tgt + 55 - shooter.heading) % 360
                            diff_left = (brg_to_tgt - 55 - shooter.heading) % 360
                            if min(diff_right, 360-diff_right) < min(diff_left, 360-diff_left):
                                shooter.crank_heading = (brg_to_tgt + 55) % 360
                            else:
                                shooter.crank_heading = (brg_to_tgt - 55) % 360
                                
                            if shooter.side == "Blue":
                                fox = "Fox 3" if wdef.seeker == "ARH" else "Fox 1"
                                self.log(f"{shooter.callsign}: {fox}, cranking to {int(shooter.crank_heading)}°")
                    break 

    def _resolve_missile_outcomes(self) -> None:
        for m in self.missiles:
            if not m.active and getattr(m, 'detonated', False):
                radius = m.wdef.splash_radius_km if m.wdef.splash_radius_km > 0 else 0.05 
                self.explosions.append(Explosion(m.lat, m.lon, radius))

                if m.wdef.splash_radius_km > 0:
                    for unit in self.units:
                        if not unit.alive: continue
                        if m.status == "HIT" and unit.uid == m.target.uid: continue 
                        
                        dist_km = haversine(m.lat, m.lon, unit.lat, unit.lon)
                        if dist_km <= m.wdef.splash_radius_km:
                            falloff_mult = 1.0 - (dist_km / m.wdef.splash_radius_km)
                            unit.take_damage(m.wdef.damage * falloff_mult)
                
                if m.status == "HIT":
                    if m.target.alive: m.target.trigger_flash()
                    
                    if getattr(m, 'did_kill', False): 
                        pts = m.target.platform.value_points
                        if m.shooter.side == "Blue": self.score_blue += pts
                        else: self.score_red += pts
                        self.log(f"SPLASH {m.target.callsign}! (+{pts} pts)")
                        self.aar_log.append(f"[{self._fmt_time(self.game_time)}] {m.shooter.callsign} ({m.shooter.platform.display_name}) destroyed {m.target.callsign} ({m.target.platform.display_name}) with {m.wdef.display_name}.")
                elif m.status == "MISSED" and not m.is_ballistic and m.wdef.domain != "ground":
                    if m.target.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
                        self.log(f"{m.target.callsign} evaded.")
                    else:
                        self.log(f"Missile missed {m.target.callsign}.")
                m.detonated = False

    def _update_contacts(self) -> None:
        _RANK = {"NONE": 0, "FAINT": 1, "PROBABLE": 2, "CONFIRMED": 3}
        
        blue_active = [u for u in self.units if u.side == "Blue" and u.alive]
        red_active  = [u for u in self.units if u.side == "Red"  and u.alive]
        
        for b in blue_active: update_local_contacts([b], red_active, b.local_contacts, self.game_time)
        for r in red_active:  update_local_contacts([r], blue_active, r.local_contacts, self.game_time)

        self.blue_network.clear()
        self.red_network.clear()
        
        blue_c2 = [u for u in blue_active if u.platform.unit_type in ("awacs", "airbase")]
        red_c2  = [u for u in red_active if u.platform.unit_type in ("awacs", "airbase")]
        
        for u in self.units:
            c2_nodes = blue_c2 if u.side == "Blue" else red_c2
            master_net = self.blue_network if u.side == "Blue" else self.red_network
            
            connected = False
            if u.platform.unit_type in ("awacs", "airbase"):
                connected = True
            else:
                for c2 in c2_nodes:
                    if check_line_of_sight(u.lat, u.lon, u.altitude_ft, c2.lat, c2.lon, c2.altitude_ft):
                        connected = True
                        break
            
            u.datalink_active = connected
            
            if connected:
                for uid, local_c in u.local_contacts.items():
                    net_c = master_net.get(uid)
                    if net_c is None or local_c.pos_error_km < net_c.pos_error_km:
                        master_net[uid] = local_c

        for u in self.units:
            master_net = self.blue_network if u.side == "Blue" else self.red_network
            u.merged_contacts = dict(u.local_contacts)
            if u.datalink_active:
                for uid, net_c in master_net.items():
                    local_c = u.merged_contacts.get(uid)
                    if local_c is None or net_c.pos_error_km < local_c.pos_error_km:
                        u.merged_contacts[uid] = net_c

        for r in red_active:
            r.is_detected = (r.uid in self.blue_network)

    def _tick_flashes(self) -> None:
        for u in self.units: u.tick_flash()

    def _purge_dead(self) -> None:
        self.missiles = [m for m in self.missiles if m.active]
        alive_units = []
        for u in self.units:
            if u.alive:
                alive_units.append(u)
            else:
                self._units_by_uid.pop(u.uid, None)
                
        self.units = alive_units

    def blue_units(self) -> list[Unit]: return [u for u in self.units if u.side == "Blue"]
    def red_units(self) -> list[Unit]: return [u for u in self.units if u.side == "Red"]

    def is_game_over(self) -> Optional[str]:
        if self.game_over_reason: return self.game_over_reason
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