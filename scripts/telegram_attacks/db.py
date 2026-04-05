import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2.extras import RealDictCursor

from shared.config import DB_DSN, TABLE_NAME, UPDATE_WINDOW_HOURS


CREATE_SQL = f'''
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id BIGSERIAL PRIMARY KEY,
    attack_date DATE NOT NULL,
    area TEXT NOT NULL,
    targets_names TEXT NOT NULL,
    target_type TEXT,
    attack_type TEXT,
    combined_strike BOOLEAN,
    drone_scale TEXT,
    repeated_attack BOOLEAN,
    air_defense_active BOOLEAN,
    shutdown BOOLEAN,
    fire BOOLEAN,
    explosions_reported SMALLINT,
    hit_confirmed BOOLEAN,
    report_type TEXT,
    damage_level TEXT,
    source TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_attacks_area_date ON {TABLE_NAME}(area, attack_date);
'''

REQUIRED_COLUMNS = {
    "attack_date": "date",
    "area": "text",
    "targets_names": "text",
    "target_type": "text",
    "attack_type": "text",
    "combined_strike": "boolean",
    "drone_scale": "text",
    "repeated_attack": "boolean",
    "air_defense_active": "boolean",
    "shutdown": "boolean",
    "fire": "boolean",
    "explosions_reported": "smallint",
    "hit_confirmed": "boolean",
    "report_type": "text",
    "damage_level": "text",
    "source": "text",
}

DAMAGE_RANK = {"low": 1, "medium": 2, "high": 3}


def get_conn():
    return psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor)


def validate_schema(conn):
    with conn.cursor() as cur:
        cur.execute(
            '''
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = %s
            ''',
            (TABLE_NAME,),
        )
        rows = cur.fetchall()

    actual = {row["column_name"]: row["data_type"] for row in rows}
    missing = [col for col in REQUIRED_COLUMNS if col not in actual]
    if missing:
        raise RuntimeError(
            f"Table '{TABLE_NAME}' is missing required columns: {', '.join(missing)}"
        )


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_SQL)
    conn.commit()
    validate_schema(conn)


def _split_targets(targets_names: str) -> set[str]:
    if not targets_names:
        return set()
    return {
        part.strip().lower()
        for part in targets_names.split(" | ")
        if part.strip()
    }


def _has_target_overlap(a: str, b: str) -> bool:
    """True when targets overlap OR either side is empty/unknown (non-conflicting)."""
    sa = _split_targets(a)
    sb = _split_targets(b)
    # If either side has no parsed targets, treat as compatible (not conflicting)
    if not sa or not sb:
        return True
    return not sa.isdisjoint(sb)


def find_recent_existing_by_area(conn, area, attack_date, targets_names=None):
    with conn.cursor() as cur:
        cur.execute(
            f'''
            SELECT *
            FROM {TABLE_NAME}
            WHERE area = %s
              AND attack_date BETWEEN %s - (%s || ' hours')::interval AND %s
            ORDER BY attack_date DESC, updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            ''',
            (area, attack_date, UPDATE_WINDOW_HOURS, attack_date),
        )
        rows = cur.fetchall()

    if not rows:
        return None

    for row in rows:
        row_dict = dict(row)
        if _has_target_overlap(row_dict.get("targets_names"), targets_names):
            return row_dict

    return None


def _merge_attack_type(old: str, new: str, report_type: str) -> str:
    old = old or "unknown"
    new = new or "unknown"

    if old == "unknown" and new != "unknown":
        return new

    if old != "unknown" and new == "unknown":
        return old

    if old == new:
        return old

    return old


def _pick_better(old, new, rank_map):
    return new if rank_map.get(new, 0) > rank_map.get(old, 0) else old


def insert_attack(conn, data):
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'''
                INSERT INTO {TABLE_NAME}
                (
                    attack_date,
                    area,
                    targets_names,
                    target_type,
                    attack_type,
                    combined_strike,
                    drone_scale,
                    repeated_attack,
                    air_defense_active,
                    shutdown,
                    fire,
                    explosions_reported,
                    hit_confirmed,
                    report_type,
                    damage_level,
                    source
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ''',
                (
                    data["attack_date"],
                    data["area"],
                    data["targets_names"],
                    data["target_type"],
                    data["attack_type"],
                    data["combined_strike"],
                    data["drone_scale"],
                    data["repeated_attack"],
                    data["air_defense_active"],
                    data["shutdown"],
                    data["fire"],
                    data["explosions_reported"],
                    data["hit_confirmed"],
                    data["report_type"],
                    data["damage_level"],
                    data["source"],
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _is_unknown(v) -> bool:
    """True for None, empty string, or 'Unknown Area'."""
    return not v or str(v).strip().lower() in ("", "unknown area", "unknown")


def build_updated_record(existing, data):
    # area: keep existing unless it's unknown/empty
    area = existing.get("area")
    if _is_unknown(area) and not _is_unknown(data.get("area")):
        area = data["area"]

    # targets_names: keep existing unless empty
    targets_names = existing.get("targets_names")
    if _is_unknown(targets_names) and not _is_unknown(data.get("targets_names")):
        targets_names = data["targets_names"]

    # target_type: keep existing unless empty
    target_type = existing.get("target_type")
    if _is_unknown(target_type) and not _is_unknown(data.get("target_type")):
        target_type = data["target_type"]

    merged = {
        "match_attack_date": existing["attack_date"],
        "match_area": existing["area"],
        "match_targets_names": existing["targets_names"],

        "area": area,
        "targets_names": targets_names,
        "target_type": target_type,
        "combined_strike": existing.get("combined_strike"),
        "drone_scale": existing.get("drone_scale"),
        "repeated_attack": existing.get("repeated_attack"),
        "report_type": existing.get("report_type"),
        "shutdown": existing.get("shutdown"),

        "attack_type": _merge_attack_type(
            existing.get("attack_type"),
            data.get("attack_type"),
            data.get("report_type"),
        ),
        "air_defense_active": bool(existing.get("air_defense_active") or data.get("air_defense_active")),
        "fire": bool(existing.get("fire") or data.get("fire")),
        "explosions_reported": max(existing.get("explosions_reported") or 0, data.get("explosions_reported") or 0),
        "hit_confirmed": bool(existing.get("hit_confirmed") or data.get("hit_confirmed")),
        "damage_level": _pick_better(existing.get("damage_level"), data.get("damage_level"), DAMAGE_RANK),

        "source": (existing.get("source") or "") + "\n\n--- UPDATE ---\n\n" + (data.get("source") or ""),
    }

    return merged


def diff_update_fields(existing, merged):
    tracked = [
        "area",
        "targets_names",
        "target_type",
        "attack_type",
        "air_defense_active",
        "fire",
        "explosions_reported",
        "hit_confirmed",
        "damage_level",
    ]

    diffs = []
    for field in tracked:
        old = existing.get(field)
        new = merged.get(field)
        if old != new:
            diffs.append((field, old, new))
    return diffs


def update_attack(conn, merged, diffs):
    if not diffs:
        return

    field_to_column = {
        "area": "area",
        "targets_names": "targets_names",
        "target_type": "target_type",
        "attack_type": "attack_type",
        "air_defense_active": "air_defense_active",
        "fire": "fire",
        "explosions_reported": "explosions_reported",
        "hit_confirmed": "hit_confirmed",
        "damage_level": "damage_level",
    }

    set_parts = []
    values = []

    for field, _, _ in diffs:
        set_parts.append(f"{field_to_column[field]} = %s")
        values.append(merged[field])

    set_parts.append("source = %s")
    values.append(merged["source"])

    set_parts.append("updated_at = NOW()")

    values.extend([
        merged["match_attack_date"],
        merged["match_area"],
        merged["match_targets_names"],
    ])

    sql = f'''
        UPDATE {TABLE_NAME}
        SET {", ".join(set_parts)}
        WHERE attack_date = %s
          AND area = %s
          AND targets_names = %s
    '''

    try:
        with conn.cursor() as cur:
            cur.execute(sql, values)
        conn.commit()
    except Exception:
        conn.rollback()
        raise