"""
scripts/analysis/multi_target_analysis.py
────────────────────────────────────────────────────────────────────────────
Multi-target analysis: which discourse feature patterns predict
different attack characteristics — not just "will there be an attack?"

Phases
──────
1. ML grid search over (outcome × lookback × horizon):
     binary targets  → LogisticRegression + AUC
     count targets   → Ridge regression   + R²
   Saved: data/analysis/multi_target_results.csv

2. Feature × outcome correlation matrix (lb=10, h=1):
   Pearson r between each discourse feature and each outcome.
   Saved: data/analysis/feature_outcome_corr.csv

3. Attack profile clustering (K-means on discourse features, lb=10):
   Each cluster answers "what attacks follow this discourse pattern?"
   Saved: data/analysis/attack_profiles.csv
          data/analysis/attack_profiles.json  (UI-ready)

Usage
─────
    python scripts/analysis/multi_target_analysis.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from sklearn.cluster import KMeans
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.config import DB_DSN, EVENTS_TABLE, ET_TABLE, TARGETS_TABLE
from analysis.window_analysis import (
    load_attacks, load_discourse,
    aggregate_attacks, build_full_daily,
    DISCOURSE_COLS, DISCOURSE_COLS_EXT, CV_SPLITS,
    add_derived_discourse, _compute_rolling_stats, build_features, BASELINE_WINDOW,
    MAX_POS_RATE,
)

# ─────────────────────────────────────────────────────────────────────────────

LOOKBACKS  = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14, 21, 30]
HORIZONS   = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14, 21]
N_PROFILES = 5
PROFILE_LOOKBACK = 10
PROFILE_HORIZON  = 3

# op definitions:
#   gt0  → int(window_sum > 0)
#   ge3  → int(window_max >= 3)
#   sum  → float(window_sum)
#   max  → float(window_max)
_BASE_OUTCOME_TARGETS: list[dict] = [
    # Core binary outcomes
    {"name": "any_attack",      "col": "attack_count",    "type": "binary", "op": "gt0"},
    {"name": "significant",     "col": "significant",     "type": "binary", "op": "gt0"},
    {"name": "any_fire",        "col": "fire_count",      "type": "binary", "op": "gt0"},
    {"name": "any_hit",         "col": "hit_count",       "type": "binary", "op": "gt0"},
    {"name": "any_shutdown",    "col": "shutdown_count",  "type": "binary", "op": "gt0"},
    {"name": "deep_strike",     "col": "depth_score",     "type": "binary", "op": "ge3"},
    {"name": "combined_strike",  "col": "combined_count",  "type": "binary", "op": "gt0"},
    {"name": "repeated_strike", "col": "repeated_count",  "type": "binary", "op": "gt0"},
    # Threshold-based binary (replacing regression targets)
    {"name": "multi_attack",    "col": "attack_count",    "type": "binary", "op": "sum_ge", "threshold": 3},
    {"name": "any_explosion",   "col": "explosion_total", "type": "binary", "op": "gt0"},
    {"name": "very_deep",       "col": "depth_score",     "type": "binary", "op": "max_ge", "threshold": 4},
    {"name": "multi_fire",      "col": "fire_count",      "type": "binary", "op": "sum_ge", "threshold": 2},
]

# Target-type outcomes are added dynamically after loading the DB
_TTYPE_MIN_OCCURRENCES = 5


# ─────────────────────────────────────────────────────────────────────────────
#  Target-type data loading
# ─────────────────────────────────────────────────────────────────────────────

def load_target_type_daily(conn) -> tuple[pd.DataFrame, list[str]]:
    """
    Returns (daily_df, ttype_col_names) where daily_df has one row per date
    and a binary column for each sufficiently common target type.
    Column names are prefixed with 'ttype_'.
    """
    sql = f"""
        SELECT
            e.attack_date,
            lower(trim(t.target_type)) AS target_type
        FROM {EVENTS_TABLE} e
        JOIN {ET_TABLE} et ON et.event_id = e.event_id
        JOIN {TARGETS_TABLE} t ON t.target_id = et.target_id
        WHERE e.status = 'active'
          AND t.target_type IS NOT NULL
          AND lower(trim(t.target_type)) NOT IN ('unknown', 'other', '')
        ORDER BY e.attack_date
    """
    raw = pd.read_sql(sql, conn, parse_dates=["attack_date"])
    if raw.empty:
        return pd.DataFrame(columns=["date"]), []

    counts = raw["target_type"].value_counts()
    keep   = counts[counts >= _TTYPE_MIN_OCCURRENCES].index.tolist()
    raw    = raw[raw["target_type"].isin(keep)]
    if raw.empty:
        return pd.DataFrame(columns=["date"]), []

    raw["value"] = 1
    pivot = (
        raw.groupby(["attack_date", "target_type"])["value"]
        .max()
        .unstack(fill_value=0)
        .reset_index()
        .rename(columns={"attack_date": "date"})
    )
    type_cols = [c for c in pivot.columns if c != "date"]
    rename = {c: f"ttype_{c.replace(' ', '_').replace('/', '_')}" for c in type_cols}
    pivot.rename(columns=rename, inplace=True)
    ttype_cols = list(rename.values())

    return pivot, ttype_cols


# ─────────────────────────────────────────────────────────────────────────────
#  Outcome computation
# ─────────────────────────────────────────────────────────────────────────────

def _compute_outcome(future_slice: pd.DataFrame, outcome: dict) -> float:
    col = outcome["col"]
    op  = outcome["op"]
    if col not in future_slice.columns:
        return 0.0
    if op == "gt0":
        return float(future_slice[col].sum() > 0)
    if op == "ge3":
        return float(future_slice[col].max() >= 3)
    if op == "sum_ge":
        return float(future_slice[col].sum() >= outcome.get("threshold", 1))
    if op == "max_ge":
        return float(future_slice[col].max() >= outcome.get("threshold", 1))
    # Legacy ops kept for backward compatibility
    if op == "sum":
        return float(future_slice[col].sum())
    if op == "max":
        return float(future_slice[col].max())
    raise ValueError(f"Unknown op: {op!r}")


def build_multi_dataset(
    df: pd.DataFrame, lookback: int, horizon: int, outcome: dict,
) -> tuple[pd.DataFrame, pd.Series]:
    """X = temporal discourse features over [D-lookback, D-1], y = outcome over [D, D+horizon-1]."""
    disc_cols = [c for c in DISCOURSE_COLS_EXT if c in df.columns]
    rolling_means, rolling_stds = _compute_rolling_stats(df, disc_cols)
    X_rows, y_rows = [], []
    feat_names = None
    n = len(df)

    for i in range(lookback, n - horizon + 1):
        window = df.iloc[i - lookback : i]
        future = df.iloc[i : i + horizon]
        bm = rolling_means.iloc[i - 1]
        bs = rolling_stds.iloc[i - 1]
        x, names = build_features(window, disc_cols, bm, bs)
        if feat_names is None:
            feat_names = names
        X_rows.append(x)
        y_rows.append(_compute_outcome(future, outcome))

    if not X_rows:
        return pd.DataFrame(), pd.Series(dtype=float, name=outcome["name"])

    X = pd.DataFrame(X_rows, columns=feat_names)
    y = pd.Series(y_rows, name=outcome["name"])
    return X, y


# ─────────────────────────────────────────────────────────────────────────────
#  Evaluators
# ─────────────────────────────────────────────────────────────────────────────

def _make_xgb(pos: int, neg: int) -> XGBClassifier:
    return XGBClassifier(
        n_estimators=100, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.6, min_child_weight=5,
        scale_pos_weight=neg / max(pos, 1),
        eval_metric="logloss", random_state=42, verbosity=0,
    )


def _evaluate_binary(X: pd.DataFrame, y: pd.Series) -> dict:
    base = {"auc": float("nan"), "auc_std": float("nan"),
            "n": len(X), "n_folds": 0, "pos_rate": float(y.mean())}
    if len(np.unique(y)) < 2:
        return base

    tscv = TimeSeriesSplit(n_splits=CV_SPLITS)
    aucs: list[float] = []

    for tr, te in tscv.split(X):
        Xtr, Xte = X.iloc[tr].values, X.iloc[te].values
        ytr, yte = y.iloc[tr], y.iloc[te]
        if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
            continue
        pos_tr = int(ytr.sum())
        m = _make_xgb(pos_tr, len(ytr) - pos_tr)
        m.fit(Xtr, ytr)
        aucs.append(roc_auc_score(yte, m.predict_proba(Xte)[:, 1]))

    return {**base,
            "auc":     float(np.mean(aucs)) if aucs else float("nan"),
            "auc_std": float(np.std(aucs))  if aucs else float("nan"),
            "n_folds": len(aucs)}


# ─────────────────────────────────────────────────────────────────────────────
#  Phase 1 — Grid search
# ─────────────────────────────────────────────────────────────────────────────

def run_analysis(df: pd.DataFrame, outcomes: list[dict]) -> pd.DataFrame:
    rows = []
    for outcome in outcomes:
        if outcome["type"] != "binary":
            continue
        best_val, best_info = float("-inf"), {}
        best_val_fb, best_info_fb = float("-inf"), {}  # fallback (ignores pos gate)

        for lookback in LOOKBACKS:
            for horizon in HORIZONS:
                X, y = build_multi_dataset(df, lookback, horizon, outcome)
                m   = _evaluate_binary(X, y)
                val = m.get("auc", float("nan"))
                row = {"outcome": outcome["name"], "type": "binary",
                       "lookback": lookback, "horizon": horizon, **m}
                rows.append(row)
                if np.isnan(val) or m.get("n_folds", 0) < 2:
                    continue
                if val > best_val_fb:
                    best_val_fb, best_info_fb = val, row
                if m.get("pos_rate", 1.0) <= MAX_POS_RATE and val > best_val:
                    best_val, best_info = val, row

        # Fall back to unconstrained best if nothing passed the pos-rate gate
        if not best_info and best_info_fb:
            best_val, best_info = best_val_fb, best_info_fb
            flag = f"  ⚠ pos={best_info['pos_rate']:.0%} > {MAX_POS_RATE:.0%} (saturated)"
        else:
            flag = ""

        if best_info:
            print(f"  {outcome['name']:22s}  best lb={int(best_info['lookback']):>2} "
                  f"h={int(best_info['horizon'])}  "
                  f"auc={best_val:.3f} ±{best_info.get('auc_std', float('nan')):.3f}  "
                  f"pos={best_info['pos_rate']:.1%}{flag}")
        else:
            print(f"  {outcome['name']:22s}  no valid model")

    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────────────────────
#  Phase 2 — Feature × outcome correlation matrix
# ─────────────────────────────────────────────────────────────────────────────

def compute_feature_corr(df: pd.DataFrame, outcomes: list[dict],
                         lookback: int = 10, horizon: int = 1) -> pd.DataFrame:
    """Pearson r between each temporal feature representation and each outcome at (lb, h)."""
    disc_cols = [c for c in DISCOURSE_COLS_EXT if c in df.columns]
    rolling_means, rolling_stds = _compute_rolling_stats(df, disc_cols)
    n = len(df)
    feat_rows, outcome_rows, feat_names = [], [], None

    for i in range(lookback, n - horizon + 1):
        window = df.iloc[i - lookback : i]
        future = df.iloc[i : i + horizon]
        bm = rolling_means.iloc[i - 1]
        bs = rolling_stds.iloc[i - 1]
        x, names = build_features(window, disc_cols, bm, bs)
        if feat_names is None:
            feat_names = names
        feat_rows.append(x)
        outcome_rows.append([_compute_outcome(future, oc) for oc in outcomes])

    if not feat_rows:
        return pd.DataFrame()

    X = pd.DataFrame(feat_rows, columns=feat_names)
    Y = pd.DataFrame(outcome_rows, columns=[oc["name"] for oc in outcomes])

    return pd.DataFrame(
        {oc: X.corrwith(Y[oc]) for oc in Y.columns},
        index=feat_names,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Phase 3 — Attack profiles (K-means)
# ─────────────────────────────────────────────────────────────────────────────

def find_attack_profiles(df: pd.DataFrame, outcomes: list[dict],
                         lookback: int = PROFILE_LOOKBACK,
                         horizon: int  = PROFILE_HORIZON,
                         n_clusters: int = N_PROFILES) -> tuple[pd.DataFrame, list[dict]]:
    """
    Cluster discourse feature windows into N profiles.
    Returns (profiles_df, profiles_json_list).
    """
    disc_cols = [c for c in DISCOURSE_COLS_EXT if c in df.columns]
    rolling_means, rolling_stds = _compute_rolling_stats(df, disc_cols)
    n = len(df)
    feat_rows, outcome_rows, feat_names = [], [], None

    for i in range(lookback, n - horizon + 1):
        window = df.iloc[i - lookback : i]
        future = df.iloc[i : i + horizon]
        bm = rolling_means.iloc[i - 1]
        bs = rolling_stds.iloc[i - 1]
        x, names = build_features(window, disc_cols, bm, bs)
        if feat_names is None:
            feat_names = names
        feat_rows.append(x)
        outcome_rows.append([_compute_outcome(future, oc) for oc in outcomes])

    X = pd.DataFrame(feat_rows, columns=feat_names)
    Y = pd.DataFrame(outcome_rows, columns=[oc["name"] for oc in outcomes])

    scaler = StandardScaler()
    X_s    = scaler.fit_transform(X)
    km     = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = km.fit_predict(X_s)

    rows, json_out = [], []

    for k in range(n_clusters):
        mask = labels == k
        cx   = km.cluster_centers_[k]
        dominant   = [feat_names[i] for i, z in enumerate(cx) if z >  0.5]
        suppressed = [feat_names[i] for i, z in enumerate(cx) if z < -0.5]

        outcome_means = {oc: float(Y[oc][mask].mean()) for oc in Y.columns}

        row = {
            "cluster": k + 1,
            "n":       int(mask.sum()),
            "dominant_features":   ", ".join(dominant)   or "—",
            "suppressed_features": ", ".join(suppressed) or "—",
            **outcome_means,
        }
        rows.append(row)

        # Human-readable label: summarize the dominant pattern
        if dominant:
            label = "High: " + ", ".join(dominant[:3])
        else:
            label = "Neutral / low discourse"
        if suppressed:
            label += " | Low: " + ", ".join(suppressed[:2])

        json_out.append({
            "cluster":    k + 1,
            "n":          int(mask.sum()),
            "label":      label,
            "dominant":   dominant,
            "suppressed": suppressed,
            "outcomes":   outcome_means,
        })

    profiles_df = pd.DataFrame(rows).sort_values("cluster").reset_index(drop=True)
    json_out    = sorted(json_out, key=lambda d: d["cluster"])
    return profiles_df, json_out


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def run() -> None:
    print("Connecting to database …")
    conn        = psycopg2.connect(DB_DSN)
    raw_attacks = load_attacks(conn)
    disc        = load_discourse(conn)
    ttype_daily, ttype_cols = load_target_type_daily(conn)
    conn.close()

    attacks_daily = aggregate_attacks(raw_attacks)
    df = build_full_daily(attacks_daily, disc)
    df = add_derived_discourse(df)

    # Merge target-type binary columns into df
    if not ttype_daily.empty and ttype_cols:
        df = df.merge(ttype_daily, on="date", how="left")
        df[ttype_cols] = df[ttype_cols].fillna(0).astype(int)
        print(f"  Target types loaded: {', '.join(ttype_cols)}")
    else:
        print("  No target types found (or all below threshold).")

    print(f"  Dataset: {len(df)} rows | "
          f"{df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}")

    # Build full outcome list (base + target-type binary outcomes)
    outcomes: list[dict] = list(_BASE_OUTCOME_TARGETS)
    for col in ttype_cols:
        outcomes.append({"name": col, "col": col, "type": "binary", "op": "gt0"})

    out_dir = Path(__file__).resolve().parents[2] / "data" / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Phase 1 ───────────────────────────────────────────────────────────────
    print(f"\n{'═'*72}")
    print("PHASE 1 — Multi-target grid search")
    print(f"  (lookbacks={LOOKBACKS}, horizons={HORIZONS})")
    print(f"{'─'*72}")

    results = run_analysis(df, outcomes)
    results_path = out_dir / "multi_target_results.csv"
    results.to_csv(results_path, index=False)
    print(f"\n  Saved → {results_path}")

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    print(f"\n{'═'*72}")
    print("PHASE 2 — Feature × Outcome correlation  (lb=10, h=1)")
    print(f"{'─'*72}")

    corr = compute_feature_corr(df, outcomes, lookback=10, horizon=1)
    print(corr.round(3).to_string())

    corr_path = out_dir / "feature_outcome_corr.csv"
    corr.to_csv(corr_path)
    print(f"\n  Saved → {corr_path}")

    # ── Phase 3 ───────────────────────────────────────────────────────────────
    print(f"\n{'═'*72}")
    print(f"PHASE 3 — Attack profiles  (K={N_PROFILES}, lb={PROFILE_LOOKBACK}, h={PROFILE_HORIZON})")
    print(f"{'─'*72}")

    profiles_df, profiles_json = find_attack_profiles(df, outcomes)

    binary_oc  = [oc["name"] for oc in outcomes if oc["type"] == "binary"][:5]
    print(f"\n  {'#':>3}  {'n':>5}  {'dominant features':<38}  "
          + "  ".join(f"{o:>12}" for o in binary_oc))
    print(f"  {'─'*80}")
    for _, row in profiles_df.iterrows():
        vals = "  ".join(f"{row[o]:>12.1%}" for o in binary_oc)
        print(f"  {int(row['cluster']):>3}  {int(row['n']):>5}  "
              f"{row['dominant_features']:<38}  {vals}")

    profiles_csv  = out_dir / "attack_profiles.csv"
    profiles_json_path = out_dir / "attack_profiles.json"
    profiles_df.to_csv(profiles_csv, index=False)
    with open(profiles_json_path, "w", encoding="utf-8") as f:
        json.dump(profiles_json, f, indent=2, ensure_ascii=False)

    print(f"\n  Saved → {profiles_csv}")
    print(f"  Saved → {profiles_json_path}")
    print(f"\n{'═'*72}")


if __name__ == "__main__":
    run()
