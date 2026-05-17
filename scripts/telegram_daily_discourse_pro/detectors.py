"""
detectors.py — discourse-level classifiers for daily feature extraction.

Provides high-level message classification used by features.py.
Imports core pattern matching from shared.detectors.
"""
import re
from collections import Counter

from shared.text_utils import lower_text
from shared.detectors import (
    _has_any,
    FIRE_PATTERNS,
    EXPLOSION_PATTERNS,
    AIR_DEFENSE_PATTERNS,
    is_russia_related,
    is_energy_event_candidate,
    REGION_ALIASES,
)


# ---------------------------------------------------------------------------
# Pattern constants — exported for use in features.py
# ---------------------------------------------------------------------------

DRONE_PATTERNS = [
    r"\bбпла\b",
    r"беспилотник\w*",
    r"дрон\w*",
]

EXPLOSION_OR_FIRE_PATTERNS = EXPLOSION_PATTERNS + FIRE_PATTERNS

AIRPORT_PATTERNS = [
    r"аэропорт\w*",
    r"аэродром\w*",
    r"закрыт\w*.*воздушн",
    r"закрыто.*воздушн.*пространств",
    r"временно.*закрыт",
]

UNCERTAINTY_PATTERNS = [
    r"предположительно",
    r"возможно",
    r"по\s+данным",
    r"по\s+информации",
    r"сообщают",
    r"якобы",
    r"предварительно",
]

ENERGY_PATTERNS = [
    r"нефтебаз\w*",
    r"\bнпз\b",
    r"нефтеперерабат\w*",
    r"нефтекомплекс\w*",
    r"газопровод\w*",
    r"электростанц\w*",
    r"подстанц\w*",
    r"компрессорн\w*\s+станц\w*",
    r"\bтэц\b",
    r"\bаэс\b",
    r"\bлэп\b",
]

REFINERY_OR_OIL_DEPOT_PATTERNS = [
    r"нефтебаз\w*",
    r"нефтехранилищ\w*",
    r"\bнпз\b",
    r"нефтеперерабат\w*",
    r"нефтекомплекс\w*",
]

OTHER_ENERGY_INFRA_PATTERNS = [
    r"газопровод\w*",
    r"электростанц\w*",
    r"подстанц\w*",
    r"компрессорн\w*\s+станц\w*",
    r"\bлэп\b",
    r"высоковольтн\w*\s+лини\w*",
    r"нефтепровод\w*",
]

_WAR_RELATED_PATTERNS = [
    r"удар\w*",
    r"атак\w*",
    r"ракет\w*",
    r"\bбпла\b",
    r"беспилотник\w*",
    r"дрон\w*",
    r"взрыв\w*",
    r"пожар\w*",
    r"обстрел\w*",
]

_RUSSIA_ATTACKS_UKRAINE_PATTERNS = [
    r"россия\s+атаковал\w*\s+украин",
    r"вс\s*рф\s+удар",
    r"российск\w*\s+ракет\w*.*украин",
    r"удар\w*\s+по\s+украин",
    r"российск\w*\s+атак\w*.*украин",
]

_UKRAINE_ATTACKS_RUSSIA_PATTERNS = [
    r"украин\w*\s+атаковал\w*",
    r"украинск\w*\s+(?:дрон|бпла|ракет)",
    r"вс\s*у\s+удар",
    r"украинск\w*\s+удар\w*.*(?:по\s+)?росс",
    r"всу.*атаков",
]

_OFFICIAL_SOURCES = [
    "минобороны", "губернатор", "генштаб", "пресс-служб", "официальн",
]


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def count_matches(text: str, patterns: list) -> int:
    t = lower_text(text)
    return sum(1 for p in patterns if re.search(p, t))


def extract_regions(text: str) -> list:
    t = lower_text(text)
    return [value for key, value in REGION_ALIASES.items() if key in t]


def summarize_regions(counter: Counter, top_n: int = 3) -> list:
    return [{"region": r, "count": c} for r, c in counter.most_common(top_n)]


# ---------------------------------------------------------------------------
# Message-level classifiers
# ---------------------------------------------------------------------------

def is_war_related(text: str) -> bool:
    return _has_any(lower_text(text), _WAR_RELATED_PATTERNS)


def is_russian_strike_in_ukraine(text: str) -> bool:
    return _has_any(lower_text(text), _RUSSIA_ATTACKS_UKRAINE_PATTERNS)


def is_ukrainian_strike_in_russia_general(text: str) -> bool:
    return _has_any(lower_text(text), _UKRAINE_ATTACKS_RUSSIA_PATTERNS)


def is_mixed_war_context(text: str) -> bool:
    t = lower_text(text)
    return (
        _has_any(t, _RUSSIA_ATTACKS_UKRAINE_PATTERNS)
        and _has_any(t, _UKRAINE_ATTACKS_RUSSIA_PATTERNS)
    )


def is_relevant_russia_pre_signal(text: str) -> bool:
    """Pre-attack signals: drones active over Russia, air defense alerts, airport closures."""
    t = lower_text(text)
    has_drone = _has_any(t, DRONE_PATTERNS)
    has_air_def = _has_any(t, AIR_DEFENSE_PATTERNS)
    has_airport = _has_any(t, AIRPORT_PATTERNS)
    return (has_drone or has_air_def or has_airport) and is_russia_related(t)


def is_russian_drone_air_defense(text: str) -> bool:
    t = lower_text(text)
    return _has_any(t, DRONE_PATTERNS) and _has_any(t, AIR_DEFENSE_PATTERNS)


def is_relevant_russia_drone_activity(text: str) -> bool:
    t = lower_text(text)
    return _has_any(t, DRONE_PATTERNS) and is_russia_related(t)


def is_target_energy_attack(text: str) -> bool:
    """Energy infrastructure attack in Russia."""
    return is_energy_event_candidate(text)


def is_relevant_energy_confirmation(text: str) -> bool:
    """Official confirmation of an energy attack."""
    t = lower_text(text)
    official = any(src in t for src in _OFFICIAL_SOURCES)
    energy = _has_any(t, ENERGY_PATTERNS)
    return official and energy
