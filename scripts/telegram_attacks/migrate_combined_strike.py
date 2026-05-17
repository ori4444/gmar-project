"""
scripts/telegram_attacks/migrate_combined_strike.py
────────────────────────────────────────────────────
Populate combined_strike for legacy attack_events rows where the field is NULL.

Logic (matches the live parser):
  combined_strike = TRUE   if ≥ 2 active events share the same attack_date
                            (i.e., the attack was part of a multi-target/multi-area wave)
  combined_strike = FALSE  if this event is the only one on its date

Rows that already have combined_strike set (TRUE or FALSE) are left untouched.

Usage
─────
    python scripts/telegram_attacks/migrate_combined_strike.py [--dry-run]

    --dry-run   Print the counts that would be updated without committing.
"""
import argparse
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.config import DB_DSN, EVENTS_TABLE


def run(dry_run: bool = False) -> None:
    conn = psycopg2.connect(DB_DSN)
    cur  = conn.cursor()

    # Count nulls before
    cur.execute(f"SELECT COUNT(*) FROM {EVENTS_TABLE} WHERE combined_strike IS NULL AND status = 'active'")
    null_before = cur.fetchone()[0]
    print(f"Rows with combined_strike IS NULL (active): {null_before}")

    if null_before == 0:
        print("Nothing to migrate.")
        conn.close()
        return

    # Dates with ≥ 2 events → combined
    cur.execute(f"""
        SELECT attack_date
        FROM {EVENTS_TABLE}
        WHERE status = 'active'
        GROUP BY attack_date
        HAVING COUNT(*) >= 2
    """)
    combined_dates = {row[0] for row in cur.fetchall()}
    print(f"Dates with ≥2 events (will set TRUE): {len(combined_dates)}")

    # Count how many NULL rows would be updated each way
    cur.execute(f"""
        SELECT
            SUM(CASE WHEN attack_date = ANY(%s) THEN 1 ELSE 0 END) AS set_true,
            SUM(CASE WHEN attack_date != ALL(%s) THEN 1 ELSE 0 END) AS set_false
        FROM {EVENTS_TABLE}
        WHERE status = 'active'
          AND combined_strike IS NULL
    """, (list(combined_dates), list(combined_dates)))
    row = cur.fetchone()
    n_true, n_false = int(row[0] or 0), int(row[1] or 0)
    print(f"  → Will set TRUE:  {n_true}")
    print(f"  → Will set FALSE: {n_false}")

    if dry_run:
        print("DRY RUN — no changes committed.")
        conn.close()
        return

    # Set TRUE for multi-event dates
    if combined_dates:
        cur.execute(f"""
            UPDATE {EVENTS_TABLE}
            SET combined_strike = TRUE
            WHERE status = 'active'
              AND combined_strike IS NULL
              AND attack_date = ANY(%s)
        """, (list(combined_dates),))
        print(f"Updated {cur.rowcount} rows → TRUE")

    # Set FALSE for single-event dates
    cur.execute(f"""
        UPDATE {EVENTS_TABLE}
        SET combined_strike = FALSE
        WHERE status = 'active'
          AND combined_strike IS NULL
    """)
    print(f"Updated {cur.rowcount} rows → FALSE")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print counts without committing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
