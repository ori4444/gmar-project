"""
Multi-target analysis page — attack profiles, feature-outcome correlations,
and best-model windows per outcome.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from . import palette as P

_SCRIPTS = str(Path(__file__).resolve().parents[2])
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

_ANALYSIS_DIR = Path(__file__).resolve().parents[3] / "data" / "analysis"


def _lbl(text: str, size: int = 12, bold: bool = False, color: str | None = None) -> QLabel:
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


def _item(text: str, align: Qt.AlignmentFlag = Qt.AlignCenter) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    it.setTextAlignment(align)
    return it


# ─────────────────────────────────────────────────────────────────────────────
#  Profile card
# ─────────────────────────────────────────────────────────────────────────────

_SHOWN_BINARY = [
    ("any_attack",   "Any Attack"),
    ("significant",  "Significant"),
    ("any_fire",     "Fire"),
    ("deep_strike",  "Deep Strike"),
    ("any_hit",      "Confirmed Hit"),
]
_SHOWN_COUNTS = [
    ("attack_count", "Avg attacks"),
    ("max_depth",    "Avg depth"),
]


class ProfileCard(QWidget):
    def __init__(self, profile: dict, parent=None):
        super().__init__(parent)
        self.setFixedSize(224, 300)
        self.setStyleSheet(f"""
            ProfileCard {{
                background:{P.INPUT_BG}; border:1px solid {P.DIVIDER};
                border-radius:12px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(5)

        # Title
        lay.addWidget(_lbl(f"Profile {profile['cluster']}", 13, bold=True, color=P.TXT))
        lay.addWidget(_lbl(f"{profile.get('n', 0)} day-windows", 10, color=P.TXT3))
        lay.addWidget(_hline())

        # Feature pattern
        dominant   = profile.get("dominant", [])
        suppressed = profile.get("suppressed", [])
        if dominant:
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(_lbl("↑", 11, bold=True, color=P.GREEN))
            lbl = _lbl(", ".join(dominant[:3]), 10)
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)
            lay.addLayout(row)
        if suppressed:
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(_lbl("↓", 11, bold=True, color=P.RED))
            lbl = _lbl(", ".join(suppressed[:2]), 10)
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)
            lay.addLayout(row)
        if not dominant and not suppressed:
            lay.addWidget(_lbl("Neutral / mixed", 10, color=P.TXT3))

        lay.addWidget(_hline())

        # Binary outcome rates
        outcomes = profile.get("outcomes", {})
        for key, label in _SHOWN_BINARY:
            val = outcomes.get(key)
            if val is None:
                continue
            c = P.RED if val >= 0.6 else (P.AMBER if val >= 0.35 else P.GREEN)
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(_lbl(label, 10, color=P.TXT3), 1)
            row.addWidget(_lbl(f"{val:.0%}", 11, bold=True, color=c))
            lay.addLayout(row)

        # Regression averages
        for key, label in _SHOWN_COUNTS:
            val = outcomes.get(key)
            if val is None:
                continue
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(_lbl(label, 10, color=P.TXT3), 1)
            row.addWidget(_lbl(f"{val:.1f}", 11, bold=True, color=P.TXT2))
            lay.addLayout(row)

        lay.addStretch()


# ─────────────────────────────────────────────────────────────────────────────
#  Main page
# ─────────────────────────────────────────────────────────────────────────────

class MultiTargetPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._setup_ui()
        self._load_results()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(10)
        title = QLabel("Multi-Target Analysis")
        title.setStyleSheet(
            f"color:{P.TXT}; font-size:22px; font-weight:900; border:none;"
        )
        hdr.addWidget(title)
        hdr.addStretch()

        self._run_btn = QPushButton("▶  Run Analysis")
        self._run_btn.setFixedSize(150, 38)
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.setStyleSheet(f"""
            QPushButton {{
                background:{P.INDIGO}; color:#fff;
                border:none; border-radius:10px;
                font-size:13px; font-weight:800;
            }}
            QPushButton:hover {{ background:{P.INDIGO_D}; }}
            QPushButton:disabled {{ background:{P.INPUT_BG}; color:{P.TXT3}; }}
        """)
        self._run_btn.clicked.connect(self._on_run)
        hdr.addWidget(self._run_btn)
        root.addLayout(hdr)

        self._status = _lbl("No results yet — click Run Analysis to start.", 12)
        root.addWidget(self._status)
        root.addWidget(_hline())

        # Attack profiles strip
        root.addWidget(_lbl("ATTACK PROFILES", 11, bold=True, color=P.TXT3))
        root.addWidget(_lbl(
            "K-means on 10-day discourse windows → what kinds of attacks follow?",
            11, color=P.TXT3,
        ))

        prof_scroll = QScrollArea()
        prof_scroll.setWidgetResizable(True)
        prof_scroll.setFixedHeight(316)
        prof_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        prof_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        prof_scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        self._profiles_inner = QWidget()
        self._profiles_lay   = QHBoxLayout(self._profiles_inner)
        self._profiles_lay.setContentsMargins(0, 4, 0, 4)
        self._profiles_lay.setSpacing(12)
        self._profiles_lay.addWidget(_lbl("No profiles yet.", 12, color=P.TXT3))
        self._profiles_lay.addStretch()
        prof_scroll.setWidget(self._profiles_inner)
        root.addWidget(prof_scroll)

        root.addWidget(_hline())

        # Tabs: correlation + best models
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border:1px solid {P.DIVIDER}; border-radius:8px;
                background:{P.INPUT_BG};
            }}
            QTabBar::tab {{
                background:transparent; color:{P.TXT2};
                padding:6px 16px; font-size:12px; font-weight:700;
                border:none; border-bottom:2px solid transparent;
            }}
            QTabBar::tab:selected {{ color:{P.INDIGO}; border-bottom:2px solid {P.INDIGO}; }}
        """)

        self._corr_table   = self._make_table()
        self._models_table = self._make_table()
        self._tabs.addTab(self._corr_table,   "Feature × Outcome Correlation")
        self._tabs.addTab(self._models_table, "Best Model per Outcome")
        root.addWidget(self._tabs, 1)

    def _make_table(self) -> QTableWidget:
        t = QTableWidget()
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.verticalHeader().setVisible(False)
        t.setAlternatingRowColors(True)
        t.setStyleSheet(f"""
            QTableWidget {{
                background:{P.INPUT_BG}; color:{P.TXT};
                border:none; gridline-color:{P.DIVIDER}; font-size:12px;
            }}
            QHeaderView::section {{
                background:{P.BG}; color:{P.TXT2};
                border:none; border-bottom:1px solid {P.DIVIDER};
                padding:5px 8px; font-weight:700; font-size:11px;
            }}
            QTableWidget::item {{ padding:4px 8px; }}
            QTableWidget::item:selected {{ background:{P.INDIGO}; color:#fff; }}
            QTableWidget::item:alternate {{ background:{P.BG}; }}
        """)
        return t

    # ── Result loading ────────────────────────────────────────────────────────

    def _load_results(self):
        loaded = []

        profiles_path = _ANALYSIS_DIR / "attack_profiles.json"
        if profiles_path.exists():
            with open(profiles_path, encoding="utf-8") as f:
                self._update_profiles(json.load(f))
            loaded.append("profiles")

        corr_path = _ANALYSIS_DIR / "feature_outcome_corr.csv"
        if corr_path.exists():
            self._update_corr(pd.read_csv(corr_path, index_col=0))
            loaded.append("correlation")

        results_path = _ANALYSIS_DIR / "multi_target_results.csv"
        if results_path.exists():
            self._update_models(pd.read_csv(results_path))
            loaded.append("best models")

        if loaded:
            self._status.setText(f"Loaded: {', '.join(loaded)}. Click Run Analysis to refresh.")

    def _update_profiles(self, profiles: list[dict]):
        while self._profiles_lay.count():
            item = self._profiles_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not profiles:
            self._profiles_lay.addWidget(_lbl("No profiles.", 12, color=P.TXT3))
        else:
            for p in profiles:
                self._profiles_lay.addWidget(ProfileCard(p))
        self._profiles_lay.addStretch()

    def _update_corr(self, corr: pd.DataFrame):
        t = self._corr_table
        outcomes = list(corr.columns)
        features = list(corr.index)

        t.setRowCount(len(features))
        t.setColumnCount(1 + len(outcomes))
        t.setHorizontalHeaderLabels(["Feature"] + outcomes)

        for r, feat in enumerate(features):
            t.setItem(r, 0, _item(feat, Qt.AlignLeft | Qt.AlignVCenter))
            for c, oc in enumerate(outcomes):
                try:
                    v = float(corr.loc[feat, oc])
                    if v != v:  # NaN check
                        v = 0.0
                except (TypeError, ValueError):
                    v = 0.0
                cell = _item(f"{v:+.3f}")
                intensity = min(int(abs(v) * 220 * 2), 200)
                if v > 0.05:
                    cell.setBackground(QColor(0, 160, 60, intensity // 2))
                    cell.setForeground(QColor(P.GREEN))
                elif v < -0.05:
                    cell.setBackground(QColor(200, 50, 50, intensity // 2))
                    cell.setForeground(QColor(P.RED))
                t.setItem(r, c + 1, cell)

        t.resizeColumnsToContents()

    def _update_models(self, results: pd.DataFrame):
        t = self._models_table
        t.setColumnCount(7)
        t.setHorizontalHeaderLabels(
            ["Outcome", "Type", "Best LB", "Best H", "Score", "±Std", "Folds"]
        )

        rows = []
        for outcome, grp in results.groupby("outcome"):
            typ    = grp["type"].iloc[0]
            metric = "auc" if typ == "binary" else "r2"
            std_col = metric + "_std"
            valid = grp.dropna(subset=[metric])
            if "n_folds" in valid.columns:
                valid = valid[valid["n_folds"] >= 3]
            if valid.empty:
                continue
            best = valid.loc[valid[metric].idxmax()]
            rows.append((
                outcome, typ,
                int(best["lookback"]), int(best["horizon"]),
                float(best[metric]),
                float(best[std_col]) if std_col in best.index else float("nan"),
                int(best.get("n_folds", 0)),
            ))

        # Sort: binary first by AUC desc, regression by R² desc
        rows.sort(key=lambda r: (r[1] != "binary", -r[4]))
        t.setRowCount(len(rows))

        for r, (outcome, typ, lb, h, score, std, folds) in enumerate(rows):
            t.setItem(r, 0, _item(outcome, Qt.AlignLeft | Qt.AlignVCenter))
            typ_cell = _item(typ)
            typ_cell.setForeground(
                QColor(P.INDIGO) if typ == "binary" else QColor(P.AMBER)
            )
            t.setItem(r, 1, typ_cell)
            t.setItem(r, 2, _item(str(lb)))
            t.setItem(r, 3, _item(str(h)))

            score_cell = _item(f"{score:.3f}")
            threshold  = 0.55 if typ == "binary" else 0.1
            score_cell.setForeground(
                QColor(P.GREEN) if score > threshold else QColor(P.RED)
            )
            t.setItem(r, 4, score_cell)
            t.setItem(r, 5, _item(f"±{std:.3f}" if not pd.isna(std) else "—"))
            t.setItem(r, 6, _item(str(folds)))

        t.resizeColumnsToContents()

    # ── Run ───────────────────────────────────────────────────────────────────

    def _on_run(self):
        if self._busy:
            return
        self._busy = True
        self._run_btn.setEnabled(False)
        self._status.setText("Running analysis… this may take a few minutes.")
        asyncio.ensure_future(self._run_async())

    async def _run_async(self):
        try:
            def _do():
                from analysis.multi_target_analysis import run
                run()

            await asyncio.to_thread(_do)
            self._load_results()
            self._status.setText("Analysis complete. Results updated.")
        except Exception as exc:
            self._status.setText(f"Error: {exc}")
        finally:
            self._busy = False
            self._run_btn.setEnabled(True)
