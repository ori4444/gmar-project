import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from shared.config import API_ID, API_HASH, DB_DSN, LOCAL_TZ, PREFETCH_QUEUE_SIZE
from shared.telegram_client import build_client, iter_messages
from shared.text_utils import clean_text
from db import get_conn, ensure_schema, upsert_features
from features import DailyFeatures


def validate_config():
    errors = []
    if not API_ID or API_ID == 0:
        errors.append("API_ID is missing or invalid")
    if not API_HASH or "PUT_YOUR" in API_HASH:
        errors.append("API_HASH is missing or invalid")
    if not DB_DSN or "PUT_YOUR" in DB_DSN:
        errors.append("DB_DSN is missing or invalid")
    if errors:
        raise ValueError("\n".join(errors))


async def main():
    validate_config()

    start = input("Start date YYYY-MM-DD: ").strip()
    end = input("End date YYYY-MM-DD: ").strip()

    start_local = datetime.strptime(start, "%Y-%m-%d").replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=LOCAL_TZ
    )
    end_local_exclusive = (
        datetime.strptime(end, "%Y-%m-%d").replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=LOCAL_TZ
        ) + timedelta(days=1)
    )

    start_dt_utc = start_local.astimezone(timezone.utc)
    end_dt_utc_exclusive = end_local_exclusive.astimezone(timezone.utc)

    client = build_client()
    await client.start()
    conn = get_conn()
    ensure_schema(conn)

    daily_buckets: dict[object, DailyFeatures] = {}
    total_messages = 0

    try:
        async for msg in iter_messages(client, start_dt_utc, end_dt_utc_exclusive):
            text = clean_text(msg.text or "")
            if not text:
                continue

            msg_local = msg.date.astimezone(LOCAL_TZ)
            day = msg_local.date()

            if day not in daily_buckets:
                daily_buckets[day] = DailyFeatures(
                    feature_date=day,
                    source="astrapress",
                )

            daily_buckets[day].consume_message(text)
            total_messages += 1

    finally:
        await client.disconnect()

    print(f"\nFetched {total_messages} messages across {len(daily_buckets)} day(s).")

    saved = 0
    for day, features in sorted(daily_buckets.items()):
        features.finalize()
        try:
            upsert_features(conn, features)
            print(f"  Saved {day}  (war={features.war_total_messages}  energy={features.energy_attack_messages})")
            saved += 1
        except Exception as exc:
            print(f"  Failed {day}: {exc}")

    conn.close()
    print(f"\nDone. {saved}/{len(daily_buckets)} days saved.")


if __name__ == "__main__":
    asyncio.run(main())
