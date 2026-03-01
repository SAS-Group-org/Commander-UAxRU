# constants.py — all shared constants in one place

# ── Default window size (used only at startup) ───────────────────────────────
# After launch the actual size is read from pygame.display.get_surface().
WINDOW_WIDTH_DEFAULT    = 1920
WINDOW_HEIGHT_DEFAULT   = 1080
BOTTOM_PANEL_FRACTION   = 0.30  # Panel takes up 30% of window height
BOTTOM_PANEL_MIN_HEIGHT = 220   # Minimum height so text doesn't overlap
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

# ── Time-compression steps ────────────────────────────────────────────────────
TIME_SPEEDS       = [0, 1, 15, 60, 300]
TIME_SPEED_LABELS = ["PAUSE", "1x", "15x", "60x", "300x"]
DEFAULT_SPEED_IDX = 1

# ── Simulation tuning ─────────────────────────────────────────────────────────
MISSILE_TRAIL_LEN = 24
HIT_FLASH_FRAMES  = 12
MIN_PK            = 0.05
MAX_PK            = 0.95