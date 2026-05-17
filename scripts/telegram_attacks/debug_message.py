import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from parser import build_parsed_fields
from shared.text_utils import clean_text
from shared.detectors import (
    is_fast_reject,
    build_windows,
)
from shared.config import LOCAL_TZ


def read_message():
    print("\nהדבק הודעה. סיים עם END")
    print("exit / quit ליציאה")

    lines = []

    while True:
        line = input()

        if not lines and line.strip().lower() in {"exit", "quit"}:
            return None

        if line.strip() == "END":
            break

        lines.append(line)

    return "\n".join(lines).strip()


def debug_message(text: str):
    print("\n" + "=" * 80)
    print("RAW")
    print("=" * 80)
    print(text)

    cleaned = clean_text(text)

    print("\n" + "=" * 80)
    print("CLEANED")
    print("=" * 80)
    print(cleaned if cleaned else "[EMPTY]")

    if not cleaned:
        print("\nFINAL: REJECTED (empty)")
        return

    if is_fast_reject(cleaned):
        print("\nFINAL: REJECTED (fast_reject)")
        return

    windows = build_windows(cleaned)

    print(f"\nWINDOWS: {len(windows)}")

    parsed = build_parsed_fields(
        datetime.now(LOCAL_TZ),
        cleaned,
        existing_event_found=False
    )

    print(f"\nPARSED COUNT: {len(parsed)}")

    if parsed:
        print("\nFINAL: PASSED")
    else:
        print("\nFINAL: REJECTED (parser)")


def main():
    while True:
        text = read_message()
        if text is None:
            print("bye")
            break

        if not text:
            continue

        debug_message(text)


if __name__ == "__main__":
    main()