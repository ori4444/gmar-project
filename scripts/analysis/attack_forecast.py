"""
scripts/analysis/attack_forecast.py
────────────────────────────────────────────────────────────────────────────
Attack Forecast Bank — trains one calibrated XGBClassifier per target
(using best lb/h from multi_target_results.csv) and produces an
intelligence-style forecast from the latest available data.

Pipeline
────────
  1. python scripts/analysis/multi_target_analysis.py   →  multi_target_results.csv
  2. train_bank(conn)                                    →  attack_forecast_bank.pkl
  3. predict_intel(conn, precision_mode)                 →  structured forecast dict

The bank is feature-consistent: training and inference both use the full
daily df (attacks + discourse), so attack-recurrence features are included.
"""
from __future__ import annotations

import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.window_analysis import (
    load_attacks, load_discourse,
    aggregate_attacks, build_full_daily,
    DISCOURSE_COLS_EXT, CV_SPLITS,
    add_derived_discourse, _compute_rolling_stats, build_features,
    MAX_POS_RATE, MAX_AUC_STD, MIN_AUC,
)
from analysis.multi_target_analysis import (
    _BASE_OUTCOME_TARGETS, build_multi_dataset, load_target_type_daily,
)

# ─────────────────────────────────────────────────────────────────────────────
#  Paths
# ─────────────────────────────────────────────────────────────────────────────

_DATA_DIR    = Path(__file__).resolve().parents[2] / "data"
_RESULTS_CSV = _DATA_DIR / "analysis" / "multi_target_results.csv"
_BANK_DIR    = _DATA_DIR / "models"
BANK_PATH    = _BANK_DIR / "attack_forecast_bank.pkl"
_META_PATH   = _BANK_DIR / "attack_forecast_meta.json"

_LOG_DIR          = _DATA_DIR / "predictions"
PREDICTIONS_LOG   = _LOG_DIR / "predictions_log.csv"
MODEL_AUC_LOG     = _LOG_DIR / "model_auc_log.csv"

# ─────────────────────────────────────────────────────────────────────────────
#  Intelligence constants
# ─────────────────────────────────────────────────────────────────────────────

DIMENSIONS: dict[str, list[str]] = {
    "infrastructure": [
        "ttype_power_facility", "ttype_refinery", "ttype_pipeline",
        "ttype_oil_depot", "ttype_gas_facility",
    ],
    "scale": [
        "repeated_strike", "multi_attack", "combined_strike",
        "deep_strike", "very_deep",
    ],
    "effects": [
        "any_fire", "multi_fire", "any_explosion", "any_shutdown",
    ],
    "baseline": [
        "any_attack", "significant", "any_hit",
    ],
}

TARGET_LABELS: dict[str, str] = {
    "ttype_power_facility": "Power Facility",
    "ttype_refinery":       "Refinery",
    "ttype_pipeline":       "Pipeline",
    "ttype_oil_depot":      "Oil Depot",
    "ttype_gas_facility":   "Gas Facility",
    "repeated_strike":      "Repeated Strike",
    "multi_attack":         "Heavy Bombardment",
    "combined_strike":      "Combined Strike",
    "deep_strike":          "Deep Strike",
    "very_deep":            "Very Deep Strike",
    "any_fire":             "Fire",
    "multi_fire":           "Multi-Fire",
    "any_explosion":        "Explosion",
    "any_shutdown":         "Shutdown",
    "any_attack":           "Any Attack",
    "significant":          "Significant Event",
    "any_hit":              "Confirmed Hit",
}

PRECISION_THRESHOLDS: dict[str, dict] = {
    "high":   {"min_prob": 0.65, "min_auc": 0.70},
    "medium": {"min_prob": 0.55, "min_auc": 0.62},
    "low":    {"min_prob": 0.42, "min_auc": 0.55},
}

_NEAR_TERM_MAX_H = 3
_WEEKLY_MAX_H    = 10


def _make_xgb(pos: int, neg: int) -> XGBClassifier:
    return XGBClassifier(
        n_estimators=100, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.6, min_child_weight=5,
        scale_pos_weight=neg / max(pos, 1),
        eval_metric="logloss", random_state=42, verbosity=0,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Persistence helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_bank_meta() -> dict | None:
    if not _META_PATH.exists():
        return None
    with open(_META_PATH, encoding="utf-8") as f:
        return json.load(f)


def get_bank_mtime() -> str | None:
    if not BANK_PATH.exists():
        return None
    return datetime.fromtimestamp(BANK_PATH.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def _load_bank() -> dict | None:
    if not BANK_PATH.exists():
        return None
    with open(BANK_PATH, "rb") as f:
        return pickle.load(f)


# ─────────────────────────────────────────────────────────────────────────────
#  Training helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_best_params(outcomes: list[dict]) -> dict[str, dict]:
    """
    Parse multi_target_results.csv → best (lb, h) per outcome.

    Applies the same quality gates as select_quality_configs in window_analysis:
      Tier 1: pos_rate ≤ MAX_POS_RATE AND auc_std ≤ MAX_AUC_STD AND auc ≥ MIN_AUC
      Tier 2: pos_rate ≤ MAX_POS_RATE AND auc ≥ MIN_AUC (relax std gate)
      Tier 3: auc ≥ MIN_AUC (relax pos_rate gate)
      Tier 4: any config with n_folds ≥ 2 (last resort)
    """
    if not _RESULTS_CSV.exists():
        return {}
    df = pd.read_csv(_RESULTS_CSV)
    result: dict[str, dict] = {}
    for oc in outcomes:
        name = oc["name"]
        grp  = df[df["outcome"] == name].dropna(subset=["auc"]).copy()
        if "n_folds" in grp.columns:
            grp = grp[grp["n_folds"] >= 2]
        if grp.empty:
            continue

        has_std = "auc_std" in grp.columns

        # Tier 1: full quality filter
        good = grp[grp["auc"] >= MIN_AUC]
        if "pos_rate" in grp.columns:
            good = good[good["pos_rate"] <= MAX_POS_RATE]
        if has_std:
            good = good[good["auc_std"] <= MAX_AUC_STD]

        # Tier 2: relax std gate
        if good.empty:
            good = grp[grp["auc"] >= MIN_AUC]
            if "pos_rate" in grp.columns:
                good = good[good["pos_rate"] <= MAX_POS_RATE]

        # Tier 3: relax pos_rate gate
        if good.empty:
            good = grp[grp["auc"] >= MIN_AUC]

        # Tier 4: last resort — any config
        if good.empty:
            good = grp

        best = good.loc[good["auc"].idxmax()]
        result[name] = {
            "lb":      int(best["lookback"]),
            "h":       int(best["horizon"]),
            "auc":     float(best["auc"]),
            "auc_std": float(best.get("auc_std", float("nan"))),
        }
    return result


def _cv_and_train(
    df: pd.DataFrame, outcome: dict, lb: int, h: int,
) -> dict | None:
    """CV-evaluate + final-train one target. Returns bundle dict or None."""
    X, y = build_multi_dataset(df, lb, h, outcome)
    if X.empty or len(np.unique(y)) < 2:
        return None

    tscv = TimeSeriesSplit(n_splits=CV_SPLITS)
    aucs: list[float] = []
    for tr, te in tscv.split(X):
        Xtr, Xte = X.iloc[tr].values, X.iloc[te].values
        ytr, yte  = y.iloc[tr], y.iloc[te]
        if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
            continue
        pos_tr = int(ytr.sum())
        m = _make_xgb(pos_tr, len(ytr) - pos_tr)
        m.fit(Xtr, ytr)
        aucs.append(roc_auc_score(yte, m.predict_proba(Xte)[:, 1]))

    cv_auc = float(np.mean(aucs)) if aucs else float("nan")
    cv_std = float(np.std(aucs))  if aucs else float("nan")

    scaler = StandardScaler()
    X_s    = scaler.fit_transform(X.values)
    pos    = int(y.sum())
    model  = _make_xgb(pos, len(y) - pos)
    model.fit(X_s, y)

    return {
        "model":      model,
        "scaler":     scaler,
        "best_lb":    lb,
        "best_h":     h,
        "cv_auc":     cv_auc,
        "cv_std":     cv_std,
        "pos_rate":   float(y.mean()),
        "feat_names": list(X.columns),
        "n_samples":  len(X),
        "outcome":    outcome,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Logging helpers
# ─────────────────────────────────────────────────────────────────────────────

def _append_csv(path: Path, rows: list[dict]) -> None:
    """Append rows to a CSV log, writing header only if the file is new."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        import csv
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if write_header:
            w.writeheader()
        w.writerows(rows)


def _log_predictions(forecast: dict) -> None:
    """Append one row per signal to predictions_log.csv."""
    gen_at      = forecast.get("generated_at", "")
    data_through = forecast.get("data_through", "")
    precision   = forecast.get("precision_mode", "")
    warning     = forecast.get("warning_level", "")

    rows: list[dict] = []
    for band_key in ("near_term", "weekly", "extended"):
        band = forecast.get(band_key, {})
        for dim, signals in band.get("signals", {}).items():
            for s in signals:
                rows.append({
                    "generated_at":  gen_at,
                    "data_through":  data_through,
                    "precision_mode": precision,
                    "warning_level": warning,
                    "band":          band_key,
                    "dimension":     dim,
                    "target":        s.get("target", ""),
                    "label":         s.get("label", ""),
                    "prob":          round(s.get("prob", float("nan")), 4),
                    "cv_auc":        round(s.get("cv_auc", float("nan")), 4),
                    "cv_std":        round(s.get("cv_std", float("nan")), 4),
                    "best_h":        s.get("best_h", ""),
                    "best_lb":       s.get("best_lb", ""),
                    "tier":          s.get("tier") or "",
                    "passes":        int(s.get("passes", False)),
                })

    _append_csv(PREDICTIONS_LOG, rows)


def _log_model_auc(meta: dict) -> None:
    """Append one row per trained outcome to model_auc_log.csv."""
    trained_at  = meta.get("trained_at", "")
    data_range  = meta.get("data_range", ["", ""])
    rows: list[dict] = []
    for name, info in meta.get("outcomes", {}).items():
        if info.get("status") != "trained":
            continue
        rows.append({
            "trained_at":  trained_at,
            "data_from":   data_range[0] if data_range else "",
            "data_to":     data_range[1] if len(data_range) > 1 else "",
            "target":      name,
            "lb":          info.get("lb", ""),
            "h":           info.get("h", ""),
            "cv_auc":      round(info.get("cv_auc", float("nan")), 4),
            "cv_std":      round(info.get("cv_std", float("nan")), 4),
            "n":           info.get("n", ""),
            "pos_rate":    round(info.get("pos_rate", float("nan")), 4),
        })
    _append_csv(MODEL_AUC_LOG, rows)


# ─────────────────────────────────────────────────────────────────────────────
#  Public: train
# ─────────────────────────────────────────────────────────────────────────────

def train_bank(conn) -> dict:
    """
    Train all-target model bank from latest DB data.
    Reads best (lb, h) from multi_target_results.csv.
    Overwrites existing bank. Returns metadata dict.
    """
    if not _RESULTS_CSV.exists():
        raise FileNotFoundError(
            "multi_target_results.csv not found — run Multi-Target Analysis first."
        )

    print("Loading data …")
    raw_attacks     = load_attacks(conn)
    disc            = load_discourse(conn)
    ttype_daily, ttype_cols = load_target_type_daily(conn)

    attacks_daily = aggregate_attacks(raw_attacks)
    df = build_full_daily(attacks_daily, disc)
    df = add_derived_discourse(df)

    if not ttype_daily.empty and ttype_cols:
        df = df.merge(ttype_daily, on="date", how="left")
        df[ttype_cols] = df[ttype_cols].fillna(0).astype(int)

    print(f"  Dataset: {len(df)} rows · "
          f"{df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}")

    outcomes: list[dict] = list(_BASE_OUTCOME_TARGETS)
    for col in ttype_cols:
        outcomes.append({"name": col, "col": col, "type": "binary", "op": "gt0"})

    best_params = _load_best_params(outcomes)
    bank: dict[str, dict] = {}
    meta_outcomes: dict[str, dict] = {}

    print(f"\nTraining {len(outcomes)} targets …")
    for oc in outcomes:
        name = oc["name"]
        if name not in best_params:
            meta_outcomes[name] = {"status": "skipped — not in analysis results"}
            continue

        bp  = best_params[name]
        lb, h = bp["lb"], bp["h"]
        bundle = _cv_and_train(df, oc, lb, h)

        if bundle is None:
            meta_outcomes[name] = {"status": "skipped — insufficient data or single class"}
            print(f"  {name:28s}  SKIPPED")
            continue

        bank[name] = bundle
        meta_outcomes[name] = {
            "status":   "trained",
            "lb":       lb,
            "h":        h,
            "cv_auc":   bundle["cv_auc"],
            "cv_std":   bundle["cv_std"],
            "n":        bundle["n_samples"],
            "pos_rate": bundle["pos_rate"],
        }
        auc_s = f"{bundle['cv_auc']:.3f}" if bundle["cv_auc"] == bundle["cv_auc"] else "nan"
        std_s = f"{bundle['cv_std']:.3f}" if bundle["cv_std"] == bundle["cv_std"] else "nan"
        print(f"  {name:28s}  lb={lb:>2}  h={h:>2}  AUC={auc_s} ±{std_s}")

    _BANK_DIR.mkdir(parents=True, exist_ok=True)
    with open(BANK_PATH, "wb") as f:
        pickle.dump(bank, f)

    meta = {
        "trained_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_models":   len(bank),
        "data_range": [str(df["date"].iloc[0].date()), str(df["date"].iloc[-1].date())],
        "outcomes":   meta_outcomes,
    }
    with open(_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"\nBank saved → {BANK_PATH}  ({len(bank)} models)")
    _log_model_auc(meta)
    return meta


# ─────────────────────────────────────────────────────────────────────────────
#  Prediction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _raw_predict_all(bank: dict, df: pd.DataFrame) -> dict[str, dict]:
    """Run all bank models against the latest window in df."""
    disc_cols = [c for c in DISCOURSE_COLS_EXT if c in df.columns]
    rolling_means, rolling_stds = _compute_rolling_stats(df, disc_cols)
    n = len(df)

    results: dict[str, dict] = {}
    for name, m in bank.items():
        lb = m["best_lb"]
        if n < lb:
            results[name] = {"error": f"insufficient data ({n}/{lb} rows)"}
            continue

        window  = df.iloc[n - lb : n]
        bm_idx  = max(0, n - lb - 1)
        bm      = rolling_means.iloc[bm_idx]
        bs      = rolling_stds.iloc[bm_idx]
        x_feat, _ = build_features(window, disc_cols, bm, bs)

        expected = getattr(m["scaler"], "n_features_in_", None)
        if expected is not None and x_feat.shape[0] != expected:
            results[name] = {
                "error": f"feature mismatch ({x_feat.shape[0]} vs {expected}) — retrain bank"
            }
            continue

        x_s  = m["scaler"].transform(x_feat.reshape(1, -1))
        prob = float(m["model"].predict_proba(x_s)[0, 1])

        results[name] = {
            "prob":    prob,
            "cv_auc":  m["cv_auc"],
            "cv_std":  m["cv_std"],
            "best_h":  m["best_h"],
            "best_lb": lb,
        }

    return results


def _tier(prob: float, auc: float) -> str | None:
    if prob >= 0.65 and auc >= 0.70:
        return "high"
    if prob >= 0.55 and auc >= 0.62:
        return "medium"
    if prob >= 0.42 and auc >= 0.55:
        return "low"
    return None


def _warning_level(passing_signals: list[dict]) -> str:
    if not passing_signals:
        return "NONE"
    high_n = sum(1 for s in passing_signals if s.get("tier") == "high")
    if high_n >= 2:
        return "CRITICAL"
    if high_n >= 1:
        return "HIGH"
    med_n = sum(1 for s in passing_signals if s.get("tier") == "medium")
    if med_n >= 1:
        return "ELEVATED"
    return "LOW"


def _build_intel_forecast(
    raw_preds: dict[str, dict],
    precision_mode: str,
    bank_meta: dict,
    data_through: str,
) -> dict:
    threshold = PRECISION_THRESHOLDS.get(precision_mode, PRECISION_THRESHOLDS["medium"])
    min_prob  = threshold["min_prob"]
    min_auc   = threshold["min_auc"]

    all_signals: list[dict] = []
    for dim, targets in DIMENSIONS.items():
        for t in targets:
            if t not in raw_preds or "error" in raw_preds[t]:
                continue
            pred = raw_preds[t]
            prob = pred["prob"]
            auc  = pred["cv_auc"]
            tier = _tier(prob, auc)
            all_signals.append({
                "target":    t,
                "label":     TARGET_LABELS.get(t, t),
                "dimension": dim,
                "prob":      prob,
                "cv_auc":    auc,
                "cv_std":    pred.get("cv_std", float("nan")),
                "best_h":    pred["best_h"],
                "best_lb":   pred["best_lb"],
                "tier":      tier,
                "passes":    (tier is not None
                              and prob >= min_prob
                              and auc >= min_auc),
            })

    def _band(h: int) -> str:
        if h <= _NEAR_TERM_MAX_H:
            return "near_term"
        if h <= _WEEKLY_MAX_H:
            return "weekly"
        return "extended"

    def _make_band(band_key: str) -> dict:
        band_sigs  = [s for s in all_signals if _band(s["best_h"]) == band_key]
        passing    = [s for s in band_sigs if s["passes"]]
        by_dim: dict[str, list] = {
            dim: sorted(
                [s for s in passing if s["dimension"] == dim],
                key=lambda x: x["prob"], reverse=True,
            )
            for dim in DIMENSIONS
        }
        return {"signals": by_dim, "total_passing": len(passing)}

    near_term = _make_band("near_term")
    nt_passing = [s for sigs in near_term["signals"].values() for s in sigs]
    warning    = _warning_level(nt_passing)

    # Average AUC across trained models
    trained = [v for v in bank_meta.get("outcomes", {}).values()
               if v.get("status") == "trained"]
    valid_aucs = [v["cv_auc"] for v in trained
                  if v.get("cv_auc") is not None and v["cv_auc"] == v["cv_auc"]]
    avg_auc = float(np.mean(valid_aucs)) if valid_aucs else float("nan")

    return {
        "generated_at":   datetime.now().strftime("%Y-%m-%d %H:%M"),
        "data_through":   data_through,
        "precision_mode": precision_mode,
        "warning_level":  warning,
        "near_term":      near_term,
        "weekly":         _make_band("weekly"),
        "extended":       _make_band("extended"),
        "quality": {
            "n_models":   bank_meta.get("n_models", 0),
            "avg_auc":    avg_auc,
            "trained_at": bank_meta.get("trained_at", "?"),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Public: predict
# ─────────────────────────────────────────────────────────────────────────────

def predict_intel(conn, precision_mode: str = "medium") -> dict:
    """
    Load model bank, build full daily df, run all models against latest window.
    Returns a structured intelligence forecast dict.
    """
    bank = _load_bank()
    if bank is None:
        raise RuntimeError("No trained model bank found — train first.")

    meta = get_bank_meta() or {}

    raw_attacks             = load_attacks(conn)
    disc                    = load_discourse(conn)
    ttype_daily, ttype_cols = load_target_type_daily(conn)
    attacks_daily = aggregate_attacks(raw_attacks)
    df = build_full_daily(attacks_daily, disc)
    df = add_derived_discourse(df)

    if not ttype_daily.empty and ttype_cols:
        df = df.merge(ttype_daily, on="date", how="left")
        df[ttype_cols] = df[ttype_cols].fillna(0).astype(int)

    data_through = str(df["date"].iloc[-1].date())
    raw_preds    = _raw_predict_all(bank, df)

    forecast = _build_intel_forecast(raw_preds, precision_mode, meta, data_through)
    _log_predictions(forecast)
    return forecast


# ─────────────────────────────────────────────────────────────────────────────
#  Model Insights
# ─────────────────────────────────────────────────────────────────────────────

_CORR_CSV = _DATA_DIR / "analysis" / "feature_outcome_corr.csv"

_COL_LABELS: dict[str, str] = {
    "pre_drone":       "Drone activity",
    "pre_airdef":      "Air defense",
    "pre_airport":     "Airport activity",
    "pre_uncert":      "Uncertainty signals",
    "en_attack":       "Attack reports",
    "en_confirm":      "Confirmed reports",
    "en_refinery":     "Refinery reports",
    "war_total":       "War discourse",
    "war_ukr_ru":      "Ukraine-Russia conflict",
    "war_ru_internal": "Russian internal conflict",
}

_SUFFIX_LABELS: dict[str, str] = {
    "sum":        "volume",
    "ewm":        "trend",
    "lag1":       "1-day lag",
    "lag3":       "3-day lag",
    "lag7":       "7-day lag",
    "slope":      "momentum",
    "zscore":     "intensity",
    "alert_days": "alert days",
}

_FEAT_EXACT: dict[str, str] = {
    "pre_drone_x_en_attack":  "Drone × Attack combination",
    "sin_month":              "Seasonal (month)",
    "cos_month":              "Seasonal (month)",
    "is_heating_season":      "Heating season",
    "recent_attack_rate":     "Recent attack rate",
    "days_since_last_attack": "Days since last attack",
    "attack_momentum":        "Attack momentum",
}


def _feat_to_readable(name: str) -> str:
    if name in _FEAT_EXACT:
        return _FEAT_EXACT[name]
    for col in sorted(_COL_LABELS, key=len, reverse=True):
        if name.startswith(col + "_"):
            suffix = name[len(col) + 1:]
            return f"{_COL_LABELS[col]} ({_SUFFIX_LABELS.get(suffix, suffix)})"
    return name.replace("_", " ").capitalize()


def _describe_reliability(avg_auc: float, passing: list) -> dict:
    n      = len(passing)
    high_n = sum(1 for s in passing if s.get("tier") == "high")
    if avg_auc != avg_auc:
        return {"level": "Unknown", "auc": None, "n_signals": n, "high_n": high_n,
                "text": "Model quality data unavailable."}
    if avg_auc >= 0.75:
        level = "Strong"
        text  = (f"Models show strong discrimination (avg AUC {avg_auc:.2f}). "
                 f"High-confidence signals are backed by cross-validated evidence.")
    elif avg_auc >= 0.65:
        level = "Good"
        text  = (f"Good predictive accuracy across models (avg AUC {avg_auc:.2f}). "
                 f"Signals reflect tested performance on historical attack patterns.")
    elif avg_auc >= 0.55:
        level = "Moderate"
        text  = (f"Moderate model accuracy (avg AUC {avg_auc:.2f}). "
                 f"Directionally useful but should be weighted against other intelligence.")
    else:
        level = "Limited"
        text  = (f"Limited accuracy in current conditions (avg AUC {avg_auc:.2f}). "
                 f"Treat these as weak indicators only.")
    return {"level": level, "auc": avg_auc, "n_signals": n, "high_n": high_n, "text": text}


def _build_combo_insights(passing: list, corr_df) -> list:
    if corr_df is None:
        return []
    combos: list[dict] = []
    passing_targets = {s["target"] for s in passing}

    # Drone × Attack combination
    dx = "pre_drone_x_en_attack"
    if dx in corr_df.index:
        vals = [(t, float(corr_df.loc[dx, t])) for t in passing_targets
                if t in corr_df.columns and abs(float(corr_df.loc[dx, t])) > 0.07]
        vals.sort(key=lambda x: abs(x[1]), reverse=True)
        if vals:
            labels_str = ", ".join(TARGET_LABELS.get(t, t) for t, _ in vals[:3])
            combos.append({
                "pattern":  "Drone + attack discourse combination",
                "detail":   f"Concurrent drone activity and attack reporting amplifies signal for: {labels_str}.",
                "r":        vals[0][1],
                "strength": "notable",
            })

    # Lag-based advance signals (3-day and 7-day)
    for lag_days, suffix in ((3, "_lag3"), (7, "_lag7")):
        lag_feats = [f for f in corr_df.index if f.endswith(suffix)]
        best: tuple | None = None
        for f in lag_feats:
            for t in passing_targets:
                if t not in corr_df.columns:
                    continue
                r = float(corr_df.loc[f, t])
                if abs(r) > 0.06 and (best is None or abs(r) > abs(best[2])):
                    best = (f, t, r)
        if best:
            f_name, t, r = best
            col_base  = f_name[: -(len(suffix))]
            col_label = _COL_LABELS.get(col_base, col_base.replace("_", " ").title())
            combos.append({
                "pattern":  f"{col_label} — {lag_days}-day advance signal",
                "detail":   (f"{col_label} shows predictive correlation {lag_days} days prior to "
                             f"{TARGET_LABELS.get(t, t)} attacks (R={r:+.2f})."),
                "r":        r,
                "strength": "notable" if abs(r) >= 0.10 else "moderate",
            })

    # Heating season → energy infrastructure
    hs = "is_heating_season"
    if hs in corr_df.index:
        energy = [s for s in passing if s["target"] in
                  ("ttype_power_facility", "ttype_pipeline", "ttype_gas_facility")]
        if energy:
            rs = [float(corr_df.loc[hs, s["target"]]) for s in energy
                  if s["target"] in corr_df.columns]
            if rs and max(abs(v) for v in rs) > 0.05:
                avg_r = float(np.mean(rs))
                combos.append({
                    "pattern":  "Heating season escalation",
                    "detail":   (f"Energy infrastructure targeting is seasonally elevated "
                                 f"during heating months (R={avg_r:+.2f})."),
                    "r":        avg_r,
                    "strength": "seasonal",
                })

    # Air defense → infrastructure
    infra = [s for s in passing if s["dimension"] == "infrastructure"]
    if infra:
        best_ad: tuple | None = None
        for s in infra:
            t = s["target"]
            if t not in corr_df.columns:
                continue
            for f in corr_df.index:
                if "airdef" not in f:
                    continue
                r = float(corr_df.loc[f, t])
                if abs(r) > 0.05 and (best_ad is None or abs(r) > abs(best_ad[2])):
                    best_ad = (f, t, r)
        if best_ad:
            _, t, r = best_ad
            combos.append({
                "pattern":  "Air defense activity precedes infrastructure strikes",
                "detail":   (f"Elevated air defense discourse is associated with "
                             f"{TARGET_LABELS.get(t, t)} targeting (R={r:+.2f})."),
                "r":        r,
                "strength": "notable" if abs(r) >= 0.08 else "moderate",
            })

    return combos[:5]


def _build_feature_influence(passing: list, corr_df, bank: dict | None) -> list:
    if not bank or not passing:
        return []
    feat_imp: dict[str, list[float]] = {}
    feat_r:   dict[str, list[float]] = {}
    for s in passing:
        t = s["target"]
        if t not in bank:
            continue
        bundle = bank[t]
        w = s["prob"]
        for feat, imp in zip(bundle["feat_names"], bundle["model"].feature_importances_):
            r_val = 0.0
            if corr_df is not None and t in corr_df.columns and feat in corr_df.index:
                r_val = float(corr_df.loc[feat, t])
            feat_imp.setdefault(feat, []).append(imp * w)
            feat_r.setdefault(feat, []).append(r_val)

    result: list[dict] = []
    for feat, scores in feat_imp.items():
        avg_imp = float(np.mean(scores))
        avg_r   = float(np.mean(feat_r[feat]))
        result.append({
            "name":       feat,
            "readable":   _feat_to_readable(feat),
            "importance": avg_imp,
            "direction":  "positive" if avg_r >= 0 else "negative",
            "r":          avg_r,
        })
    result.sort(key=lambda x: x["importance"], reverse=True)
    return result[:8]


def build_model_insights(forecast: dict) -> dict:
    """
    Generate human-readable intelligence insights from the current forecast.
    Returns a structured dict consumed by ModelInsightsPanel in predictions.py.
    """
    passing: list[dict] = []
    for band_key in ("near_term", "weekly", "extended"):
        for dim_sigs in forecast.get(band_key, {}).get("signals", {}).values():
            passing.extend(s for s in dim_sigs if s.get("passes"))

    if not passing:
        return {"has_insights": False, "reason": "No passing signals at current precision level."}

    corr_df = None
    if _CORR_CSV.exists():
        try:
            corr_df = pd.read_csv(_CORR_CSV, index_col=0)
        except Exception:
            pass

    bank = _load_bank()

    aucs    = [s["cv_auc"] for s in passing if s.get("cv_auc") == s.get("cv_auc")]
    avg_auc = float(np.mean(aucs)) if aucs else float("nan")

    # Per-signal feature breakdown (top 5 signals by probability)
    signal_insights: list[dict] = []
    for s in sorted(passing, key=lambda x: x["prob"], reverse=True)[:5]:
        t = s["target"]
        xgb_features: list[dict] = []
        if bank and t in bank:
            bundle = bank[t]
            imps  = bundle["model"].feature_importances_
            names = bundle["feat_names"]
            for idx in np.argsort(imps)[::-1][:4]:
                fn    = names[idx]
                r_val = 0.0
                if corr_df is not None and t in corr_df.columns and fn in corr_df.index:
                    r_val = float(corr_df.loc[fn, t])
                xgb_features.append({
                    "name":       fn,
                    "readable":   _feat_to_readable(fn),
                    "importance": float(imps[idx]),
                    "direction":  "positive" if r_val >= 0 else "negative",
                    "r":          r_val,
                })
        signal_insights.append({
            "target":   t,
            "label":    s["label"],
            "prob":     s["prob"],
            "cv_auc":   s["cv_auc"],
            "tier":     s["tier"],
            "best_h":   s["best_h"],
            "best_lb":  s["best_lb"],
            "features": xgb_features,
        })

    return {
        "has_insights":      True,
        "n_passing":         len(passing),
        "reliability":       _describe_reliability(avg_auc, passing),
        "signal_insights":   signal_insights,
        "notable_combos":    _build_combo_insights(passing, corr_df),
        "feature_influence": _build_feature_influence(passing, corr_df, bank),
    }
