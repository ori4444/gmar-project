"""
scripts/analysis/timeline.py
─────────────────────────────────────────────────────────────────────────────
Interactive timeline dashboard: attack events  ↔  daily discourse features.

Produces a single self-contained HTML file and opens it in the browser.

Usage
-----
    cd C:\\Users\\ONE1\\Desktop\\Gmar
    python scripts/analysis/timeline.py

Optional
    --out PATH     override output path  (default: data/analysis/timeline.html)
    --no-open      don't auto-open browser
"""

import argparse
import os
import sys
import webbrowser
from pathlib import Path

import pandas as pd
import psycopg2
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.config import DB_DSN


# ─────────────────────────────────────────────────────────────────────────────
#  Colour palette
# ─────────────────────────────────────────────────────────────────────────────

C = dict(
    # Attack panel
    attack_bar    = "#4f46e5",
    wave_line     = "#a5b4fc",
    fire_marker   = "#ef4444",
    hit_marker    = "#10b981",
    shutdown_mark = "#f97316",

    # Attack-type stacked bars
    drone         = "#3b82f6",
    missile       = "#dc2626",
    combined      = "#7c3aed",
    unknown_type  = "#cbd5e1",

    # Geographic panel
    area_bar      = "#0891b2",
    repeated_line = "#f59e0b",

    # Pre-attack discourse
    pre_drone     = "#0ea5e9",
    pre_airport   = "#f59e0b",
    pre_airdef    = "#8b5cf6",
    pre_uncert    = "#94a3b8",

    # Energy discourse
    energy_atk    = "#059669",
    energy_conf   = "#6ee7b7",
    energy_refin  = "#1e3a2f",
    energy_other  = "#34d399",

    # War context
    war_total     = "#94a3b8",
    war_ukr_ru    = "#334155",

    # Attack-day background bands
    band_any      = "rgba(79,70,229,0.06)",
    band_intense  = "rgba(239,68,68,0.11)",
)


# ─────────────────────────────────────────────────────────────────────────────
#  Data loading
# ─────────────────────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(DB_DSN)


def load_attacks(conn) -> pd.DataFrame:
    sql = """
        SELECT
            attack_date,
            area,
            attack_type,
            target_type,
            damage_level,
            fire::int            AS fire,
            hit_confirmed::int   AS hit_confirmed,
            shutdown::int        AS shutdown,
            explosions_reported,
            repeated_attack::int AS repeated_attack,
            air_defense_active::int AS air_defense_active,
            combined_strike::int AS combined_strike
        FROM attacks
        ORDER BY attack_date
    """
    df = pd.read_sql(sql, conn, parse_dates=["attack_date"])
    return df


def load_discourse(conn) -> pd.DataFrame:
    sql = """
        SELECT
            feature_date,
            COALESCE(pre_russia_messages, 0)                  AS pre_total,
            COALESCE(pre_russia_drone_mentions, 0)            AS pre_drone,
            COALESCE(pre_russia_drone_air_defense_messages, 0)AS pre_airdef,
            COALESCE(pre_russia_airport_closure_mentions, 0)  AS pre_airport,
            COALESCE(pre_russia_uncertainty_mentions, 0)      AS pre_uncert,
            COALESCE(energy_attack_messages, 0)               AS en_attack,
            COALESCE(energy_confirmation_messages, 0)         AS en_confirm,
            COALESCE(energy_explosion_or_fire_mentions, 0)    AS en_explode,
            COALESCE(energy_refinery_or_oil_depot_messages, 0)AS en_refinery,
            COALESCE(energy_other_infra_messages, 0)          AS en_other,
            COALESCE(war_total_messages, 0)                   AS war_total,
            COALESCE(war_ukrainian_strike_in_russia_messages, 0) AS war_ukr_ru,
            COALESCE(war_russian_strike_in_ukraine_messages, 0)  AS war_ru_ukr
        FROM daily_source_discourse_features
        WHERE source = 'astrapress'
        ORDER BY feature_date
    """
    df = pd.read_sql(sql, conn, parse_dates=["feature_date"])
    return df


# ─────────────────────────────────────────────────────────────────────────────
#  Attack-side aggregation
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_attacks(raw: pd.DataFrame) -> pd.DataFrame:
    raw = raw.copy()
    raw["damage_score"] = (
        raw["damage_level"]
        .map({"high": 3, "medium": 2, "low": 1})
        .fillna(0)
    )

    daily = (
        raw.groupby("attack_date")
        .agg(
            attack_count      = ("attack_date",        "count"),
            unique_areas      = ("area",               "nunique"),
            fire_count        = ("fire",                "sum"),
            hits              = ("hit_confirmed",       "sum"),
            shutdowns         = ("shutdown",            "sum"),
            total_explosions  = ("explosions_reported", "sum"),
            repeated_count    = ("repeated_attack",     "sum"),
            air_defense_count = ("air_defense_active",  "sum"),
            damage_score_sum  = ("damage_score",        "sum"),
            avg_damage        = ("damage_score",        "mean"),
        )
        .reset_index()
    )

    # Attack type breakdown per day
    type_pivot = (
        raw.groupby(["attack_date", "attack_type"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["drone", "missile", "combined", "unknown"], fill_value=0)
        .reset_index()
    )
    daily = daily.merge(type_pivot, on="attack_date", how="left").fillna(0)

    # Derived metrics
    daily = daily.sort_values("attack_date").reset_index(drop=True)
    daily["rolling_7d"]      = daily["attack_count"].rolling(7, min_periods=1).mean()
    daily["repeated_ratio"]  = (
        daily["repeated_count"] / daily["attack_count"].clip(lower=1)
    )
    daily["damage_norm"]     = daily["avg_damage"] / 3.0  # 0-1

    return daily


# ─────────────────────────────────────────────────────────────────────────────
#  Background bands for attack days
# ─────────────────────────────────────────────────────────────────────────────

def _attack_bands(daily: pd.DataFrame):
    """
    Returns a list of (date, is_intense) for every attack day.
    intense = attack_count >= 75th percentile of non-zero days.
    """
    nonzero = daily.loc[daily["attack_count"] > 0, "attack_count"]
    threshold = nonzero.quantile(0.75) if len(nonzero) > 4 else 3

    bands = []
    for _, row in daily.iterrows():
        if row["attack_count"] > 0:
            d = row["attack_date"]
            intense = row["attack_count"] >= threshold
            bands.append((pd.Timestamp(d), intense))
    return bands


# ─────────────────────────────────────────────────────────────────────────────
#  Figure builder
# ─────────────────────────────────────────────────────────────────────────────

ROWS = 6
ROW_HEIGHTS = [0.28, 0.12, 0.14, 0.22, 0.16, 0.08]

SUBPLOT_TITLES = [
    "① Attack Volume & Intensity",
    "② Attack Type Breakdown",
    "③ Geographic Spread  ·  Repeated Targeting",
    "④ Pre-Attack Discourse  —  leading indicators",
    "⑤ Energy Infrastructure Discourse",
    "⑥ War-Context Volume",
]


def build_figure(daily: pd.DataFrame, disc: pd.DataFrame) -> go.Figure:
    fig = make_subplots(
        rows=ROWS, cols=1,
        shared_xaxes=True,
        row_heights=ROW_HEIGHTS,
        subplot_titles=SUBPLOT_TITLES,
        vertical_spacing=0.03,
        specs=[[{"secondary_y": True}]] * ROWS,
    )

    # ── shared hover style ──────────────────────────────────────────────────
    hov = dict(mode="x unified")

    atk_x  = daily["attack_date"]
    disc_x = disc["feature_date"]

    # ────────────────────────────────────────────────────────────────────────
    #  Row 1 — Attack volume & intensity
    # ────────────────────────────────────────────────────────────────────────

    # Colour each bar by avg damage score (green → amber → red)
    bar_colors = daily["avg_damage"].apply(
        lambda v: (
            "#16a34a" if v < 1.5 else
            "#d97706" if v < 2.5 else
            "#dc2626"
        )
    )

    fig.add_trace(go.Bar(
        x=atk_x, y=daily["attack_count"],
        name="Attack count",
        marker_color=bar_colors,
        opacity=0.85,
        hovertemplate=(
            "<b>%{x|%d %b %Y}</b><br>"
            "Attacks: <b>%{y}</b><extra></extra>"
        ),
        legendgroup="attacks",
    ), row=1, col=1, secondary_y=False)

    fig.add_trace(go.Scatter(
        x=atk_x, y=daily["rolling_7d"],
        name="7-day rolling mean",
        mode="lines",
        line=dict(color=C["wave_line"], width=2, dash="dot"),
        hovertemplate="%{y:.1f}<extra>7d mean</extra>",
        legendgroup="attacks",
    ), row=1, col=1, secondary_y=False)

    # Fire / hit / shutdown markers on secondary y (invisible axis, just for positioning)
    for col_name, symbol, color, label in [
        ("fire_count",  "triangle-up",    C["fire_marker"],   "Fire events"),
        ("hits",        "circle",         C["hit_marker"],    "Hit confirmed"),
        ("shutdowns",   "square",         C["shutdown_mark"], "Shutdowns"),
    ]:
        mask = daily[col_name] > 0
        fig.add_trace(go.Scatter(
            x=atk_x[mask],
            y=daily.loc[mask, col_name],
            name=label,
            mode="markers",
            marker=dict(symbol=symbol, size=9, color=color,
                        line=dict(color="white", width=1)),
            hovertemplate=f"{label}: %{{y}}<extra></extra>",
            legendgroup="events",
        ), row=1, col=1, secondary_y=True)

    # ────────────────────────────────────────────────────────────────────────
    #  Row 2 — Attack type breakdown
    # ────────────────────────────────────────────────────────────────────────

    for atk_type, color in [
        ("drone",    C["drone"]),
        ("missile",  C["missile"]),
        ("combined", C["combined"]),
        ("unknown",  C["unknown_type"]),
    ]:
        if atk_type in daily.columns and daily[atk_type].sum() > 0:
            fig.add_trace(go.Bar(
                x=atk_x, y=daily[atk_type],
                name=atk_type.title(),
                marker_color=color,
                hovertemplate=f"{atk_type.title()}: %{{y}}<extra></extra>",
                legendgroup="types",
            ), row=2, col=1, secondary_y=False)

    fig.update_layout(barmode="stack")  # applies globally; row-level stacking handled below

    # ────────────────────────────────────────────────────────────────────────
    #  Row 3 — Geographic spread + repeated targeting
    # ────────────────────────────────────────────────────────────────────────

    fig.add_trace(go.Bar(
        x=atk_x, y=daily["unique_areas"],
        name="Unique areas hit",
        marker_color=C["area_bar"],
        opacity=0.7,
        hovertemplate="Areas: <b>%{y}</b><extra></extra>",
        legendgroup="geo",
    ), row=3, col=1, secondary_y=False)

    fig.add_trace(go.Scatter(
        x=atk_x, y=(daily["repeated_ratio"] * 100).round(1),
        name="Repeated targeting %",
        mode="lines+markers",
        line=dict(color=C["repeated_line"], width=2),
        marker=dict(size=5),
        hovertemplate="Repeated: <b>%{y:.0f}%%</b><extra></extra>",
        legendgroup="geo",
    ), row=3, col=1, secondary_y=True)

    # ────────────────────────────────────────────────────────────────────────
    #  Row 4 — Pre-attack discourse  (key leading-indicator panel)
    # ────────────────────────────────────────────────────────────────────────

    for col_name, color, label, dash in [
        ("pre_drone",   C["pre_drone"],   "Pre-signal: drone mentions",   "solid"),
        ("pre_airdef",  C["pre_airdef"],  "Pre-signal: air defense",      "solid"),
        ("pre_airport", C["pre_airport"], "Pre-signal: airport closures", "dash"),
        ("pre_uncert",  C["pre_uncert"],  "Pre-signal: uncertainty",      "dot"),
    ]:
        fig.add_trace(go.Scatter(
            x=disc_x, y=disc[col_name],
            name=label,
            mode="lines",
            line=dict(color=color, width=2, dash=dash),
            fill="tozeroy" if dash == "solid" else None,
            fillcolor=color.replace(")", ",0.15)").replace("rgb", "rgba")
                        if "rgb" in color else
                        f"rgba({int(color[1:3],16)},"
                        f"{int(color[3:5],16)},"
                        f"{int(color[5:7],16)},0.12)",
            hovertemplate=f"{label}: <b>%{{y}}</b><extra></extra>",
            legendgroup="pre",
        ), row=4, col=1, secondary_y=False)

    # ────────────────────────────────────────────────────────────────────────
    #  Row 5 — Energy discourse
    # ────────────────────────────────────────────────────────────────────────

    for col_name, color, label in [
        ("en_attack",   C["energy_atk"],   "Energy: attack msgs"),
        ("en_confirm",  C["energy_conf"],  "Energy: confirmations"),
        ("en_refinery", C["energy_refin"], "Energy: refinery/depot"),
        ("en_other",    C["energy_other"], "Energy: other infra"),
    ]:
        fig.add_trace(go.Bar(
            x=disc_x, y=disc[col_name],
            name=label,
            marker_color=color,
            hovertemplate=f"{label}: <b>%{{y}}</b><extra></extra>",
            legendgroup="energy",
        ), row=5, col=1, secondary_y=False)

    # ────────────────────────────────────────────────────────────────────────
    #  Row 6 — War context
    # ────────────────────────────────────────────────────────────────────────

    for col_name, color, label in [
        ("war_total",  C["war_total"],  "War messages (total)"),
        ("war_ukr_ru", C["war_ukr_ru"], "Ukrainian strikes on Russia"),
    ]:
        fig.add_trace(go.Scatter(
            x=disc_x, y=disc[col_name],
            name=label,
            mode="lines",
            line=dict(color=color, width=1.5),
            hovertemplate=f"{label}: <b>%{{y}}</b><extra></extra>",
            legendgroup="war",
        ), row=6, col=1, secondary_y=False)

    # ────────────────────────────────────────────────────────────────────────
    #  Attack-day background bands  (drawn across ALL rows)
    # ────────────────────────────────────────────────────────────────────────

    for ts, intense in _attack_bands(daily):
        fig.add_vrect(
            x0=ts - pd.Timedelta(hours=12),
            x1=ts + pd.Timedelta(hours=12),
            fillcolor=C["band_intense"] if intense else C["band_any"],
            line_width=0,
            layer="below",
        )

    # ────────────────────────────────────────────────────────────────────────
    #  Layout
    # ────────────────────────────────────────────────────────────────────────

    fig.update_layout(
        title=dict(
            text="<b>Attack Timeline vs Daily Discourse Signals</b>",
            font=dict(size=22, family="'Inter', 'Segoe UI', sans-serif",
                      color="#0f172a"),
            x=0.01,
        ),
        height=1100,
        paper_bgcolor="#f8fafc",
        plot_bgcolor="#ffffff",
        font=dict(family="'Inter', 'Segoe UI', sans-serif",
                  size=12, color="#334155"),
        legend=dict(
            orientation="v",
            x=1.01, y=1,
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#e2e8f0",
            borderwidth=1,
            font=dict(size=11),
            tracegroupgap=14,
        ),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="white",
            bordercolor="#e2e8f0",
            font=dict(size=12, color="#1e293b"),
        ),
        barmode="stack",

        # Range selector + slider on the shared x-axis
        xaxis6=dict(
            rangeslider=dict(visible=True, thickness=0.04),
            rangeselector=dict(
                buttons=[
                    dict(count=1,  label="1m", step="month", stepmode="backward"),
                    dict(count=3,  label="3m", step="month", stepmode="backward"),
                    dict(count=6,  label="6m", step="month", stepmode="backward"),
                    dict(step="all", label="All"),
                ],
                font=dict(size=11),
                bgcolor="#e0e7ff",
                activecolor=C["attack_bar"],
            ),
        ),

        margin=dict(l=60, r=220, t=70, b=60),
    )

    # Y-axis labels
    _yax = dict(
        showgrid=True,
        gridcolor="#f1f5f9",
        gridwidth=1,
        zeroline=False,
        tickfont=dict(size=10),
    )

    fig.update_yaxes(**_yax)

    fig.update_yaxes(title_text="Attacks",      row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Events",       row=1, col=1, secondary_y=True,
                     showgrid=False)
    fig.update_yaxes(title_text="Count",        row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Areas",        row=3, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Repeated %",   row=3, col=1, secondary_y=True,
                     showgrid=False, ticksuffix="%")
    fig.update_yaxes(title_text="Messages",     row=4, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Messages",     row=5, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Messages",     row=6, col=1, secondary_y=False)

    # Subplot title styling
    for ann in fig.layout.annotations:
        ann.update(font=dict(size=13, color="#475569", family="'Inter','Segoe UI',sans-serif"),
                   x=0.0, xanchor="left")

    return fig


# ─────────────────────────────────────────────────────────────────────────────
#  Annotation: summary stats box
# ─────────────────────────────────────────────────────────────────────────────

def _add_summary(fig: go.Figure, daily: pd.DataFrame, disc: pd.DataFrame):
    total_attacks = int(daily["attack_count"].sum())
    date_range    = f"{daily['attack_date'].min().date()} → {daily['attack_date'].max().date()}"
    attack_days   = int((daily["attack_count"] > 0).sum())
    avg_per_day   = daily.loc[daily["attack_count"] > 0, "attack_count"].mean()
    disc_days     = len(disc)

    text = (
        f"<b>Dataset summary</b><br>"
        f"Period: {date_range}<br>"
        f"Total attacks recorded: <b>{total_attacks}</b><br>"
        f"Days with ≥1 attack: <b>{attack_days}</b> "
        f"({100*attack_days/max(len(daily),1):.0f}%)<br>"
        f"Avg attacks on attack days: <b>{avg_per_day:.1f}</b><br>"
        f"Discourse days available: <b>{disc_days}</b>"
    )

    fig.add_annotation(
        text=text,
        align="left",
        showarrow=False,
        x=1.01, y=0.98,
        xref="paper", yref="paper",
        xanchor="left", yanchor="top",
        bgcolor="white",
        bordercolor="#e2e8f0",
        borderwidth=1,
        borderpad=10,
        font=dict(size=11, color="#475569"),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Attack ↔ Discourse timeline dashboard")
    parser.add_argument("--out", default=None,
                        help="Output HTML path (default: data/analysis/timeline.html)")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't open browser automatically")
    args = parser.parse_args()

    # Resolve output path
    root = Path(__file__).resolve().parents[2]
    out_path = Path(args.out) if args.out else root / "data" / "analysis" / "timeline.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("Connecting to database…")
    conn = _conn()

    print("Loading attack data…")
    raw_attacks = load_attacks(conn)
    print(f"  → {len(raw_attacks):,} attack records")

    print("Loading discourse features…")
    disc = load_discourse(conn)
    print(f"  → {len(disc):,} discourse days")

    conn.close()

    if raw_attacks.empty:
        print("No attack data found — check your database connection.")
        return

    print("Aggregating…")
    daily = aggregate_attacks(raw_attacks)

    print("Building figure…")
    fig = build_figure(daily, disc)
    _add_summary(fig, daily, disc)

    print(f"Writing → {out_path}")
    fig.write_html(
        str(out_path),
        include_plotlyjs="cdn",
        full_html=True,
        config=dict(
            displayModeBar=True,
            modeBarButtonsToRemove=["lasso2d", "select2d"],
            scrollZoom=True,
            toImageButtonOptions=dict(
                format="png", filename="attack_discourse_timeline",
                height=1100, width=1800, scale=2,
            ),
        ),
    )

    if not args.no_open:
        webbrowser.open(out_path.as_uri())

    print("Done.")


if __name__ == "__main__":
    main()
