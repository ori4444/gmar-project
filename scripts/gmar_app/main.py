"""
Gmar App — unified entry point.

Sections:
  0. Home     — navigation hub
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
from telegram_attacks.translator import translate_to_hebrew, translate_to_english

from gmar_app.db import (
    get_conn, ensure_schema,
    find_recent_existing_by_area, insert_attack, fetch_attack_by_id,
    fetch_recent_attacks, build_updated_record, diff_update_fields, update_attack,
)
from gmar_app.ui.window import get_app, get_main_window, GmarMainWindow
from gmar_app.ui.home import HomePage
from gmar_app.ui.attacks import AttacksPage
from gmar_app.ui.discourse import DiscoursePage
from gmar_app.ui.graphs import GraphsPage
from gmar_app.ui.predictions import PredictionsPage
from gmar_app.ui.multi_target import MultiTargetPage


# ─────────────────────────────────────────────────────────────────────────────

def validate_config():
    errors = []
    if not API_ID or API_ID == 0:
        errors.append("API_ID missing")
    if not API_HASH or "PUT_YOUR" in API_HASH:
        errors.append("API_HASH missing")
    if not DB_DSN or "PUT_YOUR" in DB_DSN:
        errors.append("DB_DSN missing")
    if errors:
        raise ValueError("\n".join(errors))


# ─────────────────────────────────────────────────────────────────────────────
#  Producer / consumer
# ─────────────────────────────────────────────────────────────────────────────

async def producer(queue, client, start_dt_utc, end_dt_utc_exclusive, page: AttacksPage):
    page.set_status("Fetching messages from Telegram…")
    page.append_log("Starting iteration over Telegram history")

    async for msg in iter_messages(client, start_dt_utc, end_dt_utc_exclusive):
        try:
            msg_id = getattr(msg, "id", "?")
            text = clean_text(msg.text or "")
            if not text:
                continue

            preliminary = build_parsed_fields(msg.date, text, existing_event_found=False)
            if not preliminary:
                page.append_log(f"Message {msg_id}: no candidates")
                continue

            page.append_log(f"Message {msg_id}: {len(preliminary)} candidate(s) — translating")
            translated, english = await asyncio.gather(
                asyncio.to_thread(translate_to_hebrew, text),
                asyncio.to_thread(translate_to_english, text),
            )
            await queue.put((msg, text, english, translated, preliminary))

        except Exception as exc:
            page.append_log(f"Message {getattr(msg, 'id', '?')}: error — {exc}")
            continue

    page.set_status("All messages fetched")
    page.append_log("Producer finished")
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

    page.set_status("Connecting to Telegram…")
    page.append_log(f"Session: {start_date} → {end_date}  |  mode={mode}")

    client = None
    conn = None
    producer_task = None

    try:
        client = build_client()
        await client.start()
        page.append_log("Telegram connected")

        page.set_status("Connecting to database…")
        conn = get_conn()
        ensure_schema(conn)
        page.set_fetch_recent_callback(lambda limit=5: fetch_recent_attacks(conn, limit))
        page.append_log("Database ready — starting review")

        queue = asyncio.Queue(maxsize=PREFETCH_QUEUE_SIZE)
        producer_task = asyncio.create_task(
            producer(queue, client, start_dt_utc, end_dt_utc_exclusive, page)
        )

        # Flat list of all reviewable candidates; cursor navigates through it.
        history: list[dict] = []
        queue_done = False

        async def _fill_to(idx: int):
            nonlocal queue_done
            while len(history) <= idx and not queue_done:
                item = await queue.get()
                if item is None:
                    queue_done = True
                    break
                msg, text, english, translated, preliminary = item
                msg_id = getattr(msg, "id", "?")
                for parsed in preliminary:
                    try:
                        existing = await asyncio.to_thread(
                            find_recent_existing_by_area,
                            conn, parsed.area, parsed.attack_date,
                            parsed.target_type,
                        )
                        if existing:
                            merged = build_updated_record(existing, parsed.to_db_dict(text))
                            diffs  = diff_update_fields(existing, merged)
                            if not diffs:
                                page.append_log(f"Message {msg_id}: no changes — skipping")
                                continue
                            if mode == "blind":
                                update_attack(conn, merged, diffs)
                                page.append_log(f"Message {msg_id}: updated (auto)")
                                continue
                            history.append({
                                "msg_id": msg_id, "text": text,
                                "english": english, "translated": translated,
                                "parsed": parsed, "action": "UPDATE",
                                "existing": existing, "diffs": diffs,
                                "record_id": None, "last_edited": None,
                            })
                        else:
                            if mode == "blind":
                                insert_attack(conn, parsed.to_db_dict(text))
                                page.append_log(f"Message {msg_id}: inserted (auto)")
                                continue
                            history.append({
                                "msg_id": msg_id, "text": text,
                                "english": english, "translated": translated,
                                "parsed": parsed, "action": "INSERT",
                                "existing": None, "diffs": None,
                                "record_id": None, "last_edited": None,
                            })
                    except Exception as exc:
                        page.append_log(f"Message {msg_id}: error — {exc}")

        cursor = -1

        while True:
            target = cursor + 1
            await _fill_to(target)

            if target >= len(history):
                page.set_status(f"Done — {len(history)} candidate(s) reviewed")
                page.append_log(f"Review complete: {len(history)} candidate(s)")
                break

            cursor = target
            c = history[cursor]

            # If this candidate was already inserted, re-show as UPDATE.
            display_action  = c["action"]
            display_parsed  = c["last_edited"] or c["parsed"]
            display_existing = c["existing"]
            display_diffs   = c["diffs"]
            if c["record_id"] is not None:
                fresh = fetch_attack_by_id(conn, c["record_id"])
                if fresh:
                    display_existing = fresh
                    display_action   = "UPDATE"
                    display_diffs    = diff_update_fields(fresh, display_parsed.to_db_dict(c["text"]))

            can_fwd = (cursor + 1 < len(history)) or not queue_done
            page.load_candidate(
                action=display_action, parsed=display_parsed,
                original_text=c["english"], translated_text=c["translated"],
                diffs=display_diffs, counter=cursor + 1,
            )
            page.set_nav_state(can_back=(cursor > 0), can_fwd=can_fwd)

            action_code, edited_parsed = await page.async_wait_for_decision()
            c["last_edited"] = edited_parsed

            if action_code == "back":
                cursor -= 2  # loop adds +1 at top
                if cursor < -1:
                    cursor = -1
                continue

            if action_code == "fwd":
                continue

            if action_code == "q":
                page.append_log("User exited")
                if producer_task and not producer_task.done():
                    producer_task.cancel()
                return

            if action_code == "n":
                page.append_log(f"Skipped #{cursor + 1}")
                continue

            if action_code == "y":
                try:
                    if c["record_id"] is not None:
                        fresh = fetch_attack_by_id(conn, c["record_id"])
                        if fresh:
                            merged = build_updated_record(fresh, edited_parsed.to_db_dict(c["text"]))
                            diffs2 = diff_update_fields(fresh, merged)
                            if diffs2:
                                update_attack(conn, merged, diffs2)
                                page.append_log(f"#{cursor + 1}: updated")
                            else:
                                page.append_log(f"#{cursor + 1}: no changes")
                    elif display_action == "UPDATE":
                        merged = build_updated_record(display_existing, edited_parsed.to_db_dict(c["text"]))
                        diffs2 = diff_update_fields(display_existing, merged)
                        if diffs2:
                            update_attack(conn, merged, diffs2)
                            page.append_log(f"UPDATE #{cursor + 1}: saved")
                        else:
                            page.append_log(f"UPDATE #{cursor + 1}: no changes")
                    else:
                        record_id = insert_attack(conn, edited_parsed.to_db_dict(c["text"]))
                        c["record_id"] = record_id
                        page.append_log(f"INSERT #{cursor + 1}: saved")
                except Exception as db_exc:
                    page.append_log(f"#{cursor + 1}: DB error — {db_exc}")

            elif action_code == "mu":
                existing_manual = page.get_manual_update_target()
                if existing_manual:
                    try:
                        merged = build_updated_record(existing_manual, edited_parsed.to_db_dict(c["text"]))
                        diffs2 = diff_update_fields(existing_manual, merged)
                        if not diffs2:
                            page.append_log(f"Manual update #{cursor + 1}: no changes")
                        else:
                            update_attack(conn, merged, diffs2)
                            page.append_log(
                                f"Manual update #{cursor + 1}: saved (id={existing_manual['attack_id']})"
                            )
                    except Exception as db_exc:
                        page.append_log(f"Manual update #{cursor + 1}: DB error — {db_exc}")
                page.reset_to_insert()

    except Exception as exc:
        page.set_status("Error — see log")
        page.append_log(f"Critical error: {type(exc).__name__}: {exc}")

    finally:
        page.set_fetch_recent_callback(None)
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
                page.append_log("Disconnected")
            except Exception:
                pass
        page.set_status("Stopped")


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = get_app()
    app.setFont(QFont("Segoe UI", 11))

    # ── Build pages ──────────────────────────────────────────────────────────
    home_page         = HomePage()
    attacks_page      = AttacksPage()
    discourse_page    = DiscoursePage()
    graphs_page       = GraphsPage()
    multi_target_page = MultiTargetPage()
    predictions_page  = PredictionsPage()

    win = get_main_window()
    win.set_pages([
        ("🏠", "Home",      home_page),
        ("⚡", "Attacks",   attacks_page),
        ("📡", "Discourse", discourse_page),
        ("📊", "Graphs",    graphs_page),
        ("🎯", "Multi",     multi_target_page),
        ("🤖", "Predict",   predictions_page),
    ])
    win.apply_dark_stylesheet()
    win.show()
    win.switch_to(0)   # start on Home

    # Connect home page navigation
    def _navigate_to_attacks():
        win.switch_to(1)

    home_page.set_nav(
        attacks=_navigate_to_attacks,
        discourse=lambda: win.switch_to(2),
        graphs=lambda: win.switch_to(3),
        multi_target=lambda: win.switch_to(4),
        predictions=lambda: win.switch_to(5),
    )

    # ── Async event loop ─────────────────────────────────────────────────────
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    async def main_loop():
        """
        Waits for the user to start an attacks session, runs it,
        then returns to the start panel for the next session.
        """
        while True:
            params = await attacks_page.wait_for_start()
            if params is None:
                break
            mode, start_date, end_date = params
            win.switch_to(1)   # ensure Attacks tab is visible
            try:
                await run_attacks(mode, start_date, end_date, attacks_page)
            except Exception:
                pass  # errors already logged in run_attacks
            finally:
                attacks_page.show_start_panel()

    with loop:
        loop.run_until_complete(main_loop())
