"""
scripts/analysis/regime_models.py
────────────────────────────────────────────────────────────────────────────
Regime-aware model bank: detects discourse regimes via KMeans clustering
on rolling-mean discourse features, then trains a separate LogisticRegression
per regime for each prediction config.

When new years are added and the discourse shifts structurally, new regimes
are detected automatically — no manual re-labelling needed.

Architecture
────────────
  RegimeDetector  — KMeans on 30-day rolling discourse means;
                    k chosen by silhouette score unless overridden.
  RegimeModelBank — one LR model per (regime, target, lookback, horizon);
                    OOD fallback to nearest centroid's model.

Key design choice: build_dataset runs on the FULL df (so lookback windows
are always contiguous). Regime assignment is per prediction-date, so we
split X/y by regime label after the fact — no gaps in the training windows.

Usage
─────
    python scripts/analysis/regime_models.py train   [--k N]
    python scripts/analysis/regime_models.py predict
    python scripts/analysis/regime_models.py report
"""
from __future__ import annotations

import argparse
import pickle
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
    load_attacks,
    load_discourse,
    aggregate_attacks,
    build_full_daily,
    build_dataset,
    DISCOURSE_COLS,
    CV_SPLITS,
)

# ─────────────────────────────────────────────────────────────────────────────
#  Settings
# ─────────────────────────────────────────────────────────────────────────────

REGIME_ROLL   = 30        # days for rolling mean used in clustering
K_RANGE       = range(2, 10)
MIN_REGIME_N  = 25        # minimum prediction-rows per regime to train a model
OOD_SIGMA     = 2.0       # flag OOD if distance > OOD_SIGMA * median dist

# Discourse features used for regime detection (raw columns, no derived)
REGIME_FEATURES = ["pre_drone", "en_attack", "en_confirm", "war_ukr_ru"]

# Features whose centroid mean determines regime "intensity" (attack discourse level)
_INTENSITY_FEATURES = ["pre_drone", "en_attack", "en_confirm"]
_TIER_NAMES    = {"high": "High Tempo",  "mid": "Building",  "low": "Quiet Phase"}
_TIER_COLOR_KEY = {"high": "red",        "mid": "amber",     "low": "green"}

# Reduced feature set for model training (validated in experiment_improved.py)
# These are prefixes; actual columns from build_dataset look like pre_drone_sum etc.
MODEL_FEATURE_PREFIXES = ["pre_drone", "en_attack", "en_confirm", "war_ukr_ru", "depth_score"]

MODEL_CONFIGS: list[dict] = [
    {"target": "significant", "lookback": 10, "horizon": 1},
    {"target": "any_attack",  "lookback": 2,  "horizon": 3},
    {"target": "any_attack",  "lookback": 10, "horizon": 5},
]

MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "analysis"
BANK_PATH = MODEL_DIR / "regime_model_bank.pkl"


# ─────────────────────────────────────────────────────────────────────────────
#  Regime labelling — content-based, stable across retrains
# ─────────────────────────────────────────────────────────────────────────────

def _label_regimes(detector: "RegimeDetector") -> dict[int, dict]:
    """
    Assign human-readable labels to each regime from its centroid position in
    scaled feature space (positive = above-mean, negative = below-mean).

    Regimes are ranked by mean intensity across _INTENSITY_FEATURES, then bucketed
    into three tiers (low / mid / high).  When multiple regimes share a tier, a
    numeric suffix is appended ("High Tempo 2", etc.) so every label is unique.

    Returns {regime_id: {"name": str, "desc": str, "tier": str, "color_key": str}}
    """
    k     = detector.k
    cents = detector.centroids_  # (k, n_features), scaled

    intensity_idx = [
        REGIME_FEATURES.index(f)
        for f in _INTENSITY_FEATURES
        if f in REGIME_FEATURES
    ]
    intensities = cents[:, intensity_idx].mean(axis=1)
    ranked = np.argsort(intensities)  # ascending: ranked[0] = quietest

    tier_counts: dict[str, int] = {}
    result: dict[int, dict] = {}

    for rank, regime_id in enumerate(ranked):
        frac = rank / max(k - 1, 1)
        tier = "high" if frac >= 0.67 else ("mid" if frac >= 0.33 else "low")

        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        suffix = f" {tier_counts[tier]}" if tier_counts[tier] > 1 else ""
        name   = _TIER_NAMES[tier] + suffix

        # Description: two most deviated features (in scaled space)
        c      = cents[regime_id]
        top2   = sorted(
            zip(REGIME_FEATURES, c.tolist()),
            key=lambda x: abs(x[1]), reverse=True,
        )[:2]
        desc = "  ·  ".join(
            f"{f.replace('_', ' ')} {'↑' if v > 0 else '↓'}" for f, v in top2
        )

        result[int(regime_id)] = {
            "name":      name,
            "desc":      desc,
            "tier":      tier,
            "color_key": _TIER_COLOR_KEY[tier],
        }

    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Regime Detector
# ─────────────────────────────────────────────────────────────────────────────

class RegimeDetector:
    """Fits KMeans on rolling-mean discourse features; assigns a regime per day."""

    def __init__(self, k: int | None = None):
        self.k              = k
        self.scaler         = StandardScaler()
        self.km: KMeans | None = None
        self.centroids_     = np.empty((0,))
        self.median_dist_   = 1.0

    # ── internal ──────────────────────────────────────────────────────────────

    def _roll(self, df: pd.DataFrame) -> np.ndarray:
        cols = [c for c in REGIME_FEATURES if c in df.columns]
        return (
            df[cols]
            .rolling(REGIME_ROLL, min_periods=max(5, REGIME_ROLL // 3))
            .mean()
            .fillna(0.0)
            .values
        )

    def _distances_from_assigned(self, X_scaled: np.ndarray) -> np.ndarray:
        labels = self.km.predict(X_scaled)
        return np.linalg.norm(X_scaled - self.centroids_[labels], axis=1)

    # ── public ────────────────────────────────────────────────────────────────

    def fit(self, df: pd.DataFrame) -> "RegimeDetector":
        X = self.scaler.fit_transform(self._roll(df))

        if self.k is None:
            best_k, best_s = 2, -1.0
            for k in K_RANGE:
                if k >= len(X) // max(MIN_REGIME_N, 1):
                    break
                km     = KMeans(n_clusters=k, random_state=0, n_init=10)
                labels = km.fit_predict(X)
                if len(np.unique(labels)) < 2:
                    continue
                s = silhouette_score(X, labels, sample_size=min(1000, len(X)))
                if s > best_s:
                    best_k, best_s = k, s
            self.k = best_k

        self.km          = KMeans(n_clusters=self.k, random_state=0, n_init=10)
        self.km.fit(X)
        self.centroids_  = self.km.cluster_centers_
        self.median_dist_ = float(np.median(self._distances_from_assigned(X)))
        return self

    def assign_series(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Returns (labels, distances, is_ood) for every row in df.
        Cold-start rows (first REGIME_ROLL days) are labelled -1.
        """
        X      = self.scaler.transform(self._roll(df))
        labels = self.km.predict(X).astype(int)
        dists  = self._distances_from_assigned(X)
        is_ood = dists > OOD_SIGMA * self.median_dist_

        cold = np.arange(len(df)) < REGIME_ROLL
        labels[cold] = -1
        is_ood[cold] = True

        return labels, dists, is_ood

    def assign_window(self, window: pd.DataFrame) -> tuple[int, float, bool]:
        """Assign the most recent row of `window` (used at prediction time)."""
        labels, dists, is_ood = self.assign_series(window)
        return int(labels[-1]), float(dists[-1]), bool(is_ood[-1])


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _select_model_cols(X: pd.DataFrame) -> pd.DataFrame:
    """Keep only columns whose prefix matches MODEL_FEATURE_PREFIXES."""
    keep = [c for c in X.columns
            if any(c.startswith(p + "_") or c == p for p in MODEL_FEATURE_PREFIXES)]
    return X[keep] if keep else X


def _cv_auc(X: pd.DataFrame, y: pd.Series) -> tuple[float, float]:
    """TimeSeriesSplit AUC; returns (mean, std) or (nan, nan) if not viable."""
    n_splits = min(CV_SPLITS, max(2, len(X) // 20))
    tscv   = TimeSeriesSplit(n_splits=n_splits)
    scaler = StandardScaler()
    model  = LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs")
    aucs: list[float] = []

    for tr, te in tscv.split(X):
        Xtr, Xte = X.iloc[tr], X.iloc[te]
        ytr, yte = y.iloc[tr], y.iloc[te]
        if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
            continue
        Xs = scaler.fit_transform(Xtr)
        model.fit(Xs, ytr)
        try:
            aucs.append(roc_auc_score(yte, model.predict_proba(scaler.transform(Xte))[:, 1]))
        except ValueError:
            pass

    return (float(np.mean(aucs)), float(np.std(aucs))) if aucs else (float("nan"), float("nan"))


# ─────────────────────────────────────────────────────────────────────────────
#  Regime Model Bank
# ─────────────────────────────────────────────────────────────────────────────

class RegimeModelBank:
    """
    Trains and serves one LR per (regime_id, target, lookback, horizon).
    Prediction-date regime assignment: build_dataset runs on full df, then
    regime labels are mapped per date so lookback windows stay contiguous.
    """

    def __init__(self):
        self.detector: RegimeDetector | None = None
        # key: (regime_id, target, lookback, horizon)
        self.models: dict[tuple, dict] = {}
        self.regime_sizes_: dict[int, int] = {}
        self.regime_meta_: dict[int, dict] = {}
        self.trained_on_ = ""

    # ── Training ──────────────────────────────────────────────────────────────

    def train(self, df: pd.DataFrame, k: int | None = None) -> list[dict]:
        self.detector = RegimeDetector(k=k)
        self.detector.fit(df)
        self.regime_meta_ = _label_regimes(self.detector)

        # Day-level regime labels (indexed by df row)
        day_labels, _, _ = self.detector.assign_series(df)
        date_to_regime   = dict(zip(df["date"], day_labels))

        self.trained_on_   = (
            f"{df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}"
        )
        # Regime sizes from raw day counts (not prediction counts)
        unique_regimes     = sorted(set(day_labels) - {-1})
        self.regime_sizes_ = {r: int((day_labels == r).sum()) for r in unique_regimes}

        results = []
        for cfg in MODEL_CONFIGS:
            lb, h, target = cfg["lookback"], cfg["horizon"], cfg["target"]

            # Build dataset on the full df — windows are contiguous
            X_all, y_all, dates = build_dataset(df, lb, h, target=target)
            X_red = _select_model_cols(X_all)

            # Map each prediction date to its regime
            pred_regimes = np.array(
                [date_to_regime.get(d, -1) for d in dates], dtype=int
            )

            unique = sorted(set(pred_regimes) - {-1})

            for regime_id in unique:
                mask = pred_regimes == regime_id
                Xr   = X_red[mask]
                yr   = y_all[mask]

                if len(Xr) < MIN_REGIME_N or len(np.unique(yr)) < 2:
                    res = {**cfg, "regime": regime_id,
                           "status": f"skipped (n={len(Xr)}, classes={np.unique(yr).tolist()})"}
                    results.append(res)
                    continue

                auc_mean, auc_std = _cv_auc(Xr, yr)

                scaler = StandardScaler()
                model  = LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs")
                model.fit(scaler.fit_transform(Xr), yr)

                key = (regime_id, target, lb, h)
                self.models[key] = {
                    "model":    model,
                    "scaler":   scaler,
                    "features": list(Xr.columns),
                    "n":        len(Xr),
                    "pos_rate": float(yr.mean()),
                    "auc_cv":   auc_mean,
                    "auc_std":  auc_std,
                    "regime":   regime_id,
                    **cfg,
                }
                res = {**cfg, "regime": regime_id,
                       "status": f"AUC={auc_mean:.3f} ±{auc_std:.3f}  n={len(Xr)}  pos={yr.mean():.0%}"}
                results.append(res)

        return results

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(self, df: pd.DataFrame) -> list[dict]:
        if self.detector is None:
            raise RuntimeError("RegimeModelBank not trained.")

        out = []
        for cfg in MODEL_CONFIGS:
            lb, h, target = cfg["lookback"], cfg["horizon"], cfg["target"]

            if len(df) < lb + REGIME_ROLL:
                out.append({**cfg, "error": f"need ≥{lb + REGIME_ROLL} rows"})
                continue

            # Use tail for regime assignment (need REGIME_ROLL rows before the lookback)
            window         = df.tail(lb + REGIME_ROLL).reset_index(drop=True)
            regime_id, dist, is_ood = self.detector.assign_window(window)

            key = (regime_id, target, lb, h)
            if key not in self.models:
                fallback_key = self._nearest_regime(regime_id, target, lb, h)
                if fallback_key is None:
                    out.append({**cfg, "regime": regime_id, "is_ood": True,
                                "error": "no trained model available"})
                    continue
                key       = fallback_key
                regime_id = fallback_key[0]
                is_ood    = True

            bundle = self.models[key]

            X_all, _, _ = build_dataset(df.tail(lb + REGIME_ROLL).reset_index(drop=True), lb, h, target=target)
            if X_all.empty:
                out.append({**cfg, "error": "build_dataset returned empty"})
                continue

            feat_cols = [f for f in bundle["features"] if f in X_all.columns]
            if not feat_cols:
                out.append({**cfg, "error": "feature mismatch after reload"})
                continue

            x   = X_all[feat_cols].iloc[[-1]]
            x_s = bundle["scaler"].transform(x)

            prob  = float(bundle["model"].predict_proba(x_s)[0, 1])
            label = bool(bundle["model"].predict(x_s)[0])

            coefs    = bundle["model"].coef_[0]
            contribs = {f: float(coefs[i] * x_s[0, i]) for i, f in enumerate(feat_cols)}
            top_feats = sorted(contribs.items(), key=lambda kv: abs(kv[1]), reverse=True)[:4]

            meta = self.regime_meta_.get(regime_id, {})
            out.append({
                **cfg,
                "regime":           regime_id,
                "regime_dist":      float(dist),
                "regime_name":      meta.get("name", f"R{regime_id}"),
                "regime_desc":      meta.get("desc", ""),
                "regime_color_key": meta.get("color_key", ""),
                "is_ood":           is_ood,
                "probability":      prob,
                "label":            label,
                "top_features":     top_feats,
                "model_auc":        bundle["auc_cv"],
                "model_n":          bundle["n"],
            })

        return out

    def _nearest_regime(
        self, regime_id: int, target: str, lb: int, h: int
    ) -> tuple | None:
        candidates = [k for k in self.models if k[1] == target and k[2] == lb and k[3] == h]
        if not candidates:
            return None
        if regime_id < 0 or self.detector is None or regime_id >= len(self.detector.centroids_):
            return candidates[0]
        c     = self.detector.centroids_[regime_id]
        dists = {k: float(np.linalg.norm(c - self.detector.centroids_[k[0]]))
                 for k in candidates if k[0] < len(self.detector.centroids_)}
        return min(dists, key=dists.get) if dists else candidates[0]

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: Path = BANK_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @staticmethod
    def load(path: Path = BANK_PATH) -> "RegimeModelBank":
        with open(path, "rb") as f:
            return pickle.load(f)


# ─────────────────────────────────────────────────────────────────────────────
#  Report
# ─────────────────────────────────────────────────────────────────────────────

def report(bank: RegimeModelBank) -> None:
    det = bank.detector
    print(f"\n{'═'*68}")
    print("REGIME MODEL BANK — SUMMARY")
    print(f"{'═'*68}")
    print(f"  Trained on : {bank.trained_on_}")
    print(f"  Regimes (k): {det.k}")
    print(f"  OOD thresh : dist > {OOD_SIGMA} × {det.median_dist_:.3f} = "
          f"{OOD_SIGMA * det.median_dist_:.3f}")

    if not bank.models:
        print("  (no models trained)")
        return

    print(f"\n  {'R':>2}  {'target':<12}  {'lb':>3}  {'h':>3}  "
          f"{'AUC':>6}  {'±std':>6}  {'n':>5}  {'pos%':>5}  {'regime n':>8}")
    print(f"  {'-'*65}")

    for (rid, target, lb, h), m in sorted(bank.models.items()):
        rn = bank.regime_sizes_.get(rid, "?")
        print(
            f"  {rid:>2}  {target:<12}  {lb:>3}  {h:>3}  "
            f"{m['auc_cv']:>6.3f}  {m['auc_std']:>6.3f}  "
            f"{m['n']:>5}  {m['pos_rate']:>4.0%}  {rn!s:>8}"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  CLI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_df() -> pd.DataFrame:
    print("Connecting to database …")
    conn          = psycopg2.connect(DB_DSN)
    raw_attacks   = load_attacks(conn)
    disc          = load_discourse(conn)
    conn.close()
    attacks_daily = aggregate_attacks(raw_attacks)
    df            = build_full_daily(attacks_daily, disc)
    print(f"  {len(df)} rows  "
          f"{df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}")
    return df


def cmd_train(args) -> None:
    df   = _load_df()
    k    = int(args.k) if args.k else None
    bank = RegimeModelBank()

    print(f"\nDetecting regimes (k={'auto' if k is None else k}) …")
    results = bank.train(df, k=k)

    print(f"\n{'─'*68}")
    for r in results:
        tag = f"R{r['regime']}  {r['target']:<12}  lb={r['lookback']}  h={r['horizon']}"
        print(f"  {tag:<40}  {r['status']}")

    bank.save()
    print(f"\nSaved → {BANK_PATH}")
    report(bank)


def cmd_predict(args) -> None:
    if not BANK_PATH.exists():
        print("No trained bank found. Run: python regime_models.py train")
        return

    bank  = RegimeModelBank.load()
    df    = _load_df()
    preds = bank.predict(df)

    print(f"\n{'═'*68}")
    print("REGIME-AWARE PREDICTIONS")
    print(f"{'═'*68}")
    for p in preds:
        if "error" in p:
            print(f"  {p['target']:<12} lb={p['lookback']} h={p['horizon']}  "
                  f"ERROR: {p['error']}")
            continue
        ood     = "  [OOD]" if p.get("is_ood") else ""
        top2    = ", ".join(f"{k}:{v:+.2f}" for k, v in p.get("top_features", [])[:2])
        label   = "YES" if p["label"] else "no "
        print(
            f"  {p['target']:<12} lb={p['lookback']} h={p['horizon']}  "
            f"regime={p['regime']}{ood}  "
            f"prob={p['probability']:.2f}  label={label}  "
            f"model_auc={p.get('model_auc', float('nan')):.3f}  [{top2}]"
        )


def cmd_report(args) -> None:
    if not BANK_PATH.exists():
        print("No trained bank found.")
        return
    report(RegimeModelBank.load())


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("train", help="Detect regimes and train per-regime models")
    p.add_argument("--k", default=None, help="Number of regimes (default: auto via silhouette)")
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("predict", help="Predict using regime-aware bank")
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("report", help="Print bank summary")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
