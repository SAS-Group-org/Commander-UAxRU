# constants.py — all shared constants in one place

# ── Default window size (used only at startup) ───────────────────────────────
WINDOW_WIDTH_DEFAULT    = 1920
WINDOW_HEIGHT_DEFAULT   = 1080
BOTTOM_PANEL_FRACTION   = 0.30
BOTTOM_PANEL_MIN_HEIGHT = 220
FPS                     = 60
TILE_SIZE               = 256

# ── Unit / map colours ────────────────────────────────────────────────────────
BLUE_UNIT_COLOR    = (0,   100, 155)
RED_UNIT_COLOR     = (255,  60,  60)
SELECTED_COLOR     = (255, 230,   0)
WAYPOINT_COLOR     = (255, 120,  60)
ROUTE_LINE_COLOR   = (120, 160, 120)
RADAR_RING_COLOR   = (0,   160, 160)
MISSILE_BLUE_COLOR = (50,  100, 155)
MISSILE_RED_COLOR  = (255, 120,  50)
TRAIL_COLOR        = (255, 200,  80)
PANEL_BG           = (18,   26,  34)
TEXT_COLOR         = (200, 215, 225)
LOG_COLOR          = (160, 200, 160)

# Contact classification colours (used by renderer)
CONTACT_FAINT_COLOR    = (140, 140, 140)   # grey blip — barely detected
CONTACT_PROBABLE_COLOR = (220, 160,  60)   # amber — type resolved
CONTACT_CONFIRM_COLOR  = (255,  60,  60)   # red — full resolution

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