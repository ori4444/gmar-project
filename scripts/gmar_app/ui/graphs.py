"""
Graphs / analysis page — open the Plotly timeline in the browser.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import palette as P

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TIMELINE_SCRIPT = os.path.join(SCRIPTS_DIR, "analysis", "timeline.py")
OUTPUT_HTML = os.path.join(
    os.path.dirname(SCRIPTS_DIR), "data", "analysis", "timeline.html"
)


def _card(parent=None) -> QFrame:
    f = QFrame(parent)
    f.setStyleSheet(
        f"QFrame {{ background:{P.CARD_BG}; border:3px solid {P.CARD_BORDER};"
        f"border-radius:14px; }}"
    )
    return f


class GraphsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # Header
        hdr = QLabel("גרפים וניתוח")
        hdr.setStyleSheet(
            f"color:{P.TXT}; font-size:22px; font-weight:900; border:none;"
        )
        hdr.setAlignment(Qt.AlignRight)
        root.addWidget(hdr)

        sub = QLabel("הצג ציר זמן אינטראקטיבי של תקיפות ושיח")
        sub.setStyleSheet(f"color:{P.TXT2}; font-size:14px; border:none;")
        sub.setAlignment(Qt.AlignRight)
        root.addWidget(sub)

        # Card with big action button
        card = _card()
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(32, 28, 32, 28)
        card_lay.setSpacing(20)
        card_lay.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel("📊")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("font-size:64px; border:none;")
        card_lay.addWidget(icon_lbl)

        desc = QLabel(
            "פותח את ציר הזמן בדפדפן שלך.\n"
            "הגרף מציג תקיפות, פגיעות, שריפות ואותות שיח."
        )
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet(
            f"color:{P.TXT2}; font-size:14px; line-height:1.8; border:none;"
        )
        card_lay.addWidget(desc)

        open_btn = QPushButton("📈  פתח ציר זמן")
        open_btn.setFixedHeight(52)
        open_btn.setFixedWidth(260)
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.setStyleSheet(
            f"QPushButton{{background:{P.VIOLET};color:#fff;"
            f"border:3px solid {P.VIOLET};border-radius:12px;"
            f"font-size:16px;font-weight:800;}}"
            f"QPushButton:hover{{background:#9333ea;}}"
        )
        open_btn.clicked.connect(self._open_timeline)
        card_lay.addWidget(open_btn, alignment=Qt.AlignCenter)

        # Regen button
        regen_btn = QPushButton("🔄  חשב מחדש")
        regen_btn.setFixedHeight(44)
        regen_btn.setFixedWidth(200)
        regen_btn.setCursor(Qt.PointingHandCursor)
        regen_btn.setStyleSheet(
            f"QPushButton{{background:{P.CARD_BG};color:{P.TXT2};"
            f"border:2px solid {P.CARD_BORDER};border-radius:10px;"
            f"font-size:14px;font-weight:700;}}"
            f"QPushButton:hover{{border-color:{P.VIOLET};color:{P.VIOLET};}}"
        )
        regen_btn.clicked.connect(self._regen_timeline)
        card_lay.addWidget(regen_btn, alignment=Qt.AlignCenter)

        self._status_lbl = QLabel("")
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-size:12px; border:none;"
        )
        card_lay.addWidget(self._status_lbl)

        root.addWidget(card)
        root.addStretch()

    def _open_timeline(self):
        if os.path.exists(OUTPUT_HTML):
            import webbrowser
            webbrowser.open(f"file:///{OUTPUT_HTML.replace(os.sep, '/')}")
            self._status_lbl.setText("נפתח בדפדפן")
        else:
            self._status_lbl.setText("הקובץ לא נמצא — לחץ 'חשב מחדש' תחילה")

    def _regen_timeline(self):
        if not os.path.exists(TIMELINE_SCRIPT):
            self._status_lbl.setText(f"לא נמצא: {TIMELINE_SCRIPT}")
            return
        self._status_lbl.setText("מחשב מחדש…")
        try:
            python = sys.executable
            subprocess.Popen(
                [python, TIMELINE_SCRIPT],
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            self._status_lbl.setText("הסקריפט מורץ ברקע — פתח בדפדפן בעוד כמה שניות")
        except Exception as exc:
            self._status_lbl.setText(f"שגיאה: {exc}")
