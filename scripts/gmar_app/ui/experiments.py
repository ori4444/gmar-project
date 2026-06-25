"""
Window Experiments page — run the time-window sensitivity analysis
and display results in-app without touching CSV files manually.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
from pathlib import Path

import pandas as pd

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import palette as P


# ─────────────────────────────────────────────────────────────────────────────
#  Shared widget helpers
# ─────────────────────────────────────────────────────────────────────────────

def _hline() -> QWidget:
    w = QWidget()
    w.setFixedHeight(1)
    w.setStyleSheet(f"background:{P.DIVIDER}; border-radius:1px;")
    return w


def _lbl(text: str, size: int = 12, bold: bool = False, color: str | None = None) -> QLabel:
    c = color or P.TXT2
    w = "700" if bold else "500"
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color:{c}; font-size:{size}px; font-weight:{w}; border:none;")
    return lbl


def _styled_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectRows)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setStretchLastSection(True)
    t.setStyleSheet(f"""
        QTableWidget {{
            background: {P.INPUT_BG}; color: {P.TXT};
            border: none; border-radius: 8px;
            gridline-color: {P.DIVIDER}; font-size: 12px;
        }}
        QHeaderView::section {{
            background: {P.BG}; color: {P.TXT2};
            border: none; border-bottom: 1px solid {P.DIVIDER};
            padding: 6px 8px; font-weight: 700; font-size: 11px;
        }}
        QTableWidget::item {{ padding: 4px 8px; }}
        QTableWidget::item:selected {{ background: {P.INDIGO}; color: #fff; }}
        QTableWidget::item:alternate {{ background: {P.BG}; }}
    """)
    return t


def _item(text: str, align: Qt.AlignmentFlag = Qt.AlignCenter) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    it.setTextAlignment(align)
    return it


def _color_item(value: float | None, text: str, threshold: float = 0.0) -> QTableWidgetItem:
    it = _item(text)
    if value is not None and value == value:   # not None, not NaN
        if value > threshold:
            it.setForeground(QColor(P.GREEN))
        elif value < 0:
            it.setForeground(QColor(P.RED))
    return it


def _captured(fn, *args, **kwargs):
    """Run fn(*args) with stdout captured; return (result, log_text)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
#  Phase toggle button
# ─────────────────────────────────────────────────────────────────────────────

class PhaseToggle(QPushButton):
    def __init__(self, num: str, parent=None):
        super().__init__(f"Phase {num}", parent)
        self.setCheckable(True)
        self.setChecked(True)
        self.setFixedSize(80, 34)
        self.setCursor(Qt.PointingHandCursor)
        self._refresh()
        self.toggled.connect(lambda _: self._refresh())

    def _refresh(self):
        if self.isChecked():
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {P.INDIGO}; color: #fff;
                    border: none; border-radius: 8px;
                    font-size: 11px; font-weight: 800;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {P.TXT3};
                    border: 1px solid {P.DIVIDER}; border-radius: 8px;
                    font-size: 11px; font-weight: 700;
                }}
                QPushButton:hover {{ border-color: {P.INDIGO}; color: {P.INDIGO}; }}
            """)


# ─────────────────────────────────────────────────────────────────────────────
#  Main page
# ─────────────────────────────────────────────────────────────────────────────

class WindowExperimentsPage(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._setup_ui()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        # Header
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(10)
        icon_lbl = QLabel("🔬")
        icon_lbl.setStyleSheet("font-size:24px; border:none;")
        title_lbl = QLabel("Window Experiments")
        title_lbl.setStyleSheet(
            f"color:{P.TXT}; font-size:22px; font-weight:900; border:none;"
        )
        hdr_row.addWidget(icon_lbl)
        hdr_row.addWidget(title_lbl)
        hdr_row.addStretch()
        hdr_row.addWidget(_lbl("Phases:", 12))

        self._phase_btns: dict[str, PhaseToggle] = {}
        for n in ("1", "2", "3", "4"):
            btn = PhaseToggle(n)
            self._phase_btns[n] = btn
            hdr_row.addWidget(btn)

        hdr_row.addSpacing(8)

        self._run_btn = QPushButton("▶  Run")
        self._run_btn.setFixedSize(110, 38)
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.setStyleSheet(f"""
            QPushButton {{
                background: {P.INDIGO}; color: #fff;
                border: none; border-radius: 10px;
                font-size: 14px; font-weight: 800;
            }}
            QPushButton:hover {{ background: {P.INDIGO_D}; }}
            QPushButton:disabled {{ background: {P.INPUT_BG}; color: {P.TXT3}; }}
        """)
        self._run_btn.clicked.connect(self._on_run)
        hdr_row.addWidget(self._run_btn)
        root.addLayout(hdr_row)

        self._status_lbl = _lbl("Select phases and click Run.", 12)
        root.addWidget(self._status_lbl)
        root.addWidget(_hline())

        # Body — log (left) + tabs (right)
        body = QHBoxLayout()
        body.setSpacing(16)

        log_layout = QVBoxLayout()
        log_layout.setSpacing(6)
        log_layout.addWidget(_lbl("LOG", 10, bold=True, color=P.TXT3))
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._log.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {P.INPUT_BG}; color: {P.TXT2};
                border: 1px solid {P.DIVIDER}; border-radius: 8px;
                font-size: 11px; font-family: Consolas, monospace;
            }}
        """)
        log_layout.addWidget(self._log, 1)
        log_w = QWidget()
        log_w.setFixedWidth(420)
        log_w.setLayout(log_layout)
        body.addWidget(log_w)

        # Result tabs
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {P.DIVIDER}; border-radius: 8px;
                background: {P.INPUT_BG};
            }}
            QTabBar::tab {{
                background: {P.BG}; color: {P.TXT2};
                border: 1px solid {P.DIVIDER}; border-bottom: none;
                border-radius: 6px 6px 0 0;
                padding: 6px 16px; font-size: 12px; font-weight: 700;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background: {P.INPUT_BG}; color: {P.TXT};
                border-bottom: 1px solid {P.INPUT_BG};
            }}
            QTabBar::tab:hover {{ color: {P.INDIGO}; }}
        """)

        self._tbl_combos = _styled_table([
            "Target", "Lookback (d)", "Horizon (d)",
            "AUC", "±AUC", "F1", "±F1", "Lift", "Pos%", "N",
        ])
        self._tabs.addTab(self._tbl_combos, "Top Combos")

        self._tbl_ccf = _styled_table(["Feature", "Best lag (d)", "Pearson r"])
        self._tabs.addTab(self._tbl_ccf, "Cross-Correlation")

        self._tbl_imp = _styled_table([
            "Lookback (d)", "Horizon (d)", "Target", "Feature", "Importance", "±std",
        ])
        self._tabs.addTab(self._tbl_imp, "Feature Importance")

        self._tbl_stab = _styled_table([
            "Target", "Lookback (d)", "Horizon (d)", "Early AUC", "Late AUC", "Δ",
        ])
        self._tabs.addTab(self._tbl_stab, "Stability")

        body.addWidget(self._tabs, 1)
        root.addLayout(body, 1)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self._status_lbl.setText(msg)

    def _append_log(self, text: str):
        self._log.appendPlainText(text)
        self._log.ensureCursorVisible()

    @staticmethod
    def _fmt(v) -> str:
        if v is None or v != v:
            return "—"
        if isinstance(v, float):
            return f"{v:.3f}"
        return str(v)

    # ── Run ───────────────────────────────────────────────────────────────────

    def _on_run(self):
        if self._running:
            return
        phases = "".join(n for n, btn in self._phase_btns.items() if btn.isChecked())
        if not phases:
            self._set_status("Select at least one phase.")
            return
        self._running = True
        self._run_btn.setEnabled(False)
        self._log.clear()
        asyncio.ensure_future(self._run_async(phases))

    async def _run_async(self, phases: str):
        try:
            import os, sys
            scripts = str(Path(__file__).resolve().parents[2])
            if scripts not in sys.path:
                sys.path.insert(0, scripts)

            import psycopg2
            from shared.config import DB_DSN
            from analysis.window_analysis import (
                load_attacks, load_discourse,
                aggregate_attacks, build_full_daily,
                phase1_crosscorr, phase2_grid,
                phase3_importance, phase4_stability,
            )

            out_dir = Path(__file__).resolve().parents[3] / "data" / "analysis"
            out_dir.mkdir(parents=True, exist_ok=True)

            # Load
            self._set_status("Connecting to database…")
            self._append_log("Connecting to database…")

            def _load():
                conn = psycopg2.connect(DB_DSN)
                atk  = load_attacks(conn)
                disc = load_discourse(conn)
                conn.close()
                return atk, disc

            raw_attacks, disc = await asyncio.to_thread(_load)
            self._append_log(f"  attacks:   {len(raw_attacks):,} rows")
            self._append_log(f"  discourse: {len(disc):,} rows")

            attacks_daily = aggregate_attacks(raw_attacks)
            df = build_full_daily(attacks_daily, disc)
            self._append_log(
                f"  range: {df['date'].iloc[0].date()} → "
                f"{df['date'].iloc[-1].date()}  ({len(df)} days)"
            )

            grid_results = None

            # Phase 1
            if "1" in phases:
                self._set_status("Phase 1 — Cross-correlation…")
                self._append_log("\n═══ Phase 1 — Cross-correlation ═══")
                ccf, log1 = await asyncio.to_thread(
                    _captured, phase1_crosscorr, df, out_dir
                )
                self._append_log(log1.strip())
                self._fill_ccf(ccf)
                self._tabs.setCurrentIndex(1)

            # Phase 2
            if "2" in phases:
                self._set_status("Phase 2 — Grid search (may take 1–2 min)…")
                self._append_log("\n═══ Phase 2 — ML grid search ═══")
                grid_results, log2 = await asyncio.to_thread(
                    _captured, phase2_grid, df, out_dir
                )
                self._append_log(log2.strip())
                self._fill_combos(grid_results)
                self._tabs.setCurrentIndex(0)

            # Phase 3
            if "3" in phases:
                if grid_results is None:
                    csv = out_dir / "window_analysis.csv"
                    if csv.exists():
                        grid_results = pd.read_csv(csv)
                    else:
                        self._append_log("\nPhase 3 skipped — run Phase 2 first.")

                if grid_results is not None:
                    self._set_status("Phase 3 — Feature importance…")
                    self._append_log("\n═══ Phase 3 — Feature importance ═══")
                    imp, log3 = await asyncio.to_thread(
                        _captured, phase3_importance, df, grid_results, out_dir
                    )
                    self._append_log(log3.strip())
                    if not imp.empty:
                        self._fill_importance(imp)
                        self._tabs.setCurrentIndex(2)

            # Phase 4
            if "4" in phases:
                self._set_status("Phase 4 — Temporal stability…")
                self._append_log("\n═══ Phase 4 — Stability ═══")
                stab, log4 = await asyncio.to_thread(
                    _captured, phase4_stability, df, out_dir
                )
                self._append_log(log4.strip())
                self._fill_stability(stab)
                self._tabs.setCurrentIndex(3)

            self._set_status("Done.")
            self._append_log("\n=== All phases complete ===")

        except Exception as exc:
            import traceback
            self._append_log(f"\nError: {type(exc).__name__}: {exc}")
            self._append_log(traceback.format_exc())
            self._set_status("Error — see log")
        finally:
            self._running = False
            self._run_btn.setEnabled(True)

    # ── Table fill ────────────────────────────────────────────────────────────

    def _fill_combos(self, df: pd.DataFrame):
        tbl = self._tbl_combos
        tbl.setRowCount(0)
        if df is None or df.empty:
            return

        top = (
            df.dropna(subset=["auc"])
            .sort_values("auc", ascending=False)
            .head(20)
            .reset_index(drop=True)
        )
        tbl.setRowCount(len(top))
        for i, row in top.iterrows():
            lift = row.get("lift_over_baseline", float("nan"))
            tbl.setItem(i, 0, _item(row["target"]))
            tbl.setItem(i, 1, _item(int(row["lookback"])))
            tbl.setItem(i, 2, _item(int(row["horizon"])))
            tbl.setItem(i, 3, _color_item(row["auc"], self._fmt(row["auc"]), threshold=0.5))
            tbl.setItem(i, 4, _item(self._fmt(row["auc_std"])))
            tbl.setItem(i, 5, _item(self._fmt(row["f1"])))
            tbl.setItem(i, 6, _item(self._fmt(row["f1_std"])))
            tbl.setItem(i, 7, _color_item(lift, self._fmt(lift)))
            tbl.setItem(i, 8, _item(f"{row['pos_rate']:.0%}"))
            tbl.setItem(i, 9, _item(int(row["n"])))
        tbl.resizeColumnsToContents()

    def _fill_ccf(self, ccf: pd.DataFrame):
        tbl = self._tbl_ccf
        tbl.setRowCount(0)
        if ccf is None or ccf.empty:
            return

        rows = []
        for feat in ccf["feature"].unique():
            sub = ccf[(ccf["feature"] == feat) & (ccf["lag"] > 0)]
            if sub.empty or sub["pearson_r"].isna().all():
                rows.append((feat, None, None))
            else:
                best = sub.loc[sub["pearson_r"].abs().idxmax()]
                rows.append((feat, int(best["lag"]), float(best["pearson_r"])))

        tbl.setRowCount(len(rows))
        for i, (feat, lag, r) in enumerate(rows):
            tbl.setItem(i, 0, _item(feat, Qt.AlignLeft | Qt.AlignVCenter))
            tbl.setItem(i, 1, _item(f"{lag}d" if lag is not None else "—"))
            r_text = f"{r:+.3f}" if r is not None else "—"
            tbl.setItem(i, 2, _color_item(r, r_text))
        tbl.resizeColumnsToContents()

    def _fill_importance(self, df: pd.DataFrame):
        tbl = self._tbl_imp
        tbl.setRowCount(0)
        if df is None or df.empty:
            return

        df = df.sort_values("importance_mean", ascending=False).reset_index(drop=True)
        tbl.setRowCount(len(df))
        for i, row in df.iterrows():
            imp = row["importance_mean"]
            tbl.setItem(i, 0, _item(int(row["lookback"])))
            tbl.setItem(i, 1, _item(int(row["horizon"])))
            tbl.setItem(i, 2, _item(row["target"]))
            tbl.setItem(i, 3, _item(row["feature"], Qt.AlignLeft | Qt.AlignVCenter))
            tbl.setItem(i, 4, _color_item(imp, f"{imp:+.4f}" if imp == imp else "—"))
            tbl.setItem(i, 5, _item(f"{row['importance_std']:.4f}"))
        tbl.resizeColumnsToContents()

    def _fill_stability(self, df: pd.DataFrame):
        tbl = self._tbl_stab
        tbl.setRowCount(0)
        if df is None or df.empty:
            return

        rows = []
        for target in ("any_attack", "significant"):
            early = df[(df["period"] == "early") & (df["target"] == target)]
            late  = df[(df["period"] == "late")  & (df["target"] == target)]

            top_e = set(
                early.dropna(subset=["auc"])
                .nlargest(5, "auc")[["lookback", "horizon"]]
                .itertuples(index=False, name=None)
            )
            top_l = set(
                late.dropna(subset=["auc"])
                .nlargest(5, "auc")[["lookback", "horizon"]]
                .itertuples(index=False, name=None)
            )
            for lb, h in sorted(top_e & top_l):
                e_vals = early.loc[(early["lookback"] == lb) & (early["horizon"] == h), "auc"].values
                l_vals = late.loc[(late["lookback"] == lb)  & (late["horizon"] == h), "auc"].values
                e = float(e_vals[0]) if len(e_vals) else float("nan")
                l = float(l_vals[0]) if len(l_vals) else float("nan")
                delta = l - e if (e == e and l == l) else float("nan")
                rows.append((target, int(lb), int(h), e, l, delta))

        tbl.setRowCount(len(rows))
        for i, (target, lb, h, e, l, delta) in enumerate(rows):
            tbl.setItem(i, 0, _item(target))
            tbl.setItem(i, 1, _item(lb))
            tbl.setItem(i, 2, _item(h))
            tbl.setItem(i, 3, _item(self._fmt(e)))
            tbl.setItem(i, 4, _item(self._fmt(l)))
            tbl.setItem(i, 5, _color_item(delta, self._fmt(delta)))
        tbl.resizeColumnsToContents()
