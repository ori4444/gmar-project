import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from qasync import QEventLoop

from shared.config import API_ID, API_HASH, DB_DSN, LOCAL_TZ, PREFETCH_QUEUE_SIZE, REVIEW_MODE
from shared.telegram_client import build_client, iter_messages
from shared.text_utils import clean_text
from db import (
    get_conn,
    ensure_schema,
    find_recent_existing_by_area,
    insert_attack,
    build_updated_record,
    diff_update_fields,
    update_attack,
)
from parser import build_parsed_fields
from translator import translate_to_hebrew
from ui import get_app, get_main_window, show_startup_dialog


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


async def producer(queue, client, start_dt_utc, end_dt_utc_exclusive, window):
    window.set_status("Fetching messages from Telegram…")
    window.append_log("Starting Telegram history iterator")

    async for msg in iter_messages(client, start_dt_utc, end_dt_utc_exclusive):
        try:
            msg_id = getattr(msg, "id", "unknown")
            text = clean_text(msg.text or "")
            if not text:
                continue

            preliminary = build_parsed_fields(msg.date, text, existing_event_found=False)
            if not preliminary:
                window.append_log(f"msg {msg_id}: no candidates")
                continue

            window.append_log(f"msg {msg_id}: {len(preliminary)} candidate(s) — translating")
            translated = translate_to_hebrew(text)
            await queue.put((msg, text, translated, preliminary))

        except Exception as exc:
            window.append_log(f"msg {getattr(msg, 'id', '?')}: error — {exc}")
            continue

    window.set_status("All messages fetched")
    window.append_log("Producer finished")
    await queue.put(None)


async def main(mode: str, start_date, end_date):
    validate_config()

    start_local = datetime(start_date.year, start_date.month, start_date.day, tzinfo=LOCAL_TZ)
    end_local_exclusive = (
        datetime(end_date.year, end_date.month, end_date.day, tzinfo=LOCAL_TZ)
        + timedelta(days=1)
    )
    start_dt_utc = start_local.astimezone(timezone.utc)
    end_dt_utc_exclusive = end_local_exclusive.astimezone(timezone.utc)

    window = get_main_window()
    window.set_status("Connecting to Telegram…")
    window.append_log(f"Session: {start_date} → {end_date}  |  mode={mode}")

    client = None
    conn = None
    producer_task = None
    shown = 0

    try:
        client = build_client()
        await client.start()
        window.append_log("Telegram connected")

        window.set_status("Connecting to database…")
        conn = get_conn()
        ensure_schema(conn)
        window.append_log("Database ready — starting review")

        queue = asyncio.Queue(maxsize=PREFETCH_QUEUE_SIZE)
        producer_task = asyncio.create_task(
            producer(queue, client, start_dt_utc, end_dt_utc_exclusive, window)
        )

        while True:
            item = await queue.get()
            if item is None:
                window.set_status(f"Done — {shown} candidate(s) reviewed")
                window.append_log(f"Review finished: {shown} candidate(s) shown")
                break

            msg, text, translated, preliminary = item
            msg_id = getattr(msg, "id", "unknown")

            for parsed in preliminary:
                try:
                    existing = find_recent_existing_by_area(
                        conn, parsed.area, parsed.attack_date, parsed.targets_names,
                    )

                    if existing:
                        action = "UPDATE"
                        merged = build_updated_record(existing, parsed.to_db_dict(text))
                        diffs  = diff_update_fields(existing, merged)

                        if not diffs:
                            window.append_log(f"msg {msg_id}: no diffs — skipped")
                            continue

                        if mode == "blind":
                            try:
                                update_attack(conn, merged, diffs)
                                window.append_log(f"msg {msg_id}: blind-updated")
                            except Exception as db_exc:
                                window.append_log(f"msg {msg_id}: DB update failed — {db_exc}")
                            continue

                        shown += 1
                        window.load_candidate(
                            action=action, parsed=parsed, original_text=text,
                            translated_text=translated, diffs=diffs, counter=shown,
                        )
                        action_code, edited_parsed = await window.async_wait_for_decision()

                        if action_code == "q":
                            window.append_log("User quit")
                            if producer_task and not producer_task.done():
                                producer_task.cancel()
                            return

                        if action_code == "n":
                            window.append_log(f"Skipped UPDATE #{shown}")
                            continue

                        if action_code == "y":
                            try:
                                merged = build_updated_record(existing, edited_parsed.to_db_dict(text))
                                diffs  = diff_update_fields(existing, merged)
                                if not diffs:
                                    window.append_log(f"UPDATE #{shown}: no changes — skipped")
                                else:
                                    update_attack(conn, merged, diffs)
                                    window.append_log(f"UPDATE #{shown}: saved")
                            except Exception as db_exc:
                                window.append_log(f"UPDATE #{shown}: DB error — {db_exc}")

                    else:
                        action = "INSERT"

                        if mode == "blind":
                            try:
                                insert_attack(conn, parsed.to_db_dict(text))
                                window.append_log(f"msg {msg_id}: blind-inserted")
                            except Exception as db_exc:
                                window.append_log(f"msg {msg_id}: DB insert failed — {db_exc}")
                            continue

                        shown += 1
                        window.load_candidate(
                            action=action, parsed=parsed, original_text=text,
                            translated_text=translated, diffs=None, counter=shown,
                        )
                        action_code, edited_parsed = await window.async_wait_for_decision()

                        if action_code == "q":
                            window.append_log("User quit")
                            if producer_task and not producer_task.done():
                                producer_task.cancel()
                            return

                        if action_code == "n":
                            window.append_log(f"Skipped INSERT #{shown}")
                            continue

                        if action_code == "y":
                            try:
                                insert_attack(conn, edited_parsed.to_db_dict(text))
                                window.append_log(f"INSERT #{shown}: saved")
                            except Exception as db_exc:
                                window.append_log(f"INSERT #{shown}: DB error — {db_exc}")

                except Exception as msg_exc:
                    window.append_log(f"msg {msg_id}: error — {msg_exc}")
                    continue

    except Exception as exc:
        window.set_status("Error — see log")
        window.append_log(f"FATAL: {type(exc).__name__}: {exc}")
        raise

    finally:
        if producer_task is not None and not producer_task.done():
            producer_task.cancel()
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if client is not None:
            try:
                await client.disconnect()
                window.append_log("Disconnected")
            except Exception:
                pass
        window.set_status("Stopped")


if __name__ == "__main__":
    from datetime import date as _date

    app = get_app()

    # ── Step 1: simple date picker (pure Qt, before the async loop) ──────────
    yesterday = _date.today() - timedelta(days=1)
    opts = show_startup_dialog(yesterday, yesterday, REVIEW_MODE)
    if opts is None:
        sys.exit(0)

    mode, start_date, end_date = opts

    # ── Step 2: open the review window and run the async session ─────────────
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        loop.run_until_complete(main(mode, start_date, end_date))
