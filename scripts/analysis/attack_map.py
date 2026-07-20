"""
scripts/analysis/attack_map.py
─────────────────────────────────────────────────────────────────────────────
Generates a self-contained HTML page with every attack plotted on a
Russia/Ukraine map, bundled fully inline — no internet needed.

Plotly's built-in geo/Scattergeo trace always fetches its land/country shape
data from cdn.plot.ly at runtime (plotly.js never bundles the actual
topojson geometry), which breaks on machines without internet access. To
keep this offline like the rest of the app (see timeline.py's "no CDN"
docstring), the Russia/Ukraine outline is instead drawn as plain Cartesian
line/fill traces from static coordinate data (shared/ru_ua_borders.py), with
the attack markers plotted in the same lon/lat coordinate space.

The page embeds all attack points as a JS array so date filtering (two
<input type="date"> fields at the top) and click-to-details both run
client-side, with no server / Python round-trip after the file is written.

Usage
-----
    python scripts/analysis/attack_map.py [--out PATH]
"""

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telegram_attacks.db import get_conn, fetch_all_attacks_for_map
from shared.area_coords import get_coords
from shared.ru_ua_borders import RUSSIA, UKRAINE

DMG_COLORS = {
    "low": "#3DDC84",
    "medium": "#F5A623",
    "high": "#F0555A",
    "catastrophic": "#F0555A",
}
DEFAULT_COLOR = "#6b7280"
APPROX_COLOR = "#9297a3"

# Areas matching detectors.MACRO_REGIONS' "Occupied Territories" bucket —
# drawn as diamonds instead of circles so they're visually distinct from
# both the Russia and Ukraine polygons.
OCCUPIED_AREAS = {
    "Crimea", "Occupied Crimea", "Zaporizhzhia Oblast",
    "Luhansk Oblast", "Occupied Kherson", "Occupied Donetsk",
}
OCCUPIED_MARKER_LINE = "#FFC857"


def _is_occupied(area, macro_region) -> bool:
    if macro_region == "Occupied Territories":
        return True
    return (area or "").strip() in OCCUPIED_AREAS

_FIELD_LABELS = [
    ("area", "Area"),
    ("attack_date", "Date"),
    ("target_type", "Target"),
    ("damage_level", "Damage"),
    ("confidence", "Confidence"),
    ("hit_confirmed", "Hit confirmed"),
    ("fire", "Fire"),
    ("shutdown", "Shutdown"),
    ("explosions_reported", "Explosions"),
    ("air_defense_active", "Air defense active"),
    ("combined_strike", "Combined strike"),
    ("drone_scale", "Drone scale"),
    ("report_type", "Report type"),
    ("status", "Status"),
]


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if value is None or value == "":
        return "unknown"
    return str(value)


def _detail_html(row: dict, exact: bool) -> str:
    lines = [
        f'<div class="d-row"><span class="d-k">{html.escape(label)}</span>'
        f'<span class="d-v">{html.escape(_fmt(row.get(field)))}</span></div>'
        for field, label in _FIELD_LABELS
    ]
    note = "" if exact else (
        '<div class="d-note">📍 מיקום משוער (אזור כללי, לא נקודה מדויקת)</div>'
    )
    return (
        f'<div class="d-title">תקיפה #{row.get("attack_id")}</div>'
        + "".join(lines) + note
    )


def _multipolygon_xy(polygons: list) -> tuple[list, list]:
    """Flatten [polygon -> ring -> [lon, lat]] into one None-separated x/y
    pair, so each disjoint ring can be drawn (and filled via `fill="toself"`)
    as a single Scatter trace instead of one trace per island."""
    xs: list = []
    ys: list = []
    for poly in polygons:
        for ring in poly:
            if xs:
                xs.append(None)
                ys.append(None)
            for lon, lat in ring:
                xs.append(lon)
                ys.append(lat)
    return xs, ys


# Default viewport — Ukraine + the western/central Russian theater where the
# vast majority of attacks happen. Widened per-render (see _view_range) so a
# real attack somewhere outside this box (e.g. Far East, Siberia) is never
# silently cropped out of the initial view.
_DEFAULT_X_RANGE = [18, 72]
_DEFAULT_Y_RANGE = [42, 64]
_RANGE_PADDING = 2  # degrees of breathing room around out-of-box points


def _view_range(points: list[dict]) -> tuple[list, list]:
    lons = [p["lon"] for p in points]
    lats = [p["lat"] for p in points]
    x_min = min([_DEFAULT_X_RANGE[0]] + [l - _RANGE_PADDING for l in lons])
    x_max = max([_DEFAULT_X_RANGE[1]] + [l + _RANGE_PADDING for l in lons])
    y_min = min([_DEFAULT_Y_RANGE[0]] + [l - _RANGE_PADDING for l in lats])
    y_max = max([_DEFAULT_Y_RANGE[1]] + [l + _RANGE_PADDING for l in lats])
    return [x_min, x_max], [y_min, y_max]


def build_points(rows: list[dict]) -> list[dict]:
    points = []
    for row in rows:
        lat, lon, exact = get_coords(row.get("area"), row.get("macro_region"))
        date = row.get("attack_date")
        date_str = date.isoformat() if hasattr(date, "isoformat") else str(date)
        color = DMG_COLORS.get((row.get("damage_level") or "").lower(), DEFAULT_COLOR)
        if not exact:
            color = APPROX_COLOR
        occupied = _is_occupied(row.get("area"), row.get("macro_region"))
        points.append({
            "lat": lat,
            "lon": lon,
            "date": date_str,
            "text": f'{row.get("area")} — {date_str}',
            "color": color,
            "symbol": "diamond" if occupied else "circle",
            "line_color": OCCUPIED_MARKER_LINE if occupied else "white",
            "line_width": 2 if occupied else 1,
            "detail": _detail_html(row, exact),
            "area": row.get("area"),
            "attack_id": row.get("attack_id"),
            "target_type": row.get("target_type"),
            "hit_confirmed": bool(row.get("hit_confirmed")),
        })
    return points


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  html, body {{
    margin: 0; padding: 0; width: 100%; height: 100%;
    overflow: hidden; background: #0a0b0d;
    font-family: 'Inter','Segoe UI',sans-serif;
  }}
  #topbar {{
    position: absolute; top: 0; left: 0; right: 0; height: 52px;
    display: flex; align-items: center; gap: 10px;
    padding: 0 16px; box-sizing: border-box;
    background: rgba(17,19,24,0.92);
    border-bottom: 1px solid rgba(255,255,255,0.08);
    z-index: 10; direction: rtl;
  }}
  #topbar label {{ color: #9297a3; font-size: 12px; }}
  #topbar button {{
    background: transparent; color: #5E6AD2;
    border: 1px solid #5E6AD2; border-radius: 6px;
    padding: 5px 12px; font-size: 12px; font-weight: 700;
    cursor: pointer; font-family: inherit;
  }}
  #topbar button:hover {{ background: #5E6AD2; color: #0a0b0d; }}
  .datechip {{
    background: #16181d; color: #eef0f3 !important;
    border: 1px solid rgba(255,255,255,0.14) !important;
    min-width: 92px; text-align: center; font-weight: 600 !important;
  }}
  .datechip:hover {{ background: #1d2027; color: #eef0f3 !important; }}
  .datechip.active {{ border-color: #5E6AD2 !important; }}
  #count {{ color: #5b606c; font-size: 12px; margin-inline-start: auto; }}
  #datepicker {{
    display: none; position: absolute; z-index: 20;
    background: rgba(17,19,24,0.98); color: #eef0f3;
    border: 1px solid rgba(255,255,255,0.14); border-radius: 12px;
    padding: 14px; font-size: 12px; direction: rtl;
    box-shadow: 0 20px 56px rgba(0,0,0,0.5);
  }}
  #datepicker .dp-fields {{ display: flex; gap: 8px; margin-bottom: 12px; }}
  #datepicker .dp-field {{ display: flex; flex-direction: column; gap: 4px; align-items: center; }}
  #datepicker .dp-field label {{ color: #9297a3; font-size: 10px; }}
  #datepicker input[type=number] {{
    background: #0e0f13; color: #eef0f3;
    border: 1px solid rgba(255,255,255,0.14); border-radius: 6px;
    padding: 5px 4px; font-size: 13px; font-family: inherit;
    width: 56px; text-align: center; color-scheme: dark;
  }}
  #datepicker input[type=number]:focus {{ border-color: #5E6AD2; outline: none; }}
  #datepicker .dp-actions {{ display: flex; gap: 6px; justify-content: space-between; }}
  #datepicker .dp-actions button {{ flex: 1; padding: 5px 6px; font-size: 11px; }}
  #datepicker .dp-clear {{ border-color: #5b606c !important; color: #9297a3 !important; }}
  #datepicker .dp-clear:hover {{ background: #5b606c !important; color: #0a0b0d !important; }}
  #datepicker .dp-cancel {{ border-color: rgba(255,255,255,0.14) !important; color: #9297a3 !important; }}
  #datepicker .dp-cancel:hover {{ background: rgba(255,255,255,0.14) !important; color: #eef0f3 !important; }}
  #map {{ position: absolute; top: 52px; left: 0; right: 0; bottom: 0; }}
  #map, #chart {{ width: 100%; height: 100%; }}
  #detail {{
    display: none; position: absolute; top: 68px; inset-inline-end: 16px;
    width: 260px; max-height: calc(100% - 100px); overflow-y: auto;
    background: rgba(17,19,24,0.96); color: #eef0f3;
    border: 1px solid rgba(255,255,255,0.14); border-radius: 12px;
    padding: 14px 16px; font-size: 12px; z-index: 10;
    box-shadow: 0 20px 56px rgba(0,0,0,0.5);
    direction: rtl;
  }}
  #detail .d-close {{
    position: absolute; top: 8px; inset-inline-start: 10px;
    cursor: pointer; color: #5b606c; font-size: 16px; line-height: 1;
  }}
  #detail .d-close:hover {{ color: #eef0f3; }}
  .d-title {{ font-weight: 700; font-size: 13px; margin-bottom: 8px; color: #eef0f3; }}
  .d-row {{ display: flex; justify-content: space-between; gap: 10px; padding: 3px 0;
            border-bottom: 1px solid rgba(255,255,255,0.06); }}
  .d-k {{ color: #9297a3; }}
  .d-v {{ color: #eef0f3; font-weight: 600; text-align: end; }}
  .d-note {{ margin-top: 8px; color: #F5A623; font-size: 11px; }}
  .d-list {{ max-height: 320px; overflow-y: auto; margin-top: 4px; }}
  .d-list-row {{ display: flex; align-items: center; gap: 8px; padding: 6px 2px;
                border-bottom: 1px solid rgba(255,255,255,0.06); cursor: pointer; }}
  .d-list-row:hover {{ background: rgba(255,255,255,0.05); }}
  .d-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
  .d-list-date {{ color: #eef0f3; font-size: 11px; font-weight: 600; }}
  .d-list-target {{ color: #9297a3; font-size: 11px; flex: 1; overflow: hidden;
                    text-overflow: ellipsis; white-space: nowrap; }}
  .d-list-hit {{ color: #3DDC84; font-size: 11px; }}
  .d-back {{ color: #5E6AD2; font-size: 11px; font-weight: 700; cursor: pointer;
            margin-bottom: 8px; padding-bottom: 6px;
            border-bottom: 1px solid rgba(255,255,255,0.08); }}
  .d-back:hover {{ color: #7b85e0; }}
</style>
</head>
<body>
<div id="topbar">
  <label>מתאריך</label>
  <button class="datechip" id="chipFrom">הכל</button>
  <label>עד תאריך</label>
  <button class="datechip" id="chipTo">הכל</button>
  <button id="reset">הצג הכל</button>
  <span id="count"></span>
</div>
<div id="map">
{plotly_div}
</div>
<div id="detail"><span class="d-close" onclick="document.getElementById('detail').style.display='none'">×</span>
  <div id="detail-body"></div>
</div>
<div id="datepicker">
  <div class="dp-fields">
    <div class="dp-field">
      <label>יום</label>
      <input type="number" id="dpDay" min="1" max="31" step="1">
    </div>
    <div class="dp-field">
      <label>חודש</label>
      <input type="number" id="dpMonth" min="1" max="12" step="1">
    </div>
    <div class="dp-field">
      <label>שנה</label>
      <input type="number" id="dpYear" min="2000" max="2100" step="1">
    </div>
  </div>
  <div class="dp-actions">
    <button id="dpApply">אישור</button>
    <button class="dp-clear" id="dpClear">נקה</button>
    <button class="dp-cancel" id="dpCancel">ביטול</button>
  </div>
</div>
<script>
  var ALL_POINTS = {points_json};

  var ATTACKS_TRACE = 2;  // 0=Russia outline, 1=Ukraine outline, 2=attack markers

  // Mirrors DMG_COLORS / DEFAULT_COLOR / APPROX_COLOR in the Python source —
  // used to pick the worst-severity color to represent a stack of attacks.
  var SEVERITY_RANK = {{ '#3DDC84': 1, '#F5A623': 2, '#F0555A': 3, '#6b7280': 0, '#9297a3': -1 }};

  function markerSize(count) {{
    if (count <= 1) return 9;
    return Math.min(9 + 5 * Math.sqrt(count - 1), 26);
  }}

  function escapeHtml(s) {{
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {{
      return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c];
    }});
  }}

  // Groups the (already date-filtered) points by exact coordinate — every
  // attack recorded against the same named area shares one static centroid
  // (see shared/area_coords.py), so many attacks land on the identical
  // (lat, lon). Re-run on every render() so a stack always reflects
  // whatever date range is currently applied.
  function groupByLocation(points) {{
    var groups = {{}}, order = [];
    points.forEach(function(p) {{
      var key = p.lon + ',' + p.lat;
      if (!groups[key]) {{ groups[key] = []; order.push(key); }}
      groups[key].push(p);
    }});
    return order.map(function(key) {{
      var items = groups[key].slice().sort(function(a, b) {{
        return a.date < b.date ? 1 : (a.date > b.date ? -1 : 0);  // date desc
      }});
      // Different area names can share a coordinate (e.g. Crimea / Occupied
      // Crimea) — don't assume symbol/outline are uniform across the group,
      // derive a representative defensively instead.
      var rep = items.find(function(p) {{ return p.symbol === 'diamond'; }}) || items[0];
      var worst = items.reduce(function(best, p) {{
        return (SEVERITY_RANK[p.color] || 0) > (SEVERITY_RANK[best.color] || 0) ? p : best;
      }}, items[0]);
      return {{
        lon: items[0].lon, lat: items[0].lat, items: items, count: items.length,
        area: items[0].area, color: worst.color, symbol: rep.symbol,
        line_color: rep.line_color, line_width: rep.line_width,
        hoverText: items.length === 1 ? items[0].text
                                       : (items[0].area + ' — ' + items.length + ' תקיפות'),
        badgeText: items.length > 1 ? String(items.length) : '',
        size: markerSize(items.length),
      }};
    }});
  }}

  function render(points) {{
    var gd = document.getElementById('chart');
    var groups = groupByLocation(points);
    var x = groups.map(function(g) {{ return g.lon; }});
    var y = groups.map(function(g) {{ return g.lat; }});
    var text = groups.map(function(g) {{ return g.badgeText; }});
    var hovertext = groups.map(function(g) {{ return g.hoverText; }});
    var colors = groups.map(function(g) {{ return g.color; }});
    var symbols = groups.map(function(g) {{ return g.symbol; }});
    var lineColors = groups.map(function(g) {{ return g.line_color; }});
    var lineWidths = groups.map(function(g) {{ return g.line_width; }});
    var sizes = groups.map(function(g) {{ return g.size; }});
    var customdata = groups.map(function(g) {{ return g.items; }});
    Plotly.restyle(gd, {{
      x: [x], y: [y], text: [text], hovertext: [hovertext],
      'marker.color': [colors], 'marker.symbol': [symbols],
      'marker.line.color': [lineColors], 'marker.line.width': [lineWidths],
      'marker.size': [sizes], customdata: [customdata],
    }}, [ATTACKS_TRACE]);
    document.getElementById('count').textContent =
      points.length + ' תקיפות מוצגות ב-' + groups.length + ' מוקדים';
  }}

  function showDetail(html) {{
    document.getElementById('detail-body').innerHTML = html;
    document.getElementById('detail').style.display = 'block';
  }}

  function showList(items) {{
    var header = '<div class="d-title">' + escapeHtml(items[0].area)
      + ' — ' + items.length + ' תקיפות</div>';
    var rows = items.map(function(item, idx) {{
      return '<div class="d-list-row" data-idx="' + idx + '">'
        + '<span class="d-dot" style="background:' + item.color + '"></span>'
        + '<span class="d-list-date">' + item.date + '</span>'
        + '<span class="d-list-target">' + escapeHtml(item.target_type || '') + '</span>'
        + (item.hit_confirmed ? '<span class="d-list-hit">✓</span>' : '')
        + '</div>';
    }}).join('');
    document.getElementById('detail-body').innerHTML = header + '<div class="d-list">' + rows + '</div>';
    document.getElementById('detail').style.display = 'block';
    document.querySelectorAll('.d-list-row').forEach(function(rowEl) {{
      rowEl.addEventListener('click', function() {{
        showDetailFromList(items, parseInt(rowEl.getAttribute('data-idx'), 10));
      }});
    }});
  }}

  function showDetailFromList(items, idx) {{
    var back = '<div class="d-back" id="dBack">→ בחזרה לרשימה (' + items.length + ')</div>';
    document.getElementById('detail-body').innerHTML = back + items[idx].detail;
    document.getElementById('dBack').addEventListener('click', function() {{ showList(items); }});
  }}

  var filterFrom = null;   // ISO 'yyyy-mm-dd' or null (no lower bound)
  var filterTo = null;     // ISO 'yyyy-mm-dd' or null (no upper bound)
  var pickerTarget = null; // 'from' | 'to' — which chip opened the popup

  function applyFilter() {{
    var filtered = ALL_POINTS.filter(function(p) {{
      if (filterFrom && p.date < filterFrom) return false;
      if (filterTo && p.date > filterTo) return false;
      return true;
    }});
    render(filtered);
  }}

  function pad2(n) {{ return String(n).padStart(2, '0'); }}

  function isoToDMY(iso) {{
    var parts = iso.split('-');
    return {{ y: parseInt(parts[0], 10), m: parseInt(parts[1], 10), d: parseInt(parts[2], 10) }};
  }}

  function formatChip(iso) {{
    var dmy = isoToDMY(iso);
    return pad2(dmy.d) + '/' + pad2(dmy.m) + '/' + dmy.y;
  }}

  function daysInMonth(year, month) {{
    return new Date(year, month, 0).getDate();
  }}

  function clampDay() {{
    var y = parseInt(document.getElementById('dpYear').value, 10);
    var m = parseInt(document.getElementById('dpMonth').value, 10);
    var dayInput = document.getElementById('dpDay');
    if (!y || !m) return;
    var maxDay = daysInMonth(y, m);
    dayInput.max = maxDay;
    if (parseInt(dayInput.value, 10) > maxDay) dayInput.value = maxDay;
  }}

  function openPicker(which, anchorEl) {{
    pickerTarget = which;
    var current = which === 'from' ? filterFrom : filterTo;
    var seed = current ? isoToDMY(current) : (function() {{
      var t = new Date();
      return {{ y: t.getFullYear(), m: t.getMonth() + 1, d: t.getDate() }};
    }})();
    document.getElementById('dpYear').value = seed.y;
    document.getElementById('dpMonth').value = seed.m;
    document.getElementById('dpDay').value = seed.d;
    clampDay();

    var dp = document.getElementById('datepicker');
    var rect = anchorEl.getBoundingClientRect();
    dp.style.display = 'block';
    dp.style.top = (rect.bottom + 6) + 'px';
    // Anchor from the right edge, not the left: this is an RTL page and both
    // date chips sit near the right side of the topbar, so a left-anchored
    // popup (growing rightward, per normal CSS box layout) runs off-screen.
    dp.style.left = 'auto';
    dp.style.right = (window.innerWidth - rect.right) + 'px';

    document.getElementById('chipFrom').classList.toggle('active', which === 'from');
    document.getElementById('chipTo').classList.toggle('active', which === 'to');
  }}

  function closePicker() {{
    document.getElementById('datepicker').style.display = 'none';
    document.getElementById('chipFrom').classList.remove('active');
    document.getElementById('chipTo').classList.remove('active');
    pickerTarget = null;
  }}

  function setFilterFromPicker() {{
    var y = parseInt(document.getElementById('dpYear').value, 10);
    var m = parseInt(document.getElementById('dpMonth').value, 10);
    var d = parseInt(document.getElementById('dpDay').value, 10);
    var iso = y + '-' + pad2(m) + '-' + pad2(d);
    var chipId = pickerTarget === 'from' ? 'chipFrom' : 'chipTo';
    if (pickerTarget === 'from') {{ filterFrom = iso; }} else {{ filterTo = iso; }}
    document.getElementById(chipId).textContent = formatChip(iso);
    closePicker();
    applyFilter();
  }}

  function clearFilterFromPicker() {{
    var chipId = pickerTarget === 'from' ? 'chipFrom' : 'chipTo';
    if (pickerTarget === 'from') {{ filterFrom = null; }} else {{ filterTo = null; }}
    document.getElementById(chipId).textContent = 'הכל';
    closePicker();
    applyFilter();
  }}

  document.getElementById('chipFrom').addEventListener('click', function() {{
    openPicker('from', this);
  }});
  document.getElementById('chipTo').addEventListener('click', function() {{
    openPicker('to', this);
  }});
  document.getElementById('dpApply').addEventListener('click', setFilterFromPicker);
  document.getElementById('dpClear').addEventListener('click', clearFilterFromPicker);
  document.getElementById('dpCancel').addEventListener('click', closePicker);
  document.getElementById('dpMonth').addEventListener('change', clampDay);
  document.getElementById('dpYear').addEventListener('change', clampDay);
  document.getElementById('datepicker').addEventListener('keydown', function(ev) {{
    if (ev.key === 'Enter') setFilterFromPicker();
    if (ev.key === 'Escape') closePicker();
  }});
  document.addEventListener('click', function(ev) {{
    var dp = document.getElementById('datepicker');
    if (dp.style.display !== 'block') return;
    if (dp.contains(ev.target) || ev.target === document.getElementById('chipFrom')
        || ev.target === document.getElementById('chipTo')) return;
    closePicker();
  }});

  document.getElementById('reset').addEventListener('click', function() {{
    filterFrom = null;
    filterTo = null;
    document.getElementById('chipFrom').textContent = 'הכל';
    document.getElementById('chipTo').textContent = 'הכל';
    render(ALL_POINTS);
  }});

  (function init() {{
    var gd = document.getElementById('chart');
    render(ALL_POINTS);
    gd.on('plotly_click', function(data) {{
      if (!data.points.length) return;
      var items = data.points[0].customdata;
      if (items.length === 1) {{ showDetail(items[0].detail); }}
      else {{ showList(items); }}
    }});
    function resize() {{
      var mapDiv = document.getElementById('map');
      Plotly.relayout(gd, {{width: mapDiv.clientWidth, height: mapDiv.clientHeight}});
    }}
    window.addEventListener('resize', resize);
    resize();
  }})();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    out_path = Path(args.out) if args.out else root / "data" / "analysis" / "attack_map.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_conn()
    try:
        rows = fetch_all_attacks_for_map(conn)
    finally:
        conn.close()

    points = build_points(rows)

    import plotly.graph_objects as go

    rus_x, rus_y = _multipolygon_xy(RUSSIA)
    ukr_x, ukr_y = _multipolygon_xy(UKRAINE)

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=rus_x, y=rus_y, mode="lines", fill="toself", connectgaps=False,
        fillcolor="rgba(255,255,255,0.035)",
        line=dict(color="rgba(255,255,255,0.35)", width=1),
        hoverinfo="skip", showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=ukr_x, y=ukr_y, mode="lines", fill="toself", connectgaps=False,
        fillcolor="rgba(94,106,210,0.08)",
        line=dict(color="rgba(94,106,210,0.55)", width=1.3),
        hoverinfo="skip", showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=[p["lon"] for p in points],
        y=[p["lat"] for p in points],
        text=["" for _ in points],
        hovertext=[p["text"] for p in points],
        mode="markers+text",
        textposition="middle center",
        textfont=dict(size=10, color="#0a0b0d"),
        marker=dict(
            size=9,
            color=[p["color"] for p in points],
            symbol=[p["symbol"] for p in points],
            line=dict(
                color=[p["line_color"] for p in points],
                width=[p["line_width"] for p in points],
            ),
            opacity=0.9,
        ),
        customdata=[p["detail"] for p in points],
        hovertemplate="%{hovertext}<extra></extra>",
        showlegend=False,
    ))

    x_range, y_range = _view_range(points)

    fig.update_layout(
        paper_bgcolor="#0a0b0d",
        plot_bgcolor="#0a0b0d",
        margin=dict(l=0, r=0, t=0, b=0),
        dragmode="pan",
        xaxis=dict(
            visible=False, range=x_range,
            showgrid=False, zeroline=False,
        ),
        yaxis=dict(
            visible=False, range=y_range,
            showgrid=False, zeroline=False,
            scaleanchor="x", scaleratio=1.66,  # ~1/cos(53°N) — roughly true-proportioned at this latitude
        ),
    )

    div_html = fig.to_html(
        full_html=False,
        include_plotlyjs=True,
        div_id="chart",
        config=dict(
            displayModeBar=True,
            modeBarButtonsToRemove=["lasso2d", "select2d"],
            scrollZoom=True,
            responsive=True,
        ),
    )

    out_path.write_text(
        _HTML_TEMPLATE.format(
            plotly_div=div_html,
            points_json=json.dumps(points),
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
