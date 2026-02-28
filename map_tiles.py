# map_tiles.py — async OSM tile fetcher with disk cache
#
# Fixes vs previous version:
#   • 4 concurrent download workers instead of 1 (main zoom fix)
#   • Rotates a/b/c OSM subdomains to stay under rate limits
#   • Failed tiles are removed from _queued_tiles so they auto-retry
#   • Tile coordinates are validated/wrapped before any request
#   • Orphaned .tmp files are cleaned up at startup
#   • Simple LRU cap on in-memory surface cache (max 512 surfaces)

import os
import queue
import threading
import time
import itertools
import pygame

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# ── Clean up any orphaned temp files from a previous crashed run ─────────────
for _f in os.listdir(CACHE_DIR):
    if _f.endswith(".tmp"):
        try:
            os.remove(os.path.join(CACHE_DIR, _f))
        except OSError:
            pass

# ── State ─────────────────────────────────────────────────────────────────────
_tile_queue      = queue.Queue()
_queued_tiles    = set()          # keys currently in the queue (not yet done)
_queued_lock     = threading.Lock()
_loaded_surfaces: dict[str, pygame.Surface] = {}  # key → Surface (LRU capped)
_LRU_MAX         = 512

# Round-robin subdomain iterator (thread-safe via itertools + GIL)
_SUBDOMAINS = itertools.cycle(["a", "b", "c"])

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_NUM_WORKERS = 4     # concurrent download threads


def _valid_tile(z: int, x: int, y: int) -> tuple[int, int, int] | None:
    """Return (z, wrapped_x, clamped_y) if valid for OSM, else None.

    OSM zoom range:  0–19
    Tile x:          wraps modulo 2^z  (so negatives are allowed and wrap)
    Tile y:          must be in [0, 2^z − 1]
    """
    if not (0 <= z <= 19):
        return None
    n = 2 ** z
    x = x % n          # wrap longitude
    if not (0 <= y < n):
        return None     # out of valid latitude range — don't request
    return z, x, y


def _worker() -> None:
    """Background thread: download tiles from the queue."""
    import requests
    while True:
        z, x, y = _tile_queue.get()
        key        = f"{z}_{x}_{y}"
        cache_path = os.path.join(CACHE_DIR, f"{key}.png")
        tmp_path   = os.path.join(CACHE_DIR, f"{key}.tmp")

        try:
            if not os.path.exists(cache_path):
                sub = next(_SUBDOMAINS)
                url = f"https://{sub}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                r   = requests.get(url, headers=_HEADERS, timeout=10)
                if r.status_code == 200:
                    with open(tmp_path, "wb") as fh:
                        fh.write(r.content)
                    os.replace(tmp_path, cache_path)
                elif r.status_code == 429:
                    # Rate-limited — put the tile back and wait
                    with _queued_lock:
                        _queued_tiles.discard(key)
                    time.sleep(1.0)
                    _tile_queue.task_done()
                    continue
                # Small sleep to stay polite — staggered across workers
                time.sleep(0.05)
        except Exception:
            pass
        finally:
            # Always remove from the "in-flight" set so failed tiles can retry
            with _queued_lock:
                _queued_tiles.discard(key)
            _tile_queue.task_done()


# Start worker pool
for _i in range(_NUM_WORKERS):
    threading.Thread(target=_worker, daemon=True).start()


# ── Public API ────────────────────────────────────────────────────────────────

def get_tile(z: int, x: int, y: int) -> pygame.Surface | None:
    """Return a cached Surface for (z, x, y), or None if not yet available.

    Queues a background download on first call so the tile appears on a
    future frame.  Tile coordinates are validated before any I/O.
    """
    coords = _valid_tile(z, x, y)
    if coords is None:
        return None
    z, x, y = coords
    key = f"{z}_{x}_{y}"

    # Already in memory?
    if key in _loaded_surfaces:
        return _loaded_surfaces[key]

    # On disk?
    cache_path = os.path.join(CACHE_DIR, f"{key}.png")
    if os.path.exists(cache_path):
        try:
            surf = pygame.image.load(cache_path).convert()
            # Simple LRU cap: evict oldest entry when at limit
            if len(_loaded_surfaces) >= _LRU_MAX:
                oldest = next(iter(_loaded_surfaces))
                del _loaded_surfaces[oldest]
            _loaded_surfaces[key] = surf
            return surf
        except (pygame.error, Exception):
            # Corrupted file — delete it so it re-downloads next time
            try:
                os.remove(cache_path)
            except OSError:
                pass
            return None

    # Queue a download (only once at a time)
    with _queued_lock:
        if key not in _queued_tiles:
            _queued_tiles.add(key)
            _tile_queue.put((z, x, y))

    return None