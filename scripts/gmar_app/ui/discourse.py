"""
Discourse processing page — run the daily feature extractor.
"""
from __future__ import annotations

import asyncio
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import palette as P


def _card(parent=None) -> QFrame:
    f = QFrame(parent)
    f.setStyleSheet(
        f"QFrame {{ background:{P.CARD_BG}; border:3px solid {P.CARD_BORDER};"
        f"border-radius:14px; }}"
    )
    return f


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
        hdr = QLabel("עיבוד שיח יומי")
        hdr.setStyleSheet(
            f"color:{P.TXT}; font-size:22px; font-weight:900; border:none;"
        )
        hdr.setAlignment(Qt.AlignRight)
        root.addWidget(hdr)

        sub = QLabel("הרץ את תהליך חילוץ המאפיינים לפרק זמן שנבחר")
        sub.setStyleSheet(f"color:{P.TXT2}; font-size:14px; border:none;")
        sub.setAlignment(Qt.AlignRight)
        root.addWidget(sub)

        # Config card
        cfg_card = _card()
        cfg_lay = QHBoxLayout(cfg_card)
        cfg_lay.setContentsMargins(24, 18, 24, 18)
        cfg_lay.setSpacing(20)
        cfg_lay.setAlignment(Qt.AlignRight)

        for lbl_text, attr in [("עד", "end_edit"), ("מ", "start_edit")]:
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
                f"border:2px solid {P.INPUT_BORDER};border-radius:8px;"
                f"padding:4px 12px;font-size:14px;font-weight:700;}}"
                f"QDateEdit:focus{{border:3px solid {P.INPUT_FOCUS};}}"
                f"QDateEdit::drop-down{{border:none;}}"
            )
            setattr(self, attr, w)
            col.addWidget(lbl)
            col.addWidget(w)
            cfg_lay.addLayout(col)

        # Run button
        self._run_btn = QPushButton("▶  הרץ")
        self._run_btn.setFixedHeight(48)
        self._run_btn.setFixedWidth(130)
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.setStyleSheet(
            f"QPushButton{{background:{P.INDIGO};color:#fff;"
            f"border:3px solid {P.INDIGO};border-radius:10px;"
            f"font-size:15px;font-weight:800;}}"
            f"QPushButton:hover{{background:{P.INDIGO_D};}}"
            f"QPushButton:disabled{{background:{P.TXT3};border-color:{P.TXT3};}}"
        )
        self._run_btn.clicked.connect(self._on_run)
        cfg_lay.insertWidget(0, self._run_btn)  # left-most

        root.addWidget(cfg_card)

        # Status
        self._status_lbl = QLabel("ממתין…")
        self._status_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-size:13px; font-weight:700; border:none;"
        )
        self._status_lbl.setAlignment(Qt.AlignRight)
        root.addWidget(self._status_lbl)

        # Log
        log_card = _card()
        log_v = QVBoxLayout(log_card)
        log_v.setContentsMargins(16, 12, 16, 12)
        log_v.setSpacing(6)

        log_hdr = QLabel("יומן")
        log_hdr.setStyleSheet(
            f"color:{P.CYAN}; font-size:11px; font-weight:800; "
            f"letter-spacing:2px; border:none;"
        )
        self._log_edit = QPlainTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setStyleSheet(
            f"background:{P.BG}; color:{P.TXT2}; border:none; "
            f"font-size:11px; font-family:Consolas,monospace;"
        )

        log_v.addWidget(log_hdr)
        log_v.addWidget(self._log_edit)
        root.addWidget(log_card, 1)

    # ── Public API ────────────────────────────────────────────────────────────

    def append_log(self, msg: str):
        self._log_edit.appendPlainText(msg)

    def set_status(self, msg: str):
        self._status_lbl.setText(msg)

    # ── Run logic ─────────────────────────────────────────────────────────────

    def _on_run(self):
        if self._running:
            return
        start = _from_qdate(self.start_edit.date())
        end   = _from_qdate(self.end_edit.date())
        if start > end:
            self.set_status("שגיאה: תאריך התחלה אחרי תאריך סיום")
            return

        self._running = True
        self._run_btn.setEnabled(False)
        self.set_status(f"מריץ… {start} → {end}")
        self._log_edit.clear()

        asyncio.ensure_future(self._run_pipeline(start, end))

    async def _run_pipeline(self, start: date, end: date):
        try:
            import os, sys
            scripts = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if scripts not in sys.path:
                sys.path.insert(0, scripts)

            from datetime import timedelta
            from shared.config import DB_DSN
            from shared.telegram_client import build_client
            from telegram_daily_discourse_pro.db import get_conn, ensure_schema, upsert_features
            from telegram_daily_discourse_pro.main import process_day  # noqa: import lazily

            conn = get_conn()
            ensure_schema(conn)

            client = build_client()
            await client.start()
            self.append_log("מחובר לטלגרם")

            current = start
            while current <= end:
                self.set_status(f"מעבד {current}…")
                self.append_log(f"--- {current} ---")
                try:
                    result = await process_day(client, conn, current)
                    upsert_features(conn, result)
                    self.append_log(f"  הושלם: {current}")
                except Exception as day_exc:
                    self.append_log(f"  שגיאה ב-{current}: {day_exc}")
                current += timedelta(days=1)

            conn.close()
            await client.disconnect()
            self.set_status("הושלם!")
            self.append_log("=== הסתיים ===")

        except ImportError as ie:
            self.append_log(f"שגיאת ייבוא: {ie}")
            self.set_status("שגיאה — ראה יומן")
        except Exception as exc:
            self.append_log(f"שגיאה: {exc}")
            self.set_status("שגיאה — ראה יומן")
        finally:
            self._running = False
            self._run_btn.setEnabled(True)
