"""
Approximate lat/lon centroids for the area names used across the app
(AREA_OPTIONS in gmar_app/ui/attacks.py, REGION_ALIASES / MACRO_REGIONS in
shared/detectors.py). The DB only stores a free-text area name — there is no
geometry column — so the attacks map derives coordinates from this static
lookup instead of a real geocoder.

Coordinates are city/oblast-capital centroids, good enough for a country-scale
map marker; they are not meant for precise geolocation.
"""
from __future__ import annotations

# Specific cities / districts
AREA_COORDS: dict[str, tuple[float, float]] = {
    # Central Russia
    "Moscow Oblast": (55.75, 37.62),
    "Tula": (54.19, 37.62),
    "Serpukhov": (54.91, 37.41),
    "Aleksin": (54.51, 37.09),
    "Kaluga": (54.51, 36.26),
    "Uzlovaya": (53.98, 38.16),
    "Ryazan": (54.63, 39.73),
    "Tambov": (52.72, 41.45),
    "Lipetsk": (52.61, 39.60),
    "Voronezh": (51.66, 39.20),
    "Liski": (51.02, 39.49),
    "Oryol": (52.97, 36.07),
    "Bryansk": (53.24, 34.36),
    "Karachev": (53.13, 34.98),
    "Smolensk": (54.78, 32.05),
    "Yartsevo": (55.07, 32.70),
    "Vladimir": (56.13, 40.41),

    # Volga / Ural
    "Samara": (53.20, 50.15),
    "Syzran": (53.16, 48.47),
    "Novokuibyshevsk": (53.09, 49.95),
    "Ufa": (54.74, 55.97),
    "Bashkortostan": (54.00, 56.00),
    "Republic of Bashkortostan": (54.00, 56.00),
    "Saratov": (51.53, 46.03),
    "Engels": (51.48, 46.11),
    "Petrovsk": (52.31, 45.39),
    "Penza": (53.20, 45.00),
    "Ulyanovsk": (54.31, 48.37),
    "Nizhny Novgorod": (56.30, 44.00),
    "Chuvashia": (55.50, 47.00),
    "Cheboksary": (56.13, 47.25),
    "Kazan": (55.80, 49.11),
    "Tatarstan": (55.50, 50.50),
    "Almetyevsk": (54.90, 52.30),
    "Naberezhnye Chelny": (55.72, 52.40),
    "Nizhnekamsk": (55.63, 51.82),
    "Orenburg": (51.77, 55.10),
    "Perm": (58.01, 56.25),
    "Izhevsk": (56.85, 53.21),
    "Udmurtia": (56.85, 53.21),
    "Volgograd": (48.71, 44.50),

    # Sverdlovsk / Tyumen / Chelyabinsk
    "Ekaterinburg": (56.84, 60.61),
    "Sverdlovsk Oblast": (58.00, 60.60),
    "Tyumen Oblast": (57.15, 65.53),
    "Chelyabinsk": (55.16, 61.40),
    "Omsk": (54.99, 73.37),

    # Khanty-Mansi
    "Khanty-Mansi Autonomous Okrug": (61.00, 69.00),
    "Surgut": (61.25, 73.40),
    "Nizhnevartovsk": (60.94, 76.55),

    # South Russia
    "Krasnodar": (45.04, 38.98),
    "Tuapse": (44.10, 39.08),
    "Novorossiysk": (44.72, 37.77),
    "Gay-Kodzor": (44.85, 37.90),
    "Rostov": (47.24, 39.71),
    "Novoshakhtinsk": (47.76, 39.94),
    "Chertkovo": (49.38, 40.19),
    "Astrakhan": (46.35, 48.04),
    "Dagestan": (42.98, 47.50),

    # North / Northwest
    "Leningrad Oblast": (59.53, 30.13),
    "Kirishi": (59.45, 32.02),
    "Pskov": (57.82, 28.33),
    "Murmansk": (68.97, 33.09),
    "Vologda": (59.22, 39.89),
    "Komi Republic": (61.67, 50.81),

    # Central Black Earth
    "Kursk": (51.73, 36.19),
    "Belgorod": (50.60, 36.59),

    # Tver / North Central
    "Tver": (56.86, 35.90),
    "Udomlya": (57.88, 35.01),
    "Yaroslavl": (57.63, 39.87),

    # Far East / Siberia
    "Sakhalin Oblast": (50.60, 142.70),
    "Primorsky Krai": (45.00, 135.00),
    "Vladivostok": (43.12, 131.89),
    "Zabaykalsky Krai": (52.03, 113.50),
    "Chita": (52.03, 113.50),
    "Kemerovo": (55.35, 86.09),

    # Oblast / Krai full names
    "Tula Oblast": (54.10, 37.90),
    "Ryazan Oblast": (54.30, 40.20),
    "Voronezh Oblast": (51.20, 39.90),
    "Rostov Oblast": (47.80, 41.00),
    "Bryansk Oblast": (52.90, 33.50),
    "Vladimir Oblast": (56.00, 40.80),
    "Volgograd Oblast": (49.20, 44.00),
    "Samara Oblast": (53.50, 50.50),
    "Nizhny Novgorod Oblast": (56.00, 44.50),
    "Krasnodar Krai": (45.50, 39.50),
    "Perm Krai": (58.50, 56.80),
    "Oryol Oblast": (52.80, 36.30),

    # Occupied Territories
    "Crimea": (45.29, 34.30),
    "Occupied Crimea": (45.29, 34.30),
    "Luhansk Oblast": (48.57, 39.31),
    "Zaporizhzhia Oblast": (47.85, 35.14),
    "Occupied Kherson": (46.64, 32.62),
    "Occupied Donetsk": (48.02, 37.80),
}

# Fallback centroids keyed by macro-region name (used when the specific area
# has no entry above — e.g. macro/fallback labels stored directly as `area`,
# or new area names the parser detects that this table hasn't caught up with).
MACRO_COORDS: dict[str, tuple[float, float]] = {
    "Central Russia": (55.00, 38.00),
    "Western Russia": (54.50, 33.00),
    "Border / Western Russia": (52.50, 34.50),
    "Southern Russia": (45.50, 40.00),
    "Northern Russia": (61.00, 40.00),
    "Northwest Russia": (60.00, 33.00),
    "Eastern Russia": (56.00, 60.00),
    "Far East Russia": (53.00, 135.00),
    "Siberia": (56.00, 85.00),
    "Ural": (57.00, 60.00),
    "Volga Region": (55.00, 49.00),
    "European Russia": (55.00, 40.00),
    "Central Federal District": (55.00, 37.50),
    "Black Sea Region": (45.00, 38.50),
    "Caspian Sea": (42.00, 50.50),
    "Occupied Territories": (47.50, 35.50),
    "Russia": (61.50, 90.00),
}

# Last-resort centroid — a generic point inside European Russia, flagged as
# approximate on the map (never plotted for a real area if we can help it).
UNKNOWN_COORDS = (56.00, 45.00)


def get_coords(area: str | None, macro_region: str | None = None) -> tuple[float, float, bool]:
    """
    Resolve (lat, lon, is_exact) for an attack's area name.
    is_exact=False means the point is a regional/fallback approximation,
    not the specific place — callers should mark it visually as such.
    """
    if area:
        hit = AREA_COORDS.get(area.strip())
        if hit:
            return (hit[0], hit[1], True)

    if macro_region:
        hit = MACRO_COORDS.get(macro_region.strip())
        if hit:
            return (hit[0], hit[1], False)

    if area:
        hit = MACRO_COORDS.get(area.strip())
        if hit:
            return (hit[0], hit[1], False)

    return (UNKNOWN_COORDS[0], UNKNOWN_COORDS[1], False)
