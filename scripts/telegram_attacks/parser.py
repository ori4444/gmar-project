"""
Event-level parser: one Telegram message → zero or more ParsedEvent records,
one per distinct attack event (location + target).
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from models import ParsedEvent
except ModuleNotFoundError:
    from telegram_attacks.models import ParsedEvent

from shared.detectors import (
    split_into_attack_events,
    is_energy_event_candidate,
    is_fast_reject,
    extract_all_targets,
    merge_target_types,
    extract_area,
    detect_primary_attack,
    detect_attack_context,
    detect_fire,
    detect_explosions,
    detect_air_defense,
    detect_shutdown,
    detect_report_type,
    detect_hit_confirmed,
    detect_damage_level,
    infer_drone_scale,
    is_update_like,
    extract_attack_date,
)


def _compute_confidence(
    report_type: str,
    hit_confirmed: bool,
    fire: bool,
    shutdown: bool,
    explosions: int,
    damage_level: str,
) -> int:
    score = 50
    if report_type == "official_confirmation":   score += 25
    elif report_type == "indirect_aftermath":    score += 10
    elif report_type == "hearsay":               score -= 5
    elif report_type == "correction_update":     score += 5
    if hit_confirmed:    score += 8
    if fire:             score += 5
    if shutdown:         score += 8
    if explosions > 0:   score += min(explosions * 2, 8)
    if damage_level == "high":    score += 8
    elif damage_level == "medium": score += 4
    return max(5, min(95, score))


def _signal_score(p: ParsedEvent) -> int:
    score = 0
    if p.fire:               score += 3
    if p.hit_confirmed:      score += 2
    if p.shutdown:           score += 3
    score += min(p.explosions_reported, 5)
    if p.primary_attack:     score += 2
    if p.air_defense_active: score += 1
    score += {"high": 3, "medium": 2, "low": 1}.get(p.damage_level, 0)
    score += {"official_confirmation": 2, "indirect_aftermath": 1}.get(p.report_type, 0)
    return score


def build_parsed_fields(msg_dt, text: str, existing_event_found: bool = False) -> list[ParsedEvent]:
    """
    Parse a Telegram message into a list of ParsedEvent objects.

    The parser first splits the message into atomic event segments (one per
    distinct strike location/target), then parses each segment independently.
    All events from the same multi-event message are marked combined_strike=True.
    """
    if is_fast_reject(text):
        return []

    segments = split_into_attack_events(text)
    is_multi_event = len(segments) > 1

    parsed_items: list[ParsedEvent] = []

    for segment in segments:
        if not is_energy_event_candidate(segment):
            continue

        targets = extract_all_targets(segment)
        if not targets:
            continue

        area = extract_area(segment)
        if not area:
            area = extract_area(text)
        if not area:
            area = "Unknown Area"

        primary_attack  = detect_primary_attack(segment)
        attack_context  = detect_attack_context(segment)
        fire            = detect_fire(segment)
        update_like     = is_update_like(segment)
        air_defense     = detect_air_defense(segment)
        shutdown        = detect_shutdown(segment)
        explosions      = detect_explosions(segment)
        hit_confirmed   = detect_hit_confirmed(segment)

        meaningful_signal = any([
            primary_attack, attack_context, fire,
            air_defense, shutdown, explosions > 0, hit_confirmed,
        ])
        if not meaningful_signal:
            continue

        combined     = is_multi_event or len(targets) > 1 or existing_event_found
        attack_date  = extract_attack_date(msg_dt, segment)
        drone_scale  = infer_drone_scale(segment, update_like=update_like)
        report_type  = detect_report_type(segment)
        damage_level = detect_damage_level(segment)
        confidence   = _compute_confidence(
            report_type, hit_confirmed, fire, shutdown, explosions, damage_level
        )

        parsed_items.append(ParsedEvent(
            attack_date         = attack_date,
            area                = area,
            target_type         = merge_target_types(targets),
            combined_strike     = combined,
            drone_scale         = drone_scale,
            air_defense_active  = air_defense,
            shutdown            = shutdown,
            fire                = fire,
            explosions_reported = explosions,
            hit_confirmed       = hit_confirmed,
            report_type         = report_type,
            damage_level        = damage_level,
            confidence          = confidence,
            primary_attack      = primary_attack,
            is_update_like      = update_like,
        ))

    # Deduplicate: keep highest-signal parse per (date, area, type)
    best: dict = {}
    for p in parsed_items:
        key = (p.attack_date, p.area, p.target_type)
        if key not in best or _signal_score(p) > _signal_score(best[key]):
            best[key] = p

    return list(best.values())
