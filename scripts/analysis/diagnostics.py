"""
scripts/analysis/diagnostics.py
────────────────────────────────────────────────────────────────────────────
Four targeted diagnostics for the discourse→attack model:

  D1 – Regime detection: rolling positive-rate reveals non-stationarity
  D2 – Feature correlation: find collinear features that swap coefficients
  D3 – CV fold coefficient stability: how much do coefficients vary across folds?
  D4 – Rolling-window coefficient drift: do coefficients change sign over time?

Usage:
    python scripts/analysis/diagnostics.py [--out DIR]

    --out DIR   Output directory (default: data/analysis/diagnostics/)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.config import DB_DSN
from analysis.window_analysis import (
    load_attacks,
    load_discourse,
    aggregate_attacks,
    build_full_daily,
    build_dataset,
    DISCOURSE_COLS,
    CV_SPLITS,
)

# ─────────────────────────────────────────────────────────────────────────────
#  Configs to diagnose (top 3 model configs from predictor.py)
# ─────────────────────────────────────────────────────────────────────────────

DIAG_CONFIGS = [
    {"target": "significant", "lookback": 10, "horizon": 1},
    {"target": "any_attack",  "lookback": 2,  "horizon": 3},
    {"target": "any_attack",  "lookback": 10, "horizon": 5},
]

ROLL_WINDOW = 60   # rows per rolling training window
ROLL_STEP   = 14   # row step between windows


# ─────────────────────────────────────────────────────────────────────────────
#  D1 – Regime detection
# ─────────────────────────────────────────────────────────────────────────────

def diag_regime(df: pd.DataFrame, out_dir: Path) -> None:
    print(f"\n{'═'*60}")
    print("D1 – Regime detection (rolling positive rate)")
    print(f"{'═'*60}")
    print("  Rolling window = 30 rows; threshold = 0.70")

    rows = []
    for target in ("any_attack", "significant"):
        for lookback in [2, 10]:
            for horizon in [1, 3, 5]:
                X, y, dates = build_dataset(df, lookback, horizon, target=target)
                tmp = pd.DataFrame({"date": dates, "y": y.values})
                tmp["roll30"] = tmp["y"].rolling(30, min_periods=10).mean()

                crossing = tmp[tmp["roll30"] > 0.70]
                first_cross = (
                    crossing["date"].iloc[0].date() if not crossing.empty else None
                )
                rows.append({
                    "target": target,
                    "lookback": lookback,
                    "horizon": horizon,
                    "overall_pos_rate": float(y.mean()),
                    "regime_shift_date": str(first_cross) if first_cross else "never",
                })

    df_regime = pd.DataFrame(rows)

    print(f"\n  {'target':<14}  {'lb':>4}  {'h':>4}  {'pos_rate':>10}  {'regime_shift_date':>20}")
    print(f"  {'-'*58}")
    for _, r in df_regime.iterrows():
        print(
            f"  {r['target']:<14}  {r['lookback']:>4}  {r['horizon']:>4}"
            f"  {r['overall_pos_rate']:>10.1%}  {r['regime_shift_date']:>20}"
        )

    out_path = out_dir / "regime.csv"
    df_regime.to_csv(out_path, index=False)
    print(f"\n  Saved → {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  D2 – Feature correlation
# ─────────────────────────────────────────────────────────────────────────────

def diag_correlation(df: pd.DataFrame, out_dir: Path) -> None:
    print(f"\n{'═'*60}")
    print("D2 – Feature correlation matrix")
    print(f"{'═'*60}")

    corr = df[DISCOURSE_COLS].corr()

    header = f"  {'':16}" + "".join(f"  {f[:8]:>8}" for f in DISCOURSE_COLS)
    print(f"\n{header}")
    print(f"  {'-'*100}")
    for feat in DISCOURSE_COLS:
        row_str = f"  {feat:<16}"
        for feat2 in DISCOURSE_COLS:
            row_str += f"  {corr.loc[feat, feat2]:>8.2f}"
        print(row_str)

    print("\n  High-correlation pairs (|r| > 0.50):")
    found = False
    for i, f1 in enumerate(DISCOURSE_COLS):
        for f2 in DISCOURSE_COLS[i + 1:]:
            r = corr.loc[f1, f2]
            if abs(r) > 0.50:
                print(f"    {f1} × {f2}: r={r:+.3f}")
                found = True
    if not found:
        print("    none found")

    out_path = out_dir / "feature_corr.csv"
    corr.to_csv(out_path)
    print(f"\n  Saved → {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  D3 – CV fold coefficient stability
# ─────────────────────────────────────────────────────────────────────────────

def diag_fold_stability(df: pd.DataFrame, out_dir: Path) -> None:
    """
    Fit the model on each CV fold's training set and record coefficients.
    stability = |mean_coef| / std_coef — below 1.0 means the sign is not
    consistent across folds (pure noise).
    """
    print(f"\n{'═'*60}")
    print("D3 – CV fold coefficient stability")
    print(f"{'═'*60}")
    print("  stability = |mean| / std.  < 1.0 = sign inconsistent = UNSTABLE")

    all_rows = []

    for cfg in DIAG_CONFIGS:
        lb, h, target = cfg["lookback"], cfg["horizon"], cfg["target"]
        X, y, _ = build_dataset(df, lb, h, target=target)

        if len(np.unique(y)) < 2:
            print(f"\n  {target} lb={lb} h={h}: single class — skipped")
            continue

        tscv   = TimeSeriesSplit(n_splits=CV_SPLITS)
        scaler = StandardScaler()
        model  = LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs")

        fold_coefs: list[np.ndarray] = []
        for train_idx, _ in tscv.split(X):
            X_tr, y_tr = X.iloc[train_idx], y.iloc[train_idx]
            if len(np.unique(y_tr)) < 2:
                continue
            X_s = scaler.fit_transform(X_tr)
            model.fit(X_s, y_tr)
            fold_coefs.append(model.coef_[0].copy())

        if not fold_coefs:
            continue

        coef_arr = np.array(fold_coefs)
        means = coef_arr.mean(axis=0)
        stds  = coef_arr.std(axis=0)

        feat_names = [c.replace(f"_lb{lb}", "") for c in X.columns]

        print(f"\n  {target} lb={lb} h={h}  ({len(fold_coefs)}/{CV_SPLITS} valid folds)")
        print(f"  {'Feature':<16}  {'mean coef':>10}  {'std':>8}  {'stability':>10}  verdict")
        print(f"  {'-'*66}")

        for i, feat in enumerate(feat_names):
            m, s = means[i], stds[i]
            stab = abs(m) / s if s > 1e-9 else float("inf")
            verdict = "UNSTABLE" if stab < 1.0 else ("ok" if stab >= 2.0 else "marginal")
            print(
                f"  {feat:<16}  {m:>+10.4f}  {s:>8.4f}"
                f"  {stab:>10.2f}  {verdict}"
            )
            all_rows.append({
                "target": target, "lookback": lb, "horizon": h,
                "feature": feat,
                "coef_mean": m, "coef_std": s, "stability": stab,
            })

    if all_rows:
        out_path = out_dir / "coef_stability.csv"
        pd.DataFrame(all_rows).to_csv(out_path, index=False)
        print(f"\n  Saved → {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  D4 – Rolling window coefficient drift
# ─────────────────────────────────────────────────────────────────────────────

def diag_rolling_coefs(df: pd.DataFrame, out_dir: Path) -> None:
    """
    Train on fixed-size rolling windows and track each feature's coefficient.
    Sign flips > 1 across windows = the feature's direction reverses over time.
    """
    print(f"\n{'═'*60}")
    print(
        f"D4 – Rolling-window coefficient drift  "
        f"(win={ROLL_WINDOW} rows, step={ROLL_STEP} rows)"
    )
    print(f"{'═'*60}")

    all_rows: list[dict] = []

    for cfg in DIAG_CONFIGS:
        lb, h, target = cfg["lookback"], cfg["horizon"], cfg["target"]
        X_full, y_full, dates_full = build_dataset(df, lb, h, target=target)

        if len(X_full) < ROLL_WINDOW + 10:
            print(f"\n  {target} lb={lb} h={h}: not enough rows — skipped")
            continue

        scaler = StandardScaler()
        model  = LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs")
        feat_names = [c.replace(f"_lb{lb}", "") for c in X_full.columns]

        window_records: list[dict] = []

        for start in range(0, len(X_full) - ROLL_WINDOW, ROLL_STEP):
            end = start + ROLL_WINDOW
            X_w = X_full.iloc[start:end]
            y_w = y_full.iloc[start:end]

            if len(np.unique(y_w)) < 2:
                continue

            X_s = scaler.fit_transform(X_w)
            model.fit(X_s, y_w)

            mid_date = dates_full[start + ROLL_WINDOW // 2]
            for i, feat in enumerate(feat_names):
                window_records.append({
                    "target": target, "lookback": lb, "horizon": h,
                    "window_mid": mid_date,
                    "feature": feat,
                    "coef": float(model.coef_[0][i]),
                })

        all_rows.extend(window_records)

        if not window_records:
            continue

        wdf = pd.DataFrame(window_records)
        print(f"\n  {target} lb={lb} h={h}  ({wdf['window_mid'].nunique()} windows)")
        print(
            f"  {'Feature':<16}  {'min coef':>10}  {'max coef':>10}"
            f"  {'sign flips':>12}  verdict"
        )
        print(f"  {'-'*66}")

        for feat in feat_names:
            coefs = wdf[wdf["feature"] == feat]["coef"].values
            min_c, max_c = coefs.min(), coefs.max()
            signs  = np.sign(coefs)
            flips  = int(np.sum(signs[1:] != signs[:-1]))
            verdict = "DRIFTS" if flips > 1 else ("ok" if flips == 0 else "1 flip")
            print(
                f"  {feat:<16}  {min_c:>+10.3f}  {max_c:>+10.3f}"
                f"  {flips:>12}  {verdict}"
            )

    if all_rows:
        out_path = out_dir / "rolling_coefs.csv"
        pd.DataFrame(all_rows).to_csv(out_path, index=False)
        print(f"\n  Saved → {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

def run(out_dir: Path) -> None:
    print("Connecting to database …")
    conn        = psycopg2.connect(DB_DSN)
    raw_attacks = load_attacks(conn)
    disc        = load_discourse(conn)
    conn.close()

    print(
        f"  attacks:   {len(raw_attacks):,} rows "
        f"({raw_attacks['attack_date'].min().date()} → "
        f"{raw_attacks['attack_date'].max().date()})"
    )
    print(
        f"  discourse: {len(disc):,} rows "
        f"({disc['feature_date'].min().date()} → "
        f"{disc['feature_date'].max().date()})"
    )

    attacks_daily = aggregate_attacks(raw_attacks)
    df            = build_full_daily(attacks_daily, disc)
    out_dir.mkdir(parents=True, exist_ok=True)

    diag_regime(df, out_dir)
    diag_correlation(df, out_dir)
    diag_fold_stability(df, out_dir)
    diag_rolling_coefs(df, out_dir)

    print(f"\nAll done.  Output directory: {out_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out", default=None, help="Output directory")
    args    = parser.parse_args()
    root    = Path(__file__).resolve().parents[2]
    out_dir = Path(args.out) if args.out else root / "data" / "analysis" / "diagnostics"
    run(out_dir)


if __name__ == "__main__":
    main()
