# ── Palette ──────────────────────────────────────────────────────────────────
# Vivid, high-contrast palette with thick borders and bold typography.

BG          = "#0f172a"   # deep navy — main window background
SIDEBAR_BG  = "#1e293b"   # slightly lighter — sidebar
CARD_BG     = "#1e293b"   # card / panel background
CARD_BORDER = "#334155"   # panel border (3 px)

# Accent colors
INDIGO      = "#6366f1"
INDIGO_D    = "#4f46e5"
INDIGO_L    = "#e0e7ff"

GREEN       = "#22c55e"
GREEN_D     = "#16a34a"
GREEN_BG    = "#052e16"

RED         = "#ef4444"
RED_D       = "#dc2626"
RED_BG      = "#450a0a"

AMBER       = "#f59e0b"
AMBER_BG    = "#451a03"

CYAN        = "#06b6d4"
VIOLET      = "#a855f7"

# Text
TXT         = "#f1f5f9"   # primary
TXT2        = "#94a3b8"   # secondary
TXT3        = "#475569"   # muted

# Section nav highlight
NAV_ACTIVE_BG  = INDIGO
NAV_ACTIVE_TXT = "#ffffff"
NAV_IDLE_TXT   = TXT2

# Input fields
INPUT_BG     = "#0f172a"
INPUT_BORDER = "#475569"
INPUT_FOCUS  = INDIGO

# Per-value accent maps
ATK_COLORS = {
    "drone":    "#3b82f6",
    "missile":  "#ef4444",
    "combined": "#a855f7",
    "unknown":  TXT3,
}
DMG_COLORS = {
    "low":    "#22c55e",
    "medium": "#f59e0b",
    "high":   "#ef4444",
}
SCALE_COLORS = {
    "few":     "#60a5fa",
    "swarm":   "#3b82f6",
    "massive": "#1d4ed8",
    "":        TXT3,
}

# Fonts
FONT_FAMILY = "Segoe UI"
FONT_SIZE   = 11
