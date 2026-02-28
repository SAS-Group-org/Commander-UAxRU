# constants.py — all shared constants in one place

# ── Default window size (used only at startup) ───────────────────────────────
# After launch the actual size is read from pygame.display.get_surface().
WINDOW_WIDTH_DEFAULT  = 1024
WINDOW_HEIGHT_DEFAULT = 768
BOTTOM_PANEL_HEIGHT   = 210   # fixed height — does NOT resize with the window
FPS                   = 60
TILE_SIZE             = 256

# ── Unit / map colours ────────────────────────────────────────────────────────
BLUE_UNIT_COLOR    = (0,   200, 255)
RED_UNIT_COLOR     = (255,  60,  60)
SELECTED_COLOR     = (255, 230,   0)
WAYPOINT_COLOR     = (255, 120,  60)
ROUTE_LINE_COLOR   = (120, 160, 120)
RADAR_RING_COLOR   = (0,   160, 160)
MISSILE_BLUE_COLOR = (100, 200, 255)
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