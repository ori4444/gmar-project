"""
scripts/analysis/validate_regimes.py
────────────────────────────────────────────────────────────────────────────
Four validation checks for the regime model bank:

  V1 — Silhouette grid: is k=3 actually the best number of regimes?
  V2 — Temporal coherence: are regimes contiguous / stable over time?
  V3 — Feature profiles: do regimes differ meaningfully in discourse?
  V4 — Predictive lift: do per-regime AUCs beat the global baseline?

Usage:
    python scripts/analysis/validate_regimes.py [--k-max 6]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from sklearn.cluster import KMeans
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, silhouette_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.config import DB_DSN
from analysis.window_analysis import (
    load_attacks, load_discourse,
    aggregate_attacks, build_full_daily,
    build_dataset, DISCOURSE_COLS, CV_SPLITS,
)
from analysis.regime_models import (
    RegimeModelBank, RegimeDetector,
    BANK_PATH, REGIME_FEATURES, REGIME_ROLL,
    MODEL_CONFIGS, MIN_REGIME_N,
    _select_model_cols, _cv_auc,
)

K_MAX_DEFAULT = 6


# ─────────────────────────────────────────────────────────────────────────────
#  V1 — Silhouette grid
# ─────────────────────────────────────────────────────────────────────────────

def v1_silhouette(df: pd.DataFrame, k_max: int) -> None:
    print(f"\n{'═'*60}")
    print("V1 — Silhouette score vs. k")
    print(f"{'═'*60}")

    cols  = [c for c in REGIME_FEATURES if c in df.columns]
    scaler = StandardScaler()
    X = scaler.fit_transform(
        df[cols]
        .rolling(REGIME_ROLL, min_periods=max(5, REGIME_ROLL // 3))
        .mean()
        .fillna(0.0)
        .values
    )

    bar_width = 30
    best_k, best_s = 2, -1.0

    print(f"\n  {'k':>3}  {'silhouette':>11}  {'inertia':>10}  bar")
    print(f"  {'-'*55}")

    for k in range(2, k_max + 1):
        if k >= len(X) // MIN_REGIME_N:
            print(f"  {k:>3}  {'(not enough data)':>11}")
            continue
        km     = KMeans(n_clusters=k, random_state=0, n_init=10)
        labels = km.fit_predict(X)
        if len(np.unique(labels)) < 2:
            continue
        s   = silhouette_score(X, labels, sample_size=min(1000, len(X)))
        bar = "█" * int(max(0, s) * bar_width)
        print(f"  {k:>3}  {s:>11.4f}  {km.inertia_:>10.1f}  {bar}")
        if s > best_s:
            best_k, best_s = k, s

    print(f"\n  → Best k = {best_k}  (silhouette = {best_s:.4f})")
    if best_k == 3:
        print("    ✓ k=3 confirmed as best")
    else:
        print(f"    ⚠  current bank uses k=3 but silhouette prefers k={best_k}")


# ─────────────────────────────────────────────────────────────────────────────
#  V2 — Temporal coherence
# ─────────────────────────────────────────────────────────────────────────────

def v2_temporal(df: pd.DataFrame, bank: RegimeModelBank) -> None:
    print(f"\n{'═'*60}")
    print("V2 — Temporal coherence (regime assignment over time)")
    print(f"{'═'*60}")

    labels, _, _ = bank.detector.assign_series(df)
    regime_syms  = {-1: "·", 0: "0", 1: "1", 2: "2", 3: "3", 4: "4", 5: "5"}

    df2 = df.copy()
    df2["regime"] = labels

    # Month-by-month row: show 7-day blocks
    months = df2.groupby(df2["date"].dt.to_period("M"))
    print()
    for period, grp in months:
        row    = grp["regime"].values
        chunks = [row[i:i+7] for i in range(0, len(row), 7)]
        weeks  = "  ".join(
            "".join(regime_syms.get(r, "?") for r in c) for c in chunks
        )
        counts = {r: int((row == r).sum()) for r in np.unique(row) if r != -1}
        count_str = "  ".join(f"R{r}={n}d" for r, n in sorted(counts.items()))
        print(f"  {str(period):<8}  {weeks:<50}  {count_str}")

    # Transition count: how many times does the regime change day-to-day?
    valid   = labels[labels != -1]
    changes = int((np.diff(valid) != 0).sum())
    pct     = changes / max(len(valid) - 1, 1) * 100
    print(f"\n  Day-to-day transitions: {changes} / {len(valid)-1} days  ({pct:.1f}%)")
    if pct < 15:
        print("  ✓ Regimes are stable (low transition rate)")
    elif pct < 35:
        print("  ~ Moderate transitions — some mixing, still meaningful")
    else:
        print("  ⚠  High transition rate — regimes may be noisy")


# ─────────────────────────────────────────────────────────────────────────────
#  V3 — Feature profiles
# ─────────────────────────────────────────────────────────────────────────────

def v3_profiles(df: pd.DataFrame, bank: RegimeModelBank) -> None:
    print(f"\n{'═'*60}")
    print("V3 — Discourse feature profiles per regime")
    print(f"{'═'*60}")

    labels, _, _ = bank.detector.assign_series(df)
    df2          = df.copy()
    df2["regime"] = labels

    cols = [c for c in DISCOURSE_COLS if c in df2.columns]
    gdf  = df2[df2["regime"] != -1].groupby("regime")[cols].mean()

    # Normalise per feature (0→1) so we can compare across features
    norm = (gdf - gdf.min()) / (gdf.max() - gdf.min() + 1e-9)

    bar_max = 20
    print()
    for feat in cols:
        row_vals = []
        for r in sorted(gdf.index):
            raw  = gdf.loc[r, feat]
            nval = norm.loc[r, feat]
            bar  = "█" * int(nval * bar_max)
            row_vals.append(f"R{r}={raw:5.2f} {bar:<{bar_max}}")
        print(f"  {feat:<16}  {'  |  '.join(row_vals)}")

    # Also show attack rate per regime
    if "attack_count" in df2.columns:
        print()
        atk = df2[df2["regime"] != -1].groupby("regime")["attack_count"].agg(
            ["mean", lambda x: (x > 0).mean()]
        )
        atk.columns = ["avg_attacks_per_day", "days_with_attack"]
        for r, row in atk.iterrows():
            print(f"  R{r}: avg_attacks/day={row['avg_attacks_per_day']:.2f}  "
                  f"days_with_attack={row['days_with_attack']:.0%}")


# ─────────────────────────────────────────────────────────────────────────────
#  V4 — Predictive lift vs. global baseline
# ─────────────────────────────────────────────────────────────────────────────

def v4_lift(df: pd.DataFrame, bank: RegimeModelBank) -> None:
    print(f"\n{'═'*60}")
    print("V4 — Per-regime AUC vs. global baseline")
    print(f"{'═'*60}")

    labels, _, _ = bank.detector.assign_series(df)
    date_to_regime = dict(zip(df["date"], labels))

    print(f"\n  {'target':<12}  {'lb':>3}  {'h':>3}  "
          f"{'global AUC':>10}  {'regime':>7}  {'regime AUC':>11}  {'lift':>6}  {'n':>5}")
    print(f"  {'-'*72}")

    for cfg in MODEL_CONFIGS:
        lb, h, target = cfg["lookback"], cfg["horizon"], cfg["target"]

        X_all, y_all, dates = build_dataset(df, lb, h, target=target)
        X_red = _select_model_cols(X_all)

        # Global AUC (train on full dataset)
        g_auc, g_std = _cv_auc(X_red, y_all)

        # Per-regime AUC (from bank)
        pred_regimes = np.array([date_to_regime.get(d, -1) for d in dates], dtype=int)

        first = True
        for r in sorted(set(pred_regimes) - {-1}):
            mask = pred_regimes == r
            Xr   = X_red[mask]
            yr   = y_all[mask]

            key = (r, target, lb, h)
            if key in bank.models:
                r_auc = bank.models[key]["auc_cv"]
                r_std = bank.models[key]["auc_std"]
            elif len(Xr) >= MIN_REGIME_N and len(np.unique(yr)) >= 2:
                r_auc, r_std = _cv_auc(Xr, yr)
            else:
                r_auc, r_std = float("nan"), float("nan")

            lift = r_auc - g_auc if not (np.isnan(r_auc) or np.isnan(g_auc)) else float("nan")
            lift_tag = f"{lift:>+.3f}" if not np.isnan(lift) else "   nan"
            flag     = " ✓" if lift > 0.02 else ("  ~" if abs(lift) <= 0.02 else " ✗")

            global_col = f"{g_auc:.3f}±{g_std:.3f}" if first else " " * 10
            print(
                f"  {target:<12}  {lb:>3}  {h:>3}  "
                f"{global_col:>10}  R{r:>5}  "
                f"{r_auc:>7.3f}±{r_std:.3f}  "
                f"{lift_tag}{flag}  {mask.sum():>5}"
            )
            first = False

    print(f"\n  ✓ = regime AUC beats global by >0.02")
    print(f"  ~ = within noise (|lift| ≤ 0.02)")
    print(f"  ✗ = regime AUC worse than global")


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

def run(k_max: int) -> None:
    if not BANK_PATH.exists():
        print("No trained bank found. Run: python regime_models.py train")
        return

    bank = RegimeModelBank.load()

    print("Connecting to database …")
    conn          = psycopg2.connect(DB_DSN)
    raw_attacks   = load_attacks(conn)
    disc          = load_discourse(conn)
    conn.close()

    attacks_daily = aggregate_attacks(raw_attacks)
    df            = build_full_daily(attacks_daily, disc)
    print(f"  {len(df)} rows  "
          f"{df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}")

    v1_silhouette(df, k_max)
    v2_temporal(df, bank)
    v3_profiles(df, bank)
    v4_lift(df, bank)

    print(f"\n{'═'*60}")
    print("Validation complete.")
    print(f"{'═'*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--k-max", type=int, default=K_MAX_DEFAULT,
                        help=f"Max k to test in silhouette grid (default: {K_MAX_DEFAULT})")
    args = parser.parse_args()
    run(args.k_max)


if __name__ == "__main__":
    main()
