"""
scripts/analysis/forecast.py
────────────────────────────────────────────────────────────────────────────
Forecast pipeline — sits between analysis and the prediction UI.

Pipeline:
  multi_target_analysis.py  →  multi_target_results.csv   (analysis config)
  train_forecast(conn)      →  forecast_h{N}.pkl           (one per horizon)
                               forecast_meta.json           (provenance)
  predict_forecast(conn, h) →  live predictions for all outcomes
  predict_next_attack(conn) →  KNN historical analogy

The training step reads multi_target_results.csv to select the best
lookback per (outcome, horizon) pair, so every model is directly tied
to the preceding analysis run.
"""
from __future__ import annotations

import json
import os
import pickle
from collections import Counter
from datetime import datetime
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import roc_auc_score, r2_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.config import (
    DB_DSN, FEATURES_TABLE, EXILENOVA_CHANNEL,
    EVENTS_TABLE, ET_TABLE, TARGETS_TABLE,
)
from analysis.window_analysis import (
    load_attacks, load_discourse,
    aggregate_attacks, build_full_daily,
    DISCOURSE_COLS, DISCOURSE_COLS_EXT, CV_SPLITS, DEPTH_SCORES,
    add_derived_discourse, _compute_rolling_stats, build_features, BASELINE_WINDOW,
    build_multi_dataset,
)
from analysis.multi_target_analysis import (
    _BASE_OUTCOME_TARGETS,
)

# ─────────────────────────────────────────────────────────────────────────────

FORECAST_HORIZONS = [1, 2, 3, 5, 7, 10, 14]
FORECAST_LOOKBACK = 10          # max lookback; per-outcome lb comes from analysis
KNN_K             = 7           # neighbours for next-attack analogy
KNN_HORIZON       = 14          # days ahead to search in analogs

_MODEL_DIR   = Path(__file__).resolve().parents[2] / "data" / "analysis"
_RESULTS_CSV = _MODEL_DIR / "multi_target_results.csv"
_META_JSON   = _MODEL_DIR / "forecast_meta.json"


def _bundle_path(horizon: int) -> Path:
    return _MODEL_DIR / f"forecast_h{horizon}.pkl"


# ─────────────────────────────────────────────────────────────────────────────
#  Config / provenance helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_analysis_config() -> dict | None:
    """
    Parse multi_target_results.csv → nested dict:
      outcome → horizon → {best_lb, score, score_std}
    Returns None if analysis hasn't been run.
    """
    if not _RESULTS_CSV.exists():
        return None

    df  = pd.read_csv(_RESULTS_CSV)
    cfg: dict = {}

    for outcome, grp in df.groupby("outcome"):
        typ    = grp["type"].iloc[0]
        metric = "auc" if typ == "binary" else "r2"
        cfg[outcome] = {"type": typ, "by_horizon": {}}

        for h, hg in grp.groupby("horizon"):
            valid = hg.dropna(subset=[metric])
            if "n_folds" in valid.columns:
                valid = valid[valid["n_folds"] >= 3]
            if valid.empty:
                continue
            best = valid.loc[valid[metric].idxmax()]
            cfg[outcome]["by_horizon"][int(h)] = {
                "best_lb":   int(best["lookback"]),
                "score":     float(best[metric]),
                "score_std": float(best.get(metric + "_std", float("nan"))),
            }

    return cfg or None


def get_forecast_meta() -> dict | None:
    if not _META_JSON.exists():
        return None
    with open(_META_JSON, encoding="utf-8") as f:
        return json.load(f)


def get_analysis_mtime() -> str | None:
    if not _RESULTS_CSV.exists():
        return None
    return datetime.fromtimestamp(os.path.getmtime(_RESULTS_CSV)).strftime("%Y-%m-%d %H:%M")


# ─────────────────────────────────────────────────────────────────────────────
#  Training
# ─────────────────────────────────────────────────────────────────────────────

def _cv_score(model, scaler, X: pd.DataFrame, y: pd.Series, typ: str) -> tuple[float, int]:
    tscv   = TimeSeriesSplit(n_splits=CV_SPLITS)
    scores = []
    for tr, te in tscv.split(X):
        Xtr_s = scaler.fit_transform(X.iloc[tr].values)
        Xte_s = scaler.transform(X.iloc[te].values)
        ytr, yte = y.iloc[tr], y.iloc[te]
        if typ == "binary":
            if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
                continue
            pos_tr = int(ytr.sum())
            m = XGBClassifier(
                n_estimators=100, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.6, min_child_weight=5,
                scale_pos_weight=(len(ytr) - pos_tr) / max(pos_tr, 1),
                eval_metric="logloss", random_state=42, verbosity=0,
            )
            m.fit(Xtr_s, ytr)
            scores.append(roc_auc_score(yte, m.predict_proba(Xte_s)[:, 1]))
        else:
            if ytr.std() < 1e-9 or yte.std() < 1e-9:
                continue
            m = Ridge(alpha=1.0)
            m.fit(Xtr_s, ytr)
            scores.append(r2_score(yte, m.predict(Xte_s)))
    return (float(np.mean(scores)) if scores else float("nan"), len(scores))


def train_forecast(conn) -> dict:
    """
    For each (outcome, horizon): pick best lookback from analysis config,
    train the appropriate model, evaluate with TimeSeriesSplit.
    Saves one bundle per horizon + forecast_meta.json.
    Returns a human-readable summary dict.
    """
    analysis_cfg = load_analysis_config()

    raw_attacks   = load_attacks(conn)
    disc          = load_discourse(conn)
    attacks_daily = aggregate_attacks(raw_attacks)
    df            = build_full_daily(attacks_daily, disc)
    df            = add_derived_discourse(df)

    _MODEL_DIR.mkdir(parents=True, exist_ok=True)

    meta: dict = {
        "trained_at":     datetime.now().strftime("%Y-%m-%d %H:%M"),
        "analysis_mtime": get_analysis_mtime(),
        "analysis_used":  bool(analysis_cfg),
        "n_samples":      len(df),
        "date_range":     [str(df["date"].iloc[0].date()), str(df["date"].iloc[-1].date())],
        "horizons":       {},
        "outcomes":       {},
    }

    for h in FORECAST_HORIZONS:
        bundle: dict = {"horizon": h, "models": {}, "n_samples": len(df)}

        for oc in _BASE_OUTCOME_TARGETS:
            name = oc["name"]
            typ  = oc["type"]

            # Best lookback from analysis, fallback to FORECAST_LOOKBACK
            lb = FORECAST_LOOKBACK
            if analysis_cfg and name in analysis_cfg:
                h_cfg = analysis_cfg[name]["by_horizon"].get(h)
                if h_cfg:
                    lb = h_cfg["best_lb"]

            try:
                X, y = build_multi_dataset(df, lb, h, oc)

                if typ == "binary" and len(np.unique(y)) < 2:
                    continue
                if typ == "regression" and y.std() < 1e-9:
                    continue

                scaler = StandardScaler()
                X_s    = scaler.fit_transform(X.values)
                if typ == "binary":
                    pos   = int(y.sum())
                    model = XGBClassifier(
                        n_estimators=100, max_depth=4, learning_rate=0.05,
                        subsample=0.8, colsample_bytree=0.6, min_child_weight=5,
                        scale_pos_weight=(len(y) - pos) / max(pos, 1),
                        eval_metric="logloss", random_state=42, verbosity=0,
                    )
                else:
                    model = Ridge(alpha=1.0)

                model.fit(X_s, y)
                cv, folds = _cv_score(model, scaler, X, y, typ)

                bundle["models"][name] = {
                    "model":      model,
                    "scaler":     scaler,
                    "lookback":   lb,
                    "type":       typ,
                    "col":        oc["col"],
                    "op":         oc["op"],
                    "cv_score":   cv,
                    "n_folds":    folds,
                    "pos_rate":   float(y.mean()) if typ == "binary" else None,
                    "mean_y":     float(y.mean()) if typ == "regression" else None,
                    "feat_names": list(X.columns),
                }

                if name not in meta["outcomes"]:
                    meta["outcomes"][name] = {"type": typ, "by_horizon": {}}
                meta["outcomes"][name]["by_horizon"][h] = {
                    "lookback": lb, "cv_score": cv, "n_folds": folds,
                }

            except Exception as exc:
                if name not in meta["outcomes"]:
                    meta["outcomes"][name] = {"type": typ, "by_horizon": {}}
                meta["outcomes"][name]["by_horizon"][h] = {"error": str(exc)}

        with open(_bundle_path(h), "wb") as f:
            pickle.dump(bundle, f)
        meta["horizons"][h] = f"{len(bundle['models'])} models trained"

    with open(_META_JSON, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return meta


# ─────────────────────────────────────────────────────────────────────────────
#  Forecast prediction
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_discourse(conn, max_lb: int) -> pd.DataFrame:
    """Fetch max_lb + BASELINE_WINDOW rows so rolling stats have enough history."""
    n_rows = max_lb + BASELINE_WINDOW
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
        WHERE channel = '{EXILENOVA_CHANNEL}'
        ORDER BY feature_date DESC
        LIMIT {n_rows}
    """
    df = pd.read_sql(sql, conn, parse_dates=["feature_date"])
    return df.sort_values("feature_date").reset_index(drop=True)


def predict_forecast(conn, horizon: int) -> list[dict]:
    """
    Load the trained bundle for `horizon`, fetch current discourse,
    run all outcome models.  Returns one dict per outcome.
    """
    path = _bundle_path(horizon)
    if not path.exists():
        return [{"error": f"No trained model for horizon={horizon}. Run Training first."}]

    with open(path, "rb") as f:
        bundle = pickle.load(f)

    max_lb   = max((m["lookback"] for m in bundle["models"].values()), default=FORECAST_LOOKBACK)
    disc_all = _fetch_discourse(conn, max_lb)   # fetches max_lb + BASELINE_WINDOW rows
    disc_all = add_derived_discourse(disc_all)
    disc_cols = [c for c in DISCOURSE_COLS_EXT if c in disc_all.columns]
    rolling_means, rolling_stds = _compute_rolling_stats(disc_all, disc_cols)

    results = []
    for name, m in bundle["models"].items():
        lb = m["lookback"]
        if len(disc_all) < lb:
            results.append({
                "name": name, "type": m["type"],
                "error": f"Only {len(disc_all)}/{lb} days of discourse available",
            })
            continue

        window    = disc_all.iloc[-lb:].reset_index(drop=True)
        bm_idx    = max(0, len(disc_all) - lb - 1)
        bm        = rolling_means.iloc[bm_idx]
        bs        = rolling_stds.iloc[bm_idx]
        x_feat, _ = build_features(window, disc_cols, bm, bs)

        expected = getattr(m["scaler"], "n_features_in_", None)
        if expected is not None and x_feat.shape[0] != expected:
            results.append({
                "name": name, "type": m["type"],
                "error": (
                    f"Feature mismatch: model expects {expected}, got {x_feat.shape[0]}. "
                    "Retrain models (Train Models button)."
                ),
            })
            continue

        x_s = m["scaler"].transform(x_feat.reshape(1, -1))

        if m["type"] == "binary":
            prob  = float(m["model"].predict_proba(x_s)[0, 1])
            value = prob
        else:
            value = float(m["model"].predict(x_s)[0])
            prob  = None

        feat_names = m.get("feat_names") or []
        mdl = m["model"]
        if hasattr(mdl, "feature_importances_") and feat_names:
            imp  = mdl.feature_importances_
            contribs = {feat_names[i]: float(imp[i]) for i in range(min(len(feat_names), len(imp)))}
        elif hasattr(mdl, "coef_") and feat_names:
            coefs = mdl.coef_[0] if m["type"] == "binary" else mdl.coef_
            contribs = {feat_names[i]: float(coefs[i] * x_s[0, i]) for i in range(len(feat_names))}
        else:
            contribs = {}
        top_features = sorted(contribs.items(), key=lambda kv: abs(kv[1]), reverse=True)[:4]

        results.append({
            "name":         name,
            "type":         m["type"],
            "value":        value,
            "probability":  prob,
            "top_features": top_features,
            "lookback":     lb,
            "cv_score":     m.get("cv_score", float("nan")),
            "n_folds":      m.get("n_folds", 0),
            "pos_rate":     m.get("pos_rate"),
            "mean_y":       m.get("mean_y"),
            "data_from":    str(window["feature_date"].iloc[0].date()),
            "data_to":      str(window["feature_date"].iloc[-1].date()),
        })

    # Sort: binary first (by probability desc), then regression (by value desc)
    results.sort(key=lambda r: (r.get("type") != "binary", -(r.get("value") or 0)))
    return results


# ─────────────────────────────────────────────────────────────────────────────
#  Next attack — KNN historical analogy
# ─────────────────────────────────────────────────────────────────────────────

def predict_next_attack(conn, k: int = KNN_K) -> dict:
    """
    Find the K most similar historical discourse patterns and aggregate
    what attacks happened in the following KNN_HORIZON days.

    Uses FORECAST_LOOKBACK-day discourse windows, same feature space as the
    analysis pipeline (DISCOURSE_COLS).
    """
    LB = FORECAST_LOOKBACK

    raw_attacks   = load_attacks(conn)
    disc          = load_discourse(conn)
    attacks_daily = aggregate_attacks(raw_attacks)
    df            = build_full_daily(attacks_daily, disc)
    df            = add_derived_discourse(df)

    # Normalize raw_attacks dates to date objects for fast lookups
    raw_attacks = raw_attacks.copy()
    raw_attacks["_date"] = pd.to_datetime(raw_attacks["attack_date"]).dt.date

    # Target types per date
    type_by_date: dict[object, list[str]] = {}
    try:
        sql = f"""
            SELECT e.attack_date, lower(trim(t.target_type)) AS target_type
            FROM {EVENTS_TABLE} e
            JOIN {ET_TABLE} et ON et.event_id = e.event_id
            JOIN {TARGETS_TABLE} t ON t.target_id = et.target_id
            WHERE e.status = 'active'
              AND t.target_type NOT IN ('unknown', 'other', '')
              AND t.target_type IS NOT NULL
        """
        types_df = pd.read_sql(sql, conn, parse_dates=["attack_date"])
        for row in types_df.itertuples(index=False):
            d = row.attack_date.date()
            type_by_date.setdefault(d, []).append(row.target_type)
    except Exception:
        pass

    n = len(df)

    # Precompute rolling baseline stats for the full df
    disc_cols = [c for c in DISCOURSE_COLS_EXT if c in df.columns]
    rolling_means_df, rolling_stds_df = _compute_rolling_stats(df, disc_cols)

    # Build historical feature matrix (one row per day, LB-day window)
    feat_rows: list[np.ndarray] = []
    df_indices: list[int]       = []
    for i in range(LB, n):
        window = df.iloc[i - LB : i]
        bm = rolling_means_df.iloc[i - 1]
        bs = rolling_stds_df.iloc[i - 1]
        x, _ = build_features(window, disc_cols, bm, bs)
        feat_rows.append(x)
        df_indices.append(i)

    if not feat_rows:
        return {"error": "Not enough historical data for KNN."}

    X_hist   = np.array(feat_rows, dtype=float)
    scaler   = StandardScaler()
    X_hist_s = scaler.fit_transform(X_hist)

    # Current feature vector (most recent LB-day window)
    window_now = df.iloc[n - LB : n]
    bm_now     = rolling_means_df.iloc[n - 1]
    bs_now     = rolling_stds_df.iloc[n - 1]
    x_now, _   = build_features(window_now, disc_cols, bm_now, bs_now)
    x_now_s    = scaler.transform(x_now.reshape(1, -1))

    # Euclidean distances; mask out rows too close to the end (no future window)
    dists = np.linalg.norm(X_hist_s - x_now_s, axis=1)
    for j, ri in enumerate(df_indices):
        if ri > n - KNN_HORIZON - 1:
            dists[j] = np.inf

    top_js = [j for j in np.argsort(dists) if dists[j] != np.inf][:k]
    max_d  = max((dists[j] for j in top_js), default=1.0) + 1e-9

    analogs = []
    for j in top_js:
        ri          = df_indices[j]
        anchor_ts   = pd.Timestamp(df.iloc[ri]["date"])
        end_ts      = anchor_ts + pd.Timedelta(days=KNN_HORIZON)

        # Days to first attack in window
        days_to_next = None
        for d in range(1, KNN_HORIZON + 1):
            if ri + d < n and df["attack_count"].iloc[ri + d] > 0:
                days_to_next = d
                break

        # Slice raw attacks in the window
        mask = (raw_attacks["attack_date"] > anchor_ts) & (raw_attacks["attack_date"] <= end_ts)
        fut  = raw_attacks[mask]

        fut_regions = fut["macro_region"].dropna().tolist()
        fut_fires   = int(fut["fire"].sum())
        fut_hits    = int(fut["hit_confirmed"].sum())
        fut_depth   = int(fut["macro_region"].map(DEPTH_SCORES).fillna(0).max()) if not fut.empty else 0

        fut_types: list[str] = []
        for d in range(1, KNN_HORIZON + 1):
            fd = (anchor_ts + pd.Timedelta(days=d)).date()
            fut_types.extend(type_by_date.get(fd, []))

        analogs.append({
            "date":         str(anchor_ts.date()),
            "similarity":   float(1.0 - dists[j] / max_d),
            "days_to_next": days_to_next,
            "regions":      fut_regions,
            "target_types": fut_types,
            "fire":         fut_fires > 0,
            "hit":          fut_hits > 0,
            "max_depth":    fut_depth,
        })

    # ── Aggregate across analogs ──────────────────────────────────────────────
    days_list = [a["days_to_next"] for a in analogs if a["days_to_next"] is not None]

    all_regions = [r for a in analogs for r in a["regions"]]
    all_types   = [t for a in analogs for t in a["target_types"]]

    def _dist(items: list) -> list[tuple[str, float]]:
        c = Counter(items)
        total = sum(c.values()) or 1
        return [(k, v / total) for k, v in c.most_common(5)]

    n_ana = len(analogs)
    return {
        "days_estimate":  float(np.median(days_list)) if days_list else None,
        "days_range":     (
            float(np.percentile(days_list, 25)),
            float(np.percentile(days_list, 75)),
        ) if len(days_list) >= 2 else None,
        "region_dist":    _dist(all_regions),
        "type_dist":      _dist(all_types),
        "fire_rate":      float(np.mean([a["fire"] for a in analogs]))          if n_ana else 0.0,
        "hit_rate":       float(np.mean([a["hit"] for a in analogs]))           if n_ana else 0.0,
        "deep_rate":      float(np.mean([a["max_depth"] >= 3 for a in analogs])) if n_ana else 0.0,
        "analogs":        analogs,
        "n_with_attacks": len(days_list),
        "k":              n_ana,
        "discourse_from": str(df["date"].iloc[max(0, n - LB)].date()),
        "discourse_to":   str(df["date"].iloc[-1].date()),
        "lookback_used":  LB,
    }
