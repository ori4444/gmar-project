"""
One-time migration: copy existing rows from the legacy 'attacks' table into the
new normalized schema (attack_events, targets, event_targets).

Run once, from the scripts/ directory:
    python telegram_attacks/migrate_to_v2.py

Safe to re-run: INSERT ... ON CONFLICT DO NOTHING for targets, and a per-row
existence check for attack_events (keyed on attack_date + area + source row_id).
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2.extras import RealDictCursor

from shared.config import DB_DSN
from telegram_attacks.db import (
    get_conn, ensure_schema, get_or_create_target,
    EVENTS_TABLE, TARGETS_TABLE, ET_TABLE,
)
from shared.detectors import get_macro_region


def _split_pipe(s: str) -> list[str]:
    return [p.strip() for p in (s or "").split(" | ") if p.strip()]


def migrate(conn):
    # Ensure new tables exist
    ensure_schema(conn)

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_name = 'attacks'")
        if cur.fetchone()["n"] == 0:
            print("Legacy 'attacks' table not found — nothing to migrate.")
            return

        cur.execute("SELECT COUNT(*) AS n FROM attacks")
        total = cur.fetchone()["n"]
        print(f"Found {total} rows in legacy 'attacks' table.")

        cur.execute("SELECT * FROM attacks ORDER BY attack_id")
        rows = [dict(r) for r in cur.fetchall()]

    migrated = 0
    skipped  = 0

    for row in rows:
        with conn.cursor() as cur:
            # Skip if already migrated (check by legacy attack_id stored as a note)
            # We use a simple approach: check for exact (attack_date, area, created_at)
            cur.execute(
                f"""
                SELECT event_id FROM {EVENTS_TABLE}
                WHERE attack_date = %s AND area = %s AND created_at = %s
                LIMIT 1
                """,
                (row["attack_date"], row["area"], row["created_at"]),
            )
            if cur.fetchone():
                skipped += 1
                continue

            macro = get_macro_region(row.get("area"))

            drone_scale = row.get("drone_scale")

            try:
                cur.execute(
                    f"""
                    INSERT INTO {EVENTS_TABLE} (
                        attack_date, area, macro_region, drone_scale,
                        combined_strike,
                        air_defense_active, hit_confirmed,
                        fire_reported, explosions_count, shutdown_reported,
                        damage_level, report_type,
                        confidence, status, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, 'active', %s, %s
                    )
                    RETURNING event_id
                    """,
                    (
                        row["attack_date"],
                        row["area"],
                        macro,
                        drone_scale,
                        bool(row.get("combined_strike")),
                        bool(row.get("air_defense_active")),
                        bool(row.get("hit_confirmed")),
                        bool(row.get("fire")),
                        int(row.get("explosions_reported") or 0),
                        bool(row.get("shutdown")),
                        row.get("damage_level") or "unknown",
                        row.get("report_type") or "hearsay",
                        50,  # default confidence for migrated rows
                        row.get("created_at"),
                        row.get("updated_at"),
                    ),
                )
                event_id = cur.fetchone()["event_id"]

                # Resolve and link targets by type+area
                target_types = _split_pipe(row.get("target_type") or "")

                for ttype in target_types:
                    tid = get_or_create_target(conn, ttype, row.get("area"))
                    cur.execute(
                        f"""
                        INSERT INTO {ET_TABLE} (event_id, target_id, hit_confirmed, damage_level)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (event_id, target_id) DO NOTHING
                        """,
                        (event_id, tid,
                         bool(row.get("hit_confirmed")),
                         row.get("damage_level")),
                    )

                conn.commit()
                migrated += 1

            except Exception as exc:
                conn.rollback()
                print(f"  ERROR migrating row {row.get('attack_id')}: {exc}")

    print(f"Migration complete: {migrated} rows migrated, {skipped} already present.")


if __name__ == "__main__":
    conn = get_conn()
    try:
        migrate(conn)
    finally:
        conn.close()
