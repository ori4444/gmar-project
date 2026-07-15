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
  3. predict_intel_full(conn)                            →  (forecast dict, insights dict)

The bank is feature-consistent: training and inference both use the full
daily df (attacks + discourse), so attack-recurrence features are included.
"""
from __future__ import annotations

import json
import pickle
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

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
    add_derived_discourse, add_rolling_baselines, _compute_rolling_stats, build_features,
    FeatureCache, make_throttled_progress,
)
from analysis.multi_target_analysis import (
    _BASE_OUTCOME_TARGETS, load_target_type_daily,
)

# ─────────────────────────────────────────────────────────────────────────────
#  Paths
# ─────────────────────────────────────────────────────────────────────────────

_DATA_DIR    = Path(__file__).resolve().parents[2] / "data"
_RESULTS_CSV = _DATA_DIR / "analysis" / "multi_target_results.csv"
_BANK_DIR    = _DATA_DIR / "models"
BANK_PATH    = _BANK_DIR / "attack_forecast_bank.pkl"
_META_PATH   = _BANK_DIR / "attack_forecast_meta.json"

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

# Quality gate for candidate (lb, h) configs — replaces a blind pos_rate ceiling.
# High pos_rate alone doesn't mean a config is degenerate (AUC already accounts
# for class balance); what actually breaks a config is either (a) predicted
# probabilities that barely vary across the dataset ("always predict the base
# rate"), or (b) the negative class being concentrated in one contiguous era
# (a one-time regime shift, not a recurring signal).
_PROB_STD_FLOOR = 0.05
_NEG_EARLY_MIN  = 0.15
_NEG_EARLY_MAX  = 0.85
_MIN_NEG_N      = 8


def _diagnose_config(
    df: pd.DataFrame, outcome: dict, lb: int, h: int, cache: FeatureCache | None = None,
) -> dict | None:
    """
    Out-of-fold diagnostic for one (outcome, lb, h) candidate.
    Returns None if the config can't even be evaluated (degenerate y).
    Otherwise returns {"passes": bool, "prob_std":..., "neg_n":..., "neg_early_pct":...}.
    """
    cache = cache or FeatureCache(df)
    X, y, dates = cache.dataset(lb, h, outcome, return_dates=True)
    if X.empty or len(np.unique(y)) < 2:
        return None

    tscv = TimeSeriesSplit(n_splits=CV_SPLITS)
    oof_prob = np.full(len(y), np.nan)
    for tr, te in tscv.split(X):
        ytr = y.iloc[tr]
        if len(np.unique(ytr)) < 2:
            continue
        pos_tr = int(ytr.sum())
        m = _make_xgb(pos_tr, len(ytr) - pos_tr)
        m.fit(X.iloc[tr].values, ytr)
        oof_prob[te] = m.predict_proba(X.iloc[te].values)[:, 1]

    valid = ~np.isnan(oof_prob)
    if valid.sum() < 2:
        return None
    prob_std = float(np.std(oof_prob[valid]))

    dates = pd.Series(dates)
    neg_mask = (y.values == 0) & valid
    neg_n = int(neg_mask.sum())
    if neg_n > 0:
        midpoint = dates.iloc[len(dates) // 2]
        neg_early_pct = float((dates[neg_mask] < midpoint).mean())
    else:
        neg_early_pct = float("nan")

    passes = (
        prob_std >= _PROB_STD_FLOOR
        and neg_n >= _MIN_NEG_N
        and not np.isnan(neg_early_pct)
        and _NEG_EARLY_MIN <= neg_early_pct <= _NEG_EARLY_MAX
    )

    return {
        "passes":       passes,
        "prob_std":     prob_std,
        "neg_n":        neg_n,
        "neg_early_pct": neg_early_pct,
    }


def _load_best_params(
    outcomes: list[dict], df: pd.DataFrame,
    cache: FeatureCache | None = None,
    progress_cb: Callable[[dict], None] | None = None,
) -> dict[str, dict]:
    """
    Parse multi_target_results.csv → best (lb, h) per outcome, filtered through
    the diagnostic gate above. Candidates are tried in descending AUC order;
    the first one that passes the diagnostic wins. If none pass, falls back to
    the best-by-AUC config anyway (so training never silently skips a target)
    but flags it as unverified in the returned dict.
    """
    if not _RESULTS_CSV.exists():
        return {}
    cache = cache or FeatureCache(df)
    res = pd.read_csv(_RESULTS_CSV)
    result: dict[str, dict] = {}
    total = len(outcomes)
    for idx, oc in enumerate(outcomes):
        name = oc["name"]
        grp  = res[res["outcome"] == name].dropna(subset=["auc"]).copy()
        if "n_folds" in grp.columns:
            grp = grp[grp["n_folds"] >= 2]
        if grp.empty:
            if progress_cb:
                progress_cb({
                    "phase": "diagnostics", "milestone": True,
                    "step": idx + 1, "total": total,
                    "target": name, "label": TARGET_LABELS.get(name, name),
                    "status": "skipped",
                })
            continue
        grp = grp.sort_values("auc", ascending=False)

        chosen = None
        diag   = None
        for _, row in grp.iterrows():
            lb, h = int(row["lookback"]), int(row["horizon"])
            d = _diagnose_config(df, oc, lb, h, cache=cache)
            if d is not None and d["passes"]:
                chosen, diag = row, d
                break

        flagged = False
        if chosen is None:
            chosen  = grp.iloc[0]
            diag    = _diagnose_config(df, oc, int(chosen["lookback"]), int(chosen["horizon"]), cache=cache)
            flagged = True

        result[name] = {
            "lb":      int(chosen["lookback"]),
            "h":       int(chosen["horizon"]),
            "auc":     float(chosen["auc"]),
            "auc_std": float(chosen.get("auc_std", float("nan"))),
            "diagnostic_passed": (not flagged),
            "prob_std":          diag.get("prob_std")     if diag else None,
            "neg_early_pct":     diag.get("neg_early_pct") if diag else None,
        }
        if progress_cb:
            progress_cb({
                "phase": "diagnostics", "milestone": True,
                "step": idx + 1, "total": total,
                "target": name, "label": TARGET_LABELS.get(name, name),
                "lookback": result[name]["lb"], "horizon": result[name]["h"],
                "auc": result[name]["auc"],
                "status": "verified" if not flagged else "unverified",
            })
    return result


def _cv_and_train(
    df: pd.DataFrame, outcome: dict, lb: int, h: int,
    cache: FeatureCache | None = None,
) -> dict | None:
    """CV-evaluate + final-train one target. Returns bundle dict or None."""
    cache = cache or FeatureCache(df)
    X, y = cache.dataset(lb, h, outcome)
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
#  Public: train
# ─────────────────────────────────────────────────────────────────────────────

def train_bank(conn, progress_cb: Callable[[dict], None] | None = None) -> dict:
    """
    Train all-target model bank from latest DB data.
    Reads best (lb, h) from multi_target_results.csv.
    Overwrites existing bank. Returns metadata dict.
    """
    if not _RESULTS_CSV.exists():
        raise FileNotFoundError(
            "multi_target_results.csv not found — run Multi-Target Analysis first."
        )
    progress_cb = make_throttled_progress(progress_cb)

    def _emit(phase: str, message: str = ""):
        if progress_cb:
            progress_cb({"phase": phase, "step": 1, "total": 1, "message": message})

    # Distinct phase key from multi_target_analysis's "loading" — this is the
    # second, separate data load (train_bank's own run), and reusing "loading"
    # here would make the UI's stage stepper look like it jumped backwards.
    _emit("preparing", "Loading discourse & attack history…")
    print("Loading data …")
    raw_attacks     = load_attacks(conn)
    disc            = load_discourse(conn)
    ttype_daily, ttype_cols = load_target_type_daily(conn)

    attacks_daily = aggregate_attacks(raw_attacks)
    df = build_full_daily(attacks_daily, disc)
    df = add_derived_discourse(df)
    df = add_rolling_baselines(df)

    if not ttype_daily.empty and ttype_cols:
        df = df.merge(ttype_daily, on="date", how="left")
        df[ttype_cols] = df[ttype_cols].fillna(0).astype(int)

    print(f"  Dataset: {len(df)} rows · "
          f"{df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}")

    outcomes: list[dict] = list(_BASE_OUTCOME_TARGETS)
    for col in ttype_cols:
        outcomes.append({"name": col, "col": col, "type": "binary", "op": "gt0"})

    # Shared across diagnostics + final training below: the same (lb, h) is
    # very often revisited (diagnostic candidates come from the same grid
    # search that produced the CSV), so this avoids rebuilding features twice.
    #
    # max_label_date caps label windows at the last date attacks_daily actually
    # covers, so days past the last reviewed attack_events row are excluded
    # from training rather than silently treated as confirmed-zero-attack days.
    max_label_date = attacks_daily["date"].max() if not attacks_daily.empty else None
    cache = FeatureCache(df, max_label_date=max_label_date)

    best_params = _load_best_params(outcomes, df, cache=cache, progress_cb=progress_cb)
    bank: dict[str, dict] = {}
    meta_outcomes: dict[str, dict] = {}

    print(f"\nTraining {len(outcomes)} targets …")
    total = len(outcomes)
    for idx, oc in enumerate(outcomes):
        name = oc["name"]
        if name not in best_params:
            meta_outcomes[name] = {"status": "skipped — not in analysis results"}
            if progress_cb:
                progress_cb({
                    "phase": "training", "milestone": True,
                    "step": idx + 1, "total": total,
                    "target": name, "label": TARGET_LABELS.get(name, name),
                    "status": "skipped",
                })
            continue

        bp  = best_params[name]
        lb, h = bp["lb"], bp["h"]
        if progress_cb:
            progress_cb({
                "phase": "training", "milestone": True,
                "step": idx + 1, "total": total,
                "target": name, "label": TARGET_LABELS.get(name, name),
                "lookback": lb, "horizon": h, "status": "started",
            })
        bundle = _cv_and_train(df, oc, lb, h, cache=cache)

        if bundle is None:
            meta_outcomes[name] = {"status": "skipped — insufficient data or single class"}
            print(f"  {name:28s}  SKIPPED")
            if progress_cb:
                progress_cb({
                    "phase": "training", "milestone": True,
                    "step": idx + 1, "total": total,
                    "target": name, "label": TARGET_LABELS.get(name, name),
                    "status": "skipped",
                })
            continue

        bank[name] = bundle
        meta_outcomes[name] = {
            "status":            "trained",
            "lb":                lb,
            "h":                 h,
            "cv_auc":            bundle["cv_auc"],
            "cv_std":            bundle["cv_std"],
            "n":                 bundle["n_samples"],
            "pos_rate":          bundle["pos_rate"],
            "diagnostic_passed": bp.get("diagnostic_passed"),
            "prob_std":          bp.get("prob_std"),
            "neg_early_pct":     bp.get("neg_early_pct"),
        }
        auc_s = f"{bundle['cv_auc']:.3f}" if bundle["cv_auc"] == bundle["cv_auc"] else "nan"
        std_s = f"{bundle['cv_std']:.3f}" if bundle["cv_std"] == bundle["cv_std"] else "nan"
        flag  = "" if bp.get("diagnostic_passed") else "  ⚠ UNVERIFIED (failed diagnostic, no better fallback)"
        print(f"  {name:28s}  lb={lb:>2}  h={h:>2}  AUC={auc_s} ±{std_s}{flag}")
        if progress_cb:
            progress_cb({
                "phase": "training", "milestone": True,
                "step": idx + 1, "total": total,
                "target": name, "label": TARGET_LABELS.get(name, name),
                "lookback": lb, "horizon": h,
                "cv_auc": (None if bundle["cv_auc"] != bundle["cv_auc"] else bundle["cv_auc"]),
                "status": "trained",
            })

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
    _emit("done", "Model bank ready")
    return meta


# ─────────────────────────────────────────────────────────────────────────────
#  Prediction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _raw_predict_all(
    bank: dict, df: pd.DataFrame, attacks_end: pd.Timestamp | None = None,
) -> dict[str, dict]:
    """
    Run all bank models against the latest window in df.

    attacks_end, if given and earlier than df's true last date, is used as
    "today" for each model's attack-recurrence sub-features only (see
    build_features's attack_window) — discourse features still read the
    window ending at df's actual last row, since discourse stays current
    even when attack-event review lags behind it.
    """
    disc_cols = [c for c in DISCOURSE_COLS_EXT if c in df.columns]
    rolling_means, rolling_stds = _compute_rolling_stats(df, disc_cols)
    n = len(df)

    attack_end_idx = None
    if attacks_end is not None and attacks_end < df["date"].iloc[-1]:
        attack_end_idx = int(df.index[df["date"] <= attacks_end].max())

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

        attack_window = None
        if attack_end_idx is not None:
            aw_start = max(0, attack_end_idx - lb + 1)
            attack_window = df.iloc[aw_start : attack_end_idx + 1]

        x_feat, _ = build_features(window, disc_cols, bm, bs, attack_window=attack_window)

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


# Minimum lift (current prob − historical base rate) required, on top of the
# absolute prob/auc bars, before a tier fires. Without this, a long-horizon
# high-frequency target (e.g. any_fire over 2-3 weeks) trips "high" purely
# because that IS its normal rate — not because anything unusual is happening.
_MIN_LIFT = {"high": 0.15, "medium": 0.08, "low": 0.03}


def _tier(prob: float, auc: float, base_rate: float | None = None) -> str | None:
    lift = None if base_rate is None or base_rate != base_rate else prob - base_rate

    def _passes(min_prob: float, min_auc: float, tier_name: str) -> bool:
        if prob < min_prob or auc < min_auc:
            return False
        return lift is None or lift >= _MIN_LIFT[tier_name]

    if _passes(0.65, 0.70, "high"):
        return "high"
    if _passes(0.55, 0.62, "medium"):
        return "medium"
    if _passes(0.42, 0.55, "low"):
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
    bank_meta: dict,
    data_through: str,
) -> dict:
    outcome_meta = bank_meta.get("outcomes", {})
    all_signals: list[dict] = []
    for dim, targets in DIMENSIONS.items():
        for t in targets:
            if t not in raw_preds or "error" in raw_preds[t]:
                continue
            pred = raw_preds[t]
            prob = pred["prob"]
            auc  = pred["cv_auc"]
            base_rate = outcome_meta.get(t, {}).get("pos_rate")
            base_rate = base_rate if base_rate == base_rate else None  # NaN guard
            lift = None if base_rate is None else prob - base_rate
            tier = _tier(prob, auc, base_rate)
            all_signals.append({
                "target":    t,
                "label":     TARGET_LABELS.get(t, t),
                "dimension": dim,
                "prob":      prob,
                "cv_auc":    auc,
                "cv_std":    pred.get("cv_std", float("nan")),
                "best_h":    pred["best_h"],
                "best_lb":   pred["best_lb"],
                "base_rate": base_rate,
                "lift":      lift,
                "tier":      tier,
                "passes":    tier is not None,
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
        "warning_level":  warning,
        "near_term":      near_term,
        "weekly":         _make_band("weekly"),
        "extended":       _make_band("extended"),
        "quality": {
            "n_models":   bank_meta.get("n_models", 0),
            "avg_auc":    avg_auc,
            "trained_at": bank_meta.get("trained_at", "?"),
        },
        # Full unfiltered list (incl. non-passing signals) — bands above only
        # keep the ones that passed. build_model_insights needs the rest too,
        # e.g. to report near-misses during a quiet reading.
        "all_signals":    all_signals,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Public: predict
# ─────────────────────────────────────────────────────────────────────────────

def _load_predict_df(conn) -> tuple[pd.DataFrame, pd.Timestamp]:
    """
    Full daily feature frame, incl. per-target-type columns (needed for
    temporal-pattern mining), same shape train_bank trains against. Also
    returns `attacks_end` — the true last date the events table itself has a
    row for.

    build_full_daily's date spine runs to the LATER of the attacks feed and
    the discourse feed. In practice the events table has repeatedly lagged
    discourse/telegram monitoring by months (attack ingestion stalls while
    news monitoring keeps running) — so the tail of every attack-derived
    column (attack_count, fire_count, ttype_*, ...) beyond `attacks_end` is
    zero-fill, not "nothing happened." Anything computing a recurrence gap
    or "days since last event" must clamp "today" to attacks_end, or a stale
    events table reads as a sudden, dramatic lull.
    """
    raw_attacks   = load_attacks(conn)
    disc          = load_discourse(conn)
    attacks_daily = aggregate_attacks(raw_attacks)
    attacks_end   = attacks_daily["date"].max() if not attacks_daily.empty else disc["feature_date"].max()
    df = build_full_daily(attacks_daily, disc)
    df = add_derived_discourse(df)
    df = add_rolling_baselines(df)

    ttype_daily, ttype_cols = load_target_type_daily(conn)
    if not ttype_daily.empty and ttype_cols:
        df = df.merge(ttype_daily, on="date", how="left")
        df[ttype_cols] = df[ttype_cols].fillna(0).astype(int)

    return df, attacks_end


def predict_intel(conn) -> dict:
    """
    Load model bank, build full daily df, run all models against latest window.
    Returns a structured intelligence forecast dict.
    """
    bank = _load_bank()
    if bank is None:
        raise RuntimeError("No trained model bank found — train first.")

    meta = get_bank_meta() or {}
    df, attacks_end = _load_predict_df(conn)

    data_through = str(df["date"].iloc[-1].date())
    raw_preds    = _raw_predict_all(bank, df, attacks_end)

    return _build_intel_forecast(raw_preds, meta, data_through)


def predict_intel_full(conn) -> tuple[dict, dict]:
    """
    Same as predict_intel(), but also builds the model-insights dict off the
    same already-loaded df — avoiding a second DB round trip — so temporal
    patterns and feature clusters (which need the raw daily frame, not just
    the forecast) are available too. Preferred entry point for any UI that
    shows both the forecast and its "why" panel.
    """
    bank = _load_bank()
    if bank is None:
        raise RuntimeError("No trained model bank found — train first.")

    meta = get_bank_meta() or {}
    df, attacks_end = _load_predict_df(conn)

    data_through = str(df["date"].iloc[-1].date())
    raw_preds    = _raw_predict_all(bank, df, attacks_end)
    forecast     = _build_intel_forecast(raw_preds, meta, data_through)
    insights     = build_model_insights(forecast, df, attacks_end)

    return forecast, insights


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


# ─────────────────────────────────────────────────────────────────────────────
#  Temporal patterns — recurring day-of-week / gap-between-events structure,
#  mined straight from history. Independent of any live model prediction, so
#  it stays useful even when nothing is currently crossing a signal threshold.
# ─────────────────────────────────────────────────────────────────────────────

_OUTCOME_DAILY_META: dict[str, dict] = {oc["name"]: oc for oc in _BASE_OUTCOME_TARGETS}

_DOW_HE = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]  # dt.dayofweek: Mon=0..Sun=6


def _daily_occurred(df: pd.DataFrame, target: str) -> pd.Series | None:
    """
    Boolean series: did `target` occur on each day of df. A single-day
    threshold read of the same column/op used for the ML label — good enough
    for mining day-of-week / recurrence structure, distinct from (and simpler
    than) the label's forward-window aggregation.
    """
    if target.startswith("ttype_"):
        if target not in df.columns:
            return None
        return df[target] > 0
    meta = _OUTCOME_DAILY_META.get(target)
    if meta is None or meta["col"] not in df.columns:
        return None
    col = df[meta["col"]]
    op  = meta["op"]
    if op == "gt0":
        return col > 0
    if op == "ge3":
        return col >= 3
    if op in ("sum_ge", "max_ge"):
        return col >= meta.get("threshold", 1)
    return None


def _build_temporal_patterns(
    df: pd.DataFrame, attacks_end: pd.Timestamp | None = None,
) -> list[dict]:
    # "Today" for recurrence-gap purposes must be the events table's real
    # last row, not df's date spine (which can run further if discourse
    # monitoring outpaced attack ingestion) — see _load_predict_df.
    today = attacks_end if attacks_end is not None else df["date"].iloc[-1]
    patterns: list[dict] = []

    for dim, targets in DIMENSIONS.items():
        for t in targets:
            occurred = _daily_occurred(df, t)
            if occurred is None:
                continue
            dates = df.loc[occurred, "date"].sort_values()
            n = len(dates)
            if n < 8:
                continue

            entry = {
                "target": t, "label": TARGET_LABELS.get(t, t), "dimension": dim,
                "n_events": n, "dow": None, "gap": None,
            }
            score = 0.0

            dow_counts = dates.dt.dayofweek.value_counts(normalize=True)
            top_dow, top_share = dow_counts.idxmax(), float(dow_counts.max())
            dow_skew = top_share - (1 / 7)
            if dow_skew >= 0.08:
                entry["dow"] = {"day": _DOW_HE[int(top_dow)], "share": top_share}
                score = max(score, dow_skew)

            gaps = dates.diff().dt.days.dropna()
            if len(gaps) >= 4:
                median_gap = float(gaps.median())
                current_gap = (today - dates.iloc[-1]).days
                if median_gap > 0:
                    ratio = current_gap / median_gap
                    status = "overdue" if ratio >= 1.3 else ("recent" if ratio <= 0.6 else "typical")
                    entry["gap"] = {
                        "median_days": median_gap, "current_days": current_gap,
                        "status": status,
                    }
                    if status == "overdue":
                        score = max(score, min(ratio - 1.0, 1.5))

            if entry["dow"] or entry["gap"]:
                entry["_score"] = score
                patterns.append(entry)

    patterns.sort(key=lambda x: x["_score"], reverse=True)
    for p in patterns:
        del p["_score"]
    return patterns[:6]


def _build_situation_report(all_signals: list[dict], patterns: list[dict]) -> dict:
    """
    Always-populated read of "what's actually going on" — used when nothing
    has crossed a signal threshold, so a quiet reading means something
    concrete instead of just "no clear signs."
    """
    non_passing = [s for s in all_signals if not s["passes"] and s.get("lift") is not None]
    near_misses = sorted(non_passing, key=lambda s: s["lift"], reverse=True)[:3]
    near_misses = [s for s in near_misses if s["lift"] > -0.05]

    overdue = [p for p in patterns if (p.get("gap") or {}).get("status") == "overdue"][:3]

    return {
        "near_misses": [
            {
                "target": s["target"], "label": s["label"], "dimension": s["dimension"],
                "prob": s["prob"], "base_rate": s["base_rate"], "lift": s["lift"],
            }
            for s in near_misses
        ],
        "overdue": [
            {
                "target": p["target"], "label": p["label"], "dimension": p["dimension"],
                "current_days": p["gap"]["current_days"], "median_days": p["gap"]["median_days"],
            }
            for p in overdue
        ],
    }


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


_CLUSTER_MIN_R = 0.55


def _build_feature_clusters(df: pd.DataFrame) -> list[dict]:
    """
    Groups of discourse features that historically move together, surfaced
    only when a whole group is jointly elevated right now — independent of
    any single target's prediction.
    """
    disc_cols = [c for c in DISCOURSE_COLS_EXT if c in df.columns]
    if len(disc_cols) < 2:
        return []

    corr = df[disc_cols].corr()
    means, stds = _compute_rolling_stats(df, disc_cols)
    recent = df[disc_cols].iloc[-3:].mean()
    z = (recent - means.iloc[-1]) / stds.iloc[-1]

    clusters: list[list[str]] = []
    assigned: set[str] = set()
    for col in disc_cols:
        if col in assigned:
            continue
        partners = [
            other for other in disc_cols
            if other != col and other not in assigned
            and abs(float(corr.loc[col, other])) >= _CLUSTER_MIN_R
        ]
        if not partners:
            continue
        group = [col] + partners
        clusters.append(group)
        assigned.update(group)

    result: list[dict] = []
    for group in clusters:
        z_vals = [float(z.get(c, 0.0)) for c in group]
        active = sum(1 for v in z_vals if v >= 1.0)
        if active < 2:
            continue
        avg_r = float(np.mean([
            abs(float(corr.loc[a, b])) for i, a in enumerate(group) for b in group[i + 1:]
        ]))
        result.append({
            "members":   [_feat_to_readable(c) for c in group],
            "avg_z":     float(np.mean(z_vals)),
            "avg_r":     avg_r,
            "n_active":  active,
            "n_members": len(group),
        })

    result.sort(key=lambda x: x["avg_z"], reverse=True)
    return result[:4]


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


def build_model_insights(
    forecast: dict,
    df: pd.DataFrame | None = None,
    attacks_end: pd.Timestamp | None = None,
) -> dict:
    """
    Generate human-readable intelligence insights from the current forecast.
    Returns a structured dict consumed by ModelInsightsPanel in predictions.py.

    `df` is the same daily feature frame the forecast was built from, and
    `attacks_end` its true attack-data cutoff (both from predict_intel_full).
    Optional for backward compatibility, but without them the situation
    report/temporal patterns/feature clusters can't be built — callers
    should prefer predict_intel_full() over calling predict_intel() +
    build_model_insights() separately.
    """
    all_signals = forecast.get("all_signals")
    if all_signals is not None:
        passing = [s for s in all_signals if s.get("passes")]
    else:
        passing = []
        for band_key in ("near_term", "weekly", "extended"):
            for dim_sigs in forecast.get(band_key, {}).get("signals", {}).values():
                passing.extend(s for s in dim_sigs if s.get("passes"))

    corr_df = None
    if _CORR_CSV.exists():
        try:
            corr_df = pd.read_csv(_CORR_CSV, index_col=0)
        except Exception:
            pass

    bank = _load_bank()

    aucs = [s["cv_auc"] for s in passing if s.get("cv_auc") == s.get("cv_auc")]
    if aucs:
        avg_auc = float(np.mean(aucs))
    else:
        # No passing signal to average over (quiet reading) — fall back to
        # the bank's overall accuracy so the reliability card still means
        # something instead of going blank.
        avg_auc = forecast.get("quality", {}).get("avg_auc", float("nan"))

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
            "target":    t,
            "label":     s["label"],
            "prob":      s["prob"],
            "cv_auc":    s["cv_auc"],
            "tier":      s["tier"],
            "best_h":    s["best_h"],
            "best_lb":   s["best_lb"],
            "base_rate": s.get("base_rate"),
            "lift":      s.get("lift"),
            "features":  xgb_features,
        })

    temporal_patterns = _build_temporal_patterns(df, attacks_end) if df is not None else []
    feature_clusters  = _build_feature_clusters(df) if df is not None else []
    situation = (
        _build_situation_report(all_signals, temporal_patterns)
        if all_signals is not None else
        {"near_misses": [], "overdue": []}
    )

    # Events-table staleness is common enough here (attack ingestion has
    # repeatedly lagged discourse monitoring by months) that it needs to be
    # a visible fact, not just quietly compensated for in the gap math above.
    data_gap_days = None
    if df is not None and attacks_end is not None:
        lag = (df["date"].iloc[-1] - attacks_end).days
        if lag >= 14:
            data_gap_days = lag

    has_insights = bool(
        passing or situation["near_misses"] or situation["overdue"]
        or temporal_patterns or feature_clusters or data_gap_days
    )
    if not has_insights:
        return {"has_insights": False, "reason": "No data available."}

    return {
        "has_insights":      True,
        "n_passing":         len(passing),
        "data_gap_days":     data_gap_days,
        "attacks_data_through": str(attacks_end.date()) if attacks_end is not None else None,
        "reliability":       _describe_reliability(avg_auc, passing),
        "situation":         situation,
        "signal_insights":   signal_insights,
        "notable_combos":    _build_combo_insights(passing, corr_df),
        "feature_influence": _build_feature_influence(passing, corr_df, bank),
        "temporal_patterns": temporal_patterns,
        "feature_clusters":  feature_clusters,
    }
