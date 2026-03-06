# scenario.py — data models + DB/scenario load & save

from __future__ import annotations
import json
import math
import os
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
import pygame

from constants import MIN_PK, MAX_PK, MISSILE_TRAIL_LEN, CHAFF_PK_PENALTY, FLARE_PK_PENALTY, BURNTHROUGH_RANGE_KM
from geo import haversine, bearing, slant_range_km, check_line_of_sight
from sensor import Contact

@dataclass(frozen=True)
class WeaponDef:
    key:          str
    display_name: str
    seeker:       str
    range_km:     float
    min_range_km: float
    speed_kmh:    float
    base_pk:      float
    is_gun:       bool
    description:  str
    domain:       str     
    damage:       float   
    reload_time_s: float  
    eccm:         float
    cep_km:       float = 0.0  
    splash_radius_km: float = 0.0  
    is_point_defense: bool = False
    inevadable:   bool = False
    flight_profile: str = "direct" 
    rcs_m2:       float = 0.1   

@dataclass(frozen=True)
class PlatformDef:
    key:               str
    display_name:      str
    unit_type:         str
    speed_kmh:         float
    ceiling_ft:        int
    ecm_rating:        float
    chaff_capacity:    int
    flare_capacity:    int
    fuel_capacity_kg:  float
    fuel_burn_rate_kg_h: float
    radar_range_km:    float
    radar_type:        str
    radar_modes:       tuple[str, ...]
    esm_range_km:      float            
    ir_range_km:       float            
    default_loadout:   dict[str, int]
    available_weapons: tuple[str, ...]
    fleet_count:       int              
    player_side:       str              
    rcs_m2:            float            
    cruise_alt_ft:     float            
    rearm_time_s:      float
    max_g:             float            
    value_points:      int = 10

@dataclass
class Mission:
    name: str
    mission_type: str
    target_lat: float
    target_lon: float
    radius_km: float
    altitude_ft: float
    rtb_fuel_pct: float
    time_on_target: float = 0.0
    package_id: str = ""

@dataclass
class GameEvent:
    id: str
    condition_type: str 
    condition_val: str  
    action_type: str    
    action_val: str     
    triggered: bool = False

class Missile:
    def __init__(self, shooter: "Unit", target: "Unit", weapon_def: WeaponDef):
        self.shooter    = shooter
        self.lat        = shooter.lat
        self.lon        = shooter.lon
        self.altitude_ft = shooter.altitude_ft
        self.target     = target
        self.side       = shooter.side
        self.wdef       = weapon_def
        self.active     = True
        self.status     = "IN_FLIGHT"
        self.detonated  = False
        self.launch_dist = slant_range_km(self.lat, self.lon, self.altitude_ft, target.lat, target.lon, target.altitude_ft)
        self.trail: deque[tuple[float, float]] = deque(maxlen=MISSILE_TRAIL_LEN)
        self.eff_speed_kmh = weapon_def.speed_kmh if weapon_def.speed_kmh > 0 else 5000.0
        
        self.is_ballistic = (self.wdef.flight_profile == "ballistic")
        self.impact_lat   = target.lat
        self.impact_lon   = target.lon
        self.impact_alt_ft = target.altitude_ft
        self.did_kill     = False 
        
        if self.is_ballistic and weapon_def.cep_km > 0.0:
            angle = random.uniform(0, 360)
            dist = random.gauss(0, weapon_def.cep_km)
            self.impact_lat += (math.cos(math.radians(angle)) * dist) / 111.32
            self.impact_lon += (math.sin(math.radians(angle)) * dist) / (111.32 * max(0.0001, math.cos(math.radians(target.lat))))

    def update(self, sim_delta: float) -> None:
        if not self.active: return
        
        if self.wdef.seeker in ("SARH", "CLOS"):
            if not self.shooter.alive or not getattr(self.shooter, 'fc_radar_active', True):
                self.active = False
                self.status = "LOST"
                return
            if not check_line_of_sight(self.shooter.lat, self.shooter.lon, self.shooter.altitude_ft,
                                       self.target.lat, self.target.lon, self.target.altitude_ft):
                self.active = False
                self.status = "LOST"
                return

        if not self.target.alive and not self.is_ballistic:
            self.active = False
            self.status = "LOST"
            return

        self.trail.append((self.lat, self.lon))
        speed_kms   = self.eff_speed_kmh / 3600.0
        move_dist   = speed_kms * sim_delta
        
        t_lat = self.impact_lat if self.is_ballistic else self.target.lat
        t_lon = self.impact_lon if self.is_ballistic else self.target.lon
        t_alt = self.impact_alt_ft if self.is_ballistic else self.target.altitude_ft
        
        dist = slant_range_km(self.lat, self.lon, self.altitude_ft, t_lat, t_lon, t_alt)
        fraction_travelled = 1.0 - max(0.0, min(1.0, dist / max(1.0, self.launch_dist)))
        
        if self.wdef.flight_profile == "lofted":
            if fraction_travelled < 0.5:
                self.altitude_ft = self.shooter.altitude_ft + (60000 - self.shooter.altitude_ft) * (fraction_travelled * 2)
            else:
                self.altitude_ft = 60000 - (60000 - t_alt) * ((fraction_travelled - 0.5) * 2)
        elif self.wdef.flight_profile == "sea_skimming":
            if fraction_travelled > 0.05: self.altitude_ft = max(30.0, t_alt)
        elif self.wdef.flight_profile == "terrain_following":
            if fraction_travelled > 0.10: self.altitude_ft = max(200.0, t_alt)
        elif self.is_ballistic:
            peak_alt = self.launch_dist * 1000.0
            self.altitude_ft = math.sin(fraction_travelled * math.pi) * peak_alt + t_alt

        if dist <= move_dist:
            pk = self._calculate_terminal_pk()
            if random.random() <= pk:
                if self.target.alive: 
                    self.target.take_damage(self.wdef.damage)
                    if not self.target.alive:
                        self.did_kill = True
                self.status = "HIT"
                self.detonated = True
            else:
                self.status = "MISSED"
                if self.is_ballistic or self.wdef.domain == "ground":
                    self.detonated = True
            self.active = False
            self.lat, self.lon, self.altitude_ft = t_lat, t_lon, t_alt
        else:
            ratio    = move_dist / dist
            dlat     = t_lat - self.lat
            dlon     = t_lon - self.lon
            dalt     = t_alt - self.altitude_ft
            self.lat += dlat * ratio
            self.lon += dlon * ratio
            self.altitude_ft += dalt * ratio

    def _calculate_terminal_pk(self) -> float:
        ballistic_mult = 1.0
        if self.is_ballistic:
            actual_dist_to_target = haversine(self.impact_lat, self.impact_lon, self.target.lat, self.target.lon)
            if self.wdef.cep_km > 0.0:
                ballistic_mult = math.exp(-actual_dist_to_target / self.wdef.cep_km)
            else:
                if actual_dist_to_target > 0.15: return 0.0
        
        range_fraction = self.launch_dist / max(1.0, self.wdef.range_km)
        range_penalty = (range_fraction ** 2) * 0.40 
        
        penalty = getattr(self.shooter, 'inefficiency_penalty', 0.0)
        structural_mult = getattr(self.shooter, 'performance_mult', 1.0)
        if getattr(self.shooter, 'systems', {}).get('weapons') == 'DEGRADED': penalty += 0.25
            
        pk = (self.wdef.base_pk * (1.0 - penalty) * structural_mult) - range_penalty
        pk *= ballistic_mult
        
        track = getattr(self.shooter, 'merged_contacts', {}).get(self.target.uid)
        if track:
            pk -= (track.pos_error_km * 0.05) 
        
        if self.target.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
            g_load = getattr(self.target, 'current_g_load', 1.0)
            spd = getattr(self.target, 'current_speed_kmh', 300.0)
            if g_load > 3.0:
                em_penalty = (g_load - 3.0) * 0.05
                if spd > 1000: em_penalty *= 1.5 
                pk -= em_penalty

        if self.wdef.seeker == "ARM":
            search_on = getattr(self.target, 'search_radar_active', True)
            fc_on = getattr(self.target, 'fc_radar_active', True)
            if not search_on and not fc_on: pk -= 0.60 
            elif not fc_on: pk -= 0.30 

        if self.target.is_jamming and self.target.platform.ecm_rating > 0 and self.launch_dist > BURNTHROUGH_RANGE_KM:
            if self.wdef.seeker in ("ARH", "SARH"): pk -= max(0.0, self.target.platform.ecm_rating - self.wdef.eccm)

        if self.wdef.seeker in ("ARH", "SARH") and self.target.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
            brg_to_msl = bearing(self.target.lat, self.target.lon, self.lat, self.lon)
            aspect = abs(self.target.heading - brg_to_msl) % 360
            if (75 < aspect < 105) or (255 < aspect < 285):
                notch_penalty = 0.10 
                if self.target.altitude_ft < 5000: notch_penalty += 0.15 
                pk -= notch_penalty

        if self.wdef.seeker in ("ARH", "SARH") and self.target.chaff > 0:
            if random.random() < 0.50:
                self.target.chaff -= 1
                pk -= 0.20
        elif self.wdef.seeker == "IR" and self.target.flare > 0:
            if random.random() < 0.50:
                self.target.flare -= 1
                pk -= 0.20

        return 1.0 if self.wdef.inevadable else max(MIN_PK, min(MAX_PK, pk))

    def estimated_pk(self) -> float:
        dist = slant_range_km(self.lat, self.lon, self.altitude_ft,
                              self.target.lat, self.target.lon, self.target.altitude_ft)
        range_fraction = dist / max(1.0, self.wdef.range_km)
        range_penalty = (range_fraction ** 2) * 0.40 
        
        penalty = getattr(self.shooter, 'inefficiency_penalty', 0.0)
        structural_mult = getattr(self.shooter, 'performance_mult', 1.0)
        if getattr(self.shooter, 'systems', {}).get('weapons') == 'DEGRADED': penalty += 0.25
            
        pk = (self.wdef.base_pk * (1.0 - penalty) * structural_mult) - range_penalty
        
        track = getattr(self.shooter, 'merged_contacts', {}).get(self.target.uid)
        if track: pk -= (track.pos_error_km * 0.05)
        
        if self.wdef.seeker == "ARM":
            search_on = getattr(self.target, 'search_radar_active', True)
            fc_on = getattr(self.target, 'fc_radar_active', True)
            if not search_on and not fc_on: pk -= 0.60
            elif not fc_on: pk -= 0.30
            
        if self.target.is_jamming and self.target.platform.ecm_rating > 0 and self.launch_dist > BURNTHROUGH_RANGE_KM:
            if self.wdef.seeker in ("ARH", "SARH"): pk -= max(0.0, self.target.platform.ecm_rating - self.wdef.eccm)
        if self.wdef.seeker in ("ARH", "SARH") and self.target.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
            brg_to_msl = bearing(self.target.lat, self.target.lon, self.shooter.lat, self.shooter.lon)
            aspect = abs(self.target.heading - brg_to_msl) % 360
            if (75 < aspect < 105) or (255 < aspect < 285):
                pk -= 0.10 + (0.15 if self.target.altitude_ft < 5000 else 0.0)
        return max(MIN_PK, min(MAX_PK, pk))

class Unit:
    def __init__(self, uid: str, callsign: str, lat: float, lon: float, side: str, platform: PlatformDef, loadout: dict[str, int], image_path: Optional[str] = None, drunkness: int = 1, corruption: int = 1):
        self.uid        = uid
        self.callsign   = callsign
        self.lat        = lat
        self.lon        = lon
        self.side       = side
        self.platform   = platform
        self.loadout    = dict(loadout)
        self.current_loadout_role: str = "DEFAULT"
        self.image_path = image_path
        self.fuel_kg    = platform.fuel_capacity_kg

        self.hp: float = 1.0
        self.damage_state: str = "OK"
        self.systems = {"search_radar": "OK", "fc_radar": "OK", "mobility": "OK", "weapons": "OK"}
        self.fire_intensity: float = 0.0 
        
        self.drunkness  = drunkness
        self.corruption = corruption

        # FIX: Upgraded waypoints to 3-tuples (lat, lon, target_altitude_ft)
        self.waypoints: list[tuple[float, float, float]] = []
        self.heading    = 0.0
        self.selected   = False
        self.is_detected = False
        self.flash_frames = 0
        self.selected_weapon: Optional[str] = None
        self.auto_engage = True if platform.unit_type in ("tank", "ifv", "apc", "recon", "tank_destroyer", "sam", "artillery") else False
        
        self.roe: str = "FREE" if side == "Red" else "TIGHT" 
        self.mission: Optional[Mission] = None
        
        self.home_uid: str = ""
        self.duty_state: str = "ACTIVE"  
        self.duty_timer: float = 0.0

        self.emcon_state: str = "ACTIVE" 
        self.is_jamming: bool = False
        self.search_radar_active: bool = True
        self.fc_radar_active: bool = True
        self.iff_active: bool = True 
        
        self.chaff: int = platform.chaff_capacity
        self.flare: int = platform.flare_capacity

        self.is_evading: bool = False
        self.last_evasion_time: float = 0.0

        self.weapon_cooldowns: dict[str, float] = {k: 0.0 for k in self.loadout.keys()}
        self.altitude_ft: float = platform.cruise_alt_ft
        self.target_altitude_ft: float = self.altitude_ft

        self.home_lat: float = lat
        self.home_lon: float = lon
        
        self.datalink_active: bool = True
        self.local_contacts: dict[str, Contact] = {}
        self.merged_contacts: dict[str, Contact] = {}

        self.current_speed_kmh: float = platform.speed_kmh
        self.target_heading: float = 0.0
        self.current_g_load: float = 1.0
        
        self.leader_uid: str = ""
        self.formation_slot: int = 0 
        self.is_intercepting: bool = False
        self.saved_waypoints: list[tuple[float, float, float]] = []
        
        self.formation: str = "WEDGE"
        self.flight_doctrine: str = "STANDARD"
        
        self.wra_range_pct: float = 0.90
        self.wra_qty: int = 1
        
        self.is_cranking: bool = False
        self.crank_timer: float = 0.0
        self.crank_heading: float = 0.0

    def set_emcon(self, state: str) -> None:
        self.emcon_state = state
        if state == "SILENT":
            self.search_radar_active = False
            self.fc_radar_active = False
            self.is_jamming = False
            self.iff_active = False
        elif state == "SEARCH_ONLY":
            self.search_radar_active = True
            self.fc_radar_active = False
            self.is_jamming = False
            self.iff_active = True
        elif state == "ACTIVE":
            self.search_radar_active = True
            self.fc_radar_active = True
            self.is_jamming = False
            self.iff_active = True
        elif state == "BLINDING":
            self.search_radar_active = True
            self.fc_radar_active = True
            self.is_jamming = True
            self.iff_active = True

    @property
    def alive(self) -> bool: return self.hp > 0.0

    @property
    def performance_mult(self) -> float:
        if not self.alive: return 0.0
        mult = 1.0
        if self.damage_state == "LIGHT": mult = 0.8
        elif self.damage_state == "MODERATE": mult = 0.6
        elif self.damage_state == "HEAVY": mult = 0.4
        if self.systems["mobility"] == "DEGRADED":
            mult *= 0.6
        elif self.systems["mobility"] == "DESTROYED":
            is_air = self.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs")
            if is_air: mult *= 0.3
            else: mult = 0.0
        return mult

    @property
    def drunkness_label(self) -> str: return {1: "Sober", 2: "Tipsy", 3: "Intoxicated", 4: "Wasted", 5: "Yeltsin"}.get(self.drunkness, "Sober")
    @property
    def corruption_label(self) -> str: return {1: "Clean", 2: "Grass Eater", 3: "Dirty", 4: "Meat Eater", 5: "Shoigu"}.get(self.corruption, "Clean")
    @property
    def inefficiency_penalty(self) -> float: return ((self.drunkness - 1) + (self.corruption - 1)) * 0.08

    def take_damage(self, amount: float, is_dot: bool = False) -> None:
        if not self.alive: return
        self.hp = max(0.0, self.hp - amount)
        
        if self.hp > 0 and amount > 0 and not is_dot:
            sys_hit_chance = (amount / 0.25) * 0.50
            if random.random() < sys_hit_chance:
                sys_key = random.choice(list(self.systems.keys()))
                if self.systems[sys_key] == "OK": self.systems[sys_key] = "DEGRADED"
                elif self.systems[sys_key] == "DEGRADED": self.systems[sys_key] = "DESTROYED"
            
            if random.random() < amount * 1.5:  
                self.fire_intensity = min(1.0, self.fire_intensity + 0.35)
        
        if self.hp <= 0.0:
            self.damage_state = "KILLED"
            self.is_jamming = False 
            self.search_radar_active = False
            self.fc_radar_active = False
            self.iff_active = False
            self.fire_intensity = 0.0
            self.systems = {"search_radar": "DESTROYED", "fc_radar": "DESTROYED", "mobility": "DESTROYED", "weapons": "DESTROYED"}
        elif self.hp <= 0.25: self.damage_state = "HEAVY"
        elif self.hp <= 0.50: self.damage_state = "MODERATE"
        elif self.hp <= 0.75: self.damage_state = "LIGHT"
        else: self.damage_state = "OK"
        
        if self.systems["search_radar"] == "DESTROYED": self.search_radar_active = False
        if self.systems["fc_radar"] == "DESTROYED": self.fc_radar_active = False

    def add_waypoint(self, lat: float, lon: float, alt: float = -1.0) -> None:
        if self.platform.unit_type not in ("fighter", "attacker", "helicopter", "awacs") and self.systems["mobility"] == "DESTROYED":
            return
        self.is_cranking = False
        self.waypoints.append((lat, lon, alt))
        self._recalc_heading()

    def clear_waypoints(self) -> None:
        self.is_cranking = False 
        self.waypoints.clear()

    def _recalc_heading(self) -> None:
        if self.waypoints:
            if self.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
                self.target_heading = bearing(self.lat, self.lon, self.waypoints[0][0], self.waypoints[0][1])
            else:
                self.heading = bearing(self.lat, self.lon, self.waypoints[0][0], self.waypoints[0][1])

    def update(self, sim_delta: float, game_time: float = 0.0) -> None:
        if not self.alive: return
        
        if self.fire_intensity > 0:
            burn_dmg = (self.fire_intensity * 0.015) * sim_delta
            self.take_damage(burn_dmg, is_dot=True)
            dc_effectiveness = 0.025 * self.performance_mult * (1.0 - self.inefficiency_penalty)
            self.fire_intensity -= dc_effectiveness * sim_delta
            if self.fire_intensity <= 0: self.fire_intensity = 0.0
        
        if self.duty_state == "REARMING":
            self.duty_timer -= sim_delta
            if self.duty_timer <= 0:
                self.duty_state = "READY"
                self.duty_timer = 0.0
                
        if self.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs") and self.duty_state != "ACTIVE": return
        
        for k in self.weapon_cooldowns:
            if self.weapon_cooldowns[k] > 0: self.weapon_cooldowns[k] = max(0.0, self.weapon_cooldowns[k] - sim_delta)
        
        if self.altitude_ft != self.target_altitude_ft:
            climb_rate_fps = 166.67 * self.performance_mult
            alt_diff = self.target_altitude_ft - self.altitude_ft
            step = climb_rate_fps * sim_delta
            if abs(alt_diff) <= step: self.altitude_ft = self.target_altitude_ft
            else: self.altitude_ft += math.copysign(step, alt_diff)

        target_spd = self.platform.speed_kmh * self.performance_mult
        is_wingman = bool(getattr(self, 'leader_uid', ""))
        
        if self.mission and self.mission.time_on_target > game_time and self.waypoints and not self.is_evading and not is_wingman:
            dist_to_tgt = 0.0
            curr_pos = (self.lat, self.lon)
            for wp in self.waypoints:
                dist_to_tgt += haversine(curr_pos[0], curr_pos[1], wp[0], wp[1])
                curr_pos = wp
            
            time_rem = self.mission.time_on_target - game_time
            if time_rem > 0:
                req_speed_kmh = (dist_to_tgt / (time_rem / 3600.0))
                target_spd = min(target_spd, req_speed_kmh)
                target_spd = max(350.0, target_spd)
                
        if is_wingman and hasattr(self, 'formation_target_speed') and not self.is_evading:
            target_spd = self.formation_target_speed

        if self.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
            
            if getattr(self, 'is_cranking', False) and not self.is_evading:
                self.crank_timer -= sim_delta
                if self.crank_timer <= 0:
                    self.is_cranking = False
                    self._recalc_heading()
                else:
                    self.target_heading = self.crank_heading
                    target_spd = self.platform.speed_kmh * self.performance_mult 
            
            spd_mps = max(10.0, self.current_speed_kmh / 3.6)
            max_turn_rate = (self.platform.max_g * 9.81) / spd_mps * (180 / math.pi)
            max_turn_deg = max_turn_rate * sim_delta * self.performance_mult

            diff = (self.target_heading - self.heading + 360) % 360
            if diff > 180: diff -= 360

            if abs(diff) <= max_turn_deg:
                self.heading = self.target_heading
                self.current_g_load = 1.0
            else:
                self.heading = (self.heading + math.copysign(max_turn_deg, diff)) % 360
                self.current_g_load = self.platform.max_g * self.performance_mult

            if self.current_g_load > 2.0:
                speed_loss_rate = self.current_g_load * 12.0
                self.current_speed_kmh = max(250.0, self.current_speed_kmh - speed_loss_rate * sim_delta)
            else:
                accel_rate = 60.0
                if self.current_speed_kmh < target_spd:
                    self.current_speed_kmh = min(target_spd, self.current_speed_kmh + accel_rate * sim_delta)
                elif self.current_speed_kmh > target_spd:
                    self.current_speed_kmh = max(target_spd, self.current_speed_kmh - accel_rate * sim_delta)

            move_dist = (self.current_speed_kmh / 3600.0) * sim_delta
            rad = math.radians(self.heading)
            dlat = (math.cos(rad) * move_dist) / 111.32
            cos_lat = math.cos(math.radians(self.lat))
            dlon = (math.sin(rad) * move_dist) / (111.32 * max(0.0001, cos_lat))

            self.lat += dlat
            self.lon += dlon

            if self.waypoints and not self.is_evading and not self.is_cranking:
                tlat, tlon, talt = self.waypoints[0]
                dist_to_wp = haversine(self.lat, self.lon, tlat, tlon)
                self.target_heading = bearing(self.lat, self.lon, tlat, tlon)
                
                # Dynamically adjust altitude to match waypoint if requested
                if talt >= 0.0 and not is_wingman:
                    self.target_altitude_ft = talt
                    
                if dist_to_wp < 1.0: 
                    self.waypoints.pop(0)
                    if self.waypoints:
                        next_lat, next_lon, next_alt = self.waypoints[0]
                        self.target_heading = bearing(self.lat, self.lon, next_lat, next_lon)
                        if next_alt >= 0.0 and not is_wingman:
                            self.target_altitude_ft = next_alt
                        
        elif self.waypoints:
            speed_kms    = (self.platform.speed_kmh * self.performance_mult) / 3600.0
            dist_budget  = speed_kms * sim_delta
            while dist_budget > 0 and self.waypoints:
                tlat, tlon, _ = self.waypoints[0]
                dlat         = tlat - self.lat
                dlon         = tlon - self.lon
                lat_km       = dlat * 111.32
                lon_km       = dlon * 111.32 * math.cos(math.radians(self.lat))
                dist_to_wp   = math.hypot(lat_km, lon_km)
                
                if dist_to_wp <= dist_budget:
                    self.lat, self.lon = tlat, tlon
                    dist_budget -= dist_to_wp
                    self.waypoints.pop(0)
                    self._recalc_heading()
                else:
                    ratio      = dist_budget / dist_to_wp
                    self.lat  += dlat * ratio
                    self.lon  += dlon * ratio
                    dist_budget = 0
                    self._recalc_heading()

        if self.fuel_kg > 0 and self.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs"):
            burn_per_sec = (self.platform.fuel_burn_rate_kg_h / 3600.0) * (1.0 + self.inefficiency_penalty)
            self.fuel_kg -= burn_per_sec * sim_delta
            if self.fuel_kg <= 0:
                self.fuel_kg = 0
                self.take_damage(999.0) 

    def has_ammo(self, weapon_key: str) -> bool: return self.loadout.get(weapon_key, 0) > 0
    def expend_round(self, weapon_key: str) -> bool:
        if self.has_ammo(weapon_key):
            self.loadout[weapon_key] -= 1
            return True
        return False

    def cycle_loadout(self, db: "Database") -> str:
        roles = ["DEFAULT", "A2A", "A2G", "SEAD"]
        idx = (roles.index(self.current_loadout_role) + 1) % len(roles)
        new_role = roles[idx]
        self.current_loadout_role = new_role
        if new_role == "DEFAULT": self.loadout = dict(self.platform.default_loadout); return new_role
        new_loadout = {}
        guns = {w: 1 for w in self.platform.available_weapons if db.weapons.get(w) and db.weapons[w].is_gun}
        new_loadout.update(guns)
        aw = [w for w in self.platform.available_weapons if db.weapons.get(w) and not db.weapons[w].is_gun]
        
        if new_role == "A2A":
            a2a = [w for w in aw if db.weapons.get(w) and db.weapons[w].domain == "air"]
            for w in a2a: new_loadout[w] = 4 
        elif new_role == "A2G":
            a2g = [w for w in aw if db.weapons.get(w) and db.weapons[w].domain == "ground" and db.weapons[w].seeker != "ARM"]
            a2a = [w for w in aw if db.weapons.get(w) and db.weapons[w].domain == "air"]
            for w in a2g: new_loadout[w] = 4
            if a2a: new_loadout[a2a[0]] = 2 
        elif new_role == "SEAD":
            arms = [w for w in aw if db.weapons.get(w) and db.weapons[w].seeker == "ARM"]
            a2a = [w for w in aw if db.weapons.get(w) and db.weapons[w].domain == "air"]
            if arms:
                for w in arms: new_loadout[w] = 4
            else:
                a2g = [w for w in aw if db.weapons.get(w) and db.weapons[w].domain == "ground"]
                for w in a2g: new_loadout[w] = 4
            if a2a: new_loadout[a2a[0]] = 2
            
        self.loadout = new_loadout
        self.weapon_cooldowns = {k: 0.0 for k in self.loadout.keys()}
        return new_role

    def set_loadout_role(self, db: "Database", role: str) -> None:
        roles = ["DEFAULT", "A2A", "A2G", "SEAD"]
        target = role if role in roles else "DEFAULT"
        attempts = 0
        while self.current_loadout_role != target and attempts < 5:
            self.cycle_loadout(db)
            attempts += 1

    def best_weapon_for(self, db: "Database", target: "Unit") -> Optional[str]:
        if self.systems["weapons"] == "DESTROYED": return None
        
        target_is_air = target.platform.unit_type in ("fighter", "attacker", "helicopter", "awacs")
        best_key, best_score = None, -1.0
        is_sead = self.mission and self.mission.mission_type == "SEAD"
        is_strike = self.mission and self.mission.mission_type == "STRIKE"
        
        for wkey, qty in self.loadout.items():
            if qty <= 0: continue
            wdef = db.weapons.get(wkey)
            if not wdef: continue
            if wdef.domain == "air" and not target_is_air: continue
            if wdef.domain == "ground" and target_is_air: continue
            if wdef.seeker == "ARM" and not (getattr(target, 'search_radar_active', True) or getattr(target, 'fc_radar_active', True)): continue 
            
            score = wdef.range_km + (wdef.damage * 100) + (wdef.base_pk * 50)
            
            if is_sead and wdef.seeker == "ARM": score += 10000 
            elif not target_is_air and target.platform.unit_type in ("sam", "airbase") and wdef.seeker == "ARM": score += 5000
            
            if is_strike and not target_is_air and wdef.range_km > 20: score += 5000
            
            dist = slant_range_km(self.lat, self.lon, self.altitude_ft, target.lat, target.lon, target.altitude_ft)
            if dist < 15.0 and wdef.domain == "air":
                if wdef.seeker == "IR": score += 2000 
                
            if score > best_score:
                best_key, best_score = wkey, score
        return best_key

    def trigger_flash(self, frames: int = 12) -> None: self.flash_frames = frames
    def tick_flash(self) -> None:
        if self.flash_frames > 0: self.flash_frames -= 1
    def is_clicked(self, screen_pos: tuple[int, int], sx: float, sy: float, radius: int = 16) -> bool:
        return math.hypot(sx - screen_pos[0], sy - screen_pos[1]) <= radius


class Database:
    def __init__(self, weapons_path: str = None, units_path: str = None):
        self.weapons:   dict[str, WeaponDef]   = {}
        self.platforms: dict[str, PlatformDef] = {}

        raw_weapons = {}
        if weapons_path and os.path.exists(weapons_path):
            with open(weapons_path, encoding="utf-8") as fh: raw_weapons = json.load(fh)

        for key, d in raw_weapons.items():
            profile = d.get("flight_profile")
            if not profile:
                if d.get("seeker") == "SARH" or (d.get("range_km", 0) > 40 and d.get("domain") == "air"): profile = "lofted"
                elif "AShM" in d.get("description", "") or "Sea" in d.get("display_name", ""): profile = "sea_skimming"
                elif d.get("range_km", 0) > 100 and d.get("domain") == "ground": profile = "terrain_following"
                elif "ARTY" in key or "GMLRS" in key or "ROCKET" in key: profile = "ballistic"
                else: profile = "direct"

            w_rcs = float(d.get("rcs_m2", -1.0))
            if w_rcs < 0:
                if d.get("is_gun", False): w_rcs = 0.01
                elif "ARTY" in key: w_rcs = 0.02
                elif "ROCKET" in key or "GMLRS" in key: w_rcs = 0.04
                elif "Shadow" in key or "Low-observable" in d.get("description", ""): w_rcs = 0.005
                elif float(d.get("damage", 0)) > 1.5: w_rcs = 0.25 
                elif float(d.get("damage", 0)) > 0.8: w_rcs = 0.15 
                else: w_rcs = 0.10 

            self.weapons[key] = WeaponDef(
                key=key, display_name=d["display_name"], seeker=d["seeker"], range_km=d["range_km"],
                min_range_km=d["min_range_km"], speed_kmh=d["speed_kmh"], base_pk=d["base_pk"],
                is_gun=d["is_gun"], description=d["description"], domain=d.get("domain", "both"),
                damage=float(d.get("damage", 0.6)), reload_time_s=float(d.get("reload_time_s", 0.5 if d.get("is_gun") else 3.0)),
                eccm=float(d.get("eccm", 0.1)), cep_km=float(d.get("cep_km", 0.0)),
                splash_radius_km=float(d.get("splash_radius_km", 0.0)),
                is_point_defense=d.get("point_defense", False),
                inevadable=d.get("inevadable", False), flight_profile=profile,
                rcs_m2=w_rcs
            )

        raw_platforms = {}
        if units_path and os.path.exists(units_path):
            with open(units_path, encoding="utf-8") as fh: raw_platforms = json.load(fh)

        for key, d in raw_platforms.items():
            r_rng = d["radar"]["range_km"]
            esm_val = float(d.get("esm_range_km", r_rng * 1.5 if r_rng > 0 else (10.0 if d["type"] != "tank" else 0.0)))
            ir_val  = float(d.get("ir_range_km", 40.0 if d["type"] in ("fighter", "attacker", "awacs") else 8.0))
            
            mg = d.get("max_g")
            if not mg:
                if d["type"] == "fighter": mg = 9.0
                elif d["type"] == "attacker": mg = 5.0
                elif d["type"] == "helicopter": mg = 3.5
                elif d["type"] == "awacs": mg = 2.5
                else: mg = 1.0
            
            self.platforms[key] = PlatformDef(
                key=key, display_name=d["display_name"], unit_type=d["type"], speed_kmh=d["speed_kmh"],
                ceiling_ft=d["ceiling_ft"], ecm_rating=d["ecm_rating"],
                chaff_capacity=int(d.get("chaff_capacity", 30 if d["type"] in ("fighter", "attacker", "helicopter", "awacs") else 0)),
                flare_capacity=int(d.get("flare_capacity", 30 if d["type"] in ("fighter", "attacker", "helicopter", "awacs") else 0)),
                fuel_capacity_kg=d.get("fuel_capacity_kg", 5000.0), fuel_burn_rate_kg_h=d.get("fuel_burn_rate_kg_h", 1500.0),
                radar_range_km=r_rng, radar_type=d["radar"]["type"], radar_modes=tuple(d["radar"]["modes"]),
                esm_range_km=esm_val, ir_range_km=ir_val,
                default_loadout=d["default_loadout"], available_weapons=tuple(d.get("available_weapons", list(d["default_loadout"].keys()))),
                fleet_count=d.get("fleet_count", 0), player_side=d.get("player_side", "Any"),
                rcs_m2=float(d.get("rcs_m2", 5.0)), cruise_alt_ft=float(d.get("cruise_alt_ft", 0)), rearm_time_s=float(d.get("rearm_time_s", 120.0)),
                max_g=float(mg), value_points=int(d.get("value_points", 10))
            )

def load_scenario(path: str, db: Database) -> tuple[list[Unit], dict, list[GameEvent]]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    units: list[Unit] = []
    for ud in data.get("units", []):
        platform_key = ud["platform"]
        platform     = db.platforms.get(platform_key)
        if platform is None:
            continue

        loadout = ud.get("loadout", platform.default_loadout)

        unit = Unit(
            uid        = ud["id"],
            callsign   = ud["callsign"],
            lat        = ud["lat"],
            lon        = ud["lon"],
            side       = ud["side"],
            platform   = platform,
            loadout    = loadout,
            image_path = ud.get("image_path"),
            drunkness  = ud.get("drunkness", 1),
            corruption = ud.get("corruption", 1)
        )
        
        sys_data = ud.get("systems", {"search_radar": "OK", "fc_radar": "OK", "mobility": "OK", "weapons": "OK"})
        if "radar" in sys_data:
            r_state = sys_data.pop("radar")
            sys_data["search_radar"] = r_state
            sys_data["fc_radar"] = r_state
        if "search_radar" not in sys_data: sys_data["search_radar"] = "OK"
        if "fc_radar" not in sys_data: sys_data["fc_radar"] = "OK"
        
        unit.systems = sys_data
        unit.fire_intensity = ud.get("fire_intensity", 0.0)
        unit.roe = ud.get("roe", "FREE" if unit.side == "Red" else "TIGHT")
        
        unit.emcon_state = ud.get("emcon_state", "ACTIVE")
        unit.formation = ud.get("formation", "WEDGE")
        unit.flight_doctrine = ud.get("flight_doctrine", "STANDARD")
        unit.wra_range_pct = ud.get("wra_range_pct", 0.90)
        unit.wra_qty = ud.get("wra_qty", 1)
        unit.set_emcon(unit.emcon_state)
        
        unit.home_uid = ud.get("home_uid", "")
        unit.duty_state = ud.get("duty_state", "ACTIVE")
        unit.duty_timer = ud.get("duty_timer", 0.0)

        mdata = ud.get("mission")
        if mdata:
            unit.mission = Mission(
                name=mdata["name"],
                mission_type=mdata["type"],
                target_lat=mdata["lat"],
                target_lon=mdata["lon"],
                radius_km=mdata["radius"],
                altitude_ft=mdata["alt"],
                rtb_fuel_pct=mdata["rtb_fuel"],
                time_on_target=mdata.get("time_on_target", 0.0),
                package_id=mdata.get("package_id", "")
            )
            
        for wp in ud.get("waypoints", []):
            if len(wp) >= 3:
                unit.add_waypoint(wp[0], wp[1], wp[2])
            else:
                unit.add_waypoint(wp[0], wp[1], -1.0)

        units.append(unit)

    meta = {
        "name":        data.get("name",        "Unnamed Scenario"),
        "description": data.get("description", ""),
        "start_lat":   data.get("start_lat",   50.0),
        "start_lon":   data.get("start_lon",   30.0),
        "start_zoom":  data.get("start_zoom",  7),
    }
    
    events = []
    for ed in data.get("events", []):
        events.append(GameEvent(
            id=ed["id"], condition_type=ed["condition_type"], condition_val=str(ed["condition_val"]),
            action_type=ed["action_type"], action_val=str(ed["action_val"]), triggered=ed.get("triggered", False)
        ))
        
    return units, meta, events

def save_scenario(path: str, units: list[Unit], meta: dict, events: list[GameEvent], game_time: float = 0.0) -> None:
    units_data = []
    for u in units:
        entry = {
            "id":         u.uid,
            "platform":   u.platform.key,
            "callsign":   u.callsign,
            "side":       u.side,
            "lat":        round(u.lat, 6),
            "lon":        round(u.lon, 6),
            "image_path": u.image_path,
            "drunkness":  u.drunkness,
            "corruption": u.corruption,
            "systems":    u.systems,
            "fire_intensity": u.fire_intensity,
            "loadout":    u.loadout,
            "roe":        u.roe,
            "emcon_state": u.emcon_state,
            "formation":   u.formation,
            "flight_doctrine": u.flight_doctrine,
            "wra_range_pct": u.wra_range_pct,
            "wra_qty":     u.wra_qty,
            "home_uid":   u.home_uid,
            "duty_state": u.duty_state,
            "duty_timer": round(u.duty_timer, 1),
            "waypoints":  [[round(wp[0], 6), round(wp[1], 6), round(wp[2], 1)]
                           for wp in u.waypoints],
        }
        if u.mission:
            entry["mission"] = {
                "name": u.mission.name,
                "type": u.mission.mission_type,
                "lat": round(u.mission.target_lat, 6),
                "lon": round(u.mission.target_lon, 6),
                "radius": round(u.mission.radius_km, 2),
                "alt": round(u.mission.altitude_ft, 2),
                "rtb_fuel": round(u.mission.rtb_fuel_pct, 2),
                "time_on_target": round(u.mission.time_on_target, 1),
                "package_id": u.mission.package_id
            }
        units_data.append(entry)

    events_data = [{
        "id": e.id, "condition_type": e.condition_type, "condition_val": e.condition_val,
        "action_type": e.action_type, "action_val": e.action_val, "triggered": e.triggered
    } for e in events]

    payload = {
        **meta,
        "game_time_seconds": round(game_time, 1),
        "units": units_data,
        "events": events_data
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

def save_deployment(path: str, units: list[Unit]) -> None:
    units_data = []
    for u in units:
        if u.side != "Blue": continue
        entry = {
            "id":         u.uid,
            "platform":   u.platform.key,
            "callsign":   u.callsign,
            "side":       u.side,
            "lat":        round(u.lat, 6),
            "lon":        round(u.lon, 6),
            "image_path": u.image_path,
            "drunkness":  u.drunkness,
            "corruption": u.corruption,
            "systems":    u.systems,
            "fire_intensity": u.fire_intensity,
            "loadout":    u.loadout,
            "roe":        u.roe,
            "emcon_state": u.emcon_state,
            "formation":   u.formation,
            "flight_doctrine": u.flight_doctrine,
            "wra_range_pct": u.wra_range_pct,
            "wra_qty":     u.wra_qty,
            "home_uid":   u.home_uid,
            "duty_state": u.duty_state,
            "duty_timer": round(u.duty_timer, 1),
            "waypoints":  [[round(wp[0], 6), round(wp[1], 6), round(wp[2], 1)]
                           for wp in u.waypoints],
        }
        if u.mission:
            entry["mission"] = {
                "name": u.mission.name,
                "type": u.mission.mission_type,
                "lat": round(u.mission.target_lat, 6),
                "lon": round(u.mission.target_lon, 6),
                "radius": round(u.mission.radius_km, 2),
                "alt": round(u.mission.altitude_ft, 2),
                "rtb_fuel": round(u.mission.rtb_fuel_pct, 2),
                "time_on_target": round(u.mission.time_on_target, 1),
                "package_id": u.mission.package_id
            }
        units_data.append(entry)
        
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"deployment": units_data}, fh, indent=2)

def load_deployment(path: str, db: Database) -> list[Unit]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    
    units: list[Unit] = []
    for ud in data.get("deployment", []):
        platform_key = ud["platform"]
        platform     = db.platforms.get(platform_key)
        if platform is None:
            continue

        loadout = ud.get("loadout", platform.default_loadout)

        unit = Unit(
            uid        = ud["id"], 
            callsign   = ud["callsign"],
            lat        = ud["lat"],
            lon        = ud["lon"],
            side       = ud["side"],
            platform   = platform,
            loadout    = loadout,
            image_path = ud.get("image_path"),
            drunkness  = ud.get("drunkness", 1),
            corruption = ud.get("corruption", 1)
        )
        
        sys_data = ud.get("systems", {"search_radar": "OK", "fc_radar": "OK", "mobility": "OK", "weapons": "OK"})
        if "radar" in sys_data:
            r_state = sys_data.pop("radar")
            sys_data["search_radar"] = r_state
            sys_data["fc_radar"] = r_state
        if "search_radar" not in sys_data: sys_data["search_radar"] = "OK"
        if "fc_radar" not in sys_data: sys_data["fc_radar"] = "OK"
        
        unit.systems = sys_data
        unit.fire_intensity = ud.get("fire_intensity", 0.0)
        unit.roe = ud.get("roe", "TIGHT")
        
        unit.emcon_state = ud.get("emcon_state", "ACTIVE")
        unit.formation = ud.get("formation", "WEDGE")
        unit.flight_doctrine = ud.get("flight_doctrine", "STANDARD")
        unit.wra_range_pct = ud.get("wra_range_pct", 0.90)
        unit.wra_qty = ud.get("wra_qty", 1)
        unit.set_emcon(unit.emcon_state)
        
        unit.home_uid = ud.get("home_uid", "")
        unit.duty_state = ud.get("duty_state", "ACTIVE")
        unit.duty_timer = ud.get("duty_timer", 0.0)

        mdata = ud.get("mission")
        if mdata:
            unit.mission = Mission(
                name=mdata["name"],
                mission_type=mdata["type"],
                target_lat=mdata["lat"],
                target_lon=mdata["lon"],
                radius_km=mdata["radius"],
                altitude_ft=mdata["alt"],
                rtb_fuel_pct=mdata["rtb_fuel"],
                time_on_target=mdata.get("time_on_target", 0.0),
                package_id=mdata.get("package_id", "")
            )
            
        for wp in ud.get("waypoints", []):
            if len(wp) >= 3:
                unit.add_waypoint(wp[0], wp[1], wp[2])
            else:
                unit.add_waypoint(wp[0], wp[1], -1.0)
                
        units.append(unit)
    return units