"""
Graphs page — embedded Plotly dashboard inside QWebEngineView.

Layout:
  Left panel  → two toggle groups: Attacks (on by default) + Discourse (off)
  Right panel → single QWebEngineView with the full-screen chart

Trace names in ATTACK_SERIES / DISCOURSE_SERIES must match timeline.py exactly.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import palette as P
from . import styles as S

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TIMELINE_SCRIPT = os.path.join(SCRIPTS_DIR, "analysis", "timeline.py")
OUTPUT_HTML = os.path.join(
    os.path.dirname(SCRIPTS_DIR), "data", "analysis", "timeline.html"
)

# ── Trace definitions ─────────────────────────────────────────────────────────
# Each entry: (Hebrew label, exact trace name from timeline.py, default_visible, color)

ATTACK_SERIES = [
    ("⚡ מספר תקיפות",         "Attack Count",          True,  P.INDIGO),
    ("📈 ממוצע נע ל-7 ימים",   "7-Day Rolling Mean",    True,  "#a5b4fc"),
    ("🔥 אירועי שריפה",        "Fire Events",           True,  P.RED),
    ("✅ פגיעה מאושרת",        "Hit Confirmed",         True,  P.GREEN),
    ("🛑 השבתות",              "Shutdowns",             True,  "#fb923c"),
    ("🛡 הגנה אווירית פעילה",  "Air Defense Active",    True,  P.AMBER),
    ("🔀 תקיפות משולבות",      "Combined Strikes",      False, "#a78bfa"),
]

DISCOURSE_SERIES = [
    ("🚁 אזכורי רחפנים",          "Drone Mentions (pre)",           False, "#38bdf8"),
    ("🛡 הגנה אווירית (מוקדם)",   "Air Defense (pre)",              False, "#8b5cf6"),
    ("✈ סגירת שדות תעופה",       "Airport Closures (pre)",         False, "#fbbf24"),
    ("❓ אי-ודאות",                "Uncertainty (pre)",              False, "#94a3b8"),
    ("⚡ תקיפות אנרגיה",          "Energy Attack Messages",         False, "#34d399"),
    ("✅ אישורי תקיפות אנרגיה",   "Energy Confirmations",           False, "#6ee7b7"),
    ("🏭 בית זיקוק/מחסן",        "Energy Refinery/Depot",          False, "#10b981"),
    ("📰 הודעות מלחמה",           "War Messages (total)",           False, "#94a3b8"),
    ("🎯 תקיפות אוקראיניות",      "Ukrainian Strikes on Russia",    False, "#cbd5e1"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _panel_btn(text: str, color: str, hover: str) -> QPushButton:
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


def _group_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color:{P.TXT3}; font-family:{P.FONT_STACK}; font-size:10px; font-weight:700; "
        f"letter-spacing:1px; border:none; "
        f"padding-top:6px;"
    )
    return lbl


def _hline() -> QWidget:
    w = QWidget()
    w.setFixedHeight(1)
    w.setStyleSheet(S.divider_qss())
    return w


def _vline() -> QWidget:
    """Hairline vertical divider — replaces a solid panel border so the
    control column reads as floating over the page, not boxed into a bar."""
    w = QWidget()
    w.setFixedWidth(1)
    w.setStyleSheet(S.divider_qss())
    return w


class GraphsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._checkboxes: list[tuple[QCheckBox, str]] = []  # (cb, exact_trace_name)
        self._regen_proc = None
        self._timer = None
        self._setup_ui()

    # ── UI setup ──────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setStyleSheet(S.window_bg_qss())
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_panel())
        root.addWidget(_vline())
        root.addWidget(self._build_webview_area(), 1)

    def _build_panel(self) -> QFrame:
        # Transparent on purpose — the control column floats over the shared
        # page gradient instead of sitting inside its own solid bar; a single
        # hairline (_vline, added by the caller) is enough separation.
        panel = QFrame()
        panel.setFixedWidth(230)
        panel.setStyleSheet("QFrame { background:transparent; border:none; }")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 18, 16, 16)
        lay.setSpacing(5)

        # Header
        hdr = QLabel("📊  שכבות בגרף")
        hdr.setStyleSheet(
            f"color:{P.TXT}; font-family:{P.FONT_STACK}; font-size:14px; font-weight:700; "
            f"border:none;"
        )
        lay.addWidget(hdr)

        sub = QLabel("בחרו אילו נתונים להציג")
        sub.setStyleSheet(
            f"color:{P.TXT3}; font-family:{P.FONT_STACK}; font-size:11px; border:none;"
        )
        lay.addWidget(sub)

        lay.addSpacing(4)
        lay.addWidget(_hline())
        lay.addSpacing(4)

        # ── Group: Attacks ───────────────────────────────────────────────────
        lay.addWidget(_group_label("תקיפות"))

        for label, trace_name, default_on, color in ATTACK_SERIES:
            cb = self._make_checkbox(label, trace_name, default_on, color)
            lay.addWidget(cb)

        lay.addSpacing(4)
        lay.addWidget(_hline())
        lay.addSpacing(4)

        # ── Group: Discourse ─────────────────────────────────────────────────
        lay.addWidget(_group_label("שיח (ציר ימני)"))

        for label, trace_name, default_on, color in DISCOURSE_SERIES:
            cb = self._make_checkbox(label, trace_name, default_on, color)
            lay.addWidget(cb)

        lay.addStretch()
        lay.addWidget(_hline())
        lay.addSpacing(6)

        # Preset buttons
        presets = QHBoxLayout()
        presets.setSpacing(10)
        all_atk_btn  = _panel_btn("תקיפות",  P.INDIGO,  P.INDIGO_D)
        none_btn     = _panel_btn("נקה",    P.TXT3,    P.TXT2)
        all_atk_btn.clicked.connect(self._preset_attacks_only)
        none_btn.clicked.connect(self._preset_none)
        presets.addWidget(all_atk_btn)
        presets.addWidget(none_btn)
        lay.addLayout(presets)

        lay.addSpacing(6)

        # Regenerate button
        regen_btn = _panel_btn("🔄  עדכון נתונים", P.VIOLET, "#9333ea")
        regen_btn.setFixedHeight(38)
        regen_btn.clicked.connect(self._regen_timeline)
        lay.addWidget(regen_btn)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        self._spinner = S.LoadingSpinner(size=13)
        status_row.addWidget(self._spinner)
        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:11px; border:none; padding-top:4px;"
        )
        status_row.addWidget(self._status_lbl, 1)
        lay.addLayout(status_row)

        return panel

    def _make_checkbox(self, label: str, trace_name: str,
                       default_on: bool, color: str) -> QCheckBox:
        cb = QCheckBox(label)
        cb.setChecked(default_on)
        cb.setCursor(Qt.PointingHandCursor)
        cb.setStyleSheet(
            f"QCheckBox {{ color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:12px; "
            f"border:none; padding:3px 0; }}"
            f"QCheckBox::indicator {{ width:14px; height:14px; border-radius:4px; "
            f"border:1.5px solid {color}; }}"
            f"QCheckBox::indicator:checked   {{ background:{color}; }}"
            f"QCheckBox::indicator:unchecked {{ background:transparent; }}"
        )
        cb.toggled.connect(lambda checked, n=trace_name: self._toggle_trace(n, checked))
        self._checkboxes.append((cb, trace_name))
        return cb

    def _build_webview_area(self) -> QWidget:
        area = QWidget()
        area.setStyleSheet(S.window_bg_qss())
        lay = QVBoxLayout(area)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Topbar
        topbar = QWidget()
        topbar.setFixedHeight(46)
        topbar.setStyleSheet(f"background:{P.BG};")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(16, 6, 16, 6)
        tb.setSpacing(8)
        hdr_icon = QLabel("📈")
        hdr_icon.setStyleSheet("font-size:18px; border:none; background:transparent;")
        hdr_lbl = QLabel("גרפים וניתוח — ציר זמן אינטראקטיבי")
        hdr_lbl.setStyleSheet(
            f"color:{P.TXT}; font-family:{P.FONT_STACK}; font-size:14px; font-weight:700; "
            f"border:none; background:transparent;"
        )
        tb.addWidget(hdr_icon)
        tb.addWidget(hdr_lbl)
        tb.addStretch()
        lay.addWidget(topbar)
        lay.addWidget(_hline())

        self._web = QWebEngineView()
        self._web.setStyleSheet(f"background:{P.BG};")
        lay.addWidget(self._web, 1)

        self._load_html()
        return area

    # ── HTML loading ──────────────────────────────────────────────────────────

    def _load_html(self):
        if os.path.exists(OUTPUT_HTML):
            self._web.load(QUrl.fromLocalFile(OUTPUT_HTML))
            self._set_status("הגרף נטען", P.GREEN)
        else:
            self._web.setHtml(self._placeholder_html())
            self._set_status("לחצו על '🔄 עדכון נתונים' כדי ליצור את הגרף", P.TXT2)

    def _placeholder_html(self) -> str:
        return (
            f"<html><body style='{S.window_bg_css()}color:{P.TXT2};"
            "font-family:Inter,\"Segoe UI\",sans-serif;"
            "display:flex;align-items:center;justify-content:center;"
            "height:100vh;margin:0;'>"
            "<div style='text-align:center;'>"
            "<div style='font-size:52px;margin-bottom:16px;opacity:0.8;'>📊</div>"
            f"<div style='font-size:20px;font-weight:700;color:{P.TXT};margin-bottom:8px;'>"
            "הגרף לא נמצא</div>"
            "<div style='font-size:13px;'>"
            "לחצו על \"🔄 עדכון נתונים\" בתפריט הצד כדי ליצור את הגרף"
            "</div></div></body></html>"
        )

    # ── Trace toggling ────────────────────────────────────────────────────────

    def _toggle_trace(self, trace_name: str, visible: bool):
        """Show or hide a Plotly trace by its exact name."""
        vis_val = "true" if visible else "'legendonly'"
        js = f"""
        (function() {{
            var gd = document.getElementById('chart');
            if (!gd || !gd.data) return;
            var indices = [];
            for (var i = 0; i < gd.data.length; i++) {{
                if (gd.data[i].name === {json.dumps(trace_name)}) {{
                    indices.push(i);
                }}
            }}
            if (indices.length > 0) {{
                Plotly.restyle(gd, {{visible: {vis_val}}}, indices);
            }}
        }})();
        """
        self._web.page().runJavaScript(js)

    def _set_traces_visible(self, names: set[str]):
        """Show traces whose names are in `names`, hide all others."""
        for cb, trace_name in self._checkboxes:
            on = trace_name in names
            cb.blockSignals(True)
            cb.setChecked(on)
            cb.blockSignals(False)
            self._toggle_trace(trace_name, on)

    def _preset_attacks_only(self):
        attack_names = {t for _, t, _, _ in ATTACK_SERIES}
        self._set_traces_visible(attack_names)

    def _preset_none(self):
        self._set_traces_visible(set())

    # ── Regenerate ────────────────────────────────────────────────────────────

    def _regen_timeline(self):
        if not os.path.exists(TIMELINE_SCRIPT):
            self._set_status("הסקריפט לא נמצא", P.RED)
            return

        if self._regen_proc and self._regen_proc.poll() is None:
            self._set_status("כבר מתבצע עדכון...", P.AMBER)
            return

        self._spinner.start()
        self._set_status("יוצרים גרף…", P.TXT2)

        try:
            self._regen_proc = subprocess.Popen(
                [sys.executable, TIMELINE_SCRIPT],
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
                self._set_status("הגרף עודכן", P.GREEN)
            else:
                self._set_status(f"שגיאה (קוד {rc})", P.RED)

    def _set_status(self, msg: str, color: str = None):
        self._status_lbl.setText(msg)
        if color:
            self._status_lbl.setStyleSheet(
                f"color:{color}; font-family:{P.FONT_STACK}; font-size:11px; border:none; "
                f"padding-top:4px;"
            )

    def reset_view(self):
        """Called on re-entry: only clears a stuck spinner, never the loaded graph."""
        busy = self._regen_proc is not None and self._regen_proc.poll() is None
        if not busy:
            self._spinner.stop()
