"""
scripts/analysis/predictor.py
Train and serve the three best LogisticRegression models from the window experiment.

Model configs (from window_analysis.csv, 5/5 folds, lift > 0, sorted by AUC):
  1. significant  lb=10  h=1   AUC=0.557
  2. any_attack   lb=2   h=3   AUC=0.553
  3. any_attack   lb=10  h=5   AUC=0.539
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

_SCRIPTS = str(Path(__file__).resolve().parents[1])
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit

from shared.config import DB_DSN, FEATURES_TABLE, CHANNEL_USERNAME, EVENTS_TABLE
from analysis.window_analysis import (
    load_attacks, load_discourse,
    aggregate_attacks, build_full_daily,
    build_dataset, DISCOURSE_COLS, DEPTH_SCORES, ALL_FEATURE_COLS, CV_SPLITS,
)

# ─────────────────────────────────────────────────────────────────────────────

MODEL_CONFIGS: list[dict] = [
    {"target": "significant", "lookback": 10, "horizon": 1,  "auc": 0.557},
    {"target": "any_attack",  "lookback": 2,  "horizon": 3,  "auc": 0.553},
    {"target": "any_attack",  "lookback": 10, "horizon": 5,  "auc": 0.539},
]

MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "analysis"
_V2_PATH  = MODEL_DIR / "model_significant_lb10_h1_v2.pkl"

# Improved model: drop collinear/lagging/same-day features, add depth_score
_V2_RAW_FEATS  = ["pre_drone", "en_attack", "en_confirm", "war_ukr_ru", "depth_score"]
_V2_RECENT_OPTS = [90, 120, 180, None]  # candidate training windows (rows)


def _model_path(cfg: dict) -> Path:
    return MODEL_DIR / f"model_{cfg['target']}_lb{cfg['lookback']}_h{cfg['horizon']}.pkl"


# ─────────────────────────────────────────────────────────────────────────────
#  Training
# ─────────────────────────────────────────────────────────────────────────────

def _train_v2(df: pd.DataFrame) -> dict:
    """
    Train the improved v2 model: reduced features + depth_score + best recent window.
    Runs a mini grid search over _V2_RECENT_OPTS to pick the window with the
    highest conservative AUC score (mean − std).  Saves to _V2_PATH.
    """
    feat_cols = [f"{f}_lb10" for f in _V2_RAW_FEATS]
    X_all, y_all, _ = build_dataset(
        df, lookback=10, horizon=1, target="significant",
        feature_cols=ALL_FEATURE_COLS,
    )

    best_score, best_recent = float("-inf"), _V2_RECENT_OPTS[1]

    for recent in _V2_RECENT_OPTS:
        X = X_all.iloc[-recent:].copy() if recent else X_all.copy()
        y = y_all.iloc[-recent:].copy() if recent else y_all.copy()
        Xr = X[feat_cols]

        if len(np.unique(y)) < 2:
            continue

        tscv   = TimeSeriesSplit(n_splits=CV_SPLITS)
        scaler = StandardScaler()
        model  = LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs")
        aucs: list[float] = []

        for tr, te in tscv.split(Xr):
            Xtr, Xte = Xr.iloc[tr], Xr.iloc[te]
            ytr, yte = y.iloc[tr], y.iloc[te]
            if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
                continue
            Xs = scaler.fit_transform(Xtr)
            model.fit(Xs, ytr)
            aucs.append(roc_auc_score(yte, model.predict_proba(scaler.transform(Xte))[:, 1]))

        if len(aucs) < 3:
            continue
        score = float(np.mean(aucs)) - float(np.std(aucs))
        if score > best_score:
            best_score, best_recent = score, recent

    # Retrain on full best window
    X = X_all.iloc[-best_recent:].copy() if best_recent else X_all.copy()
    y = y_all.iloc[-best_recent:].copy() if best_recent else y_all.copy()
    Xr = X[feat_cols]

    if len(np.unique(y)) < 2:
        return {"target": "significant", "lookback": 10, "horizon": 1,
                "status": "v2 skipped — single class"}

    scaler = StandardScaler()
    model  = LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs")
    model.fit(scaler.fit_transform(Xr), y)

    bundle = {
        "model":         model,
        "scaler":        scaler,
        "features":      feat_cols,
        "n_samples":     len(Xr),
        "recent_window": best_recent,
        "target":        "significant",
        "lookback":      10,
        "horizon":       1,
        "auc_cv":        best_score,
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(_V2_PATH, "wb") as f:
        pickle.dump(bundle, f)

    return {
        "target": "significant", "lookback": 10, "horizon": 1,
        "status": f"v2 OK — {len(Xr)} samples, window={best_recent}, score={best_score:.3f}",
    }


def train_all(conn) -> list[dict]:
    """Fit all 3 v1 models + the improved v2 model on historical data."""
    raw_attacks = load_attacks(conn)
    disc = load_discourse(conn)
    attacks_daily = aggregate_attacks(raw_attacks)
    df = build_full_daily(attacks_daily, disc)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for cfg in MODEL_CONFIGS:
        X, y, _ = build_dataset(df, cfg["lookback"], cfg["horizon"], target=cfg["target"])

        if len(np.unique(y)) < 2:
            results.append({**cfg, "status": "skipped — single class"})
            continue

        scaler = StandardScaler()
        model = LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs")
        X_s = scaler.fit_transform(X)
        model.fit(X_s, y)

        with open(_model_path(cfg), "wb") as f:
            pickle.dump({"model": model, "scaler": scaler, "n_samples": len(X)}, f)

        results.append({**cfg, "status": f"OK — {len(X)} samples"})

    results.append(_train_v2(df))
    return results


# ─────────────────────────────────────────────────────────────────────────────
#  Prediction
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_recent_depth_score(conn, lookback: int) -> float:
    """Sum of daily max-depth scores for attacks in the past `lookback` days."""
    sql = f"""
        SELECT macro_region, COUNT(*) AS cnt
        FROM {EVENTS_TABLE}
        WHERE status = 'active'
          AND attack_date >= CURRENT_DATE - INTERVAL '{lookback} days'
        GROUP BY macro_region
    """
    df = pd.read_sql(sql, conn)
    if df.empty:
        return 0.0
    df["score"] = df["macro_region"].map(DEPTH_SCORES).fillna(0)
    return float(df["score"].max())


def _fetch_recent_discourse(conn, lookback: int) -> pd.DataFrame:
    sql = f"""
        SELECT
            feature_date,
            COALESCE(pre_drone,   0) AS pre_drone,
            COALESCE(pre_airdef,  0) AS pre_airdef,
            COALESCE(pre_airport, 0) AS pre_airport,
            COALESCE(pre_uncert,  0) AS pre_uncert,
            COALESCE(en_attack,   0) AS en_attack,
            COALESCE(en_confirm,  0) AS en_confirm,
            COALESCE(en_refinery, 0) AS en_refinery,
            COALESCE(war_total,   0) AS war_total,
            COALESCE(war_ukr_ru,  0) AS war_ukr_ru
        FROM {FEATURES_TABLE}
        WHERE channel = '{CHANNEL_USERNAME}'
        ORDER BY feature_date DESC
        LIMIT {lookback}
    """
    df = pd.read_sql(sql, conn, parse_dates=["feature_date"])
    return df.sort_values("feature_date").reset_index(drop=True)


def predict_all(conn) -> list[dict]:
    """
    Load each saved model, fetch the last N days of discourse from DB,
    and return one prediction dict per model.

    Each result dict contains:
      target, lookback, horizon, auc,
      probability  — float 0–1
      label        — bool
      top_features — list of (feature_name, contribution_float), top 4
      data_from / data_to — date strings of the discourse window used
      error        — present only on failure
    """
    results = []

    for cfg in MODEL_CONFIGS:
        # Prefer v2 for the significant/lb10/h1 model if it exists
        if (cfg["target"] == "significant"
                and cfg["lookback"] == 10
                and cfg["horizon"] == 1
                and _V2_PATH.exists()):
            path = _V2_PATH
        else:
            path = _model_path(cfg)

        if not path.exists():
            results.append({**cfg, "error": "not trained yet"})
            continue

        with open(path, "rb") as f:
            bundle = pickle.load(f)

        model: LogisticRegression = bundle["model"]
        scaler: StandardScaler    = bundle["scaler"]

        # v2 bundles store the exact feature list; v1 uses DISCOURSE_COLS
        feature_names: list[str] = bundle.get("features", None)
        if feature_names is not None:
            # strip _lb{lookback} suffix to get raw column names
            raw_feats = [f.replace(f"_lb{cfg['lookback']}", "") for f in feature_names]
        else:
            raw_feats = list(DISCOURSE_COLS)

        disc = _fetch_recent_discourse(conn, cfg["lookback"])
        if len(disc) < cfg["lookback"]:
            results.append({
                **cfg,
                "error": f"only {len(disc)}/{cfg['lookback']} days of discourse available",
            })
            continue

        # depth_score is not in the discourse table — fetch it separately
        disc_only = [f for f in raw_feats if f != "depth_score"]
        base_vals = disc[disc_only].sum().values.tolist()

        if "depth_score" in raw_feats:
            depth_val = _fetch_recent_depth_score(conn, cfg["lookback"])
            base_vals.insert(raw_feats.index("depth_score"), depth_val)

        x_raw    = np.array(base_vals).reshape(1, -1)
        x_scaled = scaler.transform(x_raw)

        prob  = float(model.predict_proba(x_scaled)[0, 1])
        label = bool(model.predict(x_scaled)[0])

        coefs    = model.coef_[0]
        contribs = {feat: float(coefs[i] * x_scaled[0, i])
                    for i, feat in enumerate(raw_feats)}
        top_features = sorted(contribs.items(), key=lambda kv: abs(kv[1]), reverse=True)[:4]

        results.append({
            **cfg,
            "probability":  prob,
            "label":        label,
            "top_features": top_features,
            "data_from":    str(disc["feature_date"].iloc[0].date()),
            "data_to":      str(disc["feature_date"].iloc[-1].date()),
            "model_version": "v2" if path == _V2_PATH else "v1",
        })

    return results


# ─────────────────────────────────────────────────────────────────────────────
#  Backtest
# ─────────────────────────────────────────────────────────────────────────────

def backtest(conn, cfg: dict, n_days: int = 30) -> pd.DataFrame:
    """
    Apply the saved model to the last n_days rows of historical data.
    Returns DataFrame: date, probability, predicted, actual, correct.
    """
    use_v2 = (cfg["target"] == "significant"
              and cfg["lookback"] == 10
              and cfg["horizon"] == 1
              and _V2_PATH.exists())
    path = _V2_PATH if use_v2 else _model_path(cfg)
    if not path.exists():
        return pd.DataFrame()

    with open(path, "rb") as f:
        bundle = pickle.load(f)

    model: LogisticRegression = bundle["model"]
    scaler: StandardScaler    = bundle["scaler"]
    feature_names: list[str] | None = bundle.get("features", None)

    raw_attacks = load_attacks(conn)
    disc = load_discourse(conn)
    attacks_daily = aggregate_attacks(raw_attacks)
    df = build_full_daily(attacks_daily, disc)

    feat_cols = ALL_FEATURE_COLS if feature_names is not None else None
    X, y, dates = build_dataset(df, cfg["lookback"], cfg["horizon"],
                                target=cfg["target"], feature_cols=feat_cols)

    if feature_names is not None:
        X = X[feature_names]

    X_s   = scaler.transform(X)
    probs = model.predict_proba(X_s)[:, 1]
    preds = model.predict(X_s).astype(bool)

    out = pd.DataFrame({
        "date":        dates,
        "probability": probs,
        "predicted":   preds,
        "actual":      y.values.astype(bool),
    })
    out["correct"] = out["predicted"] == out["actual"]
    return out.tail(n_days).reset_index(drop=True)
