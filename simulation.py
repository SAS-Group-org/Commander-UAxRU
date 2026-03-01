# simulation.py — real-time simulation engine
#
# AI behaviour:
#   Air units  — patrol waypoints → RTB → idle; engage air/ground in radar+weapon range
#   Ground units — stationary (no patrol); engage enemies in detection range
#                  guns have short cooldown (8s), missiles have long cooldown (45s)

from __future__ import annotations

from collections import deque
from typing import Optional

from geo import haversine
from scenario import Database, Missile, Unit, WeaponDef

_MAX_LOG = 60

# ── AI tuning ─────────────────────────────────────────────────────────────────
_HOME_ARRIVAL_KM       = 2.0    # arrive-home threshold for air units
_AI_COOLDOWN_MISSILE   = 45.0   # sim-s between missile shots (any unit type)
_AI_COOLDOWN_GUN       = 8.0    # sim-s between gun/cannon shots
_AI_ENGAGE_FRAC        = 0.90   # fire at 90 % of weapon range

# Ground unit types — these never patrol/return; they hold position
_GROUND_TYPES = {"tank", "ifv", "apc", "recon", "tank_destroyer"}


class SimulationEngine:
    """Core simulation loop — call update(real_delta) once per frame."""

    def __init__(self, units: list[Unit], db: Database):
        self.units:    list[Unit]    = units
        self.missiles: list[Missile] = []
        self.db:       Database      = db

        self.game_time:        float = 0.0
        self.time_compression: int   = 1
        self.paused:           bool  = False

        self.event_log: deque[str] = deque(maxlen=_MAX_LOG)
        self.log(f"Scenario loaded — {len(units)} units ready.")

    # ── Public ───────────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        self.event_log.append(f"[{self._fmt_time(self.game_time)}] {msg}")

    def set_compression(self, factor: int) -> None:
        self.time_compression = factor
        self.paused = (factor == 0)
        self.log("PAUSED" if self.paused else f"Time compression → {factor}×")

    # ── Main update ───────────────────────────────────────────────────────────

    def update(self, real_delta: float) -> None:
        if self.paused or self.time_compression == 0:
            return

        sim_delta = real_delta * self.time_compression
        self.game_time += sim_delta

        self._move_units(sim_delta)
        self._red_ai(sim_delta)
        self._move_missiles(sim_delta)
        self._resolve_missile_outcomes()
        self._purge_dead()
        self._update_fog_of_war()
        self._tick_flashes()

    # ── Player-initiated combat ───────────────────────────────────────────────

    def fire_weapon(self, shooter: Unit, target: Unit,
                    weapon_key: str) -> Optional[Missile]:
        wdef = self.db.weapons.get(weapon_key)
        if wdef is None:
            self.log(f"{shooter.callsign}: unknown weapon '{weapon_key}'.")
            return None
        if not shooter.has_ammo(weapon_key):
            self.log(f"{shooter.callsign}: out of {wdef.display_name}.")
            return None

        dist = haversine(shooter.lat, shooter.lon, target.lat, target.lon)
        if dist < wdef.min_range_km:
            self.log(f"{shooter.callsign}: target inside min range "
                     f"({dist:.1f} km < {wdef.min_range_km} km).")
            return None
        if dist > wdef.range_km:
            self.log(f"{shooter.callsign}: target out of range "
                     f"({dist:.1f} km > {wdef.range_km} km).")
            return None

        shooter.expend_round(weapon_key)
        missile = Missile(shooter.lat, shooter.lon, target, shooter.side, wdef)
        self.missiles.append(missile)

        est_pk = missile.estimated_pk()
        fox = {"SARH": "Fox 1", "IR": "Fox 2", "ARH": "Fox 3",
               "CANNON": "Guns", "SACLOS": "ATGM", "LASER": "ATGM"
               }.get(wdef.seeker, "Fox 3")
        self.log(f"{shooter.callsign}: {fox}! {wdef.display_name} → "
                 f"{target.callsign}  dist={dist:.1f} km  "
                 f"est.Pk={int(est_pk * 100)}%")
        return missile

    # ── Internal steps ────────────────────────────────────────────────────────

    def _move_units(self, sim_delta: float) -> None:
        for u in self.units:
            u.update(sim_delta)

    def _move_missiles(self, sim_delta: float) -> None:
        for m in self.missiles:
            m.update(sim_delta)

    # ── Red AI ────────────────────────────────────────────────────────────────

    def _red_ai(self, sim_delta: float) -> None:
        """AI for all Red units.

        Air units  — patrol → RTB → idle, engage air targets.
        Ground units — stationary, engage any Blue in range (guns + ATGMs).
        """
        blue_units = [u for u in self.units if u.side == "Blue" and u.alive]
        if not blue_units:
            return

        for red in self.units:
            if red.side != "Red" or not red.alive:
                continue

            is_ground = red.platform.unit_type in _GROUND_TYPES

            # ── Tick cooldowns ────────────────────────────────────────────────
            # Per-unit we store one cooldown; ground units need two (gun + missile).
            # We piggyback: ai_fire_cooldown covers missiles; a second attribute
            # covers guns, created lazily here.
            if red.ai_fire_cooldown > 0:
                red.ai_fire_cooldown = max(0.0, red.ai_fire_cooldown - sim_delta)
            if not hasattr(red, "ai_gun_cooldown"):
                red.ai_gun_cooldown = 0.0  # type: ignore[attr-defined]
            if red.ai_gun_cooldown > 0:  # type: ignore[attr-defined]
                red.ai_gun_cooldown = max(  # type: ignore[attr-defined]
                    0.0, red.ai_gun_cooldown - sim_delta)  # type: ignore[attr-defined]

            # ── Air unit patrol / return ──────────────────────────────────────
            if not is_ground:
                if not red.waypoints:
                    if red.ai_state == "patrol":
                        dist_home = haversine(red.lat, red.lon,
                                              red.home_lat, red.home_lon)
                        if dist_home > _HOME_ARRIVAL_KM:
                            red.add_waypoint(red.home_lat, red.home_lon)
                            red.ai_state = "returning"
                            self.log(f"{red.callsign}: patrol complete — RTB.")
                        else:
                            red.ai_state = "idle"
                    elif red.ai_state == "returning":
                        dist_home = haversine(red.lat, red.lon,
                                              red.home_lat, red.home_lon)
                        if dist_home <= _HOME_ARRIVAL_KM:
                            red.ai_state = "idle"
                            self.log(f"{red.callsign}: at base — holding.")

            # ── Engagement ────────────────────────────────────────────────────
            self._red_engage(red, blue_units, is_ground)

    def _red_engage(self, red: Unit, blue_units: list[Unit],
                    is_ground: bool) -> None:
        """Try to fire at the nearest valid Blue target."""

        gun_ready     = (red.ai_gun_cooldown <= 0)       # type: ignore[attr-defined]
        missile_ready = (red.ai_fire_cooldown <= 0)

        if not gun_ready and not missile_ready:
            return   # everything cooling down

        # Build candidate weapon list sorted by range descending
        # Ground units can also fire guns; air units prefer non-gun missiles
        candidates: list[tuple[str, WeaponDef]] = []
        for wkey, qty in red.loadout.items():
            if qty <= 0:
                continue
            wdef = self.db.weapons.get(wkey)
            if wdef is None:
                continue
            if wdef.is_gun and not gun_ready:
                continue
            if not wdef.is_gun and not missile_ready:
                continue
            # Air units skip gun weapons for AI (too short range to be useful)
            if not is_ground and wdef.is_gun:
                continue
            candidates.append((wkey, wdef))

        if not candidates:
            return

        # Sort: missiles first (longer range), then guns
        candidates.sort(key=lambda x: -x[1].range_km)

        # Find nearest Blue target in detection + weapon range
        for wkey, wdef in candidates:
            target      = None
            target_dist = float("inf")
            engage_range = wdef.range_km * _AI_ENGAGE_FRAC

            for blue in blue_units:
                dist = haversine(red.lat, red.lon, blue.lat, blue.lon)
                if dist > red.platform.radar_range_km:
                    continue
                if dist > engage_range or dist < wdef.min_range_km:
                    continue
                if dist < target_dist:
                    target      = blue
                    target_dist = dist

            if target is None:
                continue   # no target in this weapon's envelope

            # Fire
            red.expend_round(wkey)
            missile = Missile(red.lat, red.lon, target, "Red", wdef)
            self.missiles.append(missile)

            if wdef.is_gun:
                red.ai_gun_cooldown = _AI_COOLDOWN_GUN  # type: ignore[attr-defined]
            else:
                red.ai_fire_cooldown = _AI_COOLDOWN_MISSILE

            est_pk  = missile.estimated_pk()
            fire_tag = {"SACLOS": "ATGM", "LASER": "ATGM",
                        "CANNON": "Guns"}.get(wdef.seeker, "Fire")
            self.log(f"⚠ {red.callsign}: {fire_tag}! {wdef.display_name} → "
                     f"{target.callsign}  {target_dist:.1f} km  "
                     f"Pk={int(est_pk*100)}%")
            return   # one shot per AI tick

    # ── Missile resolution ────────────────────────────────────────────────────

    def _resolve_missile_outcomes(self) -> None:
        for m in self.missiles:
            if not m.active and m.status in ("HIT", "MISSED"):
                if m.status == "HIT":
                    if m.target.alive:
                        m.target.trigger_flash()
                    self.log(f"SPLASH! {m.target.callsign} destroyed "
                             f"by {m.wdef.display_name}.")
                else:
                    self.log(f"{m.target.callsign} survived "
                             f"{m.wdef.display_name}.")

    # ── Fog of war ────────────────────────────────────────────────────────────

    def _update_fog_of_war(self) -> None:
        blue_units = [u for u in self.units if u.side == "Blue" and u.alive]
        red_units  = [u for u in self.units if u.side == "Red"  and u.alive]

        for u in red_units:
            u.is_detected = False
        for u in blue_units:
            u.is_detected = False

        for red in red_units:
            for blue in blue_units:
                if haversine(blue.lat, blue.lon, red.lat, red.lon) \
                        <= blue.platform.radar_range_km:
                    red.is_detected = True
                    break

        for blue in blue_units:
            for red in red_units:
                if haversine(red.lat, red.lon, blue.lat, blue.lon) \
                        <= red.platform.radar_range_km:
                    blue.is_detected = True
                    break

    def _tick_flashes(self) -> None:
        for u in self.units:
            u.tick_flash()

    def _purge_dead(self) -> None:
        self.missiles = [m for m in self.missiles if m.active]
        self.units    = [u for u in self.units    if u.alive]

    # ── Queries ───────────────────────────────────────────────────────────────

    def blue_units(self) -> list[Unit]:
        return [u for u in self.units if u.side == "Blue"]

    def red_units(self) -> list[Unit]:
        return [u for u in self.units if u.side == "Red"]

    def is_game_over(self) -> Optional[str]:
        blues_alive = any(u.alive for u in self.units if u.side == "Blue")
        reds_alive  = any(u.alive for u in self.units if u.side == "Red")
        if not blues_alive and not reds_alive:
            return "Draw"
        if not blues_alive:
            return "Red wins"
        if not reds_alive:
            return "Blue wins"
        return None

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"