"""
Map page — every attack plotted on a Russia/Ukraine map, embedded Plotly
scattergeo inside a QWebEngineView (same pattern as graphs.py).

Date filtering (inputs at the top of the page) and click-to-details both run
client-side in the generated HTML — see analysis/attack_map.py — so no
Python round-trip is needed once the page is loaded.
"""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import palette as P
from . import styles as S

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAP_SCRIPT = os.path.join(SCRIPTS_DIR, "analysis", "attack_map.py")
OUTPUT_HTML = os.path.join(
    os.path.dirname(SCRIPTS_DIR), "data", "analysis", "attack_map.html"
)


def _panel_btn(text: str, color: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(32)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton{{background:transparent;color:{color};"
        f"border:1px solid {color};border-radius:{P.RADIUS_MD}px;"
        f"font-family:{P.FONT_STACK};font-size:12px;font-weight:700;}}"
        f"QPushButton:hover{{background:{color};color:#0a0b0d;}}"
    )
    S.bind_floating_button(btn, idle=(10, 55, 2), hover=(20, 100, 6), pressed=(4, 30, 1),
                            flash_color=color)
    return btn


def _hline() -> QWidget:
    w = QWidget()
    w.setFixedHeight(1)
    w.setStyleSheet(S.divider_qss())
    return w


class MapPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._regen_proc = None
        self._timer = None
        self._setup_ui()

    # ── UI setup ──────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setStyleSheet(S.window_bg_qss())
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Topbar
        topbar = QWidget()
        topbar.setFixedHeight(46)
        topbar.setStyleSheet(f"background:{P.BG};")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(16, 6, 16, 6)
        tb.setSpacing(8)
        hdr_icon = QLabel("🗺")
        hdr_icon.setStyleSheet("font-size:18px; border:none; background:transparent;")
        hdr_lbl = QLabel("מפת תקיפות")
        hdr_lbl.setStyleSheet(
            f"color:{P.TXT}; font-family:{P.FONT_STACK}; font-size:14px; font-weight:700; "
            f"border:none; background:transparent;"
        )
        tb.addWidget(hdr_icon)
        tb.addWidget(hdr_lbl)
        tb.addStretch()

        self._spinner = S.LoadingSpinner(size=13)
        tb.addWidget(self._spinner)
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:11px; border:none;"
        )
        tb.addWidget(self._status_lbl)

        regen_btn = _panel_btn("🔄  עדכון נתונים", P.VIOLET)
        regen_btn.clicked.connect(self._regen_map)
        tb.addWidget(regen_btn)

        root.addWidget(topbar)
        root.addWidget(_hline())

        self._web = QWebEngineView()
        self._web.setStyleSheet(f"background:{P.BG};")
        root.addWidget(self._web, 1)

        self._load_html()

    # ── HTML loading ──────────────────────────────────────────────────────────

    def _load_html(self):
        if os.path.exists(OUTPUT_HTML):
            self._web.load(QUrl.fromLocalFile(OUTPUT_HTML))
            self._set_status("המפה נטענה", P.GREEN)
        else:
            self._web.setHtml(self._placeholder_html())
            self._set_status("לחצו על '🔄 עדכון נתונים' כדי ליצור את המפה", P.TXT2)

    def _placeholder_html(self) -> str:
        return (
            f"<html><body style='{S.window_bg_css()}color:{P.TXT2};"
            "font-family:Inter,\"Segoe UI\",sans-serif;"
            "display:flex;align-items:center;justify-content:center;"
            "height:100vh;margin:0;'>"
            "<div style='text-align:center;'>"
            "<div style='font-size:52px;margin-bottom:16px;opacity:0.8;'>🗺</div>"
            f"<div style='font-size:20px;font-weight:700;color:{P.TXT};margin-bottom:8px;'>"
            "המפה לא נמצאה</div>"
            "<div style='font-size:13px;'>"
            "לחצו על \"🔄 עדכון נתונים\" כדי ליצור את המפה"
            "</div></div></body></html>"
        )

    # ── Regenerate ────────────────────────────────────────────────────────────

    def _regen_map(self):
        if not os.path.exists(MAP_SCRIPT):
            self._set_status("הסקריפט לא נמצא", P.RED)
            return

        if self._regen_proc and self._regen_proc.poll() is None:
            self._set_status("כבר מתבצע עדכון...", P.AMBER)
            return

        self._spinner.start()
        self._set_status("יוצרים מפה…", P.TXT2)

        try:
            self._regen_proc = subprocess.Popen(
                [sys.executable, MAP_SCRIPT],
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )

            if self._timer:
                self._timer.stop()
            self._timer = QTimer(self)
            self._timer.setInterval(1000)
            self._timer.timeout.connect(self._check_regen_done)
            self._timer.start()

        except Exception as exc:
            self._set_status(f"שגיאה: {exc}", P.RED)

    def _check_regen_done(self):
        if self._regen_proc and self._regen_proc.poll() is not None:
            self._timer.stop()
            self._spinner.stop()
            rc = self._regen_proc.returncode
            if rc == 0:
                self._load_html()
                self._set_status("המפה עודכנה", P.GREEN)
            else:
                self._set_status(f"שגיאה (קוד {rc})", P.RED)

    def _set_status(self, msg: str, color: str | None = None):
        self._status_lbl.setText(msg)
        if color:
            self._status_lbl.setStyleSheet(
                f"color:{color}; font-family:{P.FONT_STACK}; font-size:11px; border:none;"
            )

    def reset_view(self):
        """Called on re-entry: only clears a stuck spinner, never the loaded map."""
        busy = self._regen_proc is not None and self._regen_proc.poll() is None
        if not busy:
            self._spinner.stop()
