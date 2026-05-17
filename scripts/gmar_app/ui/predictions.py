"""
Predictions page — two modes, both explicitly tied to the analysis pipeline.

Pipeline visible to the user:
  Analysis (Multi-Target page) → Training (Train button here) → Prediction

Forecast mode:   multi-target outcome grid for a chosen horizon
Next Attack mode: KNN historical analogy (when / where / what)
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

import psycopg2
from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCalendarWidget, QDialog, QDialogButtonBox,
    QGridLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from . import palette as P

_SCRIPTS = str(Path(__file__).resolve().parents[2])
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from shared.config import DB_DSN
from analysis.forecast import (
    FORECAST_HORIZONS,
    get_forecast_meta, get_analysis_mtime, load_analysis_config,
    train_forecast, predict_forecast, predict_next_attack,
)
from analysis.regime_models import RegimeModelBank, BANK_PATH
from analysis.window_analysis import (
    load_attacks, load_discourse, aggregate_attacks, build_full_daily,
)
from analysis.attack_forecast import (
    train_bank, predict_intel, get_bank_meta, get_bank_mtime,
    BANK_PATH as FORECAST_BANK_PATH, DIMENSIONS, TARGET_LABELS,
    build_model_insights,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _lbl(text: str, size: int = 12, bold: bool = False,
         color: str | None = None) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color or P.TXT2}; font-size:{size}px; "
        f"font-weight:{'700' if bold else '500'}; border:none;"
    )
    return w


def _hline() -> QWidget:
    w = QWidget()
    w.setFixedHeight(1)
    w.setStyleSheet(f"background:{P.DIVIDER};")
    return w


def _pill(text: str, active: bool) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(32)
    btn.setCheckable(True)
    btn.setChecked(active)
    btn.setCursor(Qt.PointingHandCursor)
    _style_pill(btn)
    return btn


def _style_pill(btn: QPushButton):
    if btn.isChecked():
        btn.setStyleSheet(f"""
            QPushButton {{
                background:{P.INDIGO}; color:#fff;
                border:none; border-radius:8px;
                font-size:12px; font-weight:800; padding:4px 14px;
            }}
        """)
    else:
        btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{P.TXT2};
                border:1px solid {P.DIVIDER}; border-radius:8px;
                font-size:12px; font-weight:700; padding:4px 14px;
            }}
            QPushButton:hover {{ border-color:{P.INDIGO}; color:{P.INDIGO}; }}
        """)


def _item(text: str, align: Qt.AlignmentFlag = Qt.AlignCenter) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    it.setTextAlignment(align)
    return it


# ─────────────────────────────────────────────────────────────────────────────
#  Pipeline status widget
# ─────────────────────────────────────────────────────────────────────────────

class PipelineStatus(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"background:{P.INPUT_BG}; border:1px solid {P.DIVIDER}; border-radius:10px;"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(24)

        self._analysis_lbl = _lbl("", 11)
        self._arrow1       = _lbl("→", 14, bold=True, color=P.TXT3)
        self._training_lbl = _lbl("", 11)
        self._arrow2       = _lbl("→", 14, bold=True, color=P.TXT3)
        self._ready_lbl    = _lbl("", 11)

        lay.addWidget(_lbl("PIPELINE", 10, bold=True, color=P.TXT3))
        lay.addWidget(self._analysis_lbl)
        lay.addWidget(self._arrow1)
        lay.addWidget(self._training_lbl)
        lay.addWidget(self._arrow2)
        lay.addWidget(self._ready_lbl)
        lay.addStretch()

        self.refresh()

    def refresh(self):
        analysis_t = get_analysis_mtime()
        meta       = get_forecast_meta()

        if analysis_t:
            self._analysis_lbl.setText(f"Analysis  ●  {analysis_t}")
            self._analysis_lbl.setStyleSheet(
                f"color:{P.GREEN}; font-size:11px; font-weight:600; border:none;"
            )
        else:
            self._analysis_lbl.setText("Analysis  ○  not run")
            self._analysis_lbl.setStyleSheet(
                f"color:{P.TXT3}; font-size:11px; font-weight:600; border:none;"
            )

        if meta:
            trained_at = meta.get("trained_at", "?")
            outdated   = (
                analysis_t is not None
                and trained_at < analysis_t
            )
            color = P.AMBER if outdated else P.GREEN
            warn  = "  ⚠ analysis updated" if outdated else ""
            self._training_lbl.setText(f"Training  ●  {trained_at}{warn}")
            self._training_lbl.setStyleSheet(
                f"color:{color}; font-size:11px; font-weight:600; border:none;"
            )
            self._ready_lbl.setText("Ready to predict")
            self._ready_lbl.setStyleSheet(
                f"color:{P.GREEN}; font-size:11px; font-weight:600; border:none;"
            )
        else:
            self._training_lbl.setText("Training  ○  not trained")
            self._training_lbl.setStyleSheet(
                f"color:{P.TXT3}; font-size:11px; font-weight:600; border:none;"
            )
            self._ready_lbl.setText("Train required")
            self._ready_lbl.setStyleSheet(
                f"color:{P.RED}; font-size:11px; font-weight:600; border:none;"
            )


# ─────────────────────────────────────────────────────────────────────────────
#  Forecast outcome card
# ─────────────────────────────────────────────────────────────────────────────

_OUTCOME_LABELS = {
    "any_attack":       "Any Attack",
    "significant":      "Significant",
    "any_fire":         "Fire",
    "any_hit":          "Confirmed Hit",
    "any_shutdown":     "Shutdown",
    "deep_strike":      "Deep Strike (≥3)",
    "combined_strike":  "Combined Strike",
    "repeated_strike":  "Repeated Strike",
    "multi_attack":     "Heavy Bombardment (3+)",
    "any_explosion":    "Explosions",
    "very_deep":        "Very Deep Strike (≥4)",
    "multi_fire":       "Multi-Fire (2+)",
}


class ForecastCard(QWidget):
    def __init__(self, result: dict, parent=None):
        super().__init__(parent)
        self.setFixedSize(190, 160)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        typ = result.get("type", "binary")
        name = result.get("name", "?")
        label = _OUTCOME_LABELS.get(name, name.replace("_", " ").title())

        border_color = P.DIVIDER
        if typ == "binary":
            val = result.get("value", 0.0)
            if val >= 0.6:
                border_color = P.RED
            elif val >= 0.35:
                border_color = P.AMBER

        self.setStyleSheet(f"""
            ForecastCard {{
                background:{P.INPUT_BG}; border:1px solid {border_color};
                border-radius:10px;
            }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)

        # Name
        name_lbl = _lbl(label, 11, bold=True, color=P.TXT)
        name_lbl.setWordWrap(True)
        lay.addWidget(name_lbl)

        if "error" in result:
            lay.addWidget(_lbl(result["error"], 10, color=P.RED))
            return

        # Main value
        if typ == "binary":
            val  = result.get("value", 0.0)
            pct  = int(val * 100)
            c    = P.RED if val >= 0.6 else (P.AMBER if val >= 0.35 else P.GREEN)
            val_lbl = _lbl(f"{pct}%", 22, bold=True, color=c)
        else:
            val   = result.get("value", 0.0)
            c     = P.TXT
            val_lbl = _lbl(f"{val:.1f}", 22, bold=True, color=c)
        lay.addWidget(val_lbl)

        # Progress bar (binary only)
        if typ == "binary":
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(pct)
            bar.setTextVisible(False)
            bar.setFixedHeight(5)
            bar.setStyleSheet(f"""
                QProgressBar {{ background:{P.BG}; border:none; border-radius:2px; }}
                QProgressBar::chunk {{ background:{c}; border-radius:2px; }}
            """)
            lay.addWidget(bar)

        # CV score
        cv = result.get("cv_score", float("nan"))
        metric_label = "AUC" if typ == "binary" else "R²"
        cv_text = f"{metric_label} {cv:.3f}" if cv == cv else f"{metric_label} —"
        lay.addWidget(_lbl(cv_text, 10, color=P.TXT3))

        # Lookback info
        lb = result.get("lookback", "?")
        lay.addWidget(_lbl(f"lb={lb}d", 10, color=P.TXT3))

        lay.addStretch()


# ─────────────────────────────────────────────────────────────────────────────
#  Forecast tab
# ─────────────────────────────────────────────────────────────────────────────

class ForecastTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._horizon = FORECAST_HORIZONS[0]
        self._busy    = False
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # Horizon pills + predict button
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        ctrl.addWidget(_lbl("Horizon:", 12, bold=True, color=P.TXT))

        self._pills: list[QPushButton] = []
        horizon_labels = {1: "1d", 2: "2d", 3: "3d", 5: "5d",
                          7: "7d", 10: "10d", 14: "14d"}
        for h in FORECAST_HORIZONS:
            lbl = horizon_labels.get(h, f"{h}d")
            p = _pill(lbl, h == self._horizon)
            p.clicked.connect(lambda _, hh=h: self._select_horizon(hh))
            self._pills.append(p)
            ctrl.addWidget(p)

        ctrl.addStretch()

        self._predict_btn = QPushButton("▶  Predict")
        self._predict_btn.setFixedSize(120, 34)
        self._predict_btn.setCursor(Qt.PointingHandCursor)
        self._predict_btn.setStyleSheet(f"""
            QPushButton {{
                background:{P.INDIGO}; color:#fff;
                border:none; border-radius:8px;
                font-size:13px; font-weight:800;
            }}
            QPushButton:hover {{ background:{P.INDIGO_D}; }}
            QPushButton:disabled {{ background:{P.INPUT_BG}; color:{P.TXT3}; }}
        """)
        self._predict_btn.clicked.connect(self._on_predict)
        ctrl.addWidget(self._predict_btn)
        root.addLayout(ctrl)

        self._info_lbl = _lbl("Select horizon and click Predict.", 11, color=P.TXT3)
        root.addWidget(self._info_lbl)

        # Scrollable results grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        self._grid_widget = QWidget()
        self._grid        = QGridLayout(self._grid_widget)
        self._grid.setSpacing(10)
        self._grid.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(self._grid_widget)
        root.addWidget(scroll, 1)

    def _select_horizon(self, h: int):
        self._horizon = h
        for i, p in enumerate(self._pills):
            p.setChecked(FORECAST_HORIZONS[i] == h)
            _style_pill(p)

    def set_busy(self, busy: bool):
        self._busy = busy
        self._predict_btn.setEnabled(not busy)

    def _on_predict(self):
        if self._busy:
            return
        asyncio.ensure_future(self._predict_async())

    async def _predict_async(self):
        self.set_busy(True)
        self._info_lbl.setText("Fetching discourse and running models…")
        try:
            h = self._horizon

            def _run():
                conn = psycopg2.connect(DB_DSN)
                try:
                    return predict_forecast(conn, h)
                finally:
                    conn.close()

            results = await asyncio.to_thread(_run)
            self._show_results(results)
        except Exception as exc:
            self._info_lbl.setText(f"Error: {exc}")
        finally:
            self.set_busy(False)

    def _show_results(self, results: list[dict]):
        # Clear grid
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if results and "error" in results[0] and len(results) == 1:
            self._info_lbl.setText(results[0]["error"])
            return

        # Separate binary / regression
        binary = [r for r in results if r.get("type") == "binary" and "error" not in r]
        regr   = [r for r in results if r.get("type") == "regression" and "error" not in r]

        cols  = 4
        row_i = 0

        if binary:
            self._grid.addWidget(
                _lbl("BINARY OUTCOMES", 10, bold=True, color=P.TXT3),
                row_i, 0, 1, cols, Qt.AlignLeft,
            )
            row_i += 1
            for i, r in enumerate(binary):
                self._grid.addWidget(ForecastCard(r), row_i + i // cols, i % cols)
            row_i += (len(binary) + cols - 1) // cols

        if regr:
            self._grid.addWidget(
                _lbl("REGRESSION OUTCOMES", 10, bold=True, color=P.TXT3),
                row_i, 0, 1, cols, Qt.AlignLeft,
            )
            row_i += 1
            for i, r in enumerate(regr):
                self._grid.addWidget(ForecastCard(r), row_i + i // cols, i % cols)

        # Data info from first valid result
        valid = [r for r in results if "data_from" in r]
        if valid:
            self._info_lbl.setText(
                f"Discourse: {valid[0]['data_from']} → {valid[0]['data_to']}  |  "
                f"Horizon: {self._horizon} day(s)  |  "
                f"{len(binary)} binary  +  {len(regr)} regression"
            )


# ─────────────────────────────────────────────────────────────────────────────
#  Next attack tab
# ─────────────────────────────────────────────────────────────────────────────

def _dist_bar(label: str, fraction: float, color: str, parent: QWidget | None = None) -> QWidget:
    """Horizontal bar row: label  ████░░  45%"""
    w   = QWidget(parent)
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)

    lbl = _lbl(label, 11)
    lbl.setFixedWidth(200)
    lay.addWidget(lbl)

    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(int(fraction * 100))
    bar.setTextVisible(False)
    bar.setFixedHeight(8)
    bar.setFixedWidth(140)
    bar.setStyleSheet(f"""
        QProgressBar {{ background:{P.BG}; border:none; border-radius:4px; }}
        QProgressBar::chunk {{ background:{color}; border-radius:4px; }}
    """)
    lay.addWidget(bar)
    lay.addWidget(_lbl(f"{fraction:.0%}", 11, bold=True, color=color))
    lay.addStretch()
    return w


class NextAttackTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        ctrl = QHBoxLayout()
        ctrl.setSpacing(10)
        ctrl.addWidget(_lbl(
            "KNN historical analogy — finds similar past discourse patterns and "
            "aggregates what attacks followed.",
            11, color=P.TXT3,
        ))
        ctrl.addStretch()

        self._predict_btn = QPushButton("▶  Predict")
        self._predict_btn.setFixedSize(120, 34)
        self._predict_btn.setCursor(Qt.PointingHandCursor)
        self._predict_btn.setStyleSheet(f"""
            QPushButton {{
                background:{P.INDIGO}; color:#fff;
                border:none; border-radius:8px;
                font-size:13px; font-weight:800;
            }}
            QPushButton:hover {{ background:{P.INDIGO_D}; }}
            QPushButton:disabled {{ background:{P.INPUT_BG}; color:{P.TXT3}; }}
        """)
        self._predict_btn.clicked.connect(self._on_predict)
        ctrl.addWidget(self._predict_btn)
        root.addLayout(ctrl)

        self._info_lbl = _lbl("Click Predict to find similar historical patterns.", 11, color=P.TXT3)
        root.addWidget(self._info_lbl)
        root.addWidget(_hline())

        # Scrollable results area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        self._results_widget = QWidget()
        self._results_lay    = QVBoxLayout(self._results_widget)
        self._results_lay.setContentsMargins(0, 0, 0, 0)
        self._results_lay.setSpacing(10)
        self._results_lay.addStretch()
        scroll.setWidget(self._results_widget)
        root.addWidget(scroll, 1)

    def set_busy(self, busy: bool):
        self._busy = busy
        self._predict_btn.setEnabled(not busy)

    def _on_predict(self):
        if self._busy:
            return
        asyncio.ensure_future(self._predict_async())

    async def _predict_async(self):
        self.set_busy(True)
        self._info_lbl.setText("Loading historical data and running KNN…")
        try:
            def _run():
                conn = psycopg2.connect(DB_DSN)
                try:
                    return predict_next_attack(conn)
                finally:
                    conn.close()

            result = await asyncio.to_thread(_run)
            self._show_result(result)
        except Exception as exc:
            self._info_lbl.setText(f"Error: {exc}")
        finally:
            self.set_busy(False)

    def _show_result(self, r: dict):
        # Clear previous results
        while self._results_lay.count():
            item = self._results_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if "error" in r:
            self._results_lay.addWidget(_lbl(r["error"], 12, color=P.RED))
            self._results_lay.addStretch()
            return

        k    = r.get("k", 0)
        n_atk = r.get("n_with_attacks", 0)
        self._info_lbl.setText(
            f"lb={r['lookback_used']}d discourse  |  {k} analogs found  |  "
            f"{n_atk}/{k} had attacks in next {14} days  |  "
            f"Data: {r['discourse_from']} → {r['discourse_to']}"
        )

        # ── Timing ────────────────────────────────────────────────────────────
        self._results_lay.addWidget(_lbl("TIMING", 11, bold=True, color=P.TXT3))

        days = r.get("days_estimate")
        days_range = r.get("days_range")
        if days is not None:
            timing_text = f"Expected in  ~{days:.0f} day(s)"
            if days_range:
                timing_text += f"   (range: {days_range[0]:.0f} – {days_range[1]:.0f} days)"
        else:
            timing_text = "No attacks found in analog windows"
        self._results_lay.addWidget(_lbl(timing_text, 16, bold=True, color=P.TXT))

        self._results_lay.addWidget(_hline())

        # ── Two columns: region + type ────────────────────────────────────────
        cols_row = QHBoxLayout()
        cols_row.setSpacing(32)

        region_col = QVBoxLayout()
        region_col.setSpacing(4)
        region_col.addWidget(_lbl("MOST LIKELY REGION", 10, bold=True, color=P.TXT3))
        for label, frac in r.get("region_dist", []):
            c = P.RED if frac >= 0.4 else (P.AMBER if frac >= 0.2 else P.TXT2)
            region_col.addWidget(_dist_bar(label, frac, c))

        type_col = QVBoxLayout()
        type_col.setSpacing(4)
        type_col.addWidget(_lbl("MOST LIKELY TARGET TYPE", 10, bold=True, color=P.TXT3))
        for label, frac in r.get("type_dist", []):
            c = P.VIOLET if frac >= 0.4 else (P.INDIGO if frac >= 0.2 else P.TXT2)
            type_col.addWidget(_dist_bar(label, frac, c))
        if not r.get("type_dist"):
            type_col.addWidget(_lbl("No target type data", 11, color=P.TXT3))

        cols_row.addLayout(region_col, 1)
        cols_row.addLayout(type_col, 1)
        cols_row.addStretch()
        self._results_lay.addLayout(cols_row)

        self._results_lay.addWidget(_hline())

        # ── Characteristics ────────────────────────────────────────────────────
        self._results_lay.addWidget(_lbl("ATTACK CHARACTERISTICS", 10, bold=True, color=P.TXT3))
        chars_row = QHBoxLayout()
        chars_row.setSpacing(32)
        for label, key, color in [
            ("Fire probable",   "fire_rate",  P.RED),
            ("Confirmed hit",   "hit_rate",   P.AMBER),
            ("Deep strike ≥3",  "deep_rate",  P.VIOLET),
        ]:
            val = r.get(key, 0.0)
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(_lbl(label, 11, color=P.TXT3))
            col.addWidget(_lbl(f"{val:.0%}", 20, bold=True, color=color))
            chars_row.addLayout(col)
        chars_row.addStretch()
        self._results_lay.addLayout(chars_row)

        self._results_lay.addWidget(_hline())

        # ── Analogs table ──────────────────────────────────────────────────────
        analogs = r.get("analogs", [])
        self._results_lay.addWidget(
            _lbl(f"HISTORICAL ANALOGS  ({len(analogs)} patterns)", 10, bold=True, color=P.TXT3)
        )

        tbl = QTableWidget(len(analogs), 6)
        tbl.setHorizontalHeaderLabels(["Date", "Similarity", "→ Attack", "Region(s)", "Fire", "Hit"])
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.setAlternatingRowColors(True)
        tbl.setStyleSheet(f"""
            QTableWidget {{
                background:{P.INPUT_BG}; color:{P.TXT};
                border:none; gridline-color:{P.DIVIDER}; font-size:11px;
            }}
            QHeaderView::section {{
                background:{P.BG}; color:{P.TXT2};
                border:none; border-bottom:1px solid {P.DIVIDER};
                padding:4px 8px; font-weight:700; font-size:10px;
            }}
            QTableWidget::item {{ padding:3px 8px; }}
            QTableWidget::item:alternate {{ background:{P.BG}; }}
        """)

        for i, a in enumerate(analogs):
            regions_str = ", ".join(dict.fromkeys(a.get("regions", [])))[:40] or "—"
            days_str    = f"{a['days_to_next']} day(s)" if a.get("days_to_next") else "none in window"
            sim_pct     = f"{a['similarity']:.0%}"

            tbl.setItem(i, 0, _item(a["date"]))
            sim_cell = _item(sim_pct)
            sim_cell.setForeground(QColor(P.GREEN if a["similarity"] > 0.6 else P.TXT2))
            tbl.setItem(i, 1, sim_cell)
            tbl.setItem(i, 2, _item(days_str))
            tbl.setItem(i, 3, _item(regions_str, Qt.AlignLeft | Qt.AlignVCenter))
            fire_cell = _item("✓" if a.get("fire") else "—")
            fire_cell.setForeground(QColor(P.RED if a.get("fire") else P.TXT3))
            tbl.setItem(i, 4, fire_cell)
            hit_cell = _item("✓" if a.get("hit") else "—")
            hit_cell.setForeground(QColor(P.AMBER if a.get("hit") else P.TXT3))
            tbl.setItem(i, 5, hit_cell)

        tbl.resizeColumnsToContents()
        tbl.setFixedHeight(min(len(analogs) * 28 + 36, 250))
        self._results_lay.addWidget(tbl)
        self._results_lay.addStretch()


# ─────────────────────────────────────────────────────────────────────────────
#  Regime tab
# ─────────────────────────────────────────────────────────────────────────────

_TIER_COLORS = {"red": P.RED, "amber": P.AMBER, "green": P.GREEN}

_MODEL_LABELS = {
    ("significant", 10, 1): "Significant event  (next 1d)",
    ("any_attack",   2, 3): "Any attack  (next 3d)",
    ("any_attack",  10, 5): "Any attack  (next 5d)",
}


class _RegimeCard(QWidget):
    """Single prediction card: label | prob bar | AUC | regime badge."""

    def __init__(self, pred: dict, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"background:{P.INPUT_BG}; border:1px solid {P.DIVIDER}; border-radius:10px;"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)

        key   = (pred.get("target"), pred.get("lookback"), pred.get("horizon"))
        title = _MODEL_LABELS.get(key, f"{pred.get('target')} lb={pred.get('lookback')} h={pred.get('horizon')}")

        # Title row
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        hdr.addWidget(_lbl(title, 11, bold=True, color=P.TXT))
        hdr.addStretch()

        if pred.get("is_ood"):
            ood = _lbl("OOD", 9, bold=True, color=P.RED)
            ood.setStyleSheet(
                f"color:{P.RED}; background:{P.RED_BG}; border-radius:4px; "
                f"padding:1px 5px; font-size:9px; font-weight:800;"
            )
            hdr.addWidget(ood)

        rid     = pred.get("regime", -1)
        r_name  = pred.get("regime_name", f"R{rid}")
        r_color = _TIER_COLORS.get(pred.get("regime_color_key", ""), P.TXT3)
        badge = _lbl(f"R{rid}  {r_name}", 9, bold=True, color=r_color)
        badge.setStyleSheet(
            f"color:{r_color}; font-size:9px; font-weight:800; border:none;"
        )
        hdr.addWidget(badge)
        lay.addLayout(hdr)

        if "error" in pred:
            lay.addWidget(_lbl(pred["error"], 10, color=P.TXT3))
            return

        prob  = pred.get("probability", 0.0)
        pct   = int(prob * 100)
        c     = P.RED if prob >= 0.6 else (P.AMBER if prob >= 0.35 else P.GREEN)

        lay.addWidget(_lbl(f"{pct}%", 24, bold=True, color=c))

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(pct)
        bar.setTextVisible(False)
        bar.setFixedHeight(5)
        bar.setStyleSheet(
            f"QProgressBar {{ background:{P.BG}; border:none; border-radius:2px; }}"
            f"QProgressBar::chunk {{ background:{c}; border-radius:2px; }}"
        )
        lay.addWidget(bar)

        auc = pred.get("model_auc", float("nan"))
        auc_txt = f"AUC {auc:.3f}" if auc == auc else "AUC —"
        n   = pred.get("model_n", "?")
        lay.addWidget(_lbl(f"{auc_txt}  ·  n={n}", 10, color=P.TXT3))

        top = pred.get("top_features", [])[:2]
        if top:
            feat_str = "  ".join(
                f"{f.replace('_', ' ')} {v:+.2f}" for f, v in top
            )
            lay.addWidget(_lbl(feat_str, 9, color=P.TXT3))

        lay.addStretch()


class RegimeTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        # Controls row
        ctrl = QHBoxLayout()
        ctrl.setSpacing(10)

        self._train_btn = QPushButton("⚙  Train Regime Bank")
        self._train_btn.setFixedSize(180, 34)
        self._train_btn.setCursor(Qt.PointingHandCursor)
        self._train_btn.setStyleSheet(f"""
            QPushButton {{
                background:{P.INPUT_BG}; color:{P.TXT};
                border:1px solid {P.DIVIDER}; border-radius:8px;
                font-size:12px; font-weight:700;
            }}
            QPushButton:hover {{ border-color:{P.INDIGO}; color:{P.INDIGO}; }}
            QPushButton:disabled {{ color:{P.TXT3}; }}
        """)
        self._train_btn.clicked.connect(self._on_train)
        ctrl.addWidget(self._train_btn)

        self._predict_btn = QPushButton("▶  Predict")
        self._predict_btn.setFixedSize(110, 34)
        self._predict_btn.setCursor(Qt.PointingHandCursor)
        self._predict_btn.setStyleSheet(f"""
            QPushButton {{
                background:{P.INDIGO}; color:#fff;
                border:none; border-radius:8px;
                font-size:13px; font-weight:800;
            }}
            QPushButton:hover {{ background:{P.INDIGO_D}; }}
            QPushButton:disabled {{ background:{P.INPUT_BG}; color:{P.TXT3}; }}
        """)
        self._predict_btn.clicked.connect(self._on_predict)
        ctrl.addWidget(self._predict_btn)
        ctrl.addStretch()
        root.addLayout(ctrl)

        self._status_lbl = _lbl("", 11, color=P.TXT3)
        root.addWidget(self._status_lbl)
        root.addWidget(_hline())

        # Current regime banner
        self._regime_banner = QWidget()
        self._regime_banner.setFixedHeight(56)
        self._regime_banner.setStyleSheet(
            f"background:{P.INPUT_BG}; border:1px solid {P.DIVIDER}; border-radius:10px;"
        )
        banner_lay = QHBoxLayout(self._regime_banner)
        banner_lay.setContentsMargins(16, 8, 16, 8)
        banner_lay.setSpacing(16)
        self._regime_title_lbl = _lbl("CURRENT REGIME", 10, bold=True, color=P.TXT3)
        self._regime_name_lbl  = _lbl("—", 15, bold=True, color=P.TXT3)
        self._regime_desc_lbl  = _lbl("", 11, color=P.TXT3)
        banner_lay.addWidget(self._regime_title_lbl)
        banner_lay.addWidget(self._regime_name_lbl)
        banner_lay.addWidget(self._regime_desc_lbl)
        banner_lay.addStretch()
        root.addWidget(self._regime_banner)

        # Cards area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        self._cards_widget = QWidget()
        self._cards_lay    = QHBoxLayout(self._cards_widget)
        self._cards_lay.setContentsMargins(0, 8, 0, 8)
        self._cards_lay.setSpacing(12)
        self._cards_lay.addStretch()
        scroll.setWidget(self._cards_widget)
        root.addWidget(scroll, 1)

        self._refresh_bank_status()

    def _refresh_bank_status(self):
        if BANK_PATH.exists():
            from datetime import datetime
            mtime = datetime.fromtimestamp(BANK_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            self._status_lbl.setText(f"Regime bank trained  ●  {mtime}")
            self._status_lbl.setStyleSheet(
                f"color:{P.GREEN}; font-size:11px; font-weight:600; border:none;"
            )
        else:
            self._status_lbl.setText("Regime bank not trained — click Train Regime Bank first")
            self._status_lbl.setStyleSheet(
                f"color:{P.TXT3}; font-size:11px; font-weight:600; border:none;"
            )

    def set_busy(self, busy: bool):
        self._busy = busy
        self._train_btn.setEnabled(not busy)
        self._predict_btn.setEnabled(not busy)

    # ── Train ─────────────────────────────────────────────────────────────────

    def _on_train(self):
        if self._busy:
            return
        self.set_busy(True)
        self._status_lbl.setText("Detecting regimes and training per-regime models…")
        self._status_lbl.setStyleSheet(
            f"color:{P.TXT3}; font-size:11px; font-weight:600; border:none;"
        )
        asyncio.ensure_future(self._train_async())

    async def _train_async(self):
        try:
            def _run():
                import psycopg2 as _pg
                conn = _pg.connect(DB_DSN)
                try:
                    raw   = load_attacks(conn)
                    disc  = load_discourse(conn)
                finally:
                    conn.close()
                daily = aggregate_attacks(raw)
                df    = build_full_daily(daily, disc)
                bank  = RegimeModelBank()
                bank.train(df)
                bank.save()
                return bank

            bank = await asyncio.to_thread(_run)
            n_models = len(bank.models)
            k        = bank.detector.k
            self._status_lbl.setText(
                f"Trained  ●  {k} regimes  ·  {n_models} models  ·  "
                f"{bank.trained_on_}"
            )
            self._status_lbl.setStyleSheet(
                f"color:{P.GREEN}; font-size:11px; font-weight:600; border:none;"
            )
        except Exception as exc:
            self._status_lbl.setText(f"Training error: {exc}")
            self._status_lbl.setStyleSheet(
                f"color:{P.RED}; font-size:11px; font-weight:600; border:none;"
            )
        finally:
            self.set_busy(False)

    # ── Predict ───────────────────────────────────────────────────────────────

    def _on_predict(self):
        if self._busy:
            return
        if not BANK_PATH.exists():
            self._status_lbl.setText("Train the regime bank first.")
            return
        self.set_busy(True)
        asyncio.ensure_future(self._predict_async())

    async def _predict_async(self):
        try:
            def _run():
                import psycopg2 as _pg
                conn = _pg.connect(DB_DSN)
                try:
                    raw  = load_attacks(conn)
                    disc = load_discourse(conn)
                finally:
                    conn.close()
                daily = aggregate_attacks(raw)
                df    = build_full_daily(daily, disc)
                bank  = RegimeModelBank.load()
                preds = bank.predict(df)
                return preds

            preds = await asyncio.to_thread(_run)
            self._show_results(preds)
        except Exception as exc:
            self._status_lbl.setText(f"Prediction error: {exc}")
            self._status_lbl.setStyleSheet(
                f"color:{P.RED}; font-size:11px; font-weight:600; border:none;"
            )
        finally:
            self.set_busy(False)

    def _show_results(self, preds: list[dict]):
        # Clear cards
        while self._cards_lay.count():
            item = self._cards_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not preds:
            self._cards_lay.addStretch()
            return

        # Update regime banner from first valid prediction
        valid = [p for p in preds if "error" not in p]
        if valid:
            rid     = valid[0].get("regime", -1)
            r_name  = valid[0].get("regime_name", f"R{rid}")
            r_desc  = valid[0].get("regime_desc", "")
            r_color = _TIER_COLORS.get(valid[0].get("regime_color_key", ""), P.TXT3)
            self._regime_name_lbl.setText(f"R{rid}  {r_name}")
            self._regime_name_lbl.setStyleSheet(
                f"color:{r_color}; font-size:15px; font-weight:900; border:none;"
            )
            self._regime_desc_lbl.setText(r_desc)
            if valid[0].get("is_ood"):
                self._regime_desc_lbl.setText(r_desc + "  ·  ⚠ OOD — regime boundary")

        for pred in preds:
            card = _RegimeCard(pred)
            card.setFixedSize(220, 170)
            self._cards_lay.addWidget(card)
        self._cards_lay.addStretch()


# ─────────────────────────────────────────────────────────────────────────────
#  Model Insights cards
# ─────────────────────────────────────────────────────────────────────────────

def _make_reliability_card(rel: dict) -> QWidget:
    level = rel.get("level", "Unknown")
    text  = rel.get("text", "")
    auc   = rel.get("auc")
    n     = rel.get("n_signals", 0)
    level_color = {
        "Strong": P.GREEN, "Good": P.GREEN,
        "Moderate": P.AMBER, "Limited": P.RED,
    }.get(level, P.TXT3)

    w   = QWidget()
    w.setStyleSheet(
        f"background:{P.INPUT_BG}; border:1px solid {level_color}; border-radius:8px;"
    )
    lay = QHBoxLayout(w)
    lay.setContentsMargins(12, 8, 12, 8)
    lay.setSpacing(12)

    badge = _lbl(level, 11, bold=True, color=level_color)
    badge.setFixedWidth(72)
    lay.addWidget(badge)

    txt_w = _lbl(text, 11, color=P.TXT2)
    txt_w.setWordWrap(True)
    lay.addWidget(txt_w, 1)

    if auc is not None:
        meta = _lbl(f"{n} signal{'s' if n != 1 else ''}  ·  AUC {auc:.2f}", 10, color=P.TXT3)
        meta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(meta)

    return w


def _make_signal_card(si: dict) -> QWidget:
    tier  = si.get("tier") or "low"
    prob  = si.get("prob", 0.0)
    label = si.get("label", "?")
    tier_color = {"high": P.RED, "medium": P.AMBER, "low": P.GREEN}.get(tier, P.TXT3)

    w = QWidget()
    w.setStyleSheet(
        f"background:{P.INPUT_BG}; "
        f"border-left:3px solid {tier_color}; "
        f"border-top:1px solid {P.DIVIDER}; "
        f"border-right:1px solid {P.DIVIDER}; "
        f"border-bottom:1px solid {P.DIVIDER}; "
        f"border-radius:6px;"
    )
    lay = QVBoxLayout(w)
    lay.setContentsMargins(12, 8, 12, 8)
    lay.setSpacing(3)

    hdr = QHBoxLayout()
    hdr.setSpacing(8)
    hdr.addWidget(_lbl(label, 12, bold=True, color=P.TXT))
    hdr.addWidget(_lbl(f"{int(prob * 100)}%", 12, bold=True, color=tier_color))
    hdr.addStretch()
    h_val  = si.get("best_h", "?")
    lb_val = si.get("best_lb", "?")
    auc_val = si.get("cv_auc", float("nan"))
    auc_s   = f"AUC {auc_val:.2f}" if auc_val == auc_val else "AUC —"
    hdr.addWidget(_lbl(f"h={h_val}d  ·  {auc_s}", 10, color=P.TXT3))
    lay.addLayout(hdr)

    for f in si.get("features", [])[:3]:
        arrow = "▲" if f["direction"] == "positive" else "▼"
        arr_c = P.GREEN if f["direction"] == "positive" else P.RED
        r_str = f"R={f['r']:+.2f}" if f.get("r") else ""
        row   = QHBoxLayout()
        row.setSpacing(5)
        row.addWidget(_lbl(arrow, 10, bold=True, color=arr_c))
        row.addWidget(_lbl(f["readable"], 10, color=P.TXT2))
        row.addStretch()
        row.addWidget(_lbl(r_str, 10, color=P.TXT3))
        lay.addLayout(row)

    return w


def _make_combo_card(c: dict) -> QWidget:
    strength = c.get("strength", "moderate")
    badge_map = {
        "notable":  (P.AMBER,  "NOTABLE"),
        "moderate": (P.TXT3,   "MODERATE"),
        "seasonal": (P.INDIGO, "SEASONAL"),
    }
    badge_color, badge_text = badge_map.get(strength, (P.TXT3, strength.upper()))

    w = QWidget()
    w.setStyleSheet(
        f"background:{P.INPUT_BG}; border:1px solid {P.DIVIDER}; border-radius:6px;"
    )
    lay = QVBoxLayout(w)
    lay.setContentsMargins(12, 8, 12, 8)
    lay.setSpacing(3)

    hdr = QHBoxLayout()
    hdr.setSpacing(8)
    badge = _lbl(badge_text, 9, bold=True, color=badge_color)
    hdr.addWidget(badge)
    hdr.addWidget(_lbl(c.get("pattern", ""), 11, bold=True, color=P.TXT))
    hdr.addStretch()
    lay.addLayout(hdr)
    detail = _lbl(c.get("detail", ""), 11, color=P.TXT2)
    detail.setWordWrap(True)
    lay.addWidget(detail)

    return w


def _make_influence_section(influence: list) -> QWidget:
    w   = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)

    if not influence:
        return w

    max_imp = max(f["importance"] for f in influence) or 1.0

    for feat in influence:
        frac  = feat["importance"] / max_imp
        arrow = "▲" if feat["direction"] == "positive" else "▼"
        arr_c = P.GREEN if feat["direction"] == "positive" else P.RED

        row = QWidget()
        rl  = QHBoxLayout(row)
        rl.setContentsMargins(0, 1, 0, 1)
        rl.setSpacing(6)

        rl.addWidget(_lbl(arrow, 10, bold=True, color=arr_c))

        name_lbl = _lbl(feat["readable"], 10, color=P.TXT2)
        name_lbl.setFixedWidth(200)
        rl.addWidget(name_lbl)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(frac * 100))
        bar.setTextVisible(False)
        bar.setFixedHeight(5)
        bar.setFixedWidth(120)
        bar.setStyleSheet(
            f"QProgressBar {{ background:{P.DIVIDER}; border:none; border-radius:2px; }}"
            f"QProgressBar::chunk {{ background:{arr_c}; border-radius:2px; }}"
        )
        rl.addWidget(bar)
        rl.addStretch()
        lay.addWidget(row)

    return w


class ModelInsightsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 4)
        root.setSpacing(0)

        self._toggle = QPushButton("Why this forecast?  ▼")
        self._toggle.setCheckable(True)
        self._toggle.setChecked(False)
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{P.TXT2};
                border:none; font-size:11px; font-weight:700;
                text-align:left; padding:4px 0px;
            }}
            QPushButton:hover {{ color:{P.TXT}; }}
        """)
        self._toggle.clicked.connect(self._on_toggle)
        root.addWidget(self._toggle)

        self._content = QWidget()
        self._content.setVisible(False)
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 6, 0, 0)
        self._content_lay.setSpacing(6)
        root.addWidget(self._content)

    def _on_toggle(self, checked: bool):
        self._toggle.setText("Why this forecast?  ▲" if checked else "Why this forecast?  ▼")
        self._content.setVisible(checked)

    def update_insights(self, insights: dict):
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        rel = insights.get("reliability", {})
        self._content_lay.addWidget(_make_reliability_card(rel))

        sigs = insights.get("signal_insights", [])
        if sigs:
            self._content_lay.addWidget(_lbl("KEY SIGNAL DRIVERS", 10, bold=True, color=P.TXT3))
            for si in sigs[:3]:
                self._content_lay.addWidget(_make_signal_card(si))

        combos = insights.get("notable_combos", [])
        if combos:
            self._content_lay.addWidget(_lbl("NOTABLE PATTERNS", 10, bold=True, color=P.TXT3))
            for c in combos[:3]:
                self._content_lay.addWidget(_make_combo_card(c))

        infl = insights.get("feature_influence", [])
        if infl:
            self._content_lay.addWidget(_lbl("FEATURE INFLUENCE", 10, bold=True, color=P.TXT3))
            self._content_lay.addWidget(_make_influence_section(infl[:6]))


# ─────────────────────────────────────────────────────────────────────────────
#  Intel Forecast tab
# ─────────────────────────────────────────────────────────────────────────────

_WARNING_CONFIG = {
    "CRITICAL": (P.RED,   P.RED_BG,   "⚠  CRITICAL — Strong near-term attack signals detected"),
    "HIGH":     (P.RED,   P.RED_BG,   "⚠  HIGH RISK — Significant near-term signals"),
    "ELEVATED": (P.AMBER, P.AMBER_BG, "⚡  ELEVATED — Moderate near-term signals"),
    "LOW":      (P.GREEN, P.GREEN_BG, "↗  LOW — Weak signals detected"),
    "NONE":     (P.TXT3,  P.INPUT_BG, "—  No confident near-term signals at this precision level"),
}

_DIM_LABELS = {
    "infrastructure": "INFRASTRUCTURE",
    "scale":          "SCALE",
    "effects":        "EFFECTS",
    "baseline":       "BASELINE",
}


class _SignalPill(QWidget):
    def __init__(self, signal: dict, parent=None):
        super().__init__(parent)
        tier  = signal.get("tier") or "low"
        prob  = signal["prob"]
        label = signal["label"]
        h     = signal["best_h"]

        if tier == "high":
            fg, bg = P.RED, P.RED_BG
        elif tier == "medium":
            fg, bg = P.AMBER, P.AMBER_BG
        else:
            fg, bg = P.GREEN, P.GREEN_BG

        self.setStyleSheet(
            f"background:{bg}; border:1px solid {fg}; border-radius:6px;"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(0)

        txt = QLabel(f"{label}  {int(prob * 100)}%")
        txt.setStyleSheet(
            f"color:{fg}; font-size:11px; font-weight:700; border:none; background:transparent;"
        )
        lay.addWidget(txt)


def _make_dim_row(dim_key: str, signals: list[dict]) -> QWidget:
    w   = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 2, 0, 2)
    lay.setSpacing(8)

    lbl = _lbl(_DIM_LABELS.get(dim_key, dim_key.upper()), 10, bold=True, color=P.TXT3)
    lbl.setFixedWidth(108)
    lay.addWidget(lbl)

    if not signals:
        no_sig = _lbl("no signal", 11, color=P.TXT3)
        no_sig.setStyleSheet(
            f"color:{P.TXT3}; font-size:11px; font-style:italic; border:none;"
        )
        lay.addWidget(no_sig)
    else:
        for s in signals:
            lay.addWidget(_SignalPill(s))

    lay.addStretch()
    return w


def _make_band_section(band: dict, band_label: str, result_lay: QVBoxLayout):
    result_lay.addWidget(_lbl(band_label, 12, bold=True, color=P.TXT))

    any_signal = band.get("total_passing", 0) > 0
    if not any_signal:
        result_lay.addWidget(
            _lbl("  No confident signals at this precision level", 11, color=P.TXT3)
        )
    else:
        for dim_key in DIMENSIONS:
            sigs = band["signals"].get(dim_key, [])
            if sigs:
                result_lay.addWidget(_make_dim_row(dim_key, sigs))

    result_lay.addWidget(_hline())


class IntelForecastTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy          = False
        self._precision     = "medium"
        self._target_date   = date.today()
        self._insights_panel: ModelInsightsPanel | None = None
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # ── Controls row ──────────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        self._train_btn = QPushButton("⚙  Train Models")
        self._train_btn.setFixedSize(160, 34)
        self._train_btn.setCursor(Qt.PointingHandCursor)
        self._train_btn.setStyleSheet(f"""
            QPushButton {{
                background:{P.INPUT_BG}; color:{P.TXT};
                border:1px solid {P.DIVIDER}; border-radius:8px;
                font-size:12px; font-weight:700;
            }}
            QPushButton:hover {{ border-color:{P.INDIGO}; color:{P.INDIGO}; }}
            QPushButton:disabled {{ color:{P.TXT3}; }}
        """)
        self._train_btn.clicked.connect(self._on_train)
        ctrl.addWidget(self._train_btn)

        self._predict_btn = QPushButton("▶  Predict")
        self._predict_btn.setFixedSize(110, 34)
        self._predict_btn.setCursor(Qt.PointingHandCursor)
        self._predict_btn.setStyleSheet(f"""
            QPushButton {{
                background:{P.INDIGO}; color:#fff;
                border:none; border-radius:8px;
                font-size:13px; font-weight:800;
            }}
            QPushButton:hover {{ background:{P.INDIGO_D}; }}
            QPushButton:disabled {{ background:{P.INPUT_BG}; color:{P.TXT3}; }}
        """)
        self._predict_btn.clicked.connect(self._on_predict)
        ctrl.addWidget(self._predict_btn)

        self._date_btn = QPushButton(f"📅  {self._target_date}")
        self._date_btn.setFixedHeight(34)
        self._date_btn.setCursor(Qt.PointingHandCursor)
        self._date_btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{P.TXT2};
                border:1px solid {P.DIVIDER}; border-radius:8px;
                font-size:12px; font-weight:600; padding:0 10px;
            }}
            QPushButton:hover {{ border-color:{P.INDIGO}; color:{P.INDIGO}; }}
        """)
        self._date_btn.clicked.connect(self._open_date_picker)
        ctrl.addWidget(self._date_btn)

        ctrl.addSpacing(12)
        ctrl.addWidget(_lbl("Precision:", 11, bold=True, color=P.TXT2))

        self._prec_pills: dict[str, QPushButton] = {}
        for level in ("high", "medium", "low"):
            p = _pill(level.capitalize(), level == self._precision)
            p.clicked.connect(lambda _, lv=level: self._set_precision(lv))
            self._prec_pills[level] = p
            ctrl.addWidget(p)

        ctrl.addStretch()
        root.addLayout(ctrl)

        self._status_lbl = _lbl("", 11, color=P.TXT3)
        root.addWidget(self._status_lbl)
        root.addWidget(_hline())

        # ── Scrollable results ────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        self._results_widget = QWidget()
        self._results_lay    = QVBoxLayout(self._results_widget)
        self._results_lay.setContentsMargins(0, 0, 0, 0)
        self._results_lay.setSpacing(6)
        self._results_lay.addStretch()
        scroll.setWidget(self._results_widget)
        root.addWidget(scroll, 1)

        # ── Technical details (collapsible) ───────────────────────────────────
        self._tech_toggle = QPushButton("Technical details ▼")
        self._tech_toggle.setCheckable(True)
        self._tech_toggle.setChecked(False)
        self._tech_toggle.setCursor(Qt.PointingHandCursor)
        self._tech_toggle.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{P.TXT3};
                border:none; font-size:10px; font-weight:600;
                text-align:left; padding:2px 0px;
            }}
            QPushButton:hover {{ color:{P.TXT2}; }}
        """)
        self._tech_panel = QWidget()
        self._tech_lbl   = _lbl("", 10, color=P.TXT3)
        self._tech_lbl.setWordWrap(True)
        QVBoxLayout(self._tech_panel).addWidget(self._tech_lbl)
        self._tech_panel.setVisible(False)
        self._tech_toggle.clicked.connect(
            lambda checked: self._tech_panel.setVisible(checked)
        )
        root.addWidget(self._tech_toggle)
        root.addWidget(self._tech_panel)

        self._insights_panel = ModelInsightsPanel()
        self._insights_panel.setVisible(False)

        self._refresh_bank_status()

    def _open_date_picker(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Select target date")
        dlg.setStyleSheet(f"background:{P.BG}; color:{P.TXT};")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(12)

        cal = QCalendarWidget()
        cal.setGridVisible(True)
        qd = QDate(self._target_date.year, self._target_date.month, self._target_date.day)
        cal.setSelectedDate(qd)
        cal.setMaximumDate(QDate.currentDate())
        cal.setStyleSheet(f"""
            QCalendarWidget QWidget {{ background:{P.INPUT_BG}; color:{P.TXT}; }}
            QCalendarWidget QAbstractItemView:enabled {{ color:{P.TXT}; selection-background-color:{P.INDIGO}; }}
            QCalendarWidget QToolButton {{ background:transparent; color:{P.TXT}; border:none; }}
            QCalendarWidget QMenu {{ background:{P.INPUT_BG}; color:{P.TXT}; }}
        """)
        lay.addWidget(cal)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.setStyleSheet(f"color:{P.TXT};")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        if dlg.exec() == QDialog.Accepted:
            qsel = cal.selectedDate()
            self._target_date = date(qsel.year(), qsel.month(), qsel.day())
            self._date_btn.setText(f"📅  {self._target_date}")

    def _refresh_bank_status(self):
        mtime = get_bank_mtime()
        if mtime:
            self._status_lbl.setText(f"Ready  ●  {mtime}")
            self._status_lbl.setStyleSheet(
                f"color:{P.GREEN}; font-size:11px; font-weight:600; border:none;"
            )
        else:
            self._status_lbl.setText("Not trained — click Train Models first")
            self._status_lbl.setStyleSheet(
                f"color:{P.TXT3}; font-size:11px; font-weight:600; border:none;"
            )

    def _set_precision(self, level: str):
        self._precision = level
        for lv, btn in self._prec_pills.items():
            btn.setChecked(lv == level)
            _style_pill(btn)

    def set_busy(self, busy: bool):
        self._busy = busy
        self._train_btn.setEnabled(not busy)
        self._predict_btn.setEnabled(not busy)
        self._date_btn.setEnabled(not busy)

    # ── Train ─────────────────────────────────────────────────────────────────

    def _on_train(self):
        if self._busy:
            return
        self.set_busy(True)
        self._status_lbl.setText("Training…")
        self._status_lbl.setStyleSheet(
            f"color:{P.TXT3}; font-size:11px; font-weight:600; border:none;"
        )
        asyncio.ensure_future(self._train_async())

    async def _train_async(self):
        try:
            def _run():
                import psycopg2 as _pg
                conn = _pg.connect(DB_DSN)
                try:
                    return train_bank(conn)
                finally:
                    conn.close()

            meta = await asyncio.to_thread(_run)
            trained_at = meta.get("trained_at", "?")
            self._status_lbl.setText(f"Ready  ●  {trained_at}")
            self._status_lbl.setStyleSheet(
                f"color:{P.GREEN}; font-size:11px; font-weight:600; border:none;"
            )
        except Exception as exc:
            self._status_lbl.setText(f"Error: {exc}")
            self._status_lbl.setStyleSheet(
                f"color:{P.RED}; font-size:11px; font-weight:600; border:none;"
            )
        finally:
            self.set_busy(False)

    # ── Predict ───────────────────────────────────────────────────────────────

    def _on_predict(self):
        if self._busy:
            return
        if not FORECAST_BANK_PATH.exists():
            self._status_lbl.setText("Train models first.")
            return
        self.set_busy(True)
        self._status_lbl.setText("Generating forecast…")
        asyncio.ensure_future(self._predict_async())

    async def _predict_async(self):
        precision = self._precision
        try:
            def _run():
                import psycopg2 as _pg
                conn = _pg.connect(DB_DSN)
                try:
                    fc = predict_intel(conn, precision)
                finally:
                    conn.close()
                insights = build_model_insights(fc)
                return fc, insights

            forecast, insights = await asyncio.to_thread(_run)
            self._show_forecast(forecast, insights)
        except Exception as exc:
            self._status_lbl.setText(f"Error: {exc}")
            self._status_lbl.setStyleSheet(
                f"color:{P.RED}; font-size:11px; font-weight:600; border:none;"
            )
        finally:
            self.set_busy(False)

    # ── Render ────────────────────────────────────────────────────────────────

    def _clear_results(self):
        while self._results_lay.count():
            item = self._results_lay.takeAt(0)
            w = item.widget()
            if w is not None and w is not self._insights_panel:
                w.deleteLater()

    def _show_forecast(self, fc: dict, insights: dict | None = None):
        self._clear_results()

        # Warning banner
        warning = fc.get("warning_level", "NONE")
        fg, bg, msg = _WARNING_CONFIG.get(warning, _WARNING_CONFIG["NONE"])
        banner = QWidget()
        banner.setStyleSheet(
            f"background:{bg}; border:1px solid {fg}; border-radius:8px;"
        )
        ban_lay = QHBoxLayout(banner)
        ban_lay.setContentsMargins(14, 8, 14, 8)
        ban_lay.addWidget(_lbl(msg, 12, bold=True, color=fg))
        ban_lay.addStretch()
        self._results_lay.addWidget(banner)

        # Time band sections
        _make_band_section(fc.get("near_term", {}), "NEAR-TERM  ·  next 1–3 days",        self._results_lay)
        _make_band_section(fc.get("weekly", {}),    "WEEKLY OUTLOOK  ·  next 4–10 days",   self._results_lay)
        _make_band_section(fc.get("extended", {}),  "EXTENDED OUTLOOK  ·  next 2–3 weeks", self._results_lay)

        # Model Insights panel
        if insights and insights.get("has_insights"):
            self._insights_panel.update_insights(insights)
            self._results_lay.addWidget(self._insights_panel)
            self._insights_panel.setVisible(True)
        else:
            self._insights_panel.setVisible(False)

        # Timestamp
        data_thru = fc.get("data_through", "?")
        gen_at    = fc.get("generated_at", "?")
        self._results_lay.addWidget(
            _lbl(
                f"Target date: {self._target_date}  ·  Generated {gen_at}  ·  Data through {data_thru}",
                10, color=P.TXT3,
            )
        )
        self._results_lay.addStretch()

        # Update technical panel
        q       = fc.get("quality", {})
        avg_auc = q.get("avg_auc", float("nan"))
        auc_s   = f"{avg_auc:.3f}" if avg_auc == avg_auc else "—"
        self._tech_lbl.setText(
            f"Models: {q.get('n_models', '?')}  ·  "
            f"Avg AUC ({fc.get('precision_mode','?')} mode): {auc_s}  ·  "
            f"Trained: {q.get('trained_at', '?')}  ·  "
            f"Data through: {data_thru}"
        )

        self._status_lbl.setText(f"Forecast ready  ●  {gen_at}")
        self._status_lbl.setStyleSheet(
            f"color:{P.GREEN}; font-size:11px; font-weight:600; border:none;"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Main page
# ─────────────────────────────────────────────────────────────────────────────

class PredictionsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(0)

        title = QLabel("Predictions")
        title.setStyleSheet(
            f"color:{P.TXT}; font-size:22px; font-weight:900; border:none;"
        )
        root.addWidget(title)
        root.addSpacing(12)

        self._intel = IntelForecastTab()
        root.addWidget(self._intel, 1)

    # kept for external callers that may call set_busy on the page
    def _set_busy(self, busy: bool):
        self._intel.set_busy(busy)

