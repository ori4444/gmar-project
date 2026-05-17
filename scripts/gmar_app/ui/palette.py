# ── Palette ──────────────────────────────────────────────────────────────────
# Warm cream theme — single surface, dark text, soft-brown dividers.

# ── Surfaces (one cream everywhere) ──────────────────────────────────────────
BG          = "#fdf8f1"   # warm light cream — all surfaces
SIDEBAR_BG  = "#fdf8f1"   # same cream
CARD_BG     = "#fdf8f1"   # same cream (no separate card tones)
CARD_BORDER = "transparent"

# ── Brown divider — the only border/line color ────────────────────────────────
# Used for: section separators, sidebar boundary, outer frame, input borders.
DIVIDER     = "#c4a07a"   # sandy warm brown  (3 px thick, everywhere)

# ── Accent colors (adjusted for legibility on cream) ─────────────────────────
INDIGO      = "#4338ca"
INDIGO_D    = "#3730a3"
INDIGO_L    = "#c7d2fe"

GREEN       = "#15803d"
GREEN_D     = "#166534"
GREEN_BG    = "#dcfce7"

RED         = "#dc2626"
RED_D       = "#b91c1c"
RED_BG      = "#fee2e2"

AMBER       = "#b45309"
AMBER_BG    = "#fef3c7"

CYAN        = "#0e7490"
VIOLET      = "#6d28d9"

# ── Text (dark on cream) ──────────────────────────────────────────────────────
TXT         = "#2a1a08"   # very dark warm brown
TXT2        = "#7a5c38"   # medium warm brown
TXT3        = "#b09070"   # muted / placeholder

# ── Section nav ───────────────────────────────────────────────────────────────
NAV_ACTIVE_BG  = INDIGO
NAV_ACTIVE_TXT = "#ffffff"
NAV_IDLE_TXT   = TXT2

# ── Input fields ──────────────────────────────────────────────────────────────
INPUT_BG     = "#f2e6d2"   # slightly deeper cream
INPUT_BORDER = DIVIDER
INPUT_FOCUS  = INDIGO

# ── Per-value accent maps ─────────────────────────────────────────────────────
ATK_COLORS = {
    "drone":    "#2563eb",
    "missile":  "#dc2626",
    "combined": "#6d28d9",
    "unknown":  "#b09070",
}
DMG_COLORS = {
    "low":    "#15803d",
    "medium": "#b45309",
    "high":   "#dc2626",
}
SCALE_COLORS = {
    "few":     "#2563eb",
    "swarm":   "#1d4ed8",
    "massive": "#1e3a8a",
    "":        "#b09070",
}

# ── Fonts ─────────────────────────────────────────────────────────────────────
FONT_FAMILY = "Segoe UI"
FONT_SIZE   = 11
