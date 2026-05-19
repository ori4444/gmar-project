"""
Message ingestion page — fetch Telegram messages, save to raw_messages,
translate, and compute daily feature counts into daily_features.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from datetime import date

# ── Pre-compiled signal patterns (compiled once at import) ────────────────────
_RE_DRONE      = re.compile(r"бпла|беспилотник|дрон", re.IGNORECASE)
_RE_AIRDEF     = re.compile(r"\bпво\b|сбит|уничтожен|перехвачен|\bрэб\b|отражен.{0,8}атак", re.IGNORECASE)
_RE_AIRPORT    = re.compile(r"аэропорт", re.IGNORECASE)
_RE_UNCERT     = re.compile(r"предположительно|якобы|неподтвержденн|непроверенн", re.IGNORECASE)
_RE_OFFICIAL   = re.compile(r"минобороны|губернатор|генштаб|пресс.{0,4}служб", re.IGNORECASE)
_RE_FACILITY   = re.compile(
    r"\bнпз\b|нефтеперерабатыва|нефтебаз|нефтехранилищ|нефтепровод"
    r"|газопровод|\bнпс\b|транснефт|подстанц|электростанц|\bтэц\b|\bаэс\b",
    re.IGNORECASE,
)
_RE_ATTACK_SIG = re.compile(
    r"атак|удар|прилет|попадани|поразил|пожар|горит|взрыв|загорел|поврежден"
    r"|беспилотник|дрон|\bбпла\b",
    re.IGNORECASE,
)
_RE_REFINERY   = re.compile(
    r"\bнпз\b|нефтеперерабатыва|нефтебаз|нефтехранилищ|нефтеналивн",
    re.IGNORECASE,
)
_RE_WAR        = re.compile(r"\bбпла\b|беспилотник|дрон|удар|атак|ракет|нептун", re.IGNORECASE)
_RE_UKRSTRIKE  = re.compile(
    r"украин.{0,12}удар|всу.{0,8}(?:атак|удар)|украин.{0,8}атак",
    re.IGNORECASE,
)


def _compute_day_features(texts: list) -> dict:
    """Count signal hits for one day's messages. Pure CPU — call via asyncio.to_thread."""
    c = dict(pre_drone=0, pre_airdef=0, pre_airport=0, pre_uncert=0,
             en_attack=0, en_confirm=0, en_refinery=0, war_total=0, war_ukr_ru=0)
    for t in texts:
        if _RE_DRONE.search(t):      c["pre_drone"]   += 1
        if _RE_AIRDEF.search(t):     c["pre_airdef"]  += 1
        if _RE_AIRPORT.search(t):    c["pre_airport"] += 1
        if _RE_UNCERT.search(t):     c["pre_uncert"]  += 1
        is_energy = bool(_RE_FACILITY.search(t)) and bool(_RE_ATTACK_SIG.search(t))
        if is_energy:
            c["en_attack"] += 1
            if _RE_OFFICIAL.search(t):
                c["en_confirm"] += 1
        if _RE_REFINERY.search(t):   c["en_refinery"] += 1
        if _RE_WAR.search(t):        c["war_total"]   += 1
        if _RE_UKRSTRIKE.search(t):  c["war_ukr_ru"]  += 1
    return c

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import palette as P


def _hline() -> QWidget:
    w = QWidget()
    w.setFixedHeight(1)
    w.setStyleSheet(f"background:{P.DIVIDER}; border-radius:1px;")
    return w


def _to_qdate(v: date) -> QDate:
    return QDate(v.year, v.month, v.day)


def _from_qdate(v: QDate) -> date:
    return date(v.year(), v.month(), v.day())


class DiscoursePage(QWidget):
    """
    Allows the user to pick a date range and run the discourse pipeline.
    Shows live log output.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # Header
        hdr_row = QHBoxLayout()
        hdr_row.setAlignment(Qt.AlignRight)
        hdr_icon = QLabel("📡")
        hdr_icon.setStyleSheet("font-size:26px; border:none;")
        hdr = QLabel("Message Ingestion")
        hdr.setStyleSheet(
            f"color:{P.TXT}; font-size:24px; font-weight:900; "
            f"border:none; direction:rtl;"
        )
        hdr_row.addStretch()
        hdr_row.addWidget(hdr)
        hdr_row.addWidget(hdr_icon)
        root.addLayout(hdr_row)

        sub = QLabel("Fetch Telegram messages for the selected date range and save to raw_messages")
        sub.setStyleSheet(
            f"color:{P.TXT2}; font-size:14px; border:none; direction:rtl;"
        )
        sub.setAlignment(Qt.AlignRight)
        root.addWidget(sub)

        root.addWidget(_hline())

        # Config row
        cfg_lay = QHBoxLayout()
        cfg_lay.setContentsMargins(0, 0, 0, 0)
        cfg_lay.setSpacing(20)
        cfg_lay.setAlignment(Qt.AlignRight)

        for lbl_text, attr in [("To", "end_edit"), ("From", "start_edit")]:
            col = QVBoxLayout()
            col.setSpacing(4)
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(
                f"color:{P.TXT2}; font-size:12px; font-weight:700; border:none;"
            )
            lbl.setAlignment(Qt.AlignRight)
            w = QDateEdit()
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd  MMM  yyyy")
            w.setDate(QDate.currentDate().addDays(-1))
            w.setFixedHeight(40)
            w.setStyleSheet(
                f"QDateEdit{{background:{P.INPUT_BG};color:{P.TXT};"
                f"border:1px solid {P.INPUT_BORDER};border-radius:8px;"
                f"padding:4px 12px;font-size:14px;font-weight:700;}}"
                f"QDateEdit:focus{{border:2px solid {P.INPUT_FOCUS};}}"
                f"QDateEdit::drop-down{{border:none;}}"
            )
            setattr(self, attr, w)
            col.addWidget(lbl)
            col.addWidget(w)
            cfg_lay.addLayout(col)

        # Run button
        self._run_btn = QPushButton("▶  Run")
        self._run_btn.setFixedHeight(48)
        self._run_btn.setFixedWidth(130)
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{P.INDIGO};"
            f"border:1px solid {P.INDIGO};border-radius:10px;"
            f"font-size:15px;font-weight:800;}}"
            f"QPushButton:hover{{background:{P.INDIGO};color:#fff;}}"
            f"QPushButton:disabled{{color:{P.TXT3};border-color:{P.TXT3};}}"
        )
        self._run_btn.clicked.connect(self._on_run)
        cfg_lay.insertWidget(0, self._run_btn)  # left-most

        root.addLayout(cfg_lay)

        root.addWidget(_hline())

        # Status
        self._status_lbl = QLabel("Waiting…")
        self._status_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-size:13px; font-weight:700; border:none;"
        )
        self._status_lbl.setAlignment(Qt.AlignRight)
        root.addWidget(self._status_lbl)

        # Log
        log_hdr = QLabel("LOG")
        log_hdr.setStyleSheet(
            f"color:{P.TXT3}; font-size:11px; font-weight:800; "
            f"letter-spacing:2px; border:none;"
        )
        self._log_edit = QPlainTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setStyleSheet(
            f"background:{P.INPUT_BG}; color:{P.TXT2}; "
            f"border:1px solid {P.DIVIDER}; border-radius:8px; "
            f"font-size:11px; font-family:Consolas,monospace;"
        )

        root.addWidget(log_hdr)
        root.addWidget(self._log_edit, 1)

    # ── Public API ────────────────────────────────────────────────────────────

    def append_log(self, msg: str):
        self._log_edit.appendPlainText(msg)

    def set_status(self, msg: str):
        self._status_lbl.setText(msg)

    def _log_phase(self, n: int, msg: str):
        line = f"[{n}/3] {msg}"
        self.set_status(line)
        self.append_log(f"\n── {line}")

    # ── Run logic ─────────────────────────────────────────────────────────────

    def _on_run(self):
        if self._running:
            return
        start = _from_qdate(self.start_edit.date())
        end   = _from_qdate(self.end_edit.date())
        if start > end:
            self.set_status("Error: start date is after end date")
            return

        self._running = True
        self._run_btn.setEnabled(False)
        self.set_status(f"Running… {start} → {end}")
        self._log_edit.clear()

        asyncio.ensure_future(self._run_pipeline(start, end))

    async def _run_pipeline(self, start: date, end: date):
        conn = None
        client = None
        try:
            import os, sys
            scripts = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if scripts not in sys.path:
                sys.path.insert(0, scripts)

            from datetime import datetime, timedelta, timezone
            from shared.config import LOCAL_TZ, CHANNEL_USERNAME
            from shared.telegram_client import build_client, iter_messages
            from shared.text_utils import clean_text
            from gmar_app.db import get_conn, ensure_schema, upsert_daily_features

            total_days = max((end - start).days + 1, 1)
            start_utc  = datetime(start.year, start.month, start.day, tzinfo=LOCAL_TZ).astimezone(timezone.utc)
            end_utc    = (datetime(end.year, end.month, end.day, tzinfo=LOCAL_TZ) + timedelta(days=1)).astimezone(timezone.utc)

            # ── Phase 1: Connect ─────────────────────────────────────────────
            self._log_phase(1, "Connecting to Telegram and database…")
            conn = get_conn()
            ensure_schema(conn)
            self.append_log("  DB ready")
            client = build_client()
            await client.start()
            self.append_log(f"  Telegram connected  |  range: {start} → {end}  ({total_days} days)")

            # ── Phase 2: Fetch + accumulate in memory ────────────────────────
            self._log_phase(2, "Fetching messages from Telegram…")
            by_date = defaultdict(list)
            total = 0
            t0 = time.monotonic()
            _cur_date = None

            async for msg in iter_messages(client, start_utc, end_utc):
                text = clean_text(msg.text or "")
                if not text:
                    continue
                total += 1
                msg_date = msg.date.astimezone(LOCAL_TZ).date()

                if msg_date != _cur_date:
                    if _cur_date is not None:
                        self.append_log(f"  {_cur_date}  ·  {len(by_date[_cur_date])} msgs")
                    _cur_date = msg_date

                by_date[msg_date].append(text)

                if total % 30 == 0:
                    elapsed = time.monotonic() - t0
                    rate = total / elapsed if elapsed > 0.1 else 0
                    days_done = max((msg_date - start).days, 0)
                    pct = days_done / total_days
                    eta = ""
                    if pct > 0.03 and elapsed > 3:
                        remaining = elapsed / pct * (1 - pct)
                        eta = f"  ETA ~{remaining/60:.0f}m" if remaining > 90 else f"  ETA ~{remaining:.0f}s"
                    self.set_status(
                        f"[2/3] Fetching…  {total} msgs  |  {len(by_date)} days  |  {rate:.0f} msg/s{eta}"
                    )

            if _cur_date is not None:
                self.append_log(f"  {_cur_date}  ·  {len(by_date[_cur_date])} msgs")
            self.append_log(f"  ─  Total: {total} msgs across {len(by_date)} days")

            # ── Phase 3: Extract features + upsert ───────────────────────────
            n_days = len(by_date)
            self._log_phase(3, f"Extracting features for {n_days} days…")
            for day_idx, feat_date in enumerate(sorted(by_date.keys()), 1):
                texts = by_date[feat_date]
                status = f"[3/3] Extracting…  day {day_idx}/{n_days}  ({feat_date}, {len(texts)} msgs)"
                self.set_status(status)
                self.append_log(f"  [{day_idx}/{n_days}]  {feat_date}  ·  {len(texts)} msgs")
                counts = await asyncio.to_thread(_compute_day_features, texts)
                counts["msg_count"] = len(texts)
                upsert_daily_features(conn, feat_date, CHANNEL_USERNAME, counts)

            self.append_log(f"  ─  Upserted {n_days} rows into daily_features")

            # ── Done ─────────────────────────────────────────────────────────
            elapsed_total = time.monotonic() - t0
            self.set_status(
                f"Done  |  {total} msgs  |  {n_days} days features  "
                f"|  {elapsed_total/60:.1f} min"
            )
            self.append_log(f"\n=== Complete in {elapsed_total/60:.1f} min ===")

        except Exception as exc:
            self.append_log(f"\nError: {type(exc).__name__}: {exc}")
            self.set_status("Error — see log")
        finally:
            self._running = False
            self._run_btn.setEnabled(True)
            if conn is not None:
                try: conn.close()
                except Exception: pass
            if client is not None:
                try: await client.disconnect()
                except Exception: pass
