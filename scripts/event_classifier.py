"""
event_classifier.py
===================
5-stage pipeline for classifying attack event reports.

Pipeline:
  text -> prefilter -> classify -> validate -> extract -> output

Design principle: Precision > Recall.
Only mark is_real_event=True when all three validation gates pass.
Never infer a field — leave it None if not explicitly stated.
"""

import re
from typing import Optional


# ---------------------------------------------------------------------------
# Stage 1 — Hard Filter patterns (immediate rejection)
# ---------------------------------------------------------------------------

_HARD_FILTER = [
    # Legal / judicial
    r"\bсуд\b",
    r"\bиск\b",
    r"\bобязал\b",
    r"\bcourt\b",
    r"\blawsuit\b",
    # Map / satellite imagery captions
    r"спутниковый снимок",
    r"satellite image",
    r"\bmap shows\b",
    # Standing/general claim patterns ("X is regularly targeted")
    r"неоднократно атаков\w+",
    r"regularly targeted",
    r"has been repeatedly attacked",
    r"periodically attacked",
    r"known target",
]

# ---------------------------------------------------------------------------
# Stage 2 — Report type patterns
# ---------------------------------------------------------------------------

_UPDATE_PATTERNS = [
    # Russian
    r"пожар продолжается",
    r"продолжает гореть",
    r"горит.{0,30}сут",
    r"тушение продолжается",
    r"локализован\w*",
    r"потушили",
    r"ликвидировали пожар",
    r"обстановка стабилизировалась",
    # English
    r"fire continues",
    r"still burning",
    r"continues to burn",
    r"fire has been (contained|extinguished|controlled)",
    r"update[:\s]",
    r"earlier (today|this morning|this evening)",
]

_HISTORICAL_PATTERNS = [
    # Russian
    r"\bранее\b",
    r"\bпрежде\b",
    r"в \d{4}\s*год",
    r"по данным\s.{0,30}20\d\d",
    r"был атакован\s+\w+\s+назад",
    # English
    r"\bpreviously\b",
    r"\bprior\b",
    r"\bformerly\b",
    r"\bearlier\b",
    r"\bhistorically\b",
    r"\bin 20(1[0-9]|2[0-3])\b",
    r"was attacked in \d{4}",
    r"had been struck",
    r"has (been|previously been) (struck|attacked|hit|targeted)",
]

_LEGAL_PATTERNS = [
    r"\bсуд\b",
    r"\bиск\b",
    r"\bобязал\b",
    r"\bcourt\b",
    r"\blawsuit\b",
    r"\barbitrat\w+\b",
]

_DIRECT_ATTACK_VERB_PATTERNS = [
    # Russian — verb refers to a completed event
    r"атакован\w*",
    r"нанесен\w*\s+удар",
    r"нанесли\s+удар",
    r"нанесла\s+удар",
    r"ударили\s+по",
    r"был\w*\s+поражен\w*",
    r"был\w*\s+атакован\w*",
    r"подвергся\s+атаке",
    r"прилетел\w*",
    r"загорелся\w*",
    r"возник\w*\s+пожар",
    r"произошел\w*\s+взрыв",
    r"сообщают\s+о\s+взрыве",
    r"сообщается\s+о\s+(атаке|ударе|взрыве|пожаре)",
    # English
    r"was\s+struck",
    r"was\s+hit",
    r"was\s+attacked",
    r"was\s+targeted",
    r"caught\s+fire",
    r"explosion\s+reported",
    r"drones?\s+(struck|hit|attacked|targeted)",
    r"missiles?\s+(struck|hit|targeted)",
    r"(strike|attack)\s+on\s+the",
    r"(attacked|struck|hit)\s+the",
    r"suffered\s+a\s+strike",
    r"a\s+strike\s+(occurred|took place)",
    r"came\s+under\s+(drone|missile|artillery)\s+(attack|fire|strike)",
]

_GENERAL_CLAIM_PATTERNS = [
    r"generally\s+(targeted|attacked)",
    r"is\s+a\s+(major|key|important)\s+target",
    r"одна из главных целей",
    r"является\s+(объектом|целью)\s+атак",
    r"used to be attacked",
]

# ---------------------------------------------------------------------------
# Stage 4 — Field extraction patterns
# ---------------------------------------------------------------------------

_DRONE_PATTERNS = [
    r"\bдрон\w*\b",
    r"\bбпла\b",
    r"\bbeспилотник\w*\b",
    r"\bdrones?\b",
    r"\buavs?\b",
    r"\bshahed\b",
    r"\bgeran\w*\b",
]

_MISSILE_PATTERNS = [
    r"\bракет\w*\b",
    r"\bmissiles?\b",
    r"\bcruise missile\b",
    r"\bballistic\b",
    r"\bкрылатых?\s+ракет\w*\b",
]

_ARTILLERY_PATTERNS = [
    r"\bартиллери\w*\b",
    r"\bartillery\b",
    r"\bshelling\b",
    r"\bобстрел\w*\b",
]

# (label, patterns) — first match wins
_TARGET_TYPES = [
    ("refinery",       [r"\brefiner\w+\b", r"\bнпз\b", r"нефтеперерабат\w+"]),
    ("oil_depot",      [r"oil depot", r"fuel depot", r"нефтебаз\w+", r"нефтехранилищ\w+"]),
    ("pipeline",       [r"\bpipeline\b", r"\bнефтепровод\w*\b", r"\bгазопровод\w*\b"]),
    ("gas_facility",   [r"gas facility", r"gas infrastructure", r"газоперерабат\w+", r"компрессорн\w+\s+станц\w+"]),
    ("power_facility", [r"power (plant|facility|grid|station)", r"электростанц\w+", r"подстанц\w+", r"\bтэц\b", r"\bаэс\b"]),
    ("port",           [r"\bport\b", r"\bterminal\b", r"\bпорт\b", r"\bтерминал\b"]),
    ("storage",        [r"\bwarehouse\b", r"\bstorage\b", r"\bсклад\w*\b", r"\bхранилищ\w+\b"]),
    ("ammunition",     [r"\bammunition\b", r"\bамунициj\w*\b", r"\bарсенал\w*\b"]),
]

_FIRE_PATTERNS = [
    r"\bпожар\w*\b",
    r"\bзагорел\w+\b",
    r"\bвозгорание\b",
    r"\bвспыхнул\w+\b",
    r"\bfire\b",
    r"\bblaze\b",
    r"\bburning\b",
    r"\bignited\b",
    r"\bflames?\b",
]

_EXPLOSION_PATTERNS = [
    r"\bвзрыв\w*\b",
    r"\bдетонац\w+\b",
    r"\bexplosion\b",
    r"\bdetonat\w+\b",
    r"\bblast\b",
]

_HEAVY_DAMAGE_PATTERNS = [
    r"масштабн\w+",
    r"значительн\w+\s+(урон|ущерб|повреждени)",
    r"серьезн\w+\s+(урон|ущерб)",
    r"heavily damaged",
    r"severe damage",
    r"major fire",
    r"substantial damage",
    r"catastrophic",
]

_MODERATE_DAMAGE_PATTERNS = [
    r"умерен\w+",
    r"частичн\w+\s+(повреждени|урон)",
    r"moderate damage",
    r"partially damaged",
    r"minor damage",
    r"slight damage",
]

# Drone count: look for digit(s) near a drone keyword
_DRONE_COUNT_RE = re.compile(
    r"(\d+)\s*(?:дрон\w*|бпла|беспилотник\w*|drone|uav|shahed)",
    re.IGNORECASE,
)
_DRONE_COUNT_RE2 = re.compile(
    r"(?:дрон\w*|бпла|беспилотник\w*|drone|uav|shahed)\s*[:\s]\s*(\d+)",
    re.IGNORECASE,
)

# Target name: text inside «...» or "..." or a capitalized word sequence near a keyword
_GUILLEMET_RE = re.compile(r'«([^»]{2,60})»')
_QUOTE_RE = re.compile(r'"([A-ZА-ЯЁ][^"]{2,60})"')


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _lower(text: str) -> str:
    return text.lower()


def _has_any(text: str, patterns: list[str]) -> bool:
    t = _lower(text)
    return any(re.search(p, t) for p in patterns)


def _first_match(text: str, patterns: list[str]) -> Optional[re.Match]:
    t = _lower(text)
    for p in patterns:
        m = re.search(p, t)
        if m:
            return m
    return None


# ---------------------------------------------------------------------------
# Stage 1 — Prefilter
# ---------------------------------------------------------------------------

def prefilter(text: str) -> bool:
    """
    Returns True if the text should be immediately rejected (hard-filtered).
    """
    return _has_any(text, _HARD_FILTER)


# ---------------------------------------------------------------------------
# Stage 2 — Classify report type
# ---------------------------------------------------------------------------

def classify_report_type(text: str) -> str:
    """
    Returns one of: direct_attack | update | historical | context | legal

    Only "direct_attack" reports proceed to validation and field extraction.
    """
    if _has_any(text, _LEGAL_PATTERNS):
        return "legal"

    if _has_any(text, _HISTORICAL_PATTERNS):
        return "historical"

    if _has_any(text, _UPDATE_PATTERNS):
        return "update"

    if _has_any(text, _DIRECT_ATTACK_VERB_PATTERNS):
        return "direct_attack"

    return "context"


# ---------------------------------------------------------------------------
# Stage 3 — Validate event
# ---------------------------------------------------------------------------

def validate_event(text: str, report_type: str) -> tuple[bool, Optional[str]]:
    """
    Returns (is_valid, reason_if_invalid).

    Three gates must ALL pass:
      1. Has an attack verb
      2. No historical signal
      3. Not a general standing claim
    """
    if report_type != "direct_attack":
        return False, f"report_type:{report_type}"

    if not _has_any(text, _DIRECT_ATTACK_VERB_PATTERNS):
        return False, "no_attack_verb"

    if _has_any(text, _HISTORICAL_PATTERNS):
        return False, "historical_signal"

    if _has_any(text, _GENERAL_CLAIM_PATTERNS):
        return False, "general_claim"

    return True, None


# ---------------------------------------------------------------------------
# Stage 4 — Extract structured fields
# ---------------------------------------------------------------------------

def _extract_attack_type(text: str) -> str:
    has_drone = _has_any(text, _DRONE_PATTERNS)
    has_missile = _has_any(text, _MISSILE_PATTERNS)
    has_artillery = _has_any(text, _ARTILLERY_PATTERNS)

    types = []
    if has_drone:
        types.append("drone")
    if has_missile:
        types.append("missile")
    if has_artillery:
        types.append("artillery")

    if len(types) > 1:
        return "combined"
    if len(types) == 1:
        return types[0]
    return "unknown"


def _extract_target_type(text: str) -> str:
    t = _lower(text)
    for label, patterns in _TARGET_TYPES:
        for p in patterns:
            if re.search(p, t):
                return label
    return "unknown"


def _extract_target_name(text: str) -> Optional[str]:
    # Prefer «guillemet» quoted names
    m = _GUILLEMET_RE.search(text)
    if m:
        return m.group(1).strip()

    # Then double-quote proper names
    m = _QUOTE_RE.search(text)
    if m:
        return m.group(1).strip()

    return None


def _extract_location(text: str) -> Optional[str]:
    """
    Returns the most specific geographic mention found.
    Looks for 'в [CityName]' (Russian) or 'in [Oblast/City]' (English).
    Only returns a value if the city/region is explicitly named.
    """
    # Russian: "в Краснодарском крае", "в Саратове", "в Туапсе"
    m = re.search(
        r"\bв\s+([А-ЯЁ][а-яёА-ЯЁ]+(?:\s+[а-яёА-ЯЁ]+)?(?:\s+(?:крае|области|районе|городе))?)",
        text,
    )
    if m:
        loc = m.group(1).strip()
        # Filter out generic words that aren't locations
        if loc.lower() not in {"ходе", "результате", "связи", "том", "это", "этом"}:
            return loc

    # English: "in Rostov Oblast", "in Saratov", "in [X] region"
    m = re.search(
        r"\bin\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?(?:\s+(?:Oblast|Region|Krai|City))?)\b",
        text,
    )
    if m:
        return m.group(1).strip()

    return None


def _extract_damage_level(text: str) -> Optional[str]:
    if _has_any(text, _HEAVY_DAMAGE_PATTERNS):
        return "heavy"
    if _has_any(text, _MODERATE_DAMAGE_PATTERNS):
        return "moderate"
    return None


def _extract_drone_scale(text: str) -> Optional[int]:
    for pattern in (_DRONE_COUNT_RE, _DRONE_COUNT_RE2):
        matches = pattern.findall(text)
        if matches:
            nums = [int(n) for n in matches if n.isdigit()]
            if nums:
                return max(nums)
    return None


def _extract_fire(text: str) -> Optional[bool]:
    if _has_any(text, _FIRE_PATTERNS):
        return True
    return None


def _extract_explosions(text: str) -> Optional[str]:
    count = sum(
        len(re.findall(p, _lower(text))) for p in _EXPLOSION_PATTERNS
    )
    if count == 0:
        return None
    if count == 1:
        return "single"
    return "multiple"


def extract_fields(text: str) -> dict:
    return {
        "attack_type":  _extract_attack_type(text),
        "target_name":  _extract_target_name(text),
        "target_type":  _extract_target_type(text),
        "location":     _extract_location(text),
        "damage_level": _extract_damage_level(text),
        "drone_scale":  _extract_drone_scale(text),
        "fire":         _extract_fire(text),
        "explosions":   _extract_explosions(text),
    }


# ---------------------------------------------------------------------------
# Stage 5 — Public API
# ---------------------------------------------------------------------------

def classify_event(text: str) -> dict:
    """
    Run the full 5-stage pipeline on a single text.

    Returns:
    {
        "is_real_event": bool,
        "report_type":   str,   # direct_attack | update | historical | context | legal
        "skip_reason":   str | None,  # None if is_real_event, else reason string
        # Only present when is_real_event == True:
        "attack_type":   str,
        "target_name":   str | None,
        "target_type":   str,
        "location":      str | None,
        "damage_level":  str | None,
        "drone_scale":   int | None,
        "fire":          bool | None,
        "explosions":    str | None,  # "single" | "multiple" | None
    }
    """
    if not text or not text.strip():
        return {"is_real_event": False, "report_type": "context", "skip_reason": "empty_text"}

    # Stage 1 — Hard filter
    if prefilter(text):
        return {"is_real_event": False, "report_type": "context", "skip_reason": "hard_filter"}

    # Stage 2 — Classify report type
    report_type = classify_report_type(text)

    # Stage 3 — Validate
    is_valid, reason = validate_event(text, report_type)

    if not is_valid:
        return {
            "is_real_event": False,
            "report_type":   report_type,
            "skip_reason":   reason,
        }

    # Stage 4 — Extract fields
    fields = extract_fields(text)

    return {
        "is_real_event": True,
        "report_type":   report_type,
        "skip_reason":   None,
        **fields,
    }
