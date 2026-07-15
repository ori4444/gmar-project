"""
Message ingestion page — stream Telegram messages, scoring and upserting
daily feature counts into daily_features as each day completes.
"""
from __future__ import annotations

import asyncio
import re
import time
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


def _new_counts() -> dict:
    return dict(pre_drone=0, pre_airdef=0, pre_airport=0, pre_uncert=0,
                en_attack=0, en_confirm=0, en_refinery=0, war_total=0, war_ukr_ru=0,
                msg_count=0)


def _update_counts(c: dict, t: str) -> None:
    """Score a single message into the running day-counts dict, in place."""
    c["msg_count"] += 1
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


# ── Exilenova+ (Ukrainian-language, mixed with quoted Russian) ────────────────
# Same 9-column schema as astrapress — only the keyword patterns differ.
_RE_DRONE_UA    = re.compile(r"бпла|безпілотник|дрон|шахед\w*|\bлютий\b", re.IGNORECASE)
_RE_AIRDEF_UA   = re.compile(
    r"\bпво\b|збит\w*|збили|перехоплен\w*|уражен\w*.{0,8}(?:пво|рлс)|рлс", re.IGNORECASE
)
_RE_AIRPORT_UA  = re.compile(r"аеропорт|аэропорт", re.IGNORECASE)
_RE_UNCERT_UA   = re.compile(
    r"ймовірно|можливо.{0,8}(?:був|була|також)|непідтвердж\w*|неперевірен\w*"
    r"|предположительно|непроверенн\w*",
    re.IGNORECASE,
)
_RE_OFFICIAL_UA = re.compile(
    r"губернатор\w*|міноборони|пресслужб\w*|osint підтвердж\w*|підтвердженн\w* від",
    re.IGNORECASE,
)
_RE_FACILITY_UA = re.compile(
    r"нпз|нафтобаз\w*|нафтохім\w*|нафтопереробн\w*|нафтопровід\w*|нафтопродуктопровід\w*"
    r"|газопровід\w*|лпдс|підстанці\w*|\bтец\b|\bаес\b"
    r"|нефтебаз\w*|нефтехим\w*|нефтеперерабат\w*|нефтепровод\w*|электроподстанц\w*",
    re.IGNORECASE,
)
_RE_ATTACK_SIG_UA = re.compile(
    r"атак\w*|удар\w*|уражен\w*|поразил\w*|пожеж\w*|горит\w*|горить|вибух\w*"
    r"|загорел\w*|спалах\w*|пошкоджен\w*|прилет\w*|попадан\w*"
    r"|безпілотник\w*|дрон\w*|\bбпла\b",
    re.IGNORECASE,
)
_RE_REFINERY_UA = re.compile(
    r"нпз|нафтопереробн\w*|нафтобаз\w*|нафтохім\w*|нефтебаз\w*|нефтеперерабат\w*",
    re.IGNORECASE,
)
_RE_WAR_UA      = re.compile(
    r"\bбпла\b|безпілотник\w*|дрон\w*|шахед\w*|удар\w*|атак\w*|ракет\w*", re.IGNORECASE
)
_RE_UKRSTRIKE_UA = re.compile(
    r"сили безпілотних систем|\bсбс\b|\bсоу\b|сили оборони україни", re.IGNORECASE
)


def _update_counts_exilenova(c: dict, t: str) -> None:
    """Score a single message into the running day-counts dict, Exilenova+ keyword set (UA + quoted RU)."""
    c["msg_count"] += 1
    if _RE_DRONE_UA.search(t):      c["pre_drone"]   += 1
    if _RE_AIRDEF_UA.search(t):     c["pre_airdef"]  += 1
    if _RE_AIRPORT_UA.search(t):    c["pre_airport"] += 1
    if _RE_UNCERT_UA.search(t):     c["pre_uncert"]  += 1
    is_energy = bool(_RE_FACILITY_UA.search(t)) and bool(_RE_ATTACK_SIG_UA.search(t))
    if is_energy:
        c["en_attack"] += 1
        if _RE_OFFICIAL_UA.search(t):
            c["en_confirm"] += 1
    if _RE_REFINERY_UA.search(t):   c["en_refinery"] += 1
    if _RE_WAR_UA.search(t):        c["war_total"]   += 1
    if _RE_UKRSTRIKE_UA.search(t):  c["war_ukr_ru"]  += 1


# ── Radar (radarrussiia) — structured region+status БПЛА radar alerts ─────────
# Precursor/tactical channel (drone detected → danger declared → PVO engaged →
# hit/all-clear), distinct from astrapress/exilenova's after-the-fact OSINT
# reporting. Same 9-column schema; only the keyword patterns differ.
_RE_DRONE_RADAR    = re.compile(r"бпла|беспилотник\w*|дрон\w*|шахед\w*", re.IGNORECASE)
_RE_AIRDEF_RADAR   = re.compile(
    r"\bпво\b|работа пво|сбит\w*|уничтожен\w*|перехвачен\w*|подавлен\w*", re.IGNORECASE
)
_RE_AIRPORT_RADAR  = re.compile(r"аэропорт", re.IGNORECASE)
_RE_UNCERT_RADAR   = re.compile(
    r"предположительно|возможно|вероятно|уточня\w*|неподтвержденн\w*", re.IGNORECASE
)
_RE_OFFICIAL_RADAR = re.compile(r"минобороны|губернатор|генштаб|пресс.{0,4}служб", re.IGNORECASE)
_RE_FACILITY_RADAR = re.compile(
    r"\bнпз\b|нефтеперерабатыва|нефтебаз|нефтехранилищ|нефтепровод"
    r"|газопровод|\bнпс\b|транснефт|подстанц|электростанц|\bтэц\b|\bаэс\b",
    re.IGNORECASE,
)
_RE_ATTACK_SIG_RADAR = re.compile(
    r"атак\w*|удар\w*|прилет\w*|попадан\w*|поразил\w*|пожар\w*|горит|горел\w*"
    r"|взрыв\w*|поврежден\w*|возгоран\w*",
    re.IGNORECASE,
)
_RE_REFINERY_RADAR = re.compile(
    r"\bнпз\b|нефтеперерабатыва|нефтебаз|нефтехранилищ|нефтеналивн", re.IGNORECASE
)
_RE_WAR_RADAR       = re.compile(r"\bбпла\b|беспилотник\w*|дрон\w*|удар\w*|атак\w*|ракет\w*", re.IGNORECASE)
_RE_UKRSTRIKE_RADAR = re.compile(r"\bвсу\b|украин\w*.{0,12}(?:атак|удар)", re.IGNORECASE)


def _update_counts_radar(c: dict, t: str) -> None:
    """Score a single message into the running day-counts dict, Radar (radarrussiia) keyword set."""
    c["msg_count"] += 1
    if _RE_DRONE_RADAR.search(t):      c["pre_drone"]   += 1
    if _RE_AIRDEF_RADAR.search(t):     c["pre_airdef"]  += 1
    if _RE_AIRPORT_RADAR.search(t):    c["pre_airport"] += 1
    if _RE_UNCERT_RADAR.search(t):     c["pre_uncert"]  += 1
    is_energy = bool(_RE_FACILITY_RADAR.search(t)) and bool(_RE_ATTACK_SIG_RADAR.search(t))
    if is_energy:
        c["en_attack"] += 1
        if _RE_OFFICIAL_RADAR.search(t):
            c["en_confirm"] += 1
    if _RE_REFINERY_RADAR.search(t):   c["en_refinery"] += 1
    if _RE_WAR_RADAR.search(t):        c["war_total"]   += 1
    if _RE_UKRSTRIKE_RADAR.search(t):  c["war_ukr_ru"]  += 1

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import palette as P
from . import styles as S


def _hline() -> QWidget:
    w = QWidget()
    w.setFixedHeight(1)
    w.setStyleSheet(S.divider_qss())
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
        self.setStyleSheet(S.window_bg_qss())
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # Header
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(10)
        hdr_icon = QLabel("📡")
        hdr_icon.setStyleSheet("font-size:22px; border:none; background:transparent;")
        hdr = QLabel("קליטת הודעות")
        hdr.setStyleSheet(
            f"color:{P.TXT}; font-family:{P.FONT_STACK}; font-size:21px; font-weight:700; "
            f"border:none; background:transparent;"
        )
        hdr_row.addWidget(hdr_icon)
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        root.addLayout(hdr_row)

        sub = QLabel("שולפים הודעות מטלגרם לטווח התאריכים שנבחר ומעדכנים את הנתונים היומיים")
        sub.setStyleSheet(
            f"color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:13px; border:none; background:transparent;"
        )
        root.addWidget(sub)

        root.addWidget(_hline())

        # Config card
        cfg_card = QWidget()
        cfg_card.setObjectName("cfgCard")
        cfg_card.setStyleSheet(S.card_qss("#cfgCard"))
        S.apply_card_shadow(cfg_card, blur=32, alpha=80, y_offset=9)
        cfg_lay = QHBoxLayout(cfg_card)
        cfg_lay.setContentsMargins(18, 16, 18, 16)
        cfg_lay.setSpacing(20)

        field_label_qss = (
            f"color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:11px; font-weight:600; "
            f"letter-spacing:0.5px; border:none;"
        )
        field_input_qss = (
            "{tag}{{background:%s;color:%s;"
            "border:1px solid %s;border-radius:%dpx;"
            "padding:4px 12px;font-family:%s;font-size:13px;font-weight:600;}}"
            "{tag}:hover{{border:1px solid %s;}}"
            "{tag}:focus{{border:1px solid %s;}}"
            "{tag}::drop-down{{border:none;}}"
        ) % (P.INPUT_BG, P.TXT, P.INPUT_BORDER, P.RADIUS_MD, P.FONT_STACK, P.BORDER_STRONG, P.INPUT_FOCUS)

        chan_col = QVBoxLayout()
        chan_col.setSpacing(6)
        chan_lbl = QLabel("ערוץ")
        chan_lbl.setStyleSheet(field_label_qss)
        self._channel_combo = QComboBox()
        self._channel_combo.addItem("Astra (astrapress)", "astrapress")
        self._channel_combo.addItem("Exilenova+ (exilenova_plus)", "exilenova_plus")
        self._channel_combo.addItem("Radar (radarrussiia)", "radarrussiia")
        self._channel_combo.setFixedHeight(38)
        self._channel_combo.setStyleSheet(field_input_qss.format(tag="QComboBox"))
        chan_col.addWidget(chan_lbl)
        chan_col.addWidget(self._channel_combo)
        cfg_lay.addLayout(chan_col)

        for lbl_text, attr in [("מ-", "start_edit"), ("עד", "end_edit")]:
            col = QVBoxLayout()
            col.setSpacing(6)
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(field_label_qss)
            w = QDateEdit()
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd  MMM  yyyy")
            w.setDate(QDate.currentDate().addDays(-1))
            w.setFixedHeight(38)
            w.setStyleSheet(field_input_qss.format(tag="QDateEdit"))
            setattr(self, attr, w)
            col.addWidget(lbl)
            col.addWidget(w)
            cfg_lay.addLayout(col)

        cfg_lay.addStretch()

        # Run button
        self._run_btn = QPushButton("▶  הרצה")
        self._run_btn.setFixedHeight(38)
        self._run_btn.setFixedWidth(120)
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.setStyleSheet(
            f"QPushButton{{background:{P.INDIGO};color:#fff;"
            f"border:none;border-radius:{P.RADIUS_MD}px;"
            f"font-family:{P.FONT_STACK};font-size:13px;font-weight:700;}}"
            f"QPushButton:hover{{background:{P.INDIGO_L};}}"
            f"QPushButton:disabled{{background:{P.INPUT_BG};color:{P.TXT3};}}"
        )
        S.apply_button_shadow(self._run_btn, blur=20, alpha=100, y_offset=6)
        self._run_btn.clicked.connect(self._on_run)
        run_col = QVBoxLayout()
        run_col.setSpacing(6)
        run_spacer = QLabel(" ")
        run_spacer.setStyleSheet(field_label_qss)
        run_col.addWidget(run_spacer)
        run_col.addWidget(self._run_btn)
        cfg_lay.addLayout(run_col)

        root.addWidget(cfg_card)

        # Status
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self._spinner = S.LoadingSpinner(size=15)
        status_row.addWidget(self._spinner)
        self._status_lbl = QLabel("ממתינים…")
        self._status_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:12px; font-weight:600; border:none;"
        )
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()
        root.addLayout(status_row)

        # Log
        log_hdr = QLabel("יומן פעולות")
        log_hdr.setStyleSheet(
            f"color:{P.TXT3}; font-family:{P.FONT_STACK}; font-size:10px; font-weight:700; "
            f"letter-spacing:1.5px; border:none;"
        )
        self._log_edit = QPlainTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setStyleSheet(
            f"background:#08090b; color:{P.TXT2}; "
            f"border:1px solid {P.DIVIDER}; border-radius:{P.RADIUS_MD}px; "
            f"font-size:12px; font-family:{P.FONT_MONO}; padding:8px;"
        )

        root.addWidget(log_hdr)
        root.addWidget(self._log_edit, 1)

    # ── Public API ────────────────────────────────────────────────────────────

    def reset_view(self):
        """Called on re-entry: only clears stale spinner state, never the log/results."""
        if not self._running:
            self._spinner.stop()

    def append_log(self, msg: str):
        self._log_edit.appendPlainText(msg)

    def set_status(self, msg: str):
        self._status_lbl.setText(msg)

    def _log_phase(self, n: int, msg: str):
        line = f"[{n}/2] {msg}"
        self.set_status(line)
        self.append_log(f"\n── {line}")

    # ── Run logic ─────────────────────────────────────────────────────────────

    def _on_run(self):
        if self._running:
            return
        start = _from_qdate(self.start_edit.date())
        end   = _from_qdate(self.end_edit.date())
        if start > end:
            self.set_status("שגיאה: תאריך ההתחלה מאוחר מתאריך הסיום")
            return

        channel = self._channel_combo.currentData()

        self._running = True
        self._run_btn.setEnabled(False)
        self._spinner.start()
        self.set_status(f"רצים… {start} → {end}  ·  {channel}")
        self._log_edit.clear()

        asyncio.ensure_future(self._run_pipeline(start, end, channel))

    async def _run_pipeline(self, start: date, end: date, channel: str):
        conn = None
        client = None
        try:
            import os, sys
            scripts = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if scripts not in sys.path:
                sys.path.insert(0, scripts)

            from datetime import datetime, timedelta, timezone
            from shared.config import LOCAL_TZ
            from shared.telegram_client import build_client, iter_messages
            from shared.text_utils import clean_text
            from gmar_app.db import get_conn, ensure_schema, upsert_daily_features

            update_fn = {
                "exilenova_plus": _update_counts_exilenova,
                "radarrussiia":   _update_counts_radar,
            }.get(channel, _update_counts)

            total_days = max((end - start).days + 1, 1)
            start_utc  = datetime(start.year, start.month, start.day, tzinfo=LOCAL_TZ).astimezone(timezone.utc)
            end_utc    = (datetime(end.year, end.month, end.day, tzinfo=LOCAL_TZ) + timedelta(days=1)).astimezone(timezone.utc)

            # ── Phase 1: Connect ─────────────────────────────────────────────
            self._log_phase(1, "מתחברים לטלגרם ולמסד הנתונים…")
            conn = get_conn()
            ensure_schema(conn)
            self.append_log("  מסד הנתונים מוכן")
            client = build_client()
            await client.start()
            self.append_log(f"  טלגרם מחובר  |  ערוץ: {channel}  |  טווח: {start} → {end}  ({total_days} ימים)")

            # ── Phase 2: Fetch, count, and upsert per day (streaming) ────────
            self._log_phase(2, "שולפים הודעות ומעדכנים את הנתונים היומיים…")
            total = 0
            n_days = 0
            t0 = time.monotonic()
            cur_date = None
            cur_counts = None

            async for msg in iter_messages(client, start_utc, end_utc, channel=channel):
                text = clean_text(msg.text or "")
                if not text:
                    continue
                total += 1
                msg_date = msg.date.astimezone(LOCAL_TZ).date()

                if msg_date != cur_date:
                    if cur_date is not None:
                        upsert_daily_features(conn, cur_date, channel, cur_counts)
                        self.append_log(f"  {cur_date}  ·  {cur_counts['msg_count']} הודעות  ·  עודכן")
                        n_days += 1
                    cur_date = msg_date
                    cur_counts = _new_counts()

                update_fn(cur_counts, text)

                if total % 30 == 0:
                    elapsed = time.monotonic() - t0
                    rate = total / elapsed if elapsed > 0.1 else 0
                    days_done = max((msg_date - start).days, 0)
                    pct = days_done / total_days
                    eta = ""
                    if pct > 0.03 and elapsed > 3:
                        remaining = elapsed / pct * (1 - pct)
                        eta = f"  נותרו כ-{remaining/60:.0f} דק'" if remaining > 90 else f"  נותרו כ-{remaining:.0f} שנ'"
                    self.set_status(
                        f"[2/2] שולפים…  {total} הודעות  |  {n_days} ימים עודכנו  |  {rate:.0f} הודעות/שנ'{eta}"
                    )

            if cur_date is not None:
                upsert_daily_features(conn, cur_date, channel, cur_counts)
                self.append_log(f"  {cur_date}  ·  {cur_counts['msg_count']} הודעות  ·  עודכן")
                n_days += 1

            self.append_log(f"  ─  סה\"כ: {total} הודעות מתוך {n_days} ימים  ·  הכול עודכן בנתונים היומיים")

            # ── Done ─────────────────────────────────────────────────────────
            elapsed_total = time.monotonic() - t0
            self.set_status(
                f"הושלם  |  {total} הודעות  |  {n_days} ימי נתונים  "
                f"|  {elapsed_total/60:.1f} דק'"
            )
            self.append_log(f"\n=== הושלם תוך {elapsed_total/60:.1f} דק' ===")

        except Exception as exc:
            self.append_log(f"\nשגיאה: {type(exc).__name__}: {exc}")
            self.set_status("שגיאה — ראו יומן")
        finally:
            self._running = False
            self._run_btn.setEnabled(True)
            self._spinner.stop()
            if conn is not None:
                try: conn.close()
                except Exception: pass
            if client is not None:
                try: await client.disconnect()
                except Exception: pass
