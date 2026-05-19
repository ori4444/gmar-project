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

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TIMELINE_SCRIPT = os.path.join(SCRIPTS_DIR, "analysis", "timeline.py")
OUTPUT_HTML = os.path.join(
    os.path.dirname(SCRIPTS_DIR), "data", "analysis", "timeline.html"
)

# ── Trace definitions ─────────────────────────────────────────────────────────
# Each entry: (Hebrew label, exact trace name from timeline.py, default_visible, color)

ATTACK_SERIES = [
    ("⚡ Attack Count",       "Attack Count",          True,  P.INDIGO),
    ("📈 7-Day Rolling Mean", "7-Day Rolling Mean",    True,  "#a5b4fc"),
    ("🔥 Fire Events",        "Fire Events",           True,  P.RED),
    ("✅ Hit Confirmed",       "Hit Confirmed",         True,  P.GREEN),
    ("🛑 Shutdowns",           "Shutdowns",             True,  "#f97316"),
    ("🛡 Air Defense Active",  "Air Defense Active",    True,  P.AMBER),
    ("🔀 Combined Strikes",   "Combined Strikes",      False, "#7c3aed"),
]

DISCOURSE_SERIES = [
    ("🚁 Drone Mentions",      "Drone Mentions (pre)",           False, "#0ea5e9"),
    ("🛡 Air Defense (pre)",   "Air Defense (pre)",              False, "#8b5cf6"),
    ("✈ Airport Closures",     "Airport Closures (pre)",         False, "#fbbf24"),
    ("❓ Uncertainty",          "Uncertainty (pre)",              False, "#94a3b8"),
    ("⚡ Energy Attacks",      "Energy Attack Messages",         False, "#059669"),
    ("✅ Energy Confirmations", "Energy Confirmations",           False, "#6ee7b7"),
    ("🏭 Refinery/Depot",      "Energy Refinery/Depot",          False, "#065f46"),
    ("📰 War Messages",        "War Messages (total)",           False, "#64748b"),
    ("🎯 Ukrainian Strikes",   "Ukrainian Strikes on Russia",    False, "#334155"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _panel_btn(text: str, color: str, hover: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(32)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton{{background:transparent;color:{color};"
        f"border:2px solid {color};border-radius:7px;"
        f"font-size:12px;font-weight:700;}}"
        f"QPushButton:hover{{background:{color};color:#fff;}}"
    )
    return btn


def _group_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color:{P.TXT2}; font-size:10px; font-weight:900; "
        f"letter-spacing:1px; border:none; "
        f"padding-top:6px;"
    )
    return lbl


def _hline() -> QWidget:
    w = QWidget()
    w.setFixedHeight(1)
    w.setStyleSheet(f"background:{P.DIVIDER}; border-radius:1px;")
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
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_panel())
        root.addWidget(self._build_webview_area(), 1)

    def _build_panel(self) -> QFrame:
        panel = QFrame()
        panel.setFixedWidth(230)
        # Soft right-edge divider = panel boundary (same as sidebar pattern)
        panel.setStyleSheet(
            f"QFrame {{ background:{P.BG}; "
            f"border-right: 1px solid {P.DIVIDER}; "
            f"border-top: none; border-left: none; border-bottom: none; }}"
        )
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 16, 14, 16)
        lay.setSpacing(5)

        # Header
        hdr = QLabel("📊  Graph Layers")
        hdr.setStyleSheet(
            f"color:{P.TXT}; font-size:14px; font-weight:900; "
            f"border:none;"
        )
        lay.addWidget(hdr)

        sub = QLabel("Select which series to display")
        sub.setStyleSheet(f"color:{P.TXT3}; font-size:11px; border:none;")
        lay.addWidget(sub)

        lay.addSpacing(4)
        lay.addWidget(_hline())
        lay.addSpacing(4)

        # ── Group: Attacks ───────────────────────────────────────────────────
        lay.addWidget(_group_label("ATTACKS"))

        for label, trace_name, default_on, color in ATTACK_SERIES:
            cb = self._make_checkbox(label, trace_name, default_on, color)
            lay.addWidget(cb)

        lay.addSpacing(4)
        lay.addWidget(_hline())
        lay.addSpacing(4)

        # ── Group: Discourse ─────────────────────────────────────────────────
        lay.addWidget(_group_label("DISCOURSE (right axis)"))

        for label, trace_name, default_on, color in DISCOURSE_SERIES:
            cb = self._make_checkbox(label, trace_name, default_on, color)
            lay.addWidget(cb)

        lay.addStretch()
        lay.addWidget(_hline())
        lay.addSpacing(6)

        # Preset buttons
        presets = QHBoxLayout()
        presets.setSpacing(6)
        all_atk_btn  = _panel_btn("Attacks",  P.INDIGO,  P.INDIGO_D)
        none_btn     = _panel_btn("Clear",    P.TXT3,    P.TXT2)
        all_atk_btn.clicked.connect(self._preset_attacks_only)
        none_btn.clicked.connect(self._preset_none)
        presets.addWidget(all_atk_btn)
        presets.addWidget(none_btn)
        lay.addLayout(presets)

        lay.addSpacing(6)

        # Regenerate button
        regen_btn = _panel_btn("🔄  Update Data", P.VIOLET, "#9333ea")
        regen_btn.setFixedHeight(38)
        regen_btn.clicked.connect(self._regen_timeline)
        lay.addWidget(regen_btn)

        self._status_lbl = QLabel("")
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-size:11px; border:none; padding-top:4px;"
        )
        lay.addWidget(self._status_lbl)

        return panel

    def _make_checkbox(self, label: str, trace_name: str,
                       default_on: bool, color: str) -> QCheckBox:
        cb = QCheckBox(label)
        cb.setChecked(default_on)
        cb.setStyleSheet(
            f"QCheckBox {{ color:{P.TXT}; font-size:12px; border:none; }}"
            f"QCheckBox::indicator {{ width:15px; height:15px; border-radius:3px; "
            f"border:2px solid {color}; }}"
            f"QCheckBox::indicator:checked   {{ background:{color}; }}"
            f"QCheckBox::indicator:unchecked {{ background:transparent; }}"
        )
        cb.toggled.connect(lambda checked, n=trace_name: self._toggle_trace(n, checked))
        self._checkboxes.append((cb, trace_name))
        return cb

    def _build_webview_area(self) -> QWidget:
        area = QWidget()
        area.setStyleSheet(f"background:{P.BG};")
        lay = QVBoxLayout(area)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Topbar
        topbar = QWidget()
        topbar.setFixedHeight(46)
        topbar.setStyleSheet(f"background:{P.BG};")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(16, 6, 16, 6)
        hdr_lbl = QLabel("Graphs & Analysis — Interactive Timeline")
        hdr_lbl.setStyleSheet(
            f"color:{P.TXT}; font-size:15px; font-weight:800; border:none;"
        )
        hdr_icon = QLabel("📈")
        hdr_icon.setStyleSheet("font-size:20px; border:none;")
        tb.addStretch()
        tb.addWidget(hdr_lbl)
        tb.addWidget(hdr_icon)
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
            self._set_status("Graph loaded", P.GREEN)
        else:
            self._web.setHtml(self._placeholder_html())
            self._set_status("Click '🔄 Update Data' to generate the graph", P.TXT2)

    def _placeholder_html(self) -> str:
        return (
            f"<html><body style='background:{P.BG};color:{P.TXT2};"
            "font-family:Segoe UI,sans-serif;"
            "display:flex;align-items:center;justify-content:center;"
            "height:100vh;margin:0;direction:rtl;'>"
            "<div style='text-align:center;'>"
            "<div style='font-size:60px;margin-bottom:16px;'>📊</div>"
            f"<div style='font-size:22px;font-weight:900;color:{P.TXT};margin-bottom:8px;'>"
            "Graph not found</div>"
            "<div style='font-size:14px;'>"
            "Click \"🔄 Update Data\" in the sidebar to generate the graph"
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
            self._set_status("Script not found", P.RED)
            return

        if self._regen_proc and self._regen_proc.poll() is None:
            self._set_status("Already updating...", P.AMBER)
            return

        self._set_status("⏳ Generating graph…", P.TXT2)

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
            self._set_status(f"Error: {exc}", P.RED)

    def _check_regen_done(self):
        if self._regen_proc and self._regen_proc.poll() is not None:
            self._timer.stop()
            rc = self._regen_proc.returncode
            if rc == 0:
                self._load_html()
                self._set_status("Graph updated", P.GREEN)
            else:
                self._set_status(f"Error (code {rc})", P.RED)

    def _set_status(self, msg: str, color: str = None):
        self._status_lbl.setText(msg)
        if color:
            self._status_lbl.setStyleSheet(
                f"color:{color}; font-size:11px; border:none; "
                f"padding-top:4px;"
            )
