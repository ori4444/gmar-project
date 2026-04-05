"""
Gmar App — unified entry point.

Sections:
  1. Attacks  — fetch from Telegram, parse, review & save
  2. Discourse — run daily feature extractor
  3. Graphs   — open Plotly timeline in browser

Usage:
    python gmar_app/main.py
"""
import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from shared.config import (
    API_ID, API_HASH, DB_DSN, LOCAL_TZ,
    PREFETCH_QUEUE_SIZE, REVIEW_MODE,
)
from shared.telegram_client import build_client, iter_messages
from shared.text_utils import clean_text

from telegram_attacks.parser import build_parsed_fields
from telegram_attacks.translator import translate_to_hebrew

from gmar_app.db import (
    get_conn, ensure_schema,
    find_recent_existing_by_area, insert_attack,
    build_updated_record, diff_update_fields, update_attack,
)
from gmar_app.ui.window import get_app, get_main_window, GmarMainWindow
from gmar_app.ui.attacks import AttacksPage
from gmar_app.ui.discourse import DiscoursePage
from gmar_app.ui.graphs import GraphsPage
from gmar_app.startup import show_startup_dialog


# ─────────────────────────────────────────────────────────────────────────────

def validate_config():
    errors = []
    if not API_ID or API_ID == 0:
        errors.append("API_ID חסר")
    if not API_HASH or "PUT_YOUR" in API_HASH:
        errors.append("API_HASH חסר")
    if not DB_DSN or "PUT_YOUR" in DB_DSN:
        errors.append("DB_DSN חסר")
    if errors:
        raise ValueError("\n".join(errors))


# ─────────────────────────────────────────────────────────────────────────────
#  Producer / consumer
# ─────────────────────────────────────────────────────────────────────────────

async def producer(queue, client, start_dt_utc, end_dt_utc_exclusive, page: AttacksPage):
    page.set_status("מושך הודעות מטלגרם…")
    page.append_log("מתחיל איטרציה על היסטוריית טלגרם")

    async for msg in iter_messages(client, start_dt_utc, end_dt_utc_exclusive):
        try:
            msg_id = getattr(msg, "id", "?")
            text = clean_text(msg.text or "")
            if not text:
                continue

            preliminary = build_parsed_fields(msg.date, text, existing_event_found=False)
            if not preliminary:
                page.append_log(f"הודעה {msg_id}: אין מועמדים")
                continue

            page.append_log(f"הודעה {msg_id}: {len(preliminary)} מועמד/ים — מתרגם")
            translated = translate_to_hebrew(text)
            await queue.put((msg, text, translated, preliminary))

        except Exception as exc:
            page.append_log(f"הודעה {getattr(msg, 'id', '?')}: שגיאה — {exc}")
            continue

    page.set_status("כל ההודעות נמשכו")
    page.append_log("הפרודיוסר סיים")
    await queue.put(None)


async def run_attacks(mode: str, start_date, end_date, page: AttacksPage):
    validate_config()

    start_local = datetime(start_date.year, start_date.month, start_date.day, tzinfo=LOCAL_TZ)
    end_local_exclusive = (
        datetime(end_date.year, end_date.month, end_date.day, tzinfo=LOCAL_TZ)
        + timedelta(days=1)
    )
    start_dt_utc = start_local.astimezone(timezone.utc)
    end_dt_utc_exclusive = end_local_exclusive.astimezone(timezone.utc)

    page.set_status("מתחבר לטלגרם…")
    page.append_log(f"סשן: {start_date} → {end_date}  |  מצב={mode}")

    client = None
    conn = None
    producer_task = None
    shown = 0

    try:
        client = build_client()
        await client.start()
        page.append_log("טלגרם מחובר")

        page.set_status("מתחבר למסד נתונים…")
        conn = get_conn()
        ensure_schema(conn)
        page.append_log("מסד נתונים מוכן — מתחיל סקירה")

        queue = asyncio.Queue(maxsize=PREFETCH_QUEUE_SIZE)
        producer_task = asyncio.create_task(
            producer(queue, client, start_dt_utc, end_dt_utc_exclusive, page)
        )

        while True:
            item = await queue.get()
            if item is None:
                page.set_status(f"סיום — {shown} מועמד/ים נסקרו")
                page.append_log(f"הסקירה הסתיימה: {shown} מועמד/ים")
                break

            msg, text, translated, preliminary = item
            msg_id = getattr(msg, "id", "?")

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
                            page.append_log(f"הודעה {msg_id}: אין שינויים — דולג")
                            continue

                        if mode == "blind":
                            try:
                                update_attack(conn, merged, diffs)
                                page.append_log(f"הודעה {msg_id}: עודכן (אוטומטי)")
                            except Exception as db_exc:
                                page.append_log(f"הודעה {msg_id}: שגיאת DB — {db_exc}")
                            continue

                        shown += 1
                        page.load_candidate(
                            action=action, parsed=parsed, original_text=text,
                            translated_text=translated, diffs=diffs, counter=shown,
                        )
                        action_code, edited_parsed = await page.async_wait_for_decision()

                        if action_code == "q":
                            page.append_log("משתמש יצא")
                            if producer_task and not producer_task.done():
                                producer_task.cancel()
                            return

                        if action_code == "n":
                            page.append_log(f"דילג על UPDATE #{shown}")
                            continue

                        if action_code == "y":
                            try:
                                merged = build_updated_record(existing, edited_parsed.to_db_dict(text))
                                diffs  = diff_update_fields(existing, merged)
                                if not diffs:
                                    page.append_log(f"UPDATE #{shown}: אין שינויים")
                                else:
                                    update_attack(conn, merged, diffs)
                                    page.append_log(f"UPDATE #{shown}: נשמר")
                            except Exception as db_exc:
                                page.append_log(f"UPDATE #{shown}: שגיאת DB — {db_exc}")

                    else:
                        action = "INSERT"

                        if mode == "blind":
                            try:
                                insert_attack(conn, parsed.to_db_dict(text))
                                page.append_log(f"הודעה {msg_id}: הוכנס (אוטומטי)")
                            except Exception as db_exc:
                                page.append_log(f"הודעה {msg_id}: שגיאת DB — {db_exc}")
                            continue

                        shown += 1
                        page.load_candidate(
                            action=action, parsed=parsed, original_text=text,
                            translated_text=translated, diffs=None, counter=shown,
                        )
                        action_code, edited_parsed = await page.async_wait_for_decision()

                        if action_code == "q":
                            page.append_log("משתמש יצא")
                            if producer_task and not producer_task.done():
                                producer_task.cancel()
                            return

                        if action_code == "n":
                            page.append_log(f"דילג על INSERT #{shown}")
                            continue

                        if action_code == "y":
                            try:
                                insert_attack(conn, edited_parsed.to_db_dict(text))
                                page.append_log(f"INSERT #{shown}: נשמר")
                            except Exception as db_exc:
                                page.append_log(f"INSERT #{shown}: שגיאת DB — {db_exc}")

                except Exception as msg_exc:
                    page.append_log(f"הודעה {msg_id}: שגיאה — {msg_exc}")
                    continue

    except Exception as exc:
        page.set_status("שגיאה — ראה יומן")
        page.append_log(f"שגיאה קריטית: {type(exc).__name__}: {exc}")
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
                page.append_log("מנותק")
            except Exception:
                pass
        page.set_status("הופסק")


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from datetime import date as _date

    app = get_app()
    app.setFont(QFont("Segoe UI", 11))

    # ── Step 1: date picker (pure Qt, before async loop) ─────────────────────
    yesterday = _date.today() - timedelta(days=1)
    opts = show_startup_dialog(yesterday, yesterday, REVIEW_MODE)
    if opts is None:
        sys.exit(0)

    mode, start_date, end_date = opts

    # ── Step 2: build main window ─────────────────────────────────────────────
    attacks_page  = AttacksPage()
    discourse_page = DiscoursePage()
    graphs_page   = GraphsPage()

    win = get_main_window()
    win.set_pages([
        ("⚡", "תקיפות",  attacks_page),
        ("📡", "שיח",     discourse_page),
        ("📊", "גרפים",   graphs_page),
    ])
    win.apply_dark_stylesheet()
    win.show()

    # Switch to Attacks page immediately
    win.switch_to(0)

    # ── Step 3: run async session ─────────────────────────────────────────────
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        loop.run_until_complete(run_attacks(mode, start_date, end_date, attacks_page))
