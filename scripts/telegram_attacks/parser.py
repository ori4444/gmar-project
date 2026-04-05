import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import ParsedAttack
from shared.detectors import (
    build_windows,
    is_energy_event_candidate,
    is_fast_reject,
    extract_all_targets,
    normalize_targets_string,
    merge_target_types,
    extract_area,
    detect_primary_attack,
    detect_attack_context,
    detect_fire,
    detect_explosions,
    detect_air_defense,
    detect_shutdown,
    detect_attack_type,
    detect_report_type,
    detect_hit_confirmed,
    detect_damage_level,
    infer_drone_scale,
    is_update_like,
    detect_combined_strike,
    extract_attack_date,
)


def _signal_score(p: ParsedAttack) -> int:
    score = 0
    if p.fire:            score += 3
    if p.hit_confirmed:   score += 2
    if p.shutdown:        score += 3
    score += min(p.explosions_reported, 5)
    if p.primary_attack:  score += 2
    if p.air_defense_active: score += 1
    score += {"high": 3, "medium": 2, "low": 1}.get(p.damage_level, 0)
    score += {"official_confirmation": 2, "direct_report": 1}.get(p.report_type, 0)
    return score


def build_parsed_fields(msg_dt, text, existing_event_found: bool = False):
    if is_fast_reject(text):
        return []

    windows = build_windows(text)
    parsed_items = []

    for window in windows:
        if not is_energy_event_candidate(window):
            continue

        targets = extract_all_targets(window)
        if not targets:
            continue

        area = extract_area(window)
        if not area:
            area = extract_area(text)  # fallback: try full message
        if not area:
            area = "Unknown Area"

        primary_attack = detect_primary_attack(window)
        attack_context = detect_attack_context(window)
        fire = detect_fire(window)
        update_like = is_update_like(window)
        air_defense = detect_air_defense(window)
        shutdown = detect_shutdown(window)
        explosions = detect_explosions(window)
        attack_type = detect_attack_type(window)
        report_type = detect_report_type(window)
        hit_confirmed = detect_hit_confirmed(window)
        damage_level = detect_damage_level(window)

        meaningful_signal = any([
            primary_attack,
            attack_context,
            fire,
            air_defense,
            shutdown,
            explosions > 0,
        ])

        if not meaningful_signal:
            continue

        parsed_items.append(
            ParsedAttack(
                attack_date=extract_attack_date(msg_dt, window),
                area=area,
                targets_names=normalize_targets_string(targets),
                target_type=merge_target_types(targets),
                attack_type=attack_type,
                combined_strike=detect_combined_strike(window, targets, existing_event_found),
                drone_scale=infer_drone_scale(window, attack_type, update_like),
                repeated_attack=None,
                air_defense_active=air_defense,
                shutdown=shutdown,
                fire=fire,
                explosions_reported=explosions,
                hit_confirmed=hit_confirmed,
                report_type=report_type,
                damage_level=damage_level,
                primary_attack=primary_attack,
                is_update_like=update_like,
            )
        )

    # Deduplicate: one message should produce at most one candidate per target.
    # When multiple windows from the same message resolve to the same target_name,
    # keep only the highest-confidence parse.
    best: dict = {}
    for p in parsed_items:
        key = (p.attack_date, p.targets_names)
        if key not in best or _signal_score(p) > _signal_score(best[key]):
            best[key] = p

    return list(best.values())
