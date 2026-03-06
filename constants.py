# constants.py — all shared constants in one place

# ── Default window size (used only at startup) ───────────────────────────────
WINDOW_WIDTH_DEFAULT    = 1920
WINDOW_HEIGHT_DEFAULT   = 1080
BOTTOM_PANEL_FRACTION   = 0.30
BOTTOM_PANEL_MIN_HEIGHT = 320 
FPS                     = 60
TILE_SIZE               = 256

# ── Unit / map colours (Updated for NTDS / MIL-STD-2525 aesthetic) ────────────
BLUE_UNIT_COLOR    = (80,  180, 240)   # High-contrast Cyan
RED_UNIT_COLOR     = (255,  80,  80)   # Bright Hostile Red
SELECTED_COLOR     = (255, 230,  50)   # Sharp Yellow
WAYPOINT_COLOR     = (255, 180,  60)   # High-visibility Orange
ROUTE_LINE_COLOR   = (140, 210, 140)   # Bright Route Green
RADAR_RING_COLOR   = (60,  140, 200)   # Muted Cyan for friendly radar
MISSILE_BLUE_COLOR = (140, 220, 255)   # Intense Cyan for friendly munitions
MISSILE_RED_COLOR  = (255, 120, 120)   # Intense Red for hostile munitions
TRAIL_COLOR        = (255, 220, 100)   # Hot Yellow for exhaust trails
PANEL_BG           = (21,   24,  22)   # Deep #151816 matching the UI theme
TEXT_COLOR         = (224, 216, 192)   # Soft off-white #e0d8c0
LOG_COLOR          = (160, 200, 160)   # Soft terminal green

# Contact classification colours (used by renderer)
CONTACT_FAINT_COLOR    = (120, 120, 120)   # Ghost Grey — barely detected
CONTACT_PROBABLE_COLOR = (240, 160,  60)   # Amber Alert — type resolved
CONTACT_CONFIRM_COLOR  = (255,  80,  80)   # Hostile Red — full resolution

# ── Time-compression steps ────────────────────────────────────────────────────
TIME_SPEEDS       = [0, 1, 15, 60, 300]
TIME_SPEED_LABELS = ["PAUSE", "1x", "15x", "60x", "300x"]
DEFAULT_SPEED_IDX = 1

# ── Simulation tuning ─────────────────────────────────────────────────────────
MISSILE_TRAIL_LEN = 24
HIT_FLASH_FRAMES  = 12
MIN_PK            = 0.05
MAX_PK            = 0.95

# ── Electronic Warfare & Countermeasures ──────────────────────────────────────
BURNTHROUGH_RANGE_KM = 15.0  # Radar overpowers jamming if target is within this range
CHAFF_PK_PENALTY     = 0.25  # Pk reduction per dispensed chaff bundle
FLARE_PK_PENALTY     = 0.25  # Pk reduction per dispensed flare