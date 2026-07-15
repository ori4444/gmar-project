"""
scripts/analysis/timeline.py
─────────────────────────────────────────────────────────────────────────────
Single-figure interactive timeline: attack events vs. discourse signals.

Rules:
  • ONE figure, no subplots.
  • Attack traces on primary y-axis  (left)  — visible by default.
  • Discourse traces on secondary y-axis (right) — legendonly by default.
  • Plotly bundled inline (no CDN / internet dependency).
  • No browser is opened automatically.

Usage
-----
    python scripts/analysis/timeline.py [--out PATH]
"""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.config import (
    DB_DSN,
    EVENTS_TABLE, TARGETS_TABLE, ET_TABLE,
)
from analysis.window_analysis import load_discourse


# ─────────────────────────────────────────────────────────────────────────────
#  Exact trace names  (must match graphs.py SERIES exactly)
# ─────────────────────────────────────────────────────────────────────────────

# Attacks (primary y, visible by default)
T_ATTACK_COUNT  = "Attack Count"
T_ROLLING       = "7-Day Rolling Mean"
T_FIRE          = "Fire Events"
T_HIT           = "Hit Confirmed"
T_SHUTDOWN      = "Shutdowns"
T_AIRDEF        = "Air Defense Active"
T_COMBINED      = "Combined Strikes"

# Discourse (secondary y, legendonly by default)
T_PRE_DRONE     = "Drone Mentions (pre)"
T_PRE_AIRDEF    = "Air Defense (pre)"
T_PRE_AIRPORT   = "Airport Closures (pre)"
T_PRE_UNCERT    = "Uncertainty (pre)"
T_EN_ATTACK     = "Energy Attack Messages"
T_EN_CONFIRM    = "Energy Confirmations"
T_EN_REFINERY   = "Energy Refinery/Depot"
T_WAR_TOTAL     = "War Messages (total)"
T_WAR_UKR       = "Ukrainian Strikes on Russia"


# ─────────────────────────────────────────────────────────────────────────────
#  Colours
# ─────────────────────────────────────────────────────────────────────────────

C = dict(
    attack_bar   = "#5E6AD2",
    rolling      = "#a5b4fc",
    fire         = "#ef4444",
    hit          = "#10b981",
    shutdown     = "#fb923c",
    airdef       = "#f59e0b",
    combined     = "#a78bfa",
    # discourse
    pre_drone    = "#38bdf8",
    pre_airdef   = "#8b5cf6",
    pre_airport  = "#fbbf24",
    pre_uncert   = "#94a3b8",
    en_attack    = "#34d399",
    en_confirm   = "#6ee7b7",
    en_refinery  = "#10b981",
    war_total    = "#94a3b8",
    war_ukr      = "#cbd5e1",
    # background bands
    band_any     = "rgba(94,106,210,0.08)",
    band_intense = "rgba(239,68,68,0.16)",
)


# ─────────────────────────────────────────────────────────────────────────────
#  Data loading
# ─────────────────────────────────────────────────────────────────────────────

def _conn():
    return psycopg2.connect(DB_DSN)


def load_attacks(conn) -> pd.DataFrame:
    sql = f"""
        SELECT
            e.attack_date,
            e.area,
            e.macro_region,
            e.damage_level,
            e.confidence,
            e.fire_reported::int               AS fire,
            e.hit_confirmed::int               AS hit_confirmed,
            e.shutdown_reported::int           AS shutdown,
            e.explosions_count                 AS explosions_reported,
            e.air_defense_active::int          AS air_defense_active,
            e.combined_strike::int             AS combined_strike,
            COALESCE(
                string_agg(DISTINCT t.target_type, ' | '
                           ORDER BY t.target_type), 'unknown'
            )                                  AS target_type
        FROM {EVENTS_TABLE} e
        LEFT JOIN {ET_TABLE}      et ON et.event_id  = e.event_id
        LEFT JOIN {TARGETS_TABLE} t  ON t.target_id  = et.target_id
        WHERE e.status = 'active'
        GROUP BY e.event_id
        ORDER BY e.attack_date
    """
    df = pd.read_sql(sql, conn, parse_dates=["attack_date"])
    return df


# ─────────────────────────────────────────────────────────────────────────────
#  Aggregation
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_attacks(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=[
            "attack_date", "attack_count", "unique_areas",
            "fire_count", "hits", "shutdowns", "air_defense_count",
            "combined_count", "avg_damage", "rolling_7d",
        ])

    raw = raw.copy()
    raw["damage_score"] = raw["damage_level"].map(
        {"high": 3, "medium": 2, "low": 1}
    ).fillna(0)

    daily = (
        raw.groupby("attack_date")
        .agg(
            attack_count      = ("attack_date",          "count"),
            fire_count        = ("fire",                  "sum"),
            hits              = ("hit_confirmed",         "sum"),
            shutdowns         = ("shutdown",              "sum"),
            air_defense_count = ("air_defense_active",    "sum"),
            combined_count    = ("combined_strike",       "sum"),
            avg_damage        = ("damage_score",          "mean"),
        )
        .reset_index()
        .sort_values("attack_date")
        .reset_index(drop=True)
    )
    daily["rolling_7d"] = daily["attack_count"].rolling(7, min_periods=1).mean()
    return daily


# ─────────────────────────────────────────────────────────────────────────────
#  Background bands
# ─────────────────────────────────────────────────────────────────────────────

def _attack_bands(daily: pd.DataFrame):
    if daily.empty:
        return []
    nonzero = daily.loc[daily["attack_count"] > 0, "attack_count"]
    threshold = nonzero.quantile(0.75) if len(nonzero) > 4 else 3
    bands = []
    for _, row in daily.iterrows():
        if row["attack_count"] > 0:
            d = pd.Timestamp(row["attack_date"])
            bands.append((d, row["attack_count"] >= threshold))
    return bands


# ─────────────────────────────────────────────────────────────────────────────
#  Figure builder  —  single unified figure
# ─────────────────────────────────────────────────────────────────────────────

def build_figure(daily: pd.DataFrame, disc: pd.DataFrame) -> go.Figure:
    fig = go.Figure()

    atk_x  = daily["attack_date"]   if not daily.empty else pd.Series([], dtype="datetime64[ns]")
    disc_x = disc["feature_date"]   if not disc.empty  else pd.Series([], dtype="datetime64[ns]")

    # ── Attack-day background bands ───────────────────────────────────────────
    for ts, intense in _attack_bands(daily):
        fig.add_vrect(
            x0=ts - pd.Timedelta(hours=12),
            x1=ts + pd.Timedelta(hours=12),
            fillcolor=C["band_intense"] if intense else C["band_any"],
            line_width=0,
            layer="below",
        )

    # ── ATTACK TRACES  (primary y, visible) ──────────────────────────────────

    # Colour each bar by avg damage
    bar_colors = (
        daily["avg_damage"].apply(
            lambda v: "#22c55e" if v < 1.5 else "#f59e0b" if v < 2.5 else "#ef4444"
        )
        if not daily.empty else []
    )

    fig.add_trace(go.Bar(
        x=atk_x, y=daily["attack_count"] if not daily.empty else [],
        name=T_ATTACK_COUNT,
        marker_color=bar_colors,
        opacity=0.80,
        yaxis="y",
        visible=True,
        hovertemplate="<b>%{x|%d %b %Y}</b><br>Attacks: <b>%{y}</b><extra></extra>",
    ))

    fig.add_trace(go.Scatter(
        x=atk_x, y=daily["rolling_7d"] if not daily.empty else [],
        name=T_ROLLING,
        mode="lines",
        line=dict(color=C["rolling"], width=2.5, dash="dot"),
        yaxis="y",
        visible=True,
        hovertemplate="7-day avg: %{y:.1f}<extra></extra>",
    ))

    # Marker traces (fire / hit / shutdown / air defense)
    _markers = [
        ("fire_count",       T_FIRE,     "triangle-up",  C["fire"],     True),
        ("hits",             T_HIT,      "circle",       C["hit"],      True),
        ("shutdowns",        T_SHUTDOWN, "square",       C["shutdown"], True),
        ("air_defense_count",T_AIRDEF,   "diamond",      C["airdef"],   True),
    ]
    for col, name, symbol, color, default_vis in _markers:
        y_vals = daily[col] if not daily.empty else []
        x_vals = atk_x
        fig.add_trace(go.Scatter(
            x=x_vals, y=y_vals,
            name=name,
            mode="markers",
            marker=dict(symbol=symbol, size=10, color=color,
                        line=dict(color="white", width=1.5)),
            yaxis="y",
            visible=True,
            hovertemplate=f"{name}: %{{y}}<extra></extra>",
        ))

    fig.add_trace(go.Bar(
        x=atk_x, y=daily["combined_count"] if not daily.empty else [],
        name=T_COMBINED,
        marker_color=C["combined"],
        opacity=0.70,
        yaxis="y",
        visible="legendonly",
        hovertemplate="Combined: <b>%{y}</b><extra></extra>",
    ))

    # ── DISCOURSE TRACES  (secondary y, legendonly) ───────────────────────────

    _disc_lines = [
        ("pre_drone",  T_PRE_DRONE,   C["pre_drone"],   "solid"),
        ("pre_airdef", T_PRE_AIRDEF,  C["pre_airdef"],  "solid"),
        ("pre_airport",T_PRE_AIRPORT, C["pre_airport"], "dash"),
        ("pre_uncert", T_PRE_UNCERT,  C["pre_uncert"],  "dot"),
        ("en_attack",  T_EN_ATTACK,   C["en_attack"],   "solid"),
        ("en_confirm", T_EN_CONFIRM,  C["en_confirm"],  "solid"),
        ("en_refinery",T_EN_REFINERY, C["en_refinery"], "dash"),
        ("war_total",  T_WAR_TOTAL,   C["war_total"],   "dot"),
        ("war_ukr_ru", T_WAR_UKR,     C["war_ukr"],     "dash"),
    ]

    for col, name, color, dash in _disc_lines:
        y_vals = disc[col] if (not disc.empty and col in disc.columns) else []
        fig.add_trace(go.Scatter(
            x=disc_x, y=y_vals,
            name=name,
            mode="lines",
            line=dict(color=color, width=1.8, dash=dash),
            yaxis="y2",
            visible="legendonly",
            hovertemplate=f"{name}: <b>%{{y}}</b><extra></extra>",
        ))

    # ── Layout ────────────────────────────────────────────────────────────────

    fig.update_layout(
        autosize=True,
        paper_bgcolor="#0a0b0d",
        plot_bgcolor="#0a0b0d",
        font=dict(family="'Inter','Segoe UI',sans-serif", size=12, color="#9297a3"),

        title=dict(
            text="<b>Attack Timeline & Discourse Signals</b>",
            font=dict(size=18, color="#eef0f3"),
            x=0.01,
        ),

        # Primary y — attacks
        yaxis=dict(
            title="Attacks / Events",
            side="left",
            showgrid=True,
            gridcolor="rgba(255,255,255,0.07)",
            gridwidth=1,
            zeroline=False,
            tickfont=dict(size=11),
        ),

        # Secondary y — discourse (right side, independent scale)
        yaxis2=dict(
            title="Messages",
            side="right",
            overlaying="y",
            showgrid=False,
            zeroline=False,
            tickfont=dict(size=11, color="#5b606c"),
            title_font=dict(color="#5b606c"),
        ),

        xaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.07)",
            rangeslider=dict(visible=True, thickness=0.05, bgcolor="#111318", bordercolor="rgba(255,255,255,0.08)"),
            rangeselector=dict(
                buttons=[
                    dict(count=1,  label="1m", step="month", stepmode="backward"),
                    dict(count=3,  label="3m", step="month", stepmode="backward"),
                    dict(count=6,  label="6m", step="month", stepmode="backward"),
                    dict(step="all", label="All"),
                ],
                font=dict(size=11, color="#eef0f3"),
                bgcolor="rgba(94,106,210,0.14)",
                activecolor=C["attack_bar"],
            ),
        ),

        legend=dict(
            orientation="v",
            x=1.08, y=1,
            bgcolor="rgba(17,19,24,0.85)",
            bordercolor="rgba(255,255,255,0.08)",
            borderwidth=1,
            font=dict(size=11, color="#9297a3"),
            tracegroupgap=10,
        ),

        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#16181d",
            bordercolor="rgba(255,255,255,0.14)",
            font=dict(size=12, color="#eef0f3"),
        ),

        barmode="overlay",
        margin=dict(l=55, r=80, t=55, b=50),
    )

    return fig


# ─────────────────────────────────────────────────────────────────────────────
#  HTML shell — fills QWebEngineView completely
# ─────────────────────────────────────────────────────────────────────────────

_HTML_WRAPPER = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body {{
    margin: 0; padding: 0;
    width: 100%; height: 100%;
    overflow: hidden;
    background: #0a0b0d;
  }}
  #chart {{
    width: 100%;
    height: 100%;
  }}
</style>
</head>
<body>
{plotly_div}
<script>
  (function resize() {{
    var gd = document.getElementById('chart');
    if (gd && gd._fullLayout) {{
      Plotly.relayout(gd, {{autosize: true,
        width:  window.innerWidth,
        height: window.innerHeight - 2
      }});
    }}
  }})();
  window.addEventListener('resize', function() {{
    var gd = document.getElementById('chart');
    if (gd && gd._fullLayout) {{
      Plotly.relayout(gd, {{
        width:  window.innerWidth,
        height: window.innerHeight - 2
      }});
    }}
  }});
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    out_path = Path(args.out) if args.out else root / "data" / "analysis" / "timeline.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = _conn()
    raw_attacks = load_attacks(conn)
    disc        = load_discourse(conn)
    conn.close()

    daily = aggregate_attacks(raw_attacks)
    fig   = build_figure(daily, disc)

    div_html = fig.to_html(
        full_html=False,
        include_plotlyjs=True,   # fully inline — no internet needed
        div_id="chart",
        config=dict(
            displayModeBar=True,
            modeBarButtonsToRemove=["lasso2d", "select2d"],
            scrollZoom=True,
            responsive=True,
            toImageButtonOptions=dict(
                format="png", filename="timeline",
                height=900, width=1800, scale=2,
            ),
        ),
    )
    out_path.write_text(
        _HTML_WRAPPER.format(plotly_div=div_html),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
