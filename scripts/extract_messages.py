"""
scripts/extract_messages.py
────────────────────────────────────────────────────────────────────────────
Standalone extraction of attack-related Telegram messages for offline review.

Does NOT touch the database or UI — read-only.

Each kept message is written as one JSON line:

  {"msg_id": 123, "date": "2025-01-15T17:30:00+03:00", "text": "..."}

Filtering: messages that pass is_fast_reject and is_energy_event_candidate
are kept. No parsing is run — raw text only.

Usage
─────
    python scripts/extract_messages.py [--out PATH]

Output defaults to:  data/analysis/messages_jan_mar_2025.jsonl
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared.config import LOCAL_TZ
from shared.telegram_client import build_client, iter_messages
from shared.text_utils import clean_text
from shared.detectors import is_fast_reject, is_energy_event_candidate


START_UTC = datetime(2025, 1, 1,  0, 0, 0, tzinfo=LOCAL_TZ).astimezone(timezone.utc)
END_UTC   = datetime(2025, 4, 1,  0, 0, 0, tzinfo=LOCAL_TZ).astimezone(timezone.utc)


async def extract(out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("Connecting to Telegram …")
    client = build_client()
    await client.start()
    print(f"Connected. Scanning 2025-01-01 → 2025-03-31 …")

    total = 0
    kept  = 0

    with out_path.open("w", encoding="utf-8") as fh:
        async for msg in iter_messages(client, START_UTC, END_UTC):
            total += 1
            if total % 500 == 0:
                print(f"  … {total} scanned, {kept} kept")

            raw = msg.text or ""
            if not raw.strip():
                continue

            text = clean_text(raw)

            if is_fast_reject(text):
                continue

            if not is_energy_event_candidate(text):
                continue

            record = {
                "msg_id": msg.id,
                "date":   msg.date.astimezone(LOCAL_TZ).isoformat(),
                "text":   text,
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            kept += 1

    await client.disconnect()

    print(f"\nDone.  scanned={total:,}  kept={kept:,}")
    print(f"Output → {out_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    root     = Path(__file__).resolve().parent.parent
    out_path = Path(args.out) if args.out else root / "data" / "analysis" / "messages_jan_mar_2025.jsonl"

    asyncio.run(extract(out_path))


if __name__ == "__main__":
    main()
