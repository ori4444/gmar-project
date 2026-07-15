"""
Predictions page — two modes, both explicitly tied to the analysis pipeline.

Pipeline visible to the user:
  Create Model (analysis + training, one button) → Prediction

Forecast mode:   multi-target outcome grid for a chosen horizon
Next Attack mode: KNN historical analogy (when / where / what)
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

import psycopg2
from PySide6.QtCore import (
    QEasingCurve, QObject, QPropertyAnimation, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from . import palette as P
from . import styles as S

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
    train_bank, predict_intel_full, get_bank_meta, get_bank_mtime,
    BANK_PATH as FORECAST_BANK_PATH, DIMENSIONS, TARGET_LABELS,
)
from analysis.multi_target_analysis import run as run_multi_target_analysis


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _lbl(text: str, size: int = 12, bold: bool = False,
         color: str | None = None) -> QLabel:
    w = QLabel(text)
    w.setStyleSheet(
        f"color:{color or P.TXT2}; font-family:{P.FONT_STACK}; font-size:{size}px; "
        f"font-weight:{'700' if bold else '500'}; border:none; background:transparent;"
    )
    return w


def _hline() -> QWidget:
    w = QWidget()
    w.setFixedHeight(1)
    w.setStyleSheet(S.divider_qss())
    return w


def _format_model_facts(meta: dict) -> str:
    """Dry, factual line about the trained model bank — dates and data
    volume, not interpretation. Deliberately separate from the forecast's
    "why" panel, which is about a specific prediction."""
    parts = []
    data_range = meta.get("data_range", [])
    if len(data_range) == 2:
        span = ""
        try:
            d0 = datetime.strptime(data_range[0], "%Y-%m-%d").date()
            d1 = datetime.strptime(data_range[1], "%Y-%m-%d").date()
            span = f"  ({(d1 - d0).days} ימים)"
        except ValueError:
            pass
        parts.append(f"נתונים מ-{data_range[0]} עד {data_range[1]}{span}")
    n_models = meta.get("n_models", 0)
    parts.append(f"{n_models} מודלים מאומנים")
    return "  ·  ".join(parts)


def _pill(text: str, active: bool) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(30)
    btn.setCheckable(True)
    btn.setChecked(active)
    btn.setCursor(Qt.PointingHandCursor)
    _style_pill(btn)
    S.bind_floating_button(btn, idle=(10, 60, 2), hover=(20, 110, 6), pressed=(4, 30, 1),
                            flash_color=P.INDIGO)
    return btn


def _style_pill(btn: QPushButton):
    if btn.isChecked():
        btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{P.INDIGO_L};
                border:1.5px solid {P.INDIGO}; border-radius:{P.RADIUS_MD}px;
                font-family:{P.FONT_STACK}; font-size:12px; font-weight:700; padding:4px 14px;
            }}
        """)
    else:
        btn.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{P.TXT2};
                border:1px solid {P.DIVIDER}; border-radius:{P.RADIUS_MD}px;
                font-family:{P.FONT_STACK}; font-size:12px; font-weight:600; padding:4px 14px;
            }}
            QPushButton:hover {{ border-color:{P.INDIGO}; color:{P.INDIGO}; }}
        """)


def _item(text: str, align: Qt.AlignmentFlag = Qt.AlignCenter) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    it.setTextAlignment(align)
    return it


# Hebrew display names for the English target labels coming from
# analysis.attack_forecast.TARGET_LABELS (that module stays English —
# this is a display-only lookup, same wording as ui/story.py).
_TARGET_LABELS_HE = {
    "Power Facility":     "תחנת כוח",
    "Refinery":           "בית זיקוק",
    "Pipeline":           "צינור",
    "Oil Depot":          "מחסן דלק",
    "Gas Facility":       "מתקן גז",
    "Repeated Strike":    "תקיפה חוזרת",
    "Heavy Bombardment":  "הפגזה כבדה",
    "Combined Strike":    "תקיפה משולבת",
    "Deep Strike":        "חדירה עמוקה",
    "Very Deep Strike":   "חדירה עמוקה מאוד",
    "Fire":               "שריפה",
    "Multi-Fire":         "כמה שריפות",
    "Explosion":          "פיצוץ",
    "Shutdown":           "השבתה",
    "Any Attack":         "תקיפה כלשהי",
    "Significant Event":  "אירוע משמעותי",
    "Confirmed Hit":      "פגיעה מאושרת",
}


def _he_label(label: str | None) -> str | None:
    if label is None:
        return None
    return _TARGET_LABELS_HE.get(label, label)


# ─────────────────────────────────────────────────────────────────────────────
#  Pipeline status widget
# ─────────────────────────────────────────────────────────────────────────────

class PipelineStatus(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(S.card_qss("QWidget", radius=P.RADIUS_MD))
        S.apply_card_shadow(self, blur=30, alpha=75, y_offset=9)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(24)

        self._analysis_lbl = _lbl("", 11)
        self._arrow1       = _lbl("→", 14, bold=True, color=P.TXT3)
        self._training_lbl = _lbl("", 11)
        self._arrow2       = _lbl("→", 14, bold=True, color=P.TXT3)
        self._ready_lbl    = _lbl("", 11)

        lay.addWidget(_lbl("תהליך", 10, bold=True, color=P.TXT3))
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
            self._analysis_lbl.setText(f"ניתוח  ●  {analysis_t}")
            self._analysis_lbl.setStyleSheet(
                f"color:{P.GREEN}; font-size:11px; font-weight:600; border:none;"
            )
        else:
            self._analysis_lbl.setText("ניתוח  ○  לא בוצע")
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
            warn  = "  ⚠ הניתוח עודכן" if outdated else ""
            self._training_lbl.setText(f"אימון  ●  {trained_at}{warn}")
            self._training_lbl.setStyleSheet(
                f"color:{color}; font-size:11px; font-weight:600; border:none;"
            )
            self._ready_lbl.setText("מוכן לחיזוי")
            self._ready_lbl.setStyleSheet(
                f"color:{P.GREEN}; font-size:11px; font-weight:600; border:none;"
            )
        else:
            self._training_lbl.setText("אימון  ○  לא אומן")
            self._training_lbl.setStyleSheet(
                f"color:{P.TXT3}; font-size:11px; font-weight:600; border:none;"
            )
            self._ready_lbl.setText("נדרש אימון")
            self._ready_lbl.setStyleSheet(
                f"color:{P.RED}; font-size:11px; font-weight:600; border:none;"
            )


# ─────────────────────────────────────────────────────────────────────────────
#  Forecast outcome card
# ─────────────────────────────────────────────────────────────────────────────

_OUTCOME_LABELS = {
    "any_attack":       "תקיפה כלשהי",
    "significant":      "אירוע משמעותי",
    "any_fire":         "שריפה",
    "any_hit":          "פגיעה מאושרת",
    "any_shutdown":     "השבתה",
    "deep_strike":      "חדירה עמוקה (≥3)",
    "combined_strike":  "תקיפה משולבת",
    "repeated_strike":  "תקיפה חוזרת",
    "multi_attack":     "הפגזה כבדה (+3)",
    "any_explosion":    "פיצוצים",
    "very_deep":        "חדירה עמוקה מאוד (≥4)",
    "multi_fire":       "כמה שריפות (+2)",
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
                background:{P.CARD_BG}; border:1px solid {border_color};
                border-radius:{P.RADIUS_MD}px;
            }}
        """)
        S.apply_card_shadow(self, blur=28, alpha=75, y_offset=9)

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
                QProgressBar {{ background:{P.INPUT_BG}; border:none; border-radius:2px; }}
                QProgressBar::chunk {{
                    border-radius:2px;
                    background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {c}99, stop:1 {c});
                }}
            """)
            lay.addWidget(bar)

        # CV score
        cv = result.get("cv_score", float("nan"))
        metric_label = "AUC" if typ == "binary" else "R²"
        cv_text = f"{metric_label} {cv:.3f}" if cv == cv else f"{metric_label} —"
        lay.addWidget(_lbl(cv_text, 10, color=P.TXT3))

        # Lookback info
        lb = result.get("lookback", "?")
        lay.addWidget(_lbl(f"טווח נתונים: {lb} ימים", 10, color=P.TXT3))

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
        ctrl.setSpacing(10)
        ctrl.addWidget(_lbl("טווח זמן:", 12, bold=True, color=P.TXT))

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

        self._predict_btn = QPushButton("▶  חיזוי")
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

        self._info_lbl = _lbl("בחרו טווח זמן ולחצו על חיזוי.", 11, color=P.TXT3)
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
        self._info_lbl.setText("שולפים נתונים ומריצים את המודלים…")
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
            self._info_lbl.setText(f"שגיאה: {exc}")
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
                _lbl("תוצאות כן/לא", 10, bold=True, color=P.TXT3),
                row_i, 0, 1, cols, Qt.AlignLeft,
            )
            row_i += 1
            for i, r in enumerate(binary):
                self._grid.addWidget(ForecastCard(r), row_i + i // cols, i % cols)
            row_i += (len(binary) + cols - 1) // cols

        if regr:
            self._grid.addWidget(
                _lbl("תוצאות מספריות", 10, bold=True, color=P.TXT3),
                row_i, 0, 1, cols, Qt.AlignLeft,
            )
            row_i += 1
            for i, r in enumerate(regr):
                self._grid.addWidget(ForecastCard(r), row_i + i // cols, i % cols)

        # Data info from first valid result
        valid = [r for r in results if "data_from" in r]
        if valid:
            self._info_lbl.setText(
                f"נתונים: {valid[0]['data_from']} → {valid[0]['data_to']}  |  "
                f"טווח: {self._horizon} ימים  |  "
                f"{len(binary)} כן/לא  +  {len(regr)} מספריות"
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
        QProgressBar {{ background:{P.INPUT_BG}; border:none; border-radius:4px; }}
        QProgressBar::chunk {{
            border-radius:4px;
            background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {color}99, stop:1 {color});
        }}
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
            "השוואה להיסטוריה — מוצא תקופות עבר עם דפוסים דומים ובודק אילו תקיפות "
            "קרו אחריהן.",
            11, color=P.TXT3,
        ))
        ctrl.addStretch()

        self._predict_btn = QPushButton("▶  חיזוי")
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

        self._info_lbl = _lbl("לחצו על חיזוי כדי למצוא דפוסי עבר דומים.", 11, color=P.TXT3)
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
        self._info_lbl.setText("טוענים נתונים היסטוריים ומחפשים דמיון…")
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
            self._info_lbl.setText(f"שגיאה: {exc}")
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
            f"נתונים מ-{r['lookback_used']} ימים  |  נמצאו {k} תקופות דומות  |  "
            f"ב-{n_atk}/{k} מהן הייתה תקיפה תוך 14 ימים  |  "
            f"נתונים: {r['discourse_from']} → {r['discourse_to']}"
        )

        # ── Timing ────────────────────────────────────────────────────────────
        self._results_lay.addWidget(_lbl("תזמון", 11, bold=True, color=P.TXT3))

        days = r.get("days_estimate")
        days_range = r.get("days_range")
        if days is not None:
            timing_text = f"צפוי בעוד כ-{days:.0f} ימים"
            if days_range:
                timing_text += f"   (טווח: {days_range[0]:.0f} – {days_range[1]:.0f} ימים)"
        else:
            timing_text = "לא נמצאו תקיפות בתקופות הדומות"
        self._results_lay.addWidget(_lbl(timing_text, 16, bold=True, color=P.TXT))

        self._results_lay.addWidget(_hline())

        # ── Two columns: region + type ────────────────────────────────────────
        cols_row = QHBoxLayout()
        cols_row.setSpacing(32)

        region_col = QVBoxLayout()
        region_col.setSpacing(4)
        region_col.addWidget(_lbl("האזור הסביר ביותר", 10, bold=True, color=P.TXT3))
        for label, frac in r.get("region_dist", []):
            c = P.RED if frac >= 0.4 else (P.AMBER if frac >= 0.2 else P.TXT2)
            region_col.addWidget(_dist_bar(label, frac, c))

        type_col = QVBoxLayout()
        type_col.setSpacing(4)
        type_col.addWidget(_lbl("סוג המטרה הסביר ביותר", 10, bold=True, color=P.TXT3))
        for label, frac in r.get("type_dist", []):
            c = P.VIOLET if frac >= 0.4 else (P.INDIGO if frac >= 0.2 else P.TXT2)
            type_col.addWidget(_dist_bar(label, frac, c))
        if not r.get("type_dist"):
            type_col.addWidget(_lbl("אין נתוני סוג מטרה", 11, color=P.TXT3))

        cols_row.addLayout(region_col, 1)
        cols_row.addLayout(type_col, 1)
        cols_row.addStretch()
        self._results_lay.addLayout(cols_row)

        self._results_lay.addWidget(_hline())

        # ── Characteristics ────────────────────────────────────────────────────
        self._results_lay.addWidget(_lbl("מאפייני התקיפה הצפויה", 10, bold=True, color=P.TXT3))
        chars_row = QHBoxLayout()
        chars_row.setSpacing(32)
        for label, key, color in [
            ("צפויה שריפה",     "fire_rate",  P.RED),
            ("פגיעה מאושרת",    "hit_rate",   P.AMBER),
            ("חדירה עמוקה ≥3",  "deep_rate",  P.VIOLET),
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
            _lbl(f"תקופות דומות בעבר  ({len(analogs)} דפוסים)", 10, bold=True, color=P.TXT3)
        )

        tbl = QTableWidget(len(analogs), 6)
        tbl.setHorizontalHeaderLabels(["תאריך", "דמיון", "← תקיפה", "אזור/ים", "שריפה", "פגיעה"])
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.verticalHeader().setVisible(False)
        tbl.setAlternatingRowColors(True)
        tbl.setStyleSheet(f"""
            QTableWidget {{
                background:{P.CARD_BG}; color:{P.TXT};
                border:1px solid {P.CARD_BORDER}; border-radius:{P.RADIUS_MD}px;
                gridline-color:{P.DIVIDER}; font-family:{P.FONT_MONO}; font-size:11px;
            }}
            QHeaderView::section {{
                background:{P.SIDEBAR_BG}; color:{P.TXT2};
                border:none; border-bottom:1px solid {P.DIVIDER};
                padding:4px 8px; font-family:{P.FONT_STACK}; font-weight:700; font-size:10px;
            }}
            QTableWidget::item {{ padding:3px 8px; }}
            QTableWidget::item:alternate {{ background:{P.CARD_BG_HOVER}; }}
            QTableWidget::item:selected {{ background:{P.INDIGO_BG}; color:{P.TXT}; }}
        """)

        for i, a in enumerate(analogs):
            regions_str = ", ".join(dict.fromkeys(a.get("regions", [])))[:40] or "—"
            days_str    = f"בעוד {a['days_to_next']} ימים" if a.get("days_to_next") else "אין בטווח"
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
    ("significant", 10, 1): "אירוע משמעותי  (יום קדימה)",
    ("any_attack",   2, 3): "תקיפה כלשהי  (3 ימים קדימה)",
    ("any_attack",  10, 5): "תקיפה כלשהי  (5 ימים קדימה)",
}


class _RegimeCard(QWidget):
    """Single prediction card: label | prob bar | AUC | regime badge."""

    def __init__(self, pred: dict, parent=None):
        super().__init__(parent)
        self.setStyleSheet(S.card_qss("QWidget", radius=P.RADIUS_MD))
        S.apply_card_shadow(self, blur=28, alpha=75, y_offset=9)
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
            ood = _lbl("חריג", 9, bold=True, color=P.RED)
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
            f"QProgressBar {{ background:{P.INPUT_BG}; border:none; border-radius:2px; }}"
            f"QProgressBar::chunk {{ border-radius:2px; "
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {c}99, stop:1 {c}); }}"
        )
        lay.addWidget(bar)

        auc = pred.get("model_auc", float("nan"))
        auc_txt = f"דיוק {auc:.3f}" if auc == auc else "דיוק —"
        n   = pred.get("model_n", "?")
        lay.addWidget(_lbl(f"{auc_txt}  ·  מדגם={n}", 10, color=P.TXT3))

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

        self._train_btn = QPushButton("⚙  אימון לפי תקופות")
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

        self._predict_btn = QPushButton("▶  חיזוי")
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
        self._regime_banner.setStyleSheet(S.card_qss("QWidget", radius=P.RADIUS_MD))
        S.apply_card_shadow(self._regime_banner, blur=28, alpha=70, y_offset=9)
        banner_lay = QHBoxLayout(self._regime_banner)
        banner_lay.setContentsMargins(16, 8, 16, 8)
        banner_lay.setSpacing(16)
        self._regime_title_lbl = _lbl("המצב הנוכחי", 10, bold=True, color=P.TXT3)
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
            self._status_lbl.setText(f"האימון לפי תקופות בוצע  ●  {mtime}")
            self._status_lbl.setStyleSheet(
                f"color:{P.GREEN}; font-size:11px; font-weight:600; border:none;"
            )
        else:
            self._status_lbl.setText("עדיין לא בוצע אימון — לחצו קודם על 'אימון לפי תקופות'")
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
        self._status_lbl.setText("מזהים תקופות ומאמנים מודל לכל תקופה…")
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
                f"האימון הושלם  ●  {k} תקופות  ·  {n_models} מודלים  ·  "
                f"{bank.trained_on_}"
            )
            self._status_lbl.setStyleSheet(
                f"color:{P.GREEN}; font-size:11px; font-weight:600; border:none;"
            )
        except Exception as exc:
            self._status_lbl.setText(f"שגיאת אימון: {exc}")
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
            self._status_lbl.setText("יש לבצע קודם אימון לפי תקופות.")
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
            self._status_lbl.setText(f"שגיאת חיזוי: {exc}")
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
            self._regime_name_lbl.setText(f"מצב {rid}  {r_name}")
            self._regime_name_lbl.setStyleSheet(
                f"color:{r_color}; font-size:15px; font-weight:900; border:none;"
            )
            self._regime_desc_lbl.setText(r_desc)
            if valid[0].get("is_ood"):
                self._regime_desc_lbl.setText(r_desc + "  ·  ⚠ מצב חריג — מחוץ לתבנית המוכרת")

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
    level_he = {
        "Strong": "חזק", "Good": "טוב", "Moderate": "בינוני",
        "Limited": "מוגבל", "Unknown": "לא ידוע",
    }.get(level, level)

    w   = QWidget()
    w.setStyleSheet(
        f"background:{P.CARD_BG}; border:1px solid {level_color}; border-radius:{P.RADIUS_MD}px;"
    )
    lay = QHBoxLayout(w)
    lay.setContentsMargins(12, 8, 12, 8)
    lay.setSpacing(12)

    badge = _lbl(level_he, 11, bold=True, color=level_color)
    badge.setFixedWidth(72)
    lay.addWidget(badge)

    txt_w = _lbl(text, 11, color=P.TXT2)
    txt_w.setWordWrap(True)
    lay.addWidget(txt_w, 1)

    if auc is not None:
        meta = _lbl(f"{n} סימנ{'ים' if n != 1 else ''}  ·  דיוק {auc:.2f}", 10, color=P.TXT3)
        meta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(meta)

    return w


def _make_signal_card(si: dict) -> QWidget:
    tier  = si.get("tier") or "low"
    prob  = si.get("prob", 0.0)
    label = _he_label(si.get("label", "?"))
    tier_color = {"high": P.RED, "medium": P.AMBER, "low": P.GREEN}.get(tier, P.TXT3)

    w = QWidget()
    w.setStyleSheet(
        f"background:{P.CARD_BG}; "
        f"border-left:3px solid {tier_color}; "
        f"border-top:1px solid {P.CARD_BORDER}; "
        f"border-right:1px solid {P.CARD_BORDER}; "
        f"border-bottom:1px solid {P.CARD_BORDER}; "
        f"border-radius:{P.RADIUS_SM}px;"
    )
    lay = QVBoxLayout(w)
    lay.setContentsMargins(12, 8, 12, 8)
    lay.setSpacing(3)

    hdr = QHBoxLayout()
    hdr.setSpacing(8)
    hdr.addWidget(_lbl(label, 12, bold=True, color=P.TXT))
    base_rate = si.get("base_rate")
    prob_txt  = f"{int(prob * 100)}%"
    if base_rate is not None:
        prob_txt += f"   (רגיל {int(base_rate * 100)}%)"
    hdr.addWidget(_lbl(prob_txt, 12, bold=True, color=tier_color))
    hdr.addStretch()
    h_val  = si.get("best_h", "?")
    lb_val = si.get("best_lb", "?")
    auc_val = si.get("cv_auc", float("nan"))
    auc_s   = f"דיוק {auc_val:.2f}" if auc_val == auc_val else "דיוק —"
    hdr.addWidget(_lbl(f"טווח {h_val} ימים  ·  {auc_s}", 10, color=P.TXT3))
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
        "notable":  (P.AMBER,  "בולט"),
        "moderate": (P.TXT3,   "בינוני"),
        "seasonal": (P.INDIGO, "עונתי"),
    }
    badge_color, badge_text = badge_map.get(strength, (P.TXT3, strength))

    w = QWidget()
    w.setStyleSheet(
        f"background:{P.CARD_BG}; border:1px solid {P.CARD_BORDER}; border-radius:{P.RADIUS_SM}px;"
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
            f"QProgressBar {{ background:{P.INPUT_BG}; border:none; border-radius:2px; }}"
            f"QProgressBar::chunk {{ border-radius:2px; "
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {arr_c}99, stop:1 {arr_c}); }}"
        )
        rl.addWidget(bar)
        rl.addStretch()
        lay.addWidget(row)

    return w


def _make_data_gap_card(gap_days: int, attacks_through: str | None) -> QWidget:
    """
    Flags that the attack records themselves are stale even though discourse
    monitoring is current — this has happened repeatedly (ingestion stalls
    while news monitoring keeps running) and silently skews recurrence/gap
    reads if not called out.
    """
    w = QWidget()
    w.setStyleSheet(
        f"background:{P.RED_BG}; border:1px solid {P.RED}; border-radius:{P.RADIUS_SM}px;"
    )
    lay = QVBoxLayout(w)
    lay.setContentsMargins(12, 8, 12, 8)
    lay.setSpacing(2)
    lay.addWidget(_lbl("⚠  נתוני תקיפות לא מעודכנים", 11, bold=True, color=P.RED))
    thru = f" (עד {attacks_through})" if attacks_through else ""
    txt = _lbl(
        f"טבלת האירועים לא התעדכנה כבר {gap_days} ימים{thru} — "
        f"ניטור השיח ממשיך כרגיל, אבל תקיפות בפועל לא נרשמו מאז. "
        f"חישובי הזמן שמופיעים למטה כבר מתחשבים בפער הזה, "
        f"אך תחזיות המבוססות על קצב תקיפות אחרון עלולות להיות מוטות כלפי מטה.",
        10, color=P.TXT2,
    )
    txt.setWordWrap(True)
    lay.addWidget(txt)
    return w


def _make_near_miss_card(nm: dict) -> QWidget:
    label     = _he_label(nm.get("label", "?"))
    prob      = nm.get("prob", 0.0)
    base_rate = nm.get("base_rate")

    w = QWidget()
    w.setStyleSheet(
        f"background:{P.CARD_BG}; border:1px solid {P.CARD_BORDER}; border-radius:{P.RADIUS_SM}px;"
    )
    lay = QHBoxLayout(w)
    lay.setContentsMargins(12, 7, 12, 7)
    lay.setSpacing(8)
    lay.addWidget(_lbl(label, 11, bold=True, color=P.TXT))
    prob_txt = f"{int(prob * 100)}%"
    if base_rate is not None:
        prob_txt += f"  (רגיל {int(base_rate * 100)}%)"
    lay.addWidget(_lbl(prob_txt, 11, color=P.TXT2))
    lay.addStretch()
    lay.addWidget(_lbl("קרוב לסף אך לא חצה", 10, color=P.TXT3))
    return w


def _make_overdue_card(ov: dict) -> QWidget:
    label = _he_label(ov.get("label", "?"))
    cur   = ov.get("current_days", 0)
    med   = ov.get("median_days", 0)

    w = QWidget()
    w.setStyleSheet(
        f"background:{P.AMBER_BG}; border:1px solid {P.AMBER}; border-radius:{P.RADIUS_SM}px;"
    )
    lay = QHBoxLayout(w)
    lay.setContentsMargins(12, 7, 12, 7)
    lay.setSpacing(8)
    lay.addWidget(_lbl(label, 11, bold=True, color=P.TXT))
    lay.addWidget(_lbl(
        f"{cur} ימים מאז המקרה האחרון  ·  בדרך כלל חוזר כל כ-{int(med)} ימים",
        11, color=P.TXT2,
    ))
    lay.addStretch()
    return w


def _make_temporal_card(p: dict) -> QWidget:
    label = _he_label(p.get("label", "?"))

    w = QWidget()
    w.setStyleSheet(
        f"background:{P.CARD_BG}; border:1px solid {P.CARD_BORDER}; border-radius:{P.RADIUS_SM}px;"
    )
    lay = QVBoxLayout(w)
    lay.setContentsMargins(12, 8, 12, 8)
    lay.setSpacing(3)
    lay.addWidget(_lbl(label, 11, bold=True, color=P.TXT))

    dow = p.get("dow")
    if dow:
        lay.addWidget(_lbl(
            f"נוטה לקרות בימי {dow['day']}  ·  {int(dow['share'] * 100)}% מהמקרים",
            10, color=P.TXT2,
        ))

    gap = p.get("gap")
    if gap:
        status_he = {
            "overdue": "מאחר יחסית לרגיל",
            "recent":  "קרה לאחרונה",
            "typical": "בטווח הרגיל",
        }.get(gap["status"], "")
        lay.addWidget(_lbl(
            f"חוזר כל כ-{int(gap['median_days'])} ימים בממוצע  ·  "
            f"{gap['current_days']} ימים מאז האחרון  ·  {status_he}",
            10, color=P.TXT2,
        ))

    return w


def _make_cluster_card(c: dict) -> QWidget:
    members = "  +  ".join(c.get("members", []))

    w = QWidget()
    w.setStyleSheet(
        f"background:{P.INDIGO_BG}; border:1px solid {P.INDIGO}; border-radius:{P.RADIUS_SM}px;"
    )
    lay = QVBoxLayout(w)
    lay.setContentsMargins(12, 8, 12, 8)
    lay.setSpacing(3)
    lay.addWidget(_lbl(members, 11, bold=True, color=P.TXT))
    lay.addWidget(_lbl(
        f"עולים יחד עכשיו  ·  {c.get('n_active', 0)} מתוך {c.get('n_members', 0)} פעילים בו-זמנית",
        10, color=P.INDIGO_L,
    ))
    return w


class ModelInsightsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 4)
        root.setSpacing(0)

        self._toggle = QPushButton("למה התחזית הזאת?  ▼")
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
        self._toggle.setText("למה התחזית הזאת?  ▲" if checked else "למה התחזית הזאת?  ▼")
        self._content.setVisible(checked)

    def set_expanded(self, expanded: bool):
        """Force open/closed — used to auto-expand on a quiet reading, since
        that's exactly when the situation report carries the weight."""
        self._toggle.setChecked(expanded)
        self._on_toggle(expanded)

    def update_insights(self, insights: dict):
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        gap_days = insights.get("data_gap_days")
        if gap_days:
            self._content_lay.addWidget(_make_data_gap_card(
                gap_days, insights.get("attacks_data_through"),
            ))

        rel = insights.get("reliability", {})
        self._content_lay.addWidget(_make_reliability_card(rel))

        situation   = insights.get("situation", {})
        near_misses = situation.get("near_misses", [])
        overdue     = situation.get("overdue", [])
        if near_misses or overdue:
            self._content_lay.addWidget(_lbl("המצב הנוכחי", 10, bold=True, color=P.TXT3))
            for ov in overdue:
                self._content_lay.addWidget(_make_overdue_card(ov))
            for nm in near_misses:
                self._content_lay.addWidget(_make_near_miss_card(nm))

        sigs = insights.get("signal_insights", [])
        if sigs:
            self._content_lay.addWidget(_lbl("הסימנים העיקריים", 10, bold=True, color=P.TXT3))
            for si in sigs[:3]:
                self._content_lay.addWidget(_make_signal_card(si))

        combos = insights.get("notable_combos", [])
        if combos:
            self._content_lay.addWidget(_lbl("דפוסים בולטים", 10, bold=True, color=P.TXT3))
            for c in combos[:3]:
                self._content_lay.addWidget(_make_combo_card(c))

        patterns = insights.get("temporal_patterns", [])
        if patterns:
            self._content_lay.addWidget(_lbl("דפוסי זמן חוזרים", 10, bold=True, color=P.TXT3))
            for p in patterns[:4]:
                self._content_lay.addWidget(_make_temporal_card(p))

        clusters = insights.get("feature_clusters", [])
        if clusters:
            self._content_lay.addWidget(_lbl("אינדיקטורים שזזים יחד עכשיו", 10, bold=True, color=P.TXT3))
            for c in clusters:
                self._content_lay.addWidget(_make_cluster_card(c))

        infl = insights.get("feature_influence", [])
        if infl:
            self._content_lay.addWidget(_lbl("מה משפיע הכי הרבה", 10, bold=True, color=P.TXT3))
            self._content_lay.addWidget(_make_influence_section(infl[:6]))


# ─────────────────────────────────────────────────────────────────────────────
#  Intel Forecast tab
# ─────────────────────────────────────────────────────────────────────────────

_WARNING_CONFIG = {
    "CRITICAL": (P.RED,   P.RED_BG,   "⚠  מתח קיצוני — סימנים חזקים לתקיפה קרובה"),
    "HIGH":     (P.RED,   P.RED_BG,   "⚠  סיכון גבוה — סימנים משמעותיים לטווח הקרוב"),
    "ELEVATED": (P.AMBER, P.AMBER_BG, "⚡  מתח מוגבר — סימנים בינוניים לטווח הקרוב"),
    "LOW":      (P.GREEN, P.GREEN_BG, "↗  רגוע — נמצאו סימנים חלשים בלבד"),
    "NONE":     (P.TXT3,  P.INPUT_BG, "—  אין כרגע סימנים ברורים לטווח הקרוב"),
}

_DIM_LABELS = {
    "infrastructure": "תשתיות",
    "scale":          "היקף",
    "effects":        "השפעה",
    "baseline":       "כללי",
}


class _SignalPill(QWidget):
    def __init__(self, signal: dict, parent=None):
        super().__init__(parent)
        tier  = signal.get("tier") or "low"
        prob  = signal["prob"]
        label = _he_label(signal["label"])
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

        pill_txt = f"{label}  {int(prob * 100)}%"
        base_rate = signal.get("base_rate")
        if base_rate is not None:
            pill_txt += f"  (רגיל {int(base_rate * 100)}%)"
        txt = QLabel(pill_txt)
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
        no_sig = _lbl("אין סימן", 11, color=P.TXT3)
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
            _lbl("  אין סימנים ברורים", 11, color=P.TXT3)
        )
    else:
        for dim_key in DIMENSIONS:
            sigs = band["signals"].get(dim_key, [])
            if sigs:
                result_lay.addWidget(_make_dim_row(dim_key, sigs))

    result_lay.addWidget(_hline())


class TrainingProgressBridge(QObject):
    """
    Progress events are produced on a background thread (via asyncio.to_thread)
    but must update widgets on the GUI thread. Emitting a Qt signal from a
    worker thread is safe and Qt auto-queues delivery to the GUI thread as
    long as this object lives there — so this is the one thing worker code
    is handed as `progress_cb`.
    """
    progress = Signal(dict)


# Plain-language phase copy — (icon, title, subtitle). Written for someone
# with no ML background: no "AUC", no window notation, no model names.
_PHASE_META = {
    "loading":       ("📡", "אוספים נתונים",         "מביאים היסטוריית תקיפות ופעילות בחדשות"),
    "grid_search":   ("🔍", "מחפשים דפוסים",         "בודקים אילו טווחי זמן הכי טובים לניבוי כל סימן"),
    "correlation":   ("📈", "משווים סימנים",         "בודקים אילו דפוסי חדשות מתאימים לתקיפות אמיתיות"),
    "clustering":    ("🧩", "מקבצים סוגי תקיפות",    "ממיינים תקיפות עבר לקבוצות דומות"),
    "analysis_done": ("🧩", "מקבצים סוגי תקיפות",    "ממיינים תקיפות עבר לקבוצות דומות"),
    "preparing":     ("🔄", "מתכוננים",              "טוענים מחדש את הנתונים העדכניים"),
    "diagnostics":   ("🩺", "בודקים את התוצאות",      "מוודאים שכל דפוס אמיתי ולא מקרי"),
    "training":      ("🧠", "בונים את המודלים",       "מלמדים את המודל הסופי לזהות כל דפוס"),
    "done":          ("✅", "מוכן",                   "כל המודלים בנויים ומוכנים לחזות"),
}

# The pipeline's real phase keys repeat "loading"/"done" across its two
# sequential sub-runs (analysis, then bank training). The stepper collapses
# them into 7 stages a non-technical viewer can follow — and only ever moves
# forward, so that second "loading" doesn't look like it jumped backwards.
_STEPPER_STAGES = ["נתונים", "חיפוש", "השוואה", "קיבוץ", "בדיקה", "בנייה", "מוכן"]
_PHASE_TO_STAGE = {
    "loading": 0, "grid_search": 1, "correlation": 2, "clustering": 3,
    "analysis_done": 3, "preparing": 4, "diagnostics": 4, "training": 5, "done": 6,
}


def _quality_color(score: float | None) -> str:
    """Map a 0–1 model-quality score to a traffic-light color. Color alone
    carries the "how good" signal — no number or letter grade is shown."""
    if score is None or score != score:
        return P.TXT3
    if score >= 0.65:
        return P.GREEN
    if score >= 0.55:
        return P.AMBER
    return P.RED


class _TargetTile(QWidget):
    """One live tile per prediction target: a plain-language name plus a dot
    that smoothly morphs color as its result comes in (gray → green/amber/red).
    """

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background:{P.INPUT_BG}; border-radius:9px;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 10, 4)
        lay.setSpacing(6)
        self._dot = QLabel("●")
        lay.addWidget(self._dot)
        lay.addWidget(_lbl(label, 10, color=P.TXT2))

        self._color = QColor(P.TXT3)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(450)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_value)
        self._apply_color(self._color)

    def _apply_color(self, color: QColor):
        self._dot.setStyleSheet(
            f"color:{color.name()}; font-size:15px; border:none; background:transparent;"
        )

    def _on_anim_value(self, value):
        self._color = value
        self._apply_color(value)

    def set_score(self, score: float | None):
        target = QColor(_quality_color(score))
        if target == self._color:
            return
        self._anim.stop()
        self._anim.setStartValue(self._color)
        self._anim.setEndValue(target)
        self._anim.start()

    def reset(self):
        self._anim.stop()
        self._color = QColor(P.TXT3)
        self._apply_color(self._color)


class TrainingProgressPanel(QWidget):
    """
    Plain-language live view of the training pipeline, built for someone with
    no ML background: a stepper across the top always shows which of 7
    stages we're in and what's next, a single status line explains what that
    stage is doing in everyday words, and a color-only scoreboard lights up
    green/amber/red per signal as its result comes in.
    """

    # Overall progress bands (%) each phase maps its own step/total into —
    # grid_search dominates the true cost so it gets the widest band.
    _BANDS = {
        "loading":       (0, 3),
        "grid_search":   (3, 70),
        "correlation":   (70, 76),
        "clustering":    (76, 82),
        "analysis_done": (82, 82),
        "preparing":     (82, 85),
        "diagnostics":   (85, 92),
        "training":      (92, 100),
        "done":          (100, 100),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(S.card_qss("QWidget", radius=P.RADIUS_MD))
        S.apply_card_shadow(self, blur=30, alpha=70, y_offset=8)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        # ── Stepper: "which stage are we in, what's next" ───────────────────
        stepper_row = QHBoxLayout()
        stepper_row.setSpacing(4)
        self._stepper_lbls: list[QLabel] = []
        for i, name in enumerate(_STEPPER_STAGES):
            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignCenter)
            self._stepper_lbls.append(lbl)
            stepper_row.addWidget(lbl, 1)
            if i < len(_STEPPER_STAGES) - 1:
                stepper_row.addWidget(_lbl("›", 12, bold=True, color=P.TXT3))
        outer.addLayout(stepper_row)

        # ── Phase header: icon (pulses while active) + plain title/subtitle ──
        head = QHBoxLayout()
        head.setSpacing(8)
        self._phase_icon = _lbl("📡", 18)
        self._icon_effect = QGraphicsOpacityEffect(self._phase_icon)
        self._phase_icon.setGraphicsEffect(self._icon_effect)
        self._icon_pulse = QPropertyAnimation(self._icon_effect, b"opacity", self)
        self._icon_pulse.setDuration(900)
        self._icon_pulse.setStartValue(1.0)
        self._icon_pulse.setKeyValueAt(0.5, 0.45)
        self._icon_pulse.setEndValue(1.0)
        self._icon_pulse.setLoopCount(-1)
        self._icon_pulsing = False
        head.addWidget(self._phase_icon)

        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        self._phase_lbl    = _lbl("ממתין", 13, bold=True, color=P.TXT)
        self._subtitle_lbl = _lbl("", 10, color=P.TXT3)
        title_col.addWidget(self._phase_lbl)
        title_col.addWidget(self._subtitle_lbl)
        head.addLayout(title_col, 1)

        self._pct_lbl = _lbl("", 13, bold=True, color=P.INDIGO_L)
        head.addWidget(self._pct_lbl)
        outer.addLayout(head)

        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setValue(0)
        self._bar.setFixedHeight(8)
        self._bar.setTextVisible(False)
        self._bar.setStyleSheet(f"""
            QProgressBar {{ background:{P.INPUT_BG}; border:none; border-radius:4px; }}
            QProgressBar::chunk {{
                border-radius:4px;
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 {P.INDIGO}, stop:1 {P.VIOLET});
            }}
        """)
        outer.addWidget(self._bar)
        self._bar_anim = QPropertyAnimation(self._bar, b"value", self)
        self._bar_anim.setDuration(260)
        self._bar_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._activity_lbl = _lbl("", 11, color=P.TXT2)
        self._activity_lbl.setWordWrap(True)
        outer.addWidget(self._activity_lbl)

        # ── Scoreboard: one row per dimension, one tile per target ──────────
        board = QVBoxLayout()
        board.setSpacing(4)
        self._tiles: dict[str, _TargetTile] = {}
        for dim, targets in DIMENSIONS.items():
            row = QHBoxLayout()
            row.setSpacing(6)
            dim_lbl = _lbl(_DIM_LABELS.get(dim, dim.upper()), 9, bold=True, color=P.TXT3)
            dim_lbl.setFixedWidth(78)
            row.addWidget(dim_lbl)
            for t in targets:
                tile = _TargetTile(_he_label(TARGET_LABELS.get(t, t)))
                self._tiles[t] = tile
                row.addWidget(tile)
            row.addStretch()
            board.addLayout(row)
        outer.addLayout(board)

        self._max_stage = -1
        self._style_stepper()

    # ── Public ────────────────────────────────────────────────────────────

    def reset(self):
        self._bar_anim.stop()
        self._bar.setValue(0)
        self._phase_icon.setText("📡")
        self._phase_lbl.setText("ממתין")
        self._subtitle_lbl.setText("")
        self._pct_lbl.setText("")
        self._activity_lbl.setText("")
        self._icon_pulse.stop()
        self._icon_pulsing = False
        self._icon_effect.setOpacity(1.0)
        self._max_stage = -1
        self._style_stepper()
        for tile in self._tiles.values():
            tile.reset()

    def handle_event(self, ev: dict):
        phase = ev.get("phase", "")
        icon, phase_label, subtitle = _PHASE_META.get(phase, ("⚙", phase, ""))
        self._phase_icon.setText(icon)
        self._phase_lbl.setText(phase_label)
        self._subtitle_lbl.setText(subtitle)

        if phase == "done":
            self._icon_pulse.stop()
            self._icon_pulsing = False
            self._icon_effect.setOpacity(1.0)
        elif not self._icon_pulsing:
            self._icon_pulse.start()
            self._icon_pulsing = True

        stage = _PHASE_TO_STAGE.get(phase)
        if stage is not None and stage > self._max_stage:
            self._max_stage = stage
            self._style_stepper()

        step, total = ev.get("step"), ev.get("total")
        pct = self._overall_pct(phase, step, total)
        if pct is not None:
            self._animate_bar(int(pct * 10))
            self._pct_lbl.setText(f"{pct:.0f}%")

        target = ev.get("target") or ev.get("outcome")
        label  = ev.get("label") or (TARGET_LABELS.get(target, target) if target else None)
        label  = _he_label(label)
        self._activity_lbl.setText(self._activity_text(phase, label, step, total, ev))

        if target and ev.get("milestone"):
            self._update_tile(target, ev)

    # ── Internals ─────────────────────────────────────────────────────────

    def _activity_text(self, phase: str, label: str | None, step, total, ev: dict) -> str:
        progress = f"  ({step:,} מתוך {total:,})" if step and total and total > 1 else ""
        if phase == "grid_search" and label:
            return f"בודקים דפוסי זמן בשביל {label}{progress}"
        if phase == "diagnostics" and label:
            return f"בודקים שוב את {label}{progress}"
        if phase == "training" and label:
            status = ev.get("status")
            if status == "started":
                return f"בונים את המודל בשביל {label}{progress}"
            if status == "skipped":
                return f"אין מספיק נתונים בשביל {label} — מדלגים{progress}"
            return f"סיימנו את {label}{progress}"
        return ev.get("message") or _PHASE_META.get(phase, ("", "", ""))[2]

    def _overall_pct(self, phase: str, step, total) -> float | None:
        band = self._BANDS.get(phase)
        if band is None:
            return None
        lo, hi = band
        if not total:
            return float(lo)
        frac = max(0.0, min(1.0, (step or 0) / total))
        return lo + frac * (hi - lo)

    def _animate_bar(self, value: int):
        self._bar_anim.stop()
        self._bar_anim.setStartValue(self._bar.value())
        self._bar_anim.setEndValue(value)
        self._bar_anim.start()

    def _update_tile(self, target: str, ev: dict):
        tile = self._tiles.get(target)
        if tile is None:
            return
        if ev.get("status") == "skipped":
            tile.reset()
            return
        auc = ev.get("auc", ev.get("cv_auc"))
        if auc is not None:
            tile.set_score(float(auc))

    def _style_stepper(self):
        for i, lbl in enumerate(self._stepper_lbls):
            if i < self._max_stage:
                lbl.setText(f"✓ {_STEPPER_STAGES[i]}")
                lbl.setStyleSheet(
                    f"color:{P.GREEN}; font-family:{P.FONT_STACK}; font-size:10px; font-weight:700; "
                    f"background:{P.GREEN_BG}; border-radius:9px; padding:3px 8px;"
                )
            elif i == self._max_stage:
                lbl.setText(_STEPPER_STAGES[i])
                lbl.setStyleSheet(
                    f"color:#fff; font-family:{P.FONT_STACK}; font-size:10px; font-weight:800; "
                    f"background:{P.INDIGO}; border-radius:9px; padding:3px 8px;"
                )
            else:
                lbl.setText(_STEPPER_STAGES[i])
                lbl.setStyleSheet(
                    f"color:{P.TXT3}; font-family:{P.FONT_STACK}; font-size:10px; font-weight:600; "
                    f"background:{P.INPUT_BG}; border-radius:9px; padding:3px 8px;"
                )


class IntelForecastTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy          = False
        self._insights_panel: ModelInsightsPanel | None = None
        self._progress_bridge = TrainingProgressBridge()
        self._progress_bridge.progress.connect(self._on_progress_event)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # ── Model row — training lives here, separate from Predict ─────────────
        model_row = QHBoxLayout()
        model_row.setSpacing(10)

        self._train_btn = QPushButton("⚙  יצירת מודל")
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
        model_row.addWidget(self._train_btn)

        model_status_col = QVBoxLayout()
        model_status_col.setSpacing(1)
        self._model_status_lbl = _lbl("", 11, color=P.TXT3)
        model_status_col.addWidget(self._model_status_lbl)
        self._model_facts_lbl = _lbl("", 10, color=P.TXT3)
        model_status_col.addWidget(self._model_facts_lbl)
        model_row.addLayout(model_status_col)

        model_row.addStretch()
        root.addLayout(model_row)

        self._progress_panel = TrainingProgressPanel()
        self._progress_panel.setVisible(False)
        root.addWidget(self._progress_panel)

        root.addWidget(_hline())

        # ── Predict row ──────────────────────────────────────────────────────
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        self._predict_btn = QPushButton("▶  חיזוי")
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

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self._spinner = S.LoadingSpinner(size=14)
        status_row.addWidget(self._spinner)
        self._status_lbl = _lbl("", 11, color=P.TXT3)
        status_row.addWidget(self._status_lbl)
        status_row.addStretch()
        root.addLayout(status_row)

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
        self._tech_toggle = QPushButton("פרטים טכניים ▼")
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

    def _refresh_bank_status(self):
        mtime = get_bank_mtime()
        meta  = get_bank_meta()
        if mtime and meta:
            self._model_status_lbl.setText(f"מוכן  ●  עודכן {mtime}")
            self._model_status_lbl.setStyleSheet(
                f"color:{P.GREEN}; font-size:11px; font-weight:600; border:none;"
            )
            self._model_facts_lbl.setText(_format_model_facts(meta))
        else:
            self._model_status_lbl.setText("עדיין לא אומן — לחצו על 'יצירת מודל'")
            self._model_status_lbl.setStyleSheet(
                f"color:{P.TXT3}; font-size:11px; font-weight:600; border:none;"
            )
            self._model_facts_lbl.setText("")

    def set_busy(self, busy: bool):
        self._busy = busy
        self._train_btn.setEnabled(not busy)
        self._predict_btn.setEnabled(not busy)

    def reset_view(self):
        """Called on re-entry: only clears a stuck spinner, never the forecast results."""
        if not self._busy:
            self._spinner.stop()
            self._progress_panel.setVisible(False)

    def _on_progress_event(self, ev: dict):
        self._progress_panel.handle_event(ev)

    # ── Train ─────────────────────────────────────────────────────────────────

    def _on_train(self):
        if self._busy:
            return
        self.set_busy(True)
        self._model_status_lbl.setText("מאמנים את המודל — עדכון חי בהמשך")
        self._model_status_lbl.setStyleSheet(
            f"color:{P.TXT3}; font-size:11px; font-weight:600; border:none;"
        )
        self._progress_panel.reset()
        self._progress_panel.setVisible(True)
        asyncio.ensure_future(self._train_async())

    async def _train_async(self):
        emit = self._progress_bridge.progress.emit
        try:
            await asyncio.to_thread(run_multi_target_analysis, progress_cb=emit)

            def _train():
                import psycopg2 as _pg
                conn = _pg.connect(DB_DSN)
                try:
                    return train_bank(conn, progress_cb=emit)
                finally:
                    conn.close()

            await asyncio.to_thread(_train)
            self._refresh_bank_status()
        except Exception as exc:
            self._model_status_lbl.setText(f"שגיאה: {exc}")
            self._model_status_lbl.setStyleSheet(
                f"color:{P.RED}; font-size:11px; font-weight:600; border:none;"
            )
        finally:
            self._progress_panel.setVisible(False)
            self.set_busy(False)

    # ── Predict ───────────────────────────────────────────────────────────────

    def _on_predict(self):
        if self._busy:
            return
        if not FORECAST_BANK_PATH.exists():
            self._status_lbl.setText("יש ליצור מודל קודם.")
            return
        self.set_busy(True)
        self._spinner.start()
        self._status_lbl.setText("יוצרים תחזית…")
        asyncio.ensure_future(self._predict_async())

    async def _predict_async(self):
        try:
            def _run():
                import psycopg2 as _pg
                conn = _pg.connect(DB_DSN)
                try:
                    return predict_intel_full(conn)
                finally:
                    conn.close()

            forecast, insights = await asyncio.to_thread(_run)
            self._show_forecast(forecast, insights)
        except Exception as exc:
            self._status_lbl.setText(f"שגיאה: {exc}")
            self._status_lbl.setStyleSheet(
                f"color:{P.RED}; font-size:11px; font-weight:600; border:none;"
            )
        finally:
            self._spinner.stop()
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
        _make_band_section(fc.get("near_term", {}), "טווח קרוב  ·  1–3 ימים קדימה",        self._results_lay)
        _make_band_section(fc.get("weekly", {}),    "טווח שבועי  ·  4–10 ימים קדימה",       self._results_lay)
        _make_band_section(fc.get("extended", {}),  "טווח רחוק  ·  2–3 שבועות קדימה",       self._results_lay)

        # Model Insights panel — auto-expanded on a quiet reading, since
        # that's exactly when the situation report/patterns carry the weight.
        if insights and insights.get("has_insights"):
            self._insights_panel.update_insights(insights)
            self._results_lay.addWidget(self._insights_panel)
            self._insights_panel.setVisible(True)
            self._insights_panel.set_expanded(warning in ("NONE", "LOW"))
        else:
            self._insights_panel.setVisible(False)

        # Timestamp
        data_thru = fc.get("data_through", "?")
        gen_at    = fc.get("generated_at", "?")
        self._results_lay.addWidget(
            _lbl(
                f"נוצר ב-{gen_at}  ·  נתונים עד {data_thru}",
                10, color=P.TXT3,
            )
        )
        self._results_lay.addStretch()

        # Update technical panel
        q       = fc.get("quality", {})
        avg_auc = q.get("avg_auc", float("nan"))
        auc_s   = f"{avg_auc:.3f}" if avg_auc == avg_auc else "—"
        self._tech_lbl.setText(
            f"מודלים: {q.get('n_models', '?')}  ·  "
            f"דיוק ממוצע: {auc_s}  ·  "
            f"אומן ב: {q.get('trained_at', '?')}  ·  "
            f"נתונים עד: {data_thru}"
        )

        self._status_lbl.setText(f"התחזית מוכנה  ●  {gen_at}")
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
        self.setStyleSheet(S.window_bg_qss())
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(0)

        title = QLabel("תחזיות")
        title.setStyleSheet(
            f"color:{P.TXT}; font-family:{P.FONT_STACK}; font-size:21px; font-weight:700; border:none;"
        )
        root.addWidget(title)
        root.addSpacing(12)

        self._intel = IntelForecastTab()
        root.addWidget(self._intel, 1)

    # kept for external callers that may call set_busy on the page
    def _set_busy(self, busy: bool):
        self._intel.set_busy(busy)

    def reset_view(self):
        self._intel.reset_view()

