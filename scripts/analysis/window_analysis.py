"""
scripts/analysis/window_analysis.py
────────────────────────────────────────────────────────────────────────────
Time-window sensitivity analysis: which (lookback, horizon) pairs produce
the strongest predictive signal from discourse features to attack outcomes.

Experiment structure
────────────────────
Phase 1 — Cross-correlation (model-free)
    For each discourse feature, compute Pearson correlation with the daily
    attack signal at lags -14 … +14 days.  Positive lag = discourse leads
    attacks.  Results saved to data/analysis/crosscorr.csv.

Phase 2 — ML grid search
    For every (lookback, horizon) pair in the expanded grid:
      • LogisticRegression with TimeSeriesSplit CV
      • Reports AUC ± std, F1 ± std
      • Compares against a discourse-free baseline (intercept only)
    Results saved to data/analysis/window_analysis.csv.

Phase 3 — Per-feature permutation importance
    For the top N_TOP_COMBOS combinations by AUC, compute permutation
    importance across the 9 discourse features.
    Results saved to data/analysis/feature_importance.csv.

Phase 4 — Temporal stability
    Splits the timeline at its midpoint and reruns Phase 2 on each half
    independently.  Stable windows rank well in both halves.
    Results saved to data/analysis/stability.csv.

Usage
─────
    python scripts/analysis/window_analysis.py [--out DIR] [--phases 1234]

    --out DIR      Directory for output files (default: data/analysis/)
    --phases STR   Which phases to run, e.g. "12" runs only phases 1 & 2
                   (default: all four phases)
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from sklearn.dummy import DummyClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.config import DB_DSN, EVENTS_TABLE, FEATURES_TABLE, CHANNEL_USERNAME

# ─────────────────────────────────────────────────────────────────────────────
#  Settings
# ─────────────────────────────────────────────────────────────────────────────

LOOKBACKS  = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14, 21, 30]
HORIZONS   = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14, 21]
CV_SPLITS  = 3
MAX_LAG    = 14          # days, for cross-correlation
N_TOP_COMBOS = 3         # combos to analyse in Phase 3

# Quality gates for config selection
MAX_POS_RATE = 0.70   # above → problem saturated (model learns "always yes")
MAX_AUC_STD  = 0.15   # above → unstable across folds
MIN_LIFT     = 0.01   # must beat baseline by at least 1 pp
MIN_AUC      = 0.52   # absolute floor — too close to random

DISCOURSE_COLS = [
    "pre_drone",
    "pre_airdef",
    "pre_airport",
    "pre_uncert",
    "en_attack",
    "en_confirm",
    "en_refinery",
    "war_total",
    "war_ukr_ru",
]

# Depth score per macro-region: how far from the front line is the target?
# Scale 1 (border) → 5 (Siberia/Far East)
DEPTH_SCORES: dict[str, int] = {
    "Border / Western Russia": 1,
    "Occupied Territories":    1,
    "Southern Russia":         2,
    "Northwest Russia":        2,
    "Central Russia":          3,
    "Volga Region":            4,
    "Ural":                    4,
    "Far East Russia":         5,
    "Siberia":                 5,
}

ATTACK_FEATURE_COLS = ["depth_score"]
ALL_FEATURE_COLS    = DISCOURSE_COLS + ATTACK_FEATURE_COLS
BASELINE_WINDOW     = 30   # days of history used for z-score baseline

# Extended discourse: original 9 + derived Russia-domestic war signal
DISCOURSE_COLS_EXT  = DISCOURSE_COLS + ["war_ru_internal"]


# ─────────────────────────────────────────────────────────────────────────────
#  Data loading
# ─────────────────────────────────────────────────────────────────────────────

def load_attacks(conn) -> pd.DataFrame:
    sql = f"""
        SELECT
            e.attack_date,
            e.macro_region,
            e.fire_reported::int     AS fire,
            e.hit_confirmed::int     AS hit_confirmed,
            e.shutdown_reported::int AS shutdown,
            e.damage_level,
            e.combined_strike::int   AS combined_strike,
            EXISTS (
                SELECT 1 FROM {EVENTS_TABLE} e2
                WHERE e2.area = e.area
                  AND e2.status = 'active'
                  AND e2.event_id != e.event_id
                  AND e2.attack_date BETWEEN e.attack_date - INTERVAL '30 days'
                                         AND e.attack_date - INTERVAL '1 day'
            )::int                   AS repeated_attack,
            e.explosions_count
        FROM {EVENTS_TABLE} e
        WHERE e.status = 'active'
        ORDER BY e.attack_date
    """
    return pd.read_sql(sql, conn, parse_dates=["attack_date"])


def load_discourse(conn) -> pd.DataFrame:
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
        ORDER BY feature_date
    """
    return pd.read_sql(sql, conn, parse_dates=["feature_date"])


# ─────────────────────────────────────────────────────────────────────────────
#  Daily attack aggregation
# ─────────────────────────────────────────────────────────────────────────────

_EXTRA_ATTACK_COLS = [
    "fire_count", "hit_count", "shutdown_count", "explosion_total",
    "combined_count", "repeated_count",
]


def aggregate_attacks(raw: pd.DataFrame) -> pd.DataFrame:
    """Collapse raw per-event attacks to one row per day."""
    if raw.empty:
        return pd.DataFrame(columns=(
            ["date", "attack_count", "significant", "depth_score"] + _EXTRA_ATTACK_COLS
        ))

    raw = raw.copy()
    raw["damage_score"] = raw["damage_level"].map(
        {"high": 3, "medium": 2, "low": 1}
    ).fillna(0)

    raw["significant"] = (
        (raw["hit_confirmed"] == 1) | (raw["fire"] == 1) | (raw["damage_score"] >= 2)
    ).astype(int)

    # Max depth of any attack that day (0 if macro_region unknown)
    raw["depth_score"] = raw["macro_region"].map(DEPTH_SCORES).fillna(0).astype(int)

    daily = (
        raw.groupby("attack_date")
        .agg(
            attack_count   =("attack_date",      "count"),
            significant    =("significant",      "max"),
            depth_score    =("depth_score",      "max"),
            fire_count     =("fire",             "sum"),
            hit_count      =("hit_confirmed",    "sum"),
            shutdown_count =("shutdown",         "sum"),
            explosion_total=("explosions_count", "sum"),
            combined_count =("combined_strike",  "sum"),
            repeated_count =("repeated_attack",  "sum"),
        )
        .reset_index()
        .rename(columns={"attack_date": "date"})
    )
    return daily


def build_full_daily(
    attacks_daily: pd.DataFrame,
    discourse_daily: pd.DataFrame,
) -> pd.DataFrame:
    """
    Join attacks and discourse onto a complete date spine (no gaps).
    Returns a date-sorted DataFrame with columns: date, attack_count,
    significant, <DISCOURSE_COLS>.
    """
    disc_start = discourse_daily["feature_date"].min()
    disc_end   = discourse_daily["feature_date"].max()
    if attacks_daily.empty:
        start, end = disc_start, disc_end
    else:
        start = min(attacks_daily["date"].min(), disc_start)
        end   = max(attacks_daily["date"].max(), disc_end)
    full  = pd.DataFrame({"date": pd.date_range(start=start, end=end, freq="D")})

    disc = discourse_daily.rename(columns={"feature_date": "date"})

    attack_cols = (
        ["date", "attack_count", "significant", "depth_score"]
        + [c for c in _EXTRA_ATTACK_COLS if c in attacks_daily.columns]
    )
    df = (
        full
        .merge(attacks_daily[attack_cols], on="date", how="left")
        .merge(disc[["date"] + DISCOURSE_COLS], on="date", how="left")
        .fillna(0)
        .sort_values("date")
        .reset_index(drop=True)
    )
    return df


# ─────────────────────────────────────────────────────────────────────────────
#  Temporal feature engineering
# ─────────────────────────────────────────────────────────────────────────────

def add_derived_discourse(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add war_ru_internal = (war_total − war_ukr_ru).clip(0).
    Separates Russia-domestic military discourse from bilateral Ukraine-Russia signal.
    """
    if "war_total" in df.columns and "war_ukr_ru" in df.columns:
        df = df.copy()
        df["war_ru_internal"] = (df["war_total"] - df["war_ukr_ru"]).clip(lower=0)
    return df


def _compute_rolling_stats(
    df: pd.DataFrame,
    feature_cols: list[str],
    window: int = BASELINE_WINDOW,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rolling mean and std over `window` days (min_periods=5). Cold-start safe."""
    cols = [c for c in feature_cols if c in df.columns]
    means = df[cols].rolling(window, min_periods=5).mean().fillna(0.0)
    stds  = (
        df[cols].rolling(window, min_periods=5).std()
        .fillna(1.0).replace(0.0, 1.0)
    )
    return means, stds


def build_features(
    window: pd.DataFrame,
    feature_cols: list[str],
    baseline_mean: pd.Series | None = None,
    baseline_std:  pd.Series | None = None,
) -> tuple[np.ndarray, list[str]]:
    """
    Rich temporal feature vector from a discourse lookback window.

    Per-feature representations (8 each, for every column in feature_cols):
      _sum        — raw sum over window
      _ewm        — recency-weighted sum (α=0.7, most-recent highest)
      _lag1/3/7   — value at specific lag (0 if window shorter than lag)
      _slope      — linear trend coefficient (positive = rising)
      _zscore     — last-day z-score vs. 30-day rolling baseline
      _alert_days — count of days in window with z-score > 1.0

    Derived / context features (added automatically when data allows):
      pre_drone_x_en_attack — simultaneous multi-domain escalation (last day)
      sin_month / cos_month — cyclical month encoding (seasonal / weather proxy)
      is_heating_season     — 1 if Oct–Mar (energy attacks most economically damaging)
    """
    feature_cols = [c for c in feature_cols if c in window.columns]
    lb   = len(window)
    vals = window[feature_cols].values.astype(float)   # (lb, n_feats)

    if baseline_mean is None:
        baseline_mean = window[feature_cols].mean()
    if baseline_std is None:
        s = window[feature_cols].std().fillna(1.0)
        s[s < 1e-9] = 1.0
        baseline_std = s

    feat_vec:   list[float] = []
    feat_names: list[str]   = []

    weights = np.array([0.7 ** (lb - 1 - k) for k in range(lb)], dtype=float)
    weights /= weights.sum()
    t = np.arange(lb, dtype=float)

    for j, col in enumerate(feature_cols):
        series = vals[:, j]
        bm = float(baseline_mean[col])
        bs = float(baseline_std[col])
        bs = bs if bs > 1e-9 else 1.0

        feat_vec.append(float(series.sum()))
        feat_names.append(f"{col}_sum")

        feat_vec.append(float(np.dot(weights, series)))
        feat_names.append(f"{col}_ewm")

        for lag in (1, 3, 7):
            feat_vec.append(float(series[-lag]) if lag <= lb else 0.0)
            feat_names.append(f"{col}_lag{lag}")

        slope = float(np.polyfit(t, series, 1)[0]) if lb >= 3 else 0.0
        feat_vec.append(slope)
        feat_names.append(f"{col}_slope")

        feat_vec.append(float((series[-1] - bm) / bs))
        feat_names.append(f"{col}_zscore")

        zscores = (series - bm) / bs
        feat_vec.append(float((zscores > 1.0).sum()))
        feat_names.append(f"{col}_alert_days")

    # Cross-feature: simultaneous drone-threat + attack-discourse escalation
    if "pre_drone" in feature_cols and "en_attack" in feature_cols:
        di = feature_cols.index("pre_drone")
        ai = feature_cols.index("en_attack")
        feat_vec.append(float(vals[-1, di] * vals[-1, ai]))
        feat_names.append("pre_drone_x_en_attack")

    # Seasonal / weather proxy from last date in window
    date_col = ("date" if "date" in window.columns
                else "feature_date" if "feature_date" in window.columns
                else None)
    if date_col:
        month = pd.Timestamp(window.iloc[-1][date_col]).month
        feat_vec.append(float(np.sin(2 * np.pi * month / 12)))
        feat_names.append("sin_month")
        feat_vec.append(float(np.cos(2 * np.pi * month / 12)))
        feat_names.append("cos_month")
        feat_vec.append(float(1 if month in {10, 11, 12, 1, 2, 3} else 0))
        feat_names.append("is_heating_season")

    # Attack recurrence (from window's attack_count column)
    if "attack_count" in window.columns:
        atk = window["attack_count"].values.astype(float)
        # Fraction of days with at least one attack in the window
        feat_vec.append(float((atk > 0).mean()))
        feat_names.append("recent_attack_rate")
        # Days since last attack (0=yesterday was attacked, lb=none in window)
        last_idx = np.where(atk > 0)[0]
        days_since = float(lb - last_idx[-1] - 1) if len(last_idx) > 0 else float(lb)
        feat_vec.append(days_since)
        feat_names.append("days_since_last_attack")
        # Recency-weighted attack momentum (same decay as discourse EWM)
        feat_vec.append(float(np.dot(weights, atk)))
        feat_names.append("attack_momentum")

    return np.array(feat_vec, dtype=float), feat_names


# ─────────────────────────────────────────────────────────────────────────────
#  Dataset builder
# ─────────────────────────────────────────────────────────────────────────────

def build_dataset(
    df: pd.DataFrame,
    lookback: int,
    horizon: int,
    target: str = "any_attack",
) -> tuple[pd.DataFrame, pd.Series, list]:
    """
    Build (X, y, dates) using the rich temporal feature representation.

    target options:
      "any_attack"  — 1 if attack_count > 0 in the horizon window
      "significant" — 1 if any significant event in horizon window
    """
    df = add_derived_discourse(df)
    disc_cols = [c for c in DISCOURSE_COLS_EXT if c in df.columns]
    rolling_means, rolling_stds = _compute_rolling_stats(df, disc_cols)

    X_rows, y_rows, dates = [], [], []
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

        if target == "any_attack":
            y_rows.append(int(future["attack_count"].sum() > 0))
        else:
            y_rows.append(int(future["significant"].sum() > 0))
        dates.append(df.iloc[i]["date"])

    X = pd.DataFrame(X_rows, columns=feat_names or [])
    y = pd.Series(y_rows, name=f"{target}_h{horizon}")
    return X, y, dates


# ─────────────────────────────────────────────────────────────────────────────
#  Model evaluation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fit_eval_fold(model, scaler, X_train, X_test, y_train, y_test):
    """Fit model on one fold, return (auc, f1) or (nan, nan) if degenerate."""
    if len(np.unique(y_train)) < 2 or len(np.unique(y_test)) < 2:
        return float("nan"), float("nan")

    X_tr = scaler.fit_transform(X_train)
    X_te = scaler.transform(X_test)

    model.fit(X_tr, y_train)
    y_prob = model.predict_proba(X_te)[:, 1]
    y_pred = model.predict(X_te)

    return (
        roc_auc_score(y_test, y_prob),
        f1_score(y_test, y_pred, zero_division=0),
    )


def evaluate(X: pd.DataFrame, y: pd.Series, with_baseline: bool = True) -> dict:
    """
    LogisticRegression + optional DummyClassifier with TimeSeriesSplit CV.
    Returns mean/std AUC and F1 for both models.
    """
    n_pos   = int(y.sum())
    n_total = len(y)

    base = {
        "auc": float("nan"), "auc_std": float("nan"),
        "f1":  float("nan"), "f1_std":  float("nan"),
        "n": n_total, "pos_rate": n_pos / n_total,
        "baseline_auc": float("nan"),
        "note": "",
    }

    if len(np.unique(y)) < 2:
        base["note"] = "single class — skipped"
        return base

    tscv    = TimeSeriesSplit(n_splits=CV_SPLITS)
    scaler  = StandardScaler()
    model   = LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs")
    dummy   = DummyClassifier(strategy="stratified", random_state=0)

    aucs, f1s, baseline_aucs = [], [], []

    for train_idx, test_idx in tscv.split(X):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

        auc, f1 = _fit_eval_fold(model, scaler, X_train, X_test, y_train, y_test)
        if not np.isnan(auc):
            aucs.append(auc)
            f1s.append(f1)

        if with_baseline:
            # Baseline: fit dummy on raw X (no scaling needed)
            if len(np.unique(y_train)) >= 2 and len(np.unique(y_test)) >= 2:
                dummy.fit(X_train, y_train)
                b_prob = dummy.predict_proba(X_test)[:, 1]
                try:
                    baseline_aucs.append(roc_auc_score(y_test, b_prob))
                except ValueError:
                    pass

    valid_folds = len(aucs)
    base.update({
        "auc":          float(np.mean(aucs))          if aucs          else float("nan"),
        "auc_std":      float(np.std(aucs))           if aucs          else float("nan"),
        "f1":           float(np.mean(f1s))           if f1s           else float("nan"),
        "f1_std":       float(np.std(f1s))            if f1s           else float("nan"),
        "baseline_auc": float(np.mean(baseline_aucs)) if baseline_aucs else float("nan"),
        "note":         f"{valid_folds}/{CV_SPLITS} folds",
    })
    return base


# ─────────────────────────────────────────────────────────────────────────────
#  Phase 1 — Cross-correlation
# ─────────────────────────────────────────────────────────────────────────────

def phase1_crosscorr(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    """
    For each discourse feature, compute Pearson r with daily attack_count at
    lags -MAX_LAG … +MAX_LAG days.

    Positive lag L means the discourse feature on day D correlates with
    attacks on day D+L (discourse leads attacks).
    """
    print(f"\n{'═'*60}")
    print("PHASE 1 — Cross-correlation (model-free)")
    print(f"{'═'*60}")

    attack_sig = df["attack_count"].values.astype(float)
    rows = []

    for feat in DISCOURSE_COLS:
        feat_sig = df[feat].values.astype(float)
        for lag in range(-MAX_LAG, MAX_LAG + 1):
            if lag == 0:
                x, y = feat_sig, attack_sig
            elif lag > 0:
                # discourse on day D, attacks on day D+lag
                x = feat_sig[:-lag]
                y = attack_sig[lag:]
            else:
                # lag < 0: discourse on day D, attacks on day D+lag (attacks precede discourse)
                x = feat_sig[-lag:]
                y = attack_sig[:lag]

            if len(x) < 10:
                r = float("nan")
            else:
                r = float(np.corrcoef(x, y)[0, 1])
            rows.append({"feature": feat, "lag": lag, "pearson_r": r})

    ccf = pd.DataFrame(rows)

    # Print peak positive lags per feature
    print(f"\n  {'Feature':<20}  {'Best lag (d)':<14}  {'Pearson r':>10}")
    print(f"  {'-'*20}  {'-'*14}  {'-'*10}")
    for feat in DISCOURSE_COLS:
        sub = ccf[(ccf["feature"] == feat) & (ccf["lag"] > 0)]
        if sub.empty or sub["pearson_r"].isna().all():
            print(f"  {feat:<20}  {'—':>14}  {'—':>10}")
            continue
        best = sub.loc[sub["pearson_r"].abs().idxmax()]
        print(
            f"  {feat:<20}  lag={best['lag']:>4}d          "
            f"  r={best['pearson_r']:>+.3f}"
        )

    out_path = out_dir / "crosscorr.csv"
    ccf.to_csv(out_path, index=False)
    print(f"\n  Saved → {out_path}")
    return ccf


# ─────────────────────────────────────────────────────────────────────────────
#  Quality-aware config selection
# ─────────────────────────────────────────────────────────────────────────────

def _rejection_reason(row: pd.Series) -> str | None:
    """Return a short rejection string if the config fails any quality gate, else None."""
    if pd.isna(row.get("auc")):
        return "auc=nan"
    if row.get("pos_rate", 0.0) > MAX_POS_RATE:
        return f"saturated (pos_rate={row['pos_rate']:.0%})"
    auc_std = row.get("auc_std", float("nan"))
    if not pd.isna(auc_std) and auc_std > MAX_AUC_STD:
        return f"unstable (auc_std={auc_std:.3f})"
    if row.get("auc", 0.0) < MIN_AUC:
        return f"weak (auc={row['auc']:.3f})"
    lift = row.get("lift_over_baseline", float("nan"))
    if not pd.isna(lift) and lift < MIN_LIFT:
        return f"no lift ({lift:+.3f})"
    return None


def select_quality_configs(
    df: pd.DataFrame,
    n: int = N_TOP_COMBOS,
    target: str | None = None,
) -> pd.DataFrame:
    """
    Select up to n configs that pass all quality gates, with automatic fallback
    to progressively smaller horizons/lookbacks when nothing passes.

    Fallback tiers:
      1. Full filter (pos_rate, auc_std, auc, lift) — all horizons
      2. Same filter minus auc_std, horizon ≤ smallest horizon where any pass
      3. Best conservative score (auc − auc_std), any config
    """
    if target is not None:
        df = df[df["target"] == target].copy()

    sorted_df = df.sort_values("auc", ascending=False).reset_index(drop=True)

    # Tier 1: full quality filter
    passes = sorted_df.apply(lambda r: _rejection_reason(r) is None, axis=1)
    good   = sorted_df[passes]

    if not good.empty:
        rejected = sorted_df[~passes]
        if not rejected.empty:
            print(f"\n  [quality] skipped {len(rejected)} config(s):")
            for _, row in rejected.head(5).iterrows():
                print(f"    lb={int(row['lookback'])} h={int(row['horizon'])}: "
                      f"{_rejection_reason(row)}")
        return good.head(n).reset_index(drop=True)

    # Tier 2: relax auc_std, prefer smaller horizons
    print(f"\n  [quality] nothing passed full filter — trying smaller horizons")
    for max_h in sorted(df["horizon"].unique()):
        subset = sorted_df[sorted_df["horizon"] <= max_h]
        good = subset[
            (subset["pos_rate"].fillna(1.0) <= MAX_POS_RATE) &
            (subset["auc"].fillna(0.0)      >= MIN_AUC) &
            (subset.get("lift_over_baseline", pd.Series(float("nan"), index=subset.index))
             .fillna(MIN_LIFT)              >= MIN_LIFT) &
            subset["auc"].notna()
        ]
        if not good.empty:
            print(f"  [quality] fallback: horizon ≤ {max_h}d (relaxed std gate)")
            return good.head(n).reset_index(drop=True)

    # Tier 3: last resort — best conservative score
    print("  [quality] last resort: best conservative score (auc − auc_std)")
    fallback = (
        sorted_df
        .dropna(subset=["auc", "auc_std"])
        .assign(_score=lambda x: x["auc"] - x["auc_std"])
        .sort_values("_score", ascending=False)
        .drop(columns=["_score"])
        .head(n)
        .reset_index(drop=True)
    )
    return fallback


# ─────────────────────────────────────────────────────────────────────────────
#  Phase 2 — ML grid search
# ─────────────────────────────────────────────────────────────────────────────

def phase2_grid(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    """
    Train LogisticRegression for every (target, lookback, horizon) combination.
    Reports AUC ± std and whether it beats the discourse-free baseline.
    """
    print(f"\n{'═'*60}")
    print("PHASE 2 — ML grid search")
    print(f"{'═'*60}")

    results = []
    for target in ("any_attack", "significant"):
        print(f"\n── target = {target} {'─'*40}")
        print(
            f"  {'lb':>4}  {'h':>4}  {'AUC':>6}  {'±':>5}  "
            f"{'F1':>6}  {'±':>5}  {'base':>6}  {'lift':>6}  "
            f"{'pos%':>5}  n"
        )
        print(f"  {'-'*75}")

        for lookback in LOOKBACKS:
            for horizon in HORIZONS:
                X, y, _ = build_dataset(df, lookback, horizon, target=target)
                m = evaluate(X, y, with_baseline=True)

                lift = (
                    m["auc"] - m["baseline_auc"]
                    if not (np.isnan(m["auc"]) or np.isnan(m["baseline_auc"]))
                    else float("nan")
                )

                row = {
                    "target": target, "lookback": lookback, "horizon": horizon,
                    **m, "lift_over_baseline": lift,
                }
                results.append(row)

                def _fmt(v): return f"{v:.3f}" if not np.isnan(v) else "  nan"
                print(
                    f"  {lookback:>4}  {horizon:>4}  "
                    f"{_fmt(m['auc'])}  {_fmt(m['auc_std'])}  "
                    f"{_fmt(m['f1'])}  {_fmt(m['f1_std'])}  "
                    f"{_fmt(m['baseline_auc'])}  {_fmt(lift)}  "
                    f"{m['pos_rate']:>4.0%}  {m['n']}"
                )

    df_out = pd.DataFrame(results)
    out_path = out_dir / "window_analysis.csv"
    df_out.to_csv(out_path, index=False)

    # Ranked summary (quality-filtered)
    print(f"\n\n{'═'*60}")
    print("PHASE 2 — Top combinations (quality-filtered: stable, learnable)")
    print(f"{'═'*60}")
    for target in df_out["target"].unique():
        print(f"\n  target = {target}")
        sub = select_quality_configs(df_out, n=5, target=target)
        if sub.empty:
            print("  (no usable combination found)")
        else:
            print(
                sub[["lookback", "horizon", "auc", "auc_std", "f1", "lift_over_baseline", "pos_rate", "n"]]
                .to_string(index=False, float_format="{:.3f}".format)
            )

    print(f"\n  Saved → {out_path}")
    return df_out


# ─────────────────────────────────────────────────────────────────────────────
#  Phase 3 — Per-feature permutation importance
# ─────────────────────────────────────────────────────────────────────────────

def phase3_importance(df: pd.DataFrame, grid_results: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    """
    For the top N_TOP_COMBOS combinations (by AUC, beating baseline), fit a
    LogisticRegression on the full dataset and compute permutation importance.
    """
    print(f"\n{'═'*60}")
    print("PHASE 3 — Per-feature permutation importance")
    print(f"{'═'*60}")

    top = select_quality_configs(grid_results, n=N_TOP_COMBOS)

    if top.empty:
        print("  No usable combinations found — skipping Phase 3.")
        return pd.DataFrame()

    all_rows = []
    scaler = StandardScaler()
    model  = LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs")

    for _, combo in top.iterrows():
        lb, h, target = int(combo["lookback"]), int(combo["horizon"]), combo["target"]
        X, y, _ = build_dataset(df, lb, h, target=target)

        if len(np.unique(y)) < 2:
            continue

        X_s = scaler.fit_transform(X)
        model.fit(X_s, y)

        result = permutation_importance(
            model, X_s, y,
            n_repeats=30,
            random_state=0,
            scoring="roc_auc",
        )

        print(f"\n  lb={lb}d  h={h}d  target={target}  (AUC={combo['auc']:.3f})")
        print(f"  {'Feature':<20}  {'Importance':>10}  {'± std':>8}")
        print(f"  {'-'*44}")

        feat_names = list(X.columns)
        order = np.argsort(result.importances_mean)[::-1]
        for idx in order:
            fname = feat_names[idx]
            imp   = result.importances_mean[idx]
            std   = result.importances_std[idx]
            print(f"  {fname:<20}  {imp:>+10.4f}  {std:>8.4f}")
            all_rows.append({
                "lookback": lb, "horizon": h, "target": target,
                "feature": fname,
                "importance_mean": imp,
                "importance_std": std,
            })

    df_imp = pd.DataFrame(all_rows)
    if not df_imp.empty:
        out_path = out_dir / "feature_importance.csv"
        df_imp.to_csv(out_path, index=False)
        print(f"\n  Saved → {out_path}")
    return df_imp


# ─────────────────────────────────────────────────────────────────────────────
#  Phase 4 — Temporal stability
# ─────────────────────────────────────────────────────────────────────────────

def phase4_stability(df: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    """
    Split the dataset at its temporal midpoint and rerun Phase 2 on each half.
    A combination that ranks consistently in both halves is a stable signal.
    """
    print(f"\n{'═'*60}")
    print("PHASE 4 — Temporal stability (early vs late half)")
    print(f"{'═'*60}")

    mid = len(df) // 2
    halves = {"early": df.iloc[:mid].reset_index(drop=True),
              "late":  df.iloc[mid:].reset_index(drop=True)}

    print(f"\n  Split: early {halves['early']['date'].iloc[0].date()} → "
          f"{halves['early']['date'].iloc[-1].date()}")
    print(f"         late  {halves['late']['date'].iloc[0].date()} → "
          f"{halves['late']['date'].iloc[-1].date()}")

    all_rows = []

    for period, sub_df in halves.items():
        for target in ("any_attack", "significant"):
            for lookback in LOOKBACKS:
                for horizon in HORIZONS:
                    X, y, _ = build_dataset(sub_df, lookback, horizon, target=target)
                    m = evaluate(X, y, with_baseline=False)
                    all_rows.append({
                        "period": period, "target": target,
                        "lookback": lookback, "horizon": horizon,
                        **m,
                    })

    df_stab = pd.DataFrame(all_rows)

    # Show combinations that appear in top-5 for BOTH halves
    print(f"\n  {'target':<12}  {'lb':>4}  {'h':>4}  "
          f"{'early AUC':>10}  {'late AUC':>10}  {'delta':>8}")
    print(f"  {'-'*56}")

    for target in ("any_attack", "significant"):
        early = df_stab[(df_stab["period"] == "early") & (df_stab["target"] == target)]
        late  = df_stab[(df_stab["period"] == "late")  & (df_stab["target"] == target)]

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
        stable = top_e & top_l

        if not stable:
            print(f"  {target:<12}  — no stable combination found in top-5 of both halves")
            continue

        for lb, h in sorted(stable):
            e_auc = early.loc[(early["lookback"] == lb) & (early["horizon"] == h), "auc"].values
            l_auc = late.loc[(late["lookback"]  == lb) & (late["horizon"]  == h), "auc"].values
            e_v = e_auc[0] if len(e_auc) else float("nan")
            l_v = l_auc[0] if len(l_auc) else float("nan")
            delta = l_v - e_v if not (np.isnan(e_v) or np.isnan(l_v)) else float("nan")
            print(
                f"  {target:<12}  {lb:>4}  {h:>4}  "
                f"{e_v:>10.3f}  {l_v:>10.3f}  {delta:>+8.3f}"
            )

    out_path = out_dir / "stability.csv"
    df_stab.to_csv(out_path, index=False)
    print(f"\n  Saved → {out_path}")
    return df_stab


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────

def run(out_dir: Path, phases: str) -> None:
    print("Connecting to database …")
    conn        = psycopg2.connect(DB_DSN)
    raw_attacks = load_attacks(conn)
    disc        = load_discourse(conn)
    conn.close()

    print(
        f"  attacks:   {len(raw_attacks):,} rows  "
        f"({raw_attacks['attack_date'].min().date()} → "
        f"{raw_attacks['attack_date'].max().date()})"
    )
    print(
        f"  discourse: {len(disc):,} rows  "
        f"({disc['feature_date'].min().date()} → "
        f"{disc['feature_date'].max().date()})"
    )

    attacks_daily = aggregate_attacks(raw_attacks)
    df            = build_full_daily(attacks_daily, disc)
    out_dir.mkdir(parents=True, exist_ok=True)

    grid_results = None

    if "1" in phases:
        phase1_crosscorr(df, out_dir)

    if "2" in phases:
        grid_results = phase2_grid(df, out_dir)

    if "3" in phases:
        if grid_results is None:
            csv = out_dir / "window_analysis.csv"
            if csv.exists():
                grid_results = pd.read_csv(csv)
            else:
                print("\nPhase 3 requires Phase 2 results. Run with --phases 23.")
                return
        phase3_importance(df, grid_results, out_dir)

    if "4" in phases:
        phase4_stability(df, out_dir)

    print(f"\nAll done.  Output directory: {out_dir}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory (default: data/analysis/)",
    )
    parser.add_argument(
        "--phases",
        default="1234",
        help="Which phases to run, e.g. '12' for phases 1 and 2 only",
    )
    args    = parser.parse_args()
    root    = Path(__file__).resolve().parents[2]
    out_dir = Path(args.out) if args.out else root / "data" / "analysis"
    run(out_dir, phases=args.phases)


if __name__ == "__main__":
    main()
