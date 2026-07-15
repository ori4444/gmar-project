# ── Palette — dark intelligence-platform theme ─────────────────────────────
# Blend: Linear (50%) · Vercel (20%) · Raycast (15%) · Grafana (15%)
# Near-black surfaces, translucent hairline borders instead of heavy shadows,
# one signature accent, saturated status colors for dark-bg legibility.

# ── Surfaces ───────────────────────────────────────────────────────────────
BG              = "#0a0b0d"   # app background
BG_GRADIENT_TOP  = "#171a22"  # window-bg radial gradient — lit center
BG_GRADIENT_EDGE = "#050506"  # window-bg radial gradient — receding edge
SIDEBAR_BG      = "#0d0e11"   # sidebar panel
CARD_BG         = "#111318"   # floating card / raised surface
CARD_BG_TOP     = "#1d2027"   # gradient highlight — top-lit edge for a raised/3D look
CARD_BG_HOVER   = "#181b22"   # card hover elevation
CARD_BORDER     = "rgba(255,255,255,0.10)"
CARD_BORDER_HOVER = "rgba(255,255,255,0.20)"

# ── Borders / dividers ───────────────────────────────────────────────────────
DIVIDER         = "rgba(255,255,255,0.08)"
BORDER_STRONG   = "rgba(255,255,255,0.14)"

# ── Accent colors ─────────────────────────────────────────────────────────
INDIGO      = "#5E6AD2"
INDIGO_D    = "#4C56B0"
INDIGO_L    = "#8B93E8"
INDIGO_BG   = "rgba(94,106,210,0.14)"

GREEN       = "#3DDC84"
GREEN_D     = "#2BB86B"
GREEN_BG    = "rgba(61,220,132,0.12)"

RED         = "#F0555A"
RED_D       = "#D7383E"
RED_BG      = "rgba(240,85,90,0.12)"

AMBER       = "#F5A623"
AMBER_BG    = "rgba(245,166,35,0.12)"

CYAN        = "#3FC6E8"
VIOLET      = "#A78BFA"

# ── Text (light on near-black) ────────────────────────────────────────────
TXT         = "#eef0f3"   # primary
TXT2        = "#9297a3"   # secondary / muted
TXT3        = "#5b606c"   # tertiary / placeholder

# ── Section nav ───────────────────────────────────────────────────────────
NAV_ACTIVE_BG  = INDIGO_BG
NAV_ACTIVE_TXT = "#ffffff"
NAV_IDLE_TXT   = TXT2

# Cycled by sidebar item index so each section reads as its own object rather
# than a uniform list — one signature accent (indigo) stays reserved for Home.
NAV_ACCENTS = [INDIGO, RED, CYAN, GREEN, VIOLET, AMBER]


def with_alpha(hex_color: str, opacity: float) -> str:
    """Translucent rgba() from a #RRGGBB color. Qt QSS 8-digit hex is
    #AARRGGBB (alpha FIRST) — naively appending hex alpha digits to a
    #RRGGBB string (e.g. f"{color}22") silently parses as a different,
    wrong color instead of a translucent one. Use this instead."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{opacity})"

# ── Input fields ──────────────────────────────────────────────────────────
INPUT_BG     = "#16181d"
INPUT_BORDER = DIVIDER
INPUT_FOCUS  = INDIGO

# ── Glass (used sparingly: sidebar, modals) ───────────────────────────────
GLASS_BG     = "rgba(17,19,24,0.72)"

# ── Spacing scale (4px base grid) ──────────────────────────────────────────
SPACE_XS  = 4
SPACE_SM  = 8
SPACE_MD  = 12
SPACE_LG  = 16
SPACE_XL  = 24
SPACE_2XL = 32

# ── Radius scale ────────────────────────────────────────────────────────────
RADIUS_SM = 6
RADIUS_MD = 8
RADIUS_LG = 12
RADIUS_XL = 16

# ── Per-value accent maps (brightened/saturated for dark bg) ─────────────
ATK_COLORS = {
    "drone":    "#4C8DFF",
    "missile":  "#F0555A",
    "combined": "#A78BFA",
    "unknown":  "#6b7280",
}
DMG_COLORS = {
    "low":    "#3DDC84",
    "medium": "#F5A623",
    "high":   "#F0555A",
}
SCALE_COLORS = {
    "few":     "#60A5FA",
    "swarm":   "#3B82F6",
    "massive": "#2C5FDB",
    "":        "#6b7280",
}

# ── Fonts ────────────────────────────────────────────────────────────────────
FONT_FAMILY = "Inter"
FONT_STACK  = "'Inter','Segoe UI',sans-serif"
FONT_MONO   = "'Consolas','Cascadia Mono',monospace"
FONT_SIZE   = 11
