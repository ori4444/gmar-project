import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2.extras import RealDictCursor

from shared.config import (
    DB_DSN, UPDATE_WINDOW_HOURS,
    EVENTS_TABLE, TARGETS_TABLE, FEATURES_TABLE,
    ET_TABLE, EU_TABLE,
    CHANNEL_USERNAME,
)

# ─────────────────────────────────────────────────────────────────────────────
#  Schema DDL
# ─────────────────────────────────────────────────────────────────────────────

CREATE_SQL = f'''
-- Energy facility reference table
CREATE TABLE IF NOT EXISTS {TARGETS_TABLE} (
    target_id      BIGSERIAL PRIMARY KEY,
    target_type    TEXT NOT NULL,
    area           TEXT,
    macro_region   TEXT,
    is_canonical   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_targets_type_area
    ON {TARGETS_TABLE}(lower(target_type), lower(COALESCE(area, '')));

-- Core attack events — atomic: one location, one weapon type, one date
CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} (
    event_id           BIGSERIAL PRIMARY KEY,
    attack_date        DATE NOT NULL,
    area               TEXT NOT NULL,
    macro_region       TEXT,
    drone_scale        TEXT,
    combined_strike    BOOLEAN NOT NULL DEFAULT FALSE,
    air_defense_active BOOLEAN NOT NULL DEFAULT FALSE,
    hit_confirmed      BOOLEAN NOT NULL DEFAULT FALSE,
    fire_reported      BOOLEAN NOT NULL DEFAULT FALSE,
    explosions_count   SMALLINT NOT NULL DEFAULT 0,
    shutdown_reported  BOOLEAN NOT NULL DEFAULT FALSE,
    damage_level       TEXT NOT NULL DEFAULT 'unknown',
    report_type        TEXT NOT NULL DEFAULT 'hearsay',
    confidence         SMALLINT NOT NULL DEFAULT 50
                           CHECK (confidence BETWEEN 0 AND 100),
    status             TEXT NOT NULL DEFAULT 'active',
    merged_into        BIGINT REFERENCES {EVENTS_TABLE}(event_id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_events_area_date
    ON {EVENTS_TABLE}(area, attack_date);
CREATE INDEX IF NOT EXISTS idx_events_date
    ON {EVENTS_TABLE}(attack_date DESC);
CREATE INDEX IF NOT EXISTS idx_events_active
    ON {EVENTS_TABLE}(status) WHERE status = \'active\';

-- Many-to-many: which targets were struck in each event
CREATE TABLE IF NOT EXISTS {ET_TABLE} (
    id            BIGSERIAL PRIMARY KEY,
    event_id      BIGINT NOT NULL
                      REFERENCES {EVENTS_TABLE}(event_id) ON DELETE CASCADE,
    target_id     BIGINT NOT NULL
                      REFERENCES {TARGETS_TABLE}(target_id),
    hit_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    damage_level  TEXT,
    UNIQUE(event_id, target_id)
);
CREATE INDEX IF NOT EXISTS idx_et_event  ON {ET_TABLE}(event_id);
CREATE INDEX IF NOT EXISTS idx_et_target ON {ET_TABLE}(target_id);

-- Field-level audit log — every change to an event is recorded here
CREATE TABLE IF NOT EXISTS {EU_TABLE} (
    update_id  BIGSERIAL PRIMARY KEY,
    event_id   BIGINT NOT NULL REFERENCES {EVENTS_TABLE}(event_id),
    field_name TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_eu_event ON {EU_TABLE}(event_id);

-- Pre-computed daily discourse features derived from raw_messages
CREATE TABLE IF NOT EXISTS {FEATURES_TABLE} (
    feature_date  DATE  NOT NULL,
    channel       TEXT  NOT NULL,
    pre_drone     INT   NOT NULL DEFAULT 0,
    pre_airdef    INT   NOT NULL DEFAULT 0,
    pre_airport   INT   NOT NULL DEFAULT 0,
    pre_uncert    INT   NOT NULL DEFAULT 0,
    en_attack     INT   NOT NULL DEFAULT 0,
    en_confirm    INT   NOT NULL DEFAULT 0,
    en_refinery   INT   NOT NULL DEFAULT 0,
    war_total     INT   NOT NULL DEFAULT 0,
    war_ukr_ru    INT   NOT NULL DEFAULT 0,
    msg_count     INT   NOT NULL DEFAULT 0,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (feature_date, channel)
);
'''

# Severity ranking for merge decisions
DAMAGE_RANK = {"low": 1, "medium": 2, "high": 3, "catastrophic": 4}

# SELECT fragment that returns compat column names matching old 'attacks' schema
_EVENT_SELECT = f"""
    SELECT
        e.event_id                                                    AS attack_id,
        e.attack_date,
        e.area,
        e.macro_region,
        e.drone_scale,
        e.combined_strike,
        e.air_defense_active,
        e.hit_confirmed,
        e.fire_reported                                               AS fire,
        e.explosions_count                                            AS explosions_reported,
        e.shutdown_reported                                           AS shutdown,
        e.damage_level,
        e.report_type,
        e.confidence,
        e.status,
        e.created_at,
        e.updated_at,
        COALESCE(
            string_agg(DISTINCT t.target_type, ' | '
                       ORDER BY t.target_type), 'unknown'
        )                                                             AS target_type
    FROM {EVENTS_TABLE} e
    LEFT JOIN {ET_TABLE}      et ON et.event_id  = e.event_id
    LEFT JOIN {TARGETS_TABLE} t  ON t.target_id  = et.target_id
"""
_EVENT_GROUP = f"GROUP BY e.event_id"

# Fields tracked for UPDATE diffing (using compat names)
TRACKED = [
    "area",
    "target_type",
    "combined_strike",
    "air_defense_active",
    "fire",
    "shutdown",
    "explosions_reported",
    "hit_confirmed",
    "damage_level",
    "confidence",
]

# Compat → actual column mapping for UPDATE SET clauses
_FIELD_TO_COL = {
    "area":               "area",
    "combined_strike":    "combined_strike",
    "air_defense_active": "air_defense_active",
    "fire":               "fire_reported",
    "shutdown":           "shutdown_reported",
    "explosions_reported":"explosions_count",
    "hit_confirmed":      "hit_confirmed",
    "damage_level":       "damage_level",
    "report_type":        "report_type",
    "confidence":         "confidence",
    "drone_scale":        "drone_scale",
}


# ─────────────────────────────────────────────────────────────────────────────
#  Connection
# ─────────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DB_DSN, cursor_factory=RealDictCursor)



def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(CREATE_SQL)
        cur.execute("DROP TABLE IF EXISTS event_messages CASCADE")
        cur.execute("DROP TABLE IF EXISTS raw_messages CASCADE")
        cur.execute("ALTER TABLE IF EXISTS event_updates DROP COLUMN IF EXISTS msg_id")
        cur.execute("ALTER TABLE IF EXISTS attack_events DROP COLUMN IF EXISTS weapon_type")
        cur.execute(f"ALTER TABLE IF EXISTS {EVENTS_TABLE} DROP COLUMN IF EXISTS repeated_attack")
        cur.execute(f"ALTER TABLE IF EXISTS {TARGETS_TABLE} DROP COLUMN IF EXISTS canonical_name")
        cur.execute("DROP INDEX IF EXISTS idx_targets_name")
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _split_pipe(s: str) -> list[str]:
    return [p.strip() for p in (s or "").split(" | ") if p.strip()]


def _is_unknown(v) -> bool:
    if not v:
        return True
    s = str(v).strip().lower()
    return s in ("", "unknown area", "unknown") or s.startswith("unknown ")


def _has_type_overlap(a: str, b: str) -> bool:
    sa = {x.lower() for x in _split_pipe(a)}
    sb = {x.lower() for x in _split_pipe(b)}
    if not sa or not sb:
        return False
    return not sa.isdisjoint(sb)


def _pick_better(old, new, rank_map):
    return new if rank_map.get(new, 0) > rank_map.get(old, 0) else old


def _compute_confidence(data: dict) -> int:
    """Derive a 0–100 reliability score from parse signals."""
    score = 50
    rt = (data.get("report_type") or "hearsay").lower()
    if rt == "official_confirmation":   score += 25
    elif rt == "direct_report":         score += 10
    elif rt == "hearsay":               score -= 5
    elif rt == "correction_update":     score += 5
    if data.get("hit_confirmed"):       score += 8
    if data.get("fire"):                score += 5
    if data.get("shutdown"):            score += 8
    n_exp = int(data.get("explosions_reported") or 0)
    if n_exp > 0:                       score += min(n_exp * 2, 8)
    dmg = (data.get("damage_level") or "").lower()
    if dmg == "high":                   score += 8
    elif dmg == "medium":               score += 4
    return max(5, min(95, score))


def _merge_type_strings(existing_t: str, new_t: str) -> str:
    def _split(v):
        return [p.strip() for p in v.split(" | ") if p.strip()] if v else []
    seen, ordered = set(), []
    for t in _split(existing_t) + _split(new_t):
        if t and t not in {"unknown", "other"} and t not in seen:
            ordered.append(t)
            seen.add(t)
    return " | ".join(ordered) if ordered else (existing_t or new_t or "other")


# ─────────────────────────────────────────────────────────────────────────────
#  Target resolution
# ─────────────────────────────────────────────────────────────────────────────

def get_or_create_target(conn, target_type: str, area: str = None) -> int:
    """Return target_id for (target_type, area), creating the row if it does not exist."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT target_id FROM {TARGETS_TABLE}
            WHERE lower(target_type) = lower(%s)
              AND lower(COALESCE(area, '')) = lower(COALESCE(%s, ''))
            LIMIT 1
            """,
            (target_type, area or ''),
        )
        row = cur.fetchone()
        if row:
            return row["target_id"]

        try:
            from shared.detectors import get_macro_region
            macro = get_macro_region(area) if area else None
        except Exception:
            macro = None

        cur.execute(
            f"""
            INSERT INTO {TARGETS_TABLE}
                (target_type, area, macro_region, is_canonical)
            VALUES (%s, %s, %s, %s)
            RETURNING target_id
            """,
            (target_type or "unknown", area, macro, not _is_unknown(area)),
        )
        return cur.fetchone()["target_id"]


def _resolve_target_ids(conn, target_type: str, area: str) -> list[int]:
    types = _split_pipe(target_type)
    return [get_or_create_target(conn, ttype, area) for ttype in types]


# ─────────────────────────────────────────────────────────────────────────────
#  Raw message storage
# ─────────────────────────────────────────────────────────────────────────────


def upsert_daily_features(conn, feature_date, channel: str, counts: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {FEATURES_TABLE}
                (feature_date, channel,
                 pre_drone, pre_airdef, pre_airport, pre_uncert,
                 en_attack, en_confirm, en_refinery, war_total, war_ukr_ru,
                 msg_count, computed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (feature_date, channel) DO UPDATE SET
                pre_drone   = EXCLUDED.pre_drone,
                pre_airdef  = EXCLUDED.pre_airdef,
                pre_airport = EXCLUDED.pre_airport,
                pre_uncert  = EXCLUDED.pre_uncert,
                en_attack   = EXCLUDED.en_attack,
                en_confirm  = EXCLUDED.en_confirm,
                en_refinery = EXCLUDED.en_refinery,
                war_total   = EXCLUDED.war_total,
                war_ukr_ru  = EXCLUDED.war_ukr_ru,
                msg_count   = EXCLUDED.msg_count,
                computed_at = NOW()
            """,
            (
                feature_date, channel,
                counts.get("pre_drone",   0),
                counts.get("pre_airdef",  0),
                counts.get("pre_airport", 0),
                counts.get("pre_uncert",  0),
                counts.get("en_attack",   0),
                counts.get("en_confirm",  0),
                counts.get("en_refinery", 0),
                counts.get("war_total",   0),
                counts.get("war_ukr_ru",  0),
                counts.get("msg_count",   0),
            ),
        )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
#  UPDATE matching
# ─────────────────────────────────────────────────────────────────────────────

def find_recent_existing_by_area(
    conn, area: str, attack_date, target_type: str = None
) -> dict | None:
    """
    Find a recent active event in the same area/date window for UPDATE merging.

    Priority:
      1. Overlapping target type.
      2. Either side has unknown/empty target type (enrichment candidate).
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""
            {_EVENT_SELECT}
            WHERE e.area = %s
              AND e.attack_date
                  BETWEEN %s::date - (%s || ' hours')::interval AND %s::date
              AND e.status = 'active'
            {_EVENT_GROUP}
            ORDER BY e.attack_date DESC, e.updated_at DESC NULLS LAST
            """,
            (area, attack_date, UPDATE_WINDOW_HOURS, attack_date),
        )
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        return None

    for row in rows:
        if _has_type_overlap(row.get("target_type"), target_type):
            return row

    for row in rows:
        if _is_unknown(row.get("target_type")) or _is_unknown(target_type):
            return row

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  INSERT
# ─────────────────────────────────────────────────────────────────────────────

def insert_event(conn, data: dict) -> int:
    """Insert a new attack_event row and link its targets. Returns event_id."""
    try:
        from shared.detectors import get_macro_region
        macro = get_macro_region(data.get("area"))
    except Exception:
        macro = None

    confidence = int(data.get("confidence") or _compute_confidence(data))

    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {EVENTS_TABLE} (
                    attack_date, area, macro_region, drone_scale,
                    combined_strike,
                    air_defense_active, hit_confirmed,
                    fire_reported, explosions_count, shutdown_reported,
                    damage_level, report_type, confidence
                ) VALUES (
                    %s, %s, %s, %s,
                    %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                RETURNING event_id
                """,
                (
                    data["attack_date"],
                    data["area"],
                    macro,
                    data.get("drone_scale"),
                    bool(data.get("combined_strike")),
                    bool(data.get("air_defense_active")),
                    bool(data.get("hit_confirmed")),
                    bool(data.get("fire")),
                    int(data.get("explosions_reported") or 0),
                    bool(data.get("shutdown")),
                    data.get("damage_level") or "unknown",
                    data.get("report_type") or "hearsay",
                    confidence,
                ),
            )
            event_id = cur.fetchone()["event_id"]

            target_type = data.get("target_type") or ""
            if target_type:
                target_ids = _resolve_target_ids(conn, target_type, data.get("area"))
                for tid in target_ids:
                    cur.execute(
                        f"""
                        INSERT INTO {ET_TABLE}
                            (event_id, target_id, hit_confirmed, damage_level)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (event_id, target_id) DO NOTHING
                        """,
                        (event_id, tid,
                         bool(data.get("hit_confirmed")),
                         data.get("damage_level")),
                    )

        conn.commit()
        return event_id

    except Exception:
        conn.rollback()
        raise


def insert_attack(conn, data: dict) -> int:
    """Adapter: preserves the old insert_attack signature."""
    return insert_event(conn, data)


# ─────────────────────────────────────────────────────────────────────────────
#  FETCH
# ─────────────────────────────────────────────────────────────────────────────

def fetch_attack_by_id(conn, record_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            {_EVENT_SELECT}
            WHERE e.event_id = %s
            {_EVENT_GROUP}
            """,
            (record_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fetch_recent_attacks(conn, limit: int = 5, for_date=None) -> list[dict]:
    date_clause = "AND e.attack_date <= %s" if for_date is not None else ""
    params = (for_date, limit) if for_date is not None else (limit,)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            {_EVENT_SELECT}
            WHERE e.status = 'active'
            {date_clause}
            {_EVENT_GROUP}
            ORDER BY e.attack_date DESC NULLS LAST, e.created_at DESC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
#  UPDATE — merge new info into existing event
# ─────────────────────────────────────────────────────────────────────────────

def build_updated_record(existing: dict, data: dict) -> dict:
    """
    Merge `data` (new parse) into `existing` (compat-dict from DB).
    Returns a merged dict ready for diff + save.
    """
    area = existing.get("area")
    if _is_unknown(area) and not _is_unknown(data.get("area")):
        area = data["area"]

    target_type = _merge_type_strings(existing.get("target_type"), data.get("target_type"))

    new_confidence = _compute_confidence(data)

    return {
        # WHERE clause identifier
        "match_id": existing["attack_id"],

        # Merged fields
        "area":               area,
        "target_type":        target_type,
        "combined_strike":    bool(existing.get("combined_strike") or data.get("combined_strike")),
        "drone_scale":        existing.get("drone_scale") or data.get("drone_scale"),
        "air_defense_active": bool(
            existing.get("air_defense_active") or data.get("air_defense_active")
        ),
        "fire":               bool(existing.get("fire") or data.get("fire")),
        "shutdown":           bool(existing.get("shutdown") or data.get("shutdown")),
        "explosions_reported":max(
            int(existing.get("explosions_reported") or 0),
            int(data.get("explosions_reported") or 0),
        ),
        "hit_confirmed":      bool(existing.get("hit_confirmed") or data.get("hit_confirmed")),
        "damage_level":       _pick_better(
            existing.get("damage_level"), data.get("damage_level"), DAMAGE_RANK
        ),
        "report_type":        existing.get("report_type"),
        "confidence":         max(int(existing.get("confidence") or 50), new_confidence),
        "source": (
            (existing.get("source") or "")
            + ("\n\n--- UPDATE ---\n\n" if existing.get("source") else "")
            + (data.get("source") or "")
        ),
    }


def diff_update_fields(existing: dict, merged: dict) -> list[tuple]:
    diffs = []
    for field in TRACKED:
        old = existing.get(field)
        new = merged.get(field)
        if old != new:
            diffs.append((field, old, new))
    return diffs


def update_event(conn, event_id: int, merged: dict, diffs: list[tuple]):
    """Apply diffs to an existing event; re-syncs targets and writes audit log."""
    if not diffs:
        return

    real_diffs     = [(f, o, n) for f, o, n in diffs if f != "target_type"]
    target_changed = any(f == "target_type" for f, _, _ in diffs)

    set_parts: list[str] = []
    values:    list      = []

    for field, _, _ in real_diffs:
        col = _FIELD_TO_COL.get(field)
        if col:
            set_parts.append(f"{col} = %s")
            values.append(merged[field])

    set_parts.append("updated_at = NOW()")

    try:
        with conn.cursor() as cur:
            if values:
                values.append(event_id)
                cur.execute(
                    f"""
                    UPDATE {EVENTS_TABLE}
                    SET {", ".join(set_parts)}
                    WHERE event_id = %s
                    """,
                    values,
                )

            if target_changed:
                new_ids = _resolve_target_ids(
                    conn,
                    merged.get("target_type", ""),
                    merged.get("area", ""),
                )
                for tid in new_ids:
                    cur.execute(
                        f"""
                        INSERT INTO {ET_TABLE}
                            (event_id, target_id, hit_confirmed, damage_level)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (event_id, target_id) DO UPDATE
                            SET hit_confirmed = EXCLUDED.hit_confirmed,
                                damage_level  = EXCLUDED.damage_level
                        """,
                        (event_id, tid,
                         bool(merged.get("hit_confirmed")),
                         merged.get("damage_level")),
                    )

            # Audit log — one row per changed field
            for field, old_val, new_val in diffs:
                cur.execute(
                    f"""
                    INSERT INTO {EU_TABLE}
                        (event_id, field_name, old_value, new_value)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        event_id,
                        field,
                        str(old_val) if old_val is not None else None,
                        str(new_val) if new_val is not None else None,
                    ),
                )

        conn.commit()

    except Exception:
        conn.rollback()
        raise


def update_attack(conn, merged: dict, diffs: list[tuple]):
    """Adapter: preserves the old update_attack signature."""
    update_event(conn, merged["match_id"], merged, diffs)
