"""
scripts/analysis/experiment_improved.py
────────────────────────────────────────────────────────────────────────────
Tests targeted improvements to the significant/lb=10/h=1 model based on
three findings from diagnostics.py:

  1. Label saturation  — only h=1 is a real prediction task (pos_rate ~28%)
  2. Multicollinearity — drop 4 collinear/lagging features, keep 5
  3. Non-stationarity  — test training on recent N rows only

Collinear/lagging features dropped:
  pre_airdef   (r=0.71 with pre_drone)
  war_total    (r=0.80 with pre_drone)
  en_refinery  (lagging indicator, r=0.86 with en_attack)
  pre_uncert   (no cross-corr signal, UNSTABLE in D3)

Saved if it beats v1: data/analysis/model_significant_lb10_h1_v2.pkl

Usage:
    python scripts/analysis/experiment_improved.py
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
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
    ALL_FEATURE_COLS,
    CV_SPLITS,
)

# ─────────────────────────────────────────────────────────────────────────────

TARGET   = "significant"
LOOKBACK = 10
HORIZON  = 1

# Dropped features and reasons:
#   pre_airdef   — r=0.71 collinear with pre_drone
#   war_total    — r=0.80 collinear with pre_drone
#   en_refinery  — lagging indicator (attacks cause refinery discourse, not vice versa)
#   pre_uncert   — no cross-corr signal, UNSTABLE in D3
#   pre_airport  — same-day / hours-before signal per domain knowledge;
#                  not informative over a multi-day lookback window
REDUCED_FEATURES = [
    "pre_drone",
    "en_attack",
    "en_confirm",
    "war_ukr_ru",
    "depth_score",   # max depth of attacks in lookback window (1=border → 5=Siberia)
]

# None = all historical data
RECENT_WINDOWS = [90, 120, 180, 270, None]

MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "analysis"


# ─────────────────────────────────────────────────────────────────────────────
#  Evaluation
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate(X: pd.DataFrame, y: pd.Series) -> dict:
    """
    TimeSeriesSplit CV.
    Returns AUC, auc_std, avg_stability (|mean_coef|/std across folds), n.
    Conservative score = AUC - auc_std (penalises variance).
    """
    if len(np.unique(y)) < 2:
        return {"auc": float("nan"), "auc_std": float("nan"),
                "avg_stability": float("nan"), "n": len(X), "n_folds": 0}

    tscv   = TimeSeriesSplit(n_splits=CV_SPLITS)
    scaler = StandardScaler()
    model  = LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs")

    aucs: list[float]       = []
    fold_coefs: list[np.ndarray] = []

    for train_idx, test_idx in tscv.split(X):
        X_tr, X_te = X.iloc[train_idx], X.iloc[test_idx]
        y_tr, y_te = y.iloc[train_idx], y.iloc[test_idx]

        if len(np.unique(y_tr)) < 2 or len(np.unique(y_te)) < 2:
            continue

        X_tr_s = scaler.fit_transform(X_tr)
        X_te_s = scaler.transform(X_te)
        model.fit(X_tr_s, y_tr)

        aucs.append(roc_auc_score(y_te, model.predict_proba(X_te_s)[:, 1]))
        fold_coefs.append(model.coef_[0].copy())

    if not aucs:
        return {"auc": float("nan"), "auc_std": float("nan"),
                "avg_stability": float("nan"), "n": len(X), "n_folds": 0}

    avg_stab = float("nan")
    if len(fold_coefs) >= 2:
        coef_arr = np.array(fold_coefs)
        means    = coef_arr.mean(axis=0)
        stds     = coef_arr.std(axis=0)
        stabs    = [abs(m) / s if s > 1e-9 else 10.0 for m, s in zip(means, stds)]
        avg_stab = float(np.mean(stabs))

    return {
        "auc":           float(np.mean(aucs)),
        "auc_std":       float(np.std(aucs)),
        "avg_stability": avg_stab,
        "n":             len(X),
        "n_folds":       len(aucs),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Feature selection helper
# ─────────────────────────────────────────────────────────────────────────────

def _select(X: pd.DataFrame, feats: str) -> pd.DataFrame:
    if feats == "reduced":
        cols = [f"{f}_lb{LOOKBACK}" for f in REDUCED_FEATURES]
        return X[cols]
    return X  # "all"


# ─────────────────────────────────────────────────────────────────────────────
#  Main experiment
# ─────────────────────────────────────────────────────────────────────────────

def run() -> None:
    print("Connecting to database …")
    conn        = psycopg2.connect(DB_DSN)
    raw_attacks = load_attacks(conn)
    disc        = load_discourse(conn)
    conn.close()

    attacks_daily = aggregate_attacks(raw_attacks)
    df_full       = build_full_daily(attacks_daily, disc)

    X_all, y_all, dates_all = build_dataset(
        df_full, LOOKBACK, HORIZON, target=TARGET, feature_cols=ALL_FEATURE_COLS
    )
    print(f"  Full dataset: {len(X_all)} rows | pos_rate={y_all.mean():.1%} | "
          f"{dates_all[0].date()} → {dates_all[-1].date()}")

    # ── grid ──────────────────────────────────────────────────────────────────
    results: list[dict] = []

    print(f"\n{'═'*78}")
    print(f"  target={TARGET}  lb={LOOKBACK}  h={HORIZON}")
    print(f"{'═'*78}")
    print(f"\n  {'Variant':<32}  {'n':>5}  {'AUC':>6}  {'±std':>6}  "
          f"{'stability':>10}  {'score':>7}")
    print(f"  {'-'*72}")

    for recent in RECENT_WINDOWS:
        for feats in ("all", "reduced"):
            X = X_all.iloc[-recent:].copy() if recent else X_all.copy()
            y = y_all.iloc[-recent:].copy() if recent else y_all.copy()
            X = _select(X, feats)

            res = _evaluate(X, y)
            label_r = f"last {recent}d" if recent else "all data"
            label   = f"{label_r}  {feats} feats"
            score   = (res["auc"] - res["auc_std"]
                       if not np.isnan(res["auc"]) else float("nan"))

            print(
                f"  {label:<32}  {res['n']:>5}  "
                f"{res['auc']:>6.3f}  {res['auc_std']:>6.3f}"
                f"  {res['avg_stability']:>10.2f}  {score:>7.3f}"
            )
            results.append({"recent": recent, "feats": feats,
                            "label": label, "score": score, **res})

    # ── best variant ──────────────────────────────────────────────────────────
    valid = [r for r in results
             if not np.isnan(r["score"]) and r.get("n_folds", 0) >= 3]
    if not valid:
        print("\n  No valid variant with ≥3 folds.")
        return

    best = max(valid, key=lambda r: r["score"])
    print(f"\n  ▶  Best: {best['label']}  "
          f"AUC={best['auc']:.3f} ±{best['auc_std']:.3f}  "
          f"stability={best['avg_stability']:.2f}  score={best['score']:.3f}")

    # Compare against v1
    v1_path = MODEL_DIR / "model_significant_lb10_h1.pkl"
    if v1_path.exists():
        with open(v1_path, "rb") as f:
            v1 = pickle.load(f)
        print(f"\n  v1 was trained on {v1.get('n_samples', '?')} samples (all data, all features)")
        print(f"  v1 AUC (from window_analysis.csv): 0.557 ±0.100")

    # ── retrain best on full slice and save ──────────────────────────────────
    X_tr = X_all.iloc[-best["recent"]:].copy() if best["recent"] else X_all.copy()
    y_tr = y_all.iloc[-best["recent"]:].copy() if best["recent"] else y_all.copy()
    X_tr = _select(X_tr, best["feats"])

    scaler = StandardScaler()
    model  = LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs")
    X_s    = scaler.fit_transform(X_tr)
    model.fit(X_s, y_tr)

    bundle = {
        "model":          model,
        "scaler":         scaler,
        "features":       list(X_tr.columns),
        "n_samples":      len(X_tr),
        "auc_cv":         best["auc"],
        "auc_std_cv":     best["auc_std"],
        "avg_stability":  best["avg_stability"],
        "recent_window":  best["recent"],
        "target":         TARGET,
        "lookback":       LOOKBACK,
        "horizon":        HORIZON,
    }

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MODEL_DIR / "model_significant_lb10_h1_v2.pkl"
    with open(out_path, "wb") as f:
        pickle.dump(bundle, f)

    print(f"\n  Saved → {out_path}")
    print(f"  Features ({len(X_tr.columns)}): {', '.join(f.replace(f'_lb{LOOKBACK}', '') for f in X_tr.columns)}")
    print(f"  Trained on {len(X_tr)} samples")
    print(f"\n{'═'*78}")


if __name__ == "__main__":
    run()
