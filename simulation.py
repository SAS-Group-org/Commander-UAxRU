# simulation.py — real-time simulation engine
#
# SimulationEngine is the authoritative game-state owner.
# It advances time, moves units and missiles, resolves combat,
# runs fog-of-war, and maintains an event log.
#
# The engine knows nothing about rendering or pygame — it operates
# purely on geo-coordinates and game state.

from __future__ import annotations

from collections import deque
from typing import Optional

from geo import haversine
from scenario import Database, Missile, Unit, WeaponDef


_MAX_LOG = 60       # event log entries to keep


class SimulationEngine:
    """Core simulation loop — call update(real_delta) once per frame."""

    def __init__(self, units: list[Unit], db: Database):
        self.units:    list[Unit]    = units
        self.missiles: list[Missile] = []
        self.db:       Database      = db

        # Time state
        self.game_time:        float = 0.0   # total sim-seconds elapsed
        self.time_compression: int   = 1     # 0 = paused
        self.paused:           bool  = False

        # Event log — deque of plain strings; newest at right
        self.event_log: deque[str] = deque(maxlen=_MAX_LOG)
        self.log(f"Scenario loaded — {len(units)} units ready.")

    # ── Public helpers ────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        t = self._fmt_time(self.game_time)
        self.event_log.append(f"[{t}] {msg}")

    def set_compression(self, factor: int) -> None:
        self.time_compression = factor
        self.paused = (factor == 0)
        label = "PAUSED" if self.paused else f"{factor}x"
        self.log(f"Time compression → {label}")

    # ── Main update — called every frame ─────────────────────────────────────

    def update(self, real_delta: float) -> None:
        """Advance simulation by real_delta real-seconds × time_compression."""
        if self.paused or self.time_compression == 0:
            return

        sim_delta = real_delta * self.time_compression
        self.game_time += sim_delta

        self._move_units(sim_delta)
        self._move_missiles(sim_delta)
        self._resolve_missile_outcomes()  # must run before _purge_dead
        self._purge_dead()
        self._update_fog_of_war()
        self._tick_flashes()

    # ── Combat ───────────────────────────────────────────────────────────────

    def fire_weapon(self, shooter: Unit, target: Unit,
                    weapon_key: str) -> Optional[Missile]:
        """Attempt to fire weapon_key from shooter at target.

        Returns the new Missile on success, or None with a reason logged.
        """
        wdef: Optional[WeaponDef] = self.db.weapons.get(weapon_key)
        if wdef is None:
            self.log(f"{shooter.callsign}: unknown weapon '{weapon_key}'.")
            return None

        if not shooter.has_ammo(weapon_key):
            self.log(f"{shooter.callsign}: out of {wdef.display_name}.")
            return None

        dist = haversine(shooter.lat, shooter.lon, target.lat, target.lon)

        if dist < wdef.min_range_km:
            self.log(f"{shooter.callsign}: target inside minimum range "
                     f"({dist:.1f} km < {wdef.min_range_km} km).")
            return None

        if dist > wdef.range_km:
            self.log(f"{shooter.callsign}: target out of range "
                     f"({dist:.1f} km > {wdef.range_km} km).")
            return None

        shooter.expend_round(weapon_key)
        missile = Missile(shooter.lat, shooter.lon, target,
                          shooter.side, wdef)
        self.missiles.append(missile)

        est_pk = missile.estimated_pk()
        fox = {"SARH": "Fox 1", "IR": "Fox 2",
               "ARH":  "Fox 3", "CANNON": "Guns"}.get(wdef.seeker, "Fox 3")
        self.log(
            f"{shooter.callsign}: {fox}! {wdef.display_name} → "
            f"{target.callsign}  dist={dist:.0f} km  "
            f"est.Pk={int(est_pk * 100)}%"
        )
        return missile

    # ── Internal update steps ─────────────────────────────────────────────────

    def _move_units(self, sim_delta: float) -> None:
        for u in self.units:
            u.update(sim_delta)

    def _move_missiles(self, sim_delta: float) -> None:
        for m in self.missiles:
            m.update(sim_delta)

    def _resolve_missile_outcomes(self) -> None:
        """Log hits/misses for missiles that just became inactive.
        Called before _purge_dead so each missile is processed exactly once.
        """
        for m in self.missiles:
            if not m.active and m.status in ("HIT", "MISSED"):
                if m.status == "HIT":
                    if m.target.alive:        # target may already be dead
                        m.target.trigger_flash()
                    self.log(
                        f"SPLASH! {m.target.callsign} destroyed "
                        f"by {m.wdef.display_name}."
                    )
                else:
                    self.log(
                        f"{m.target.callsign} defeated {m.wdef.display_name} "
                        f"(ECM/miss)."
                    )

    def _update_fog_of_war(self) -> None:
        """Reset and recompute detection flags for all enemy units."""
        blue_units = [u for u in self.units if u.side == "Blue" and u.alive]
        red_units  = [u for u in self.units if u.side == "Red"  and u.alive]

        # Reset
        for u in red_units:
            u.is_detected = False
        for u in blue_units:
            u.is_detected = False

        # Blue detects Red
        for red in red_units:
            for blue in blue_units:
                if haversine(blue.lat, blue.lon, red.lat, red.lon) \
                        <= blue.platform.radar_range_km:
                    red.is_detected = True
                    break

        # Red detects Blue
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
        self.units    = [u for u in self.units if u.alive]

    # ── Queries ───────────────────────────────────────────────────────────────

    def blue_units(self) -> list[Unit]:
        return [u for u in self.units if u.side == "Blue"]

    def red_units(self) -> list[Unit]:
        return [u for u in self.units if u.side == "Red"]

    def is_game_over(self) -> Optional[str]:
        """Return 'Blue wins', 'Red wins', 'Draw', or None if ongoing."""
        blues_alive = any(u.alive for u in self.units if u.side == "Blue")
        reds_alive  = any(u.alive for u in self.units if u.side == "Red")
        if not blues_alive and not reds_alive:
            return "Draw"
        if not blues_alive:
            return "Red wins"
        if not reds_alive:
            return "Blue wins"
        return None

    # ── Utilities ─────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"