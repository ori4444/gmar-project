"""
Story page — plain-language, Hebrew, non-technical summary of what the model
found. No AUC, no lookback/horizon notation, no model names: color, shape,
dates, percentages and one dramatic headline per finding. Combines the Intel
Forecast bank (17-target model bank) and the Next Attack KNN analogy into a
single narrative.

Written for someone with zero computer background — see feedback memory
"Non-Technical Progress UI": color over numbers, plain language over jargon,
animation over static.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import psycopg2
from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from . import palette as P
from . import styles as S

_SCRIPTS = str(Path(__file__).resolve().parents[2])
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from shared.config import DB_DSN
from analysis.attack_forecast import (
    DIMENSIONS, get_bank_meta, predict_intel_full,
)
from analysis.forecast import predict_next_attack


# ─────────────────────────────────────────────────────────────────────────────
#  Hebrew copy
# ─────────────────────────────────────────────────────────────────────────────

_HE_TARGET_LABELS: dict[str, str] = {
    "ttype_power_facility": "תחנת כוח",
    "ttype_refinery":       "בית זיקוק",
    "ttype_pipeline":       "צינור",
    "ttype_oil_depot":      "מחסן דלק",
    "ttype_gas_facility":   "מתקן גז",
    "repeated_strike":      "תקיפה חוזרת",
    "multi_attack":         "הפגזה כבדה",
    "combined_strike":      "תקיפה משולבת",
    "deep_strike":          "חדירה עמוקה",
    "very_deep":            "חדירה עמוקה מאוד",
    "any_fire":             "שריפה",
    "multi_fire":           "כמה שריפות",
    "any_explosion":        "פיצוץ",
    "any_shutdown":         "השבתה",
    "any_attack":           "תקיפה כלשהי",
    "significant":          "אירוע משמעותי",
    "any_hit":              "פגיעה מאושרת",
}

_HE_TYPE_LABELS: dict[str, tuple[str, str]] = {
    "power_facility": ("תחנת כוח", "⚡"),
    "refinery":       ("בית זיקוק", "🛢️"),
    "pipeline":       ("צינור", "🚧"),
    "oil_depot":      ("מחסן דלק", "🛢️"),
    "gas_facility":   ("מתקן גז", "🔥"),
    "substation":     ("תחנת משנה", "⚡"),
    "airport":        ("שדה תעופה", "✈️"),
    "military":       ("מטרה צבאית", "🎖️"),
}

_HE_DIM_ICONS: dict[str, str] = {
    "infrastructure": "🏭", "scale": "📈", "effects": "🔥", "baseline": "📊",
}
_HE_DIM_LABELS: dict[str, str] = {
    "infrastructure": "תשתיות", "scale": "היקף", "effects": "השפעה", "baseline": "כללי",
}

_WARNING_HE: dict[str, tuple[str, str, str]] = {
    "CRITICAL": (P.RED,   "😱", "מתח קיצוני! הרבה סימנים לתקיפה קרובה"),
    "HIGH":     (P.RED,   "😨", "מתח גבוה — יש סימנים מדאיגים"),
    "ELEVATED": (P.AMBER, "😟", "קצת מתוח — יש סימנים בינוניים"),
    "LOW":      (P.GREEN, "🙂", "רגוע יחסית — הסימנים חלשים"),
    "NONE":     (P.TXT3,  "😴", "שקט לגמרי — אין סימנים ברורים כרגע"),
}

_BAND_HE: dict[str, tuple[str, str]] = {
    "near_term": ("בטווח הקרוב", "⏱️"),
    "weekly":    ("בטווח הבינוני", "📆"),
    "extended":  ("בטווח הרחוק", "🔭"),
}


def _he_target(target: str) -> str:
    return _HE_TARGET_LABELS.get(target, target.replace("_", " ").title())


def _he_type(raw: str) -> tuple[str, str]:
    return _HE_TYPE_LABELS.get(raw, (raw.replace("_", " ").title(), "🎯"))


def _tier_color(tier: str | None) -> str:
    return {"high": P.RED, "medium": P.AMBER, "low": P.GREEN}.get(tier or "", P.TXT3)


def _combo_to_hebrew(c: dict) -> str:
    pattern  = c.get("pattern", "")
    r        = c.get("r", 0.0)
    strength = {"notable": "בולט", "moderate": "מתון", "seasonal": "עונתי"}.get(
        c.get("strength", ""), "מעניין"
    )
    if "Drone" in pattern:
        return "🛸 כשיש יחד פעילות רחפנים ודיווחי תקיפה, הסיכוי לפגיעה עולה בבירור."
    m = re.search(r"(\d+)-day", pattern)
    if m and "advance signal" in pattern:
        days = m.group(1)
        return f"📡 יש איתות מוקדם בנתונים כ-{days} ימים לפני שהתקיפה קורית בפועל."
    if "Heating season" in pattern:
        return "🥶 בעונת החימום יש יותר תקיפות על תשתיות אנרגיה — מגמה עונתית ברורה."
    if "Air defense" in pattern:
        return "🛡️ עלייה בדיווחי הגנה אווירית מקדימה לרוב תקיפות על תשתיות."
    return f"🔗 נמצא קשר {strength} בין תופעות בנתונים (עוצמה {abs(r):.2f})."


# ─────────────────────────────────────────────────────────────────────────────
#  Small building blocks
# ─────────────────────────────────────────────────────────────────────────────

def _he_lbl(text: str, size: int = 12, bold: bool = False, color: str | None = None) -> QLabel:
    w = QLabel(text)
    w.setWordWrap(True)
    w.setStyleSheet(
        f"color:{color or P.TXT2}; font-family:{P.FONT_STACK}; font-size:{size}px; "
        f"font-weight:{'800' if bold else '500'}; border:none; background:transparent;"
    )
    return w


def _hline() -> QWidget:
    w = QWidget()
    w.setFixedHeight(1)
    w.setStyleSheet(S.divider_qss())
    return w


def _card(radius: int = P.RADIUS_LG, border: str | None = None) -> QWidget:
    w = QWidget()
    w.setStyleSheet(S.card_qss("QWidget", radius=radius, border=border))
    S.apply_card_shadow(w, blur=30, alpha=90, y_offset=10)
    return w


class _RingGauge(QWidget):
    """Colored donut-style percentage ring — the "shapes" half of the ask.
    Grows in from 0 on first paint for a bit of life instead of a static bar.
    """

    def __init__(self, fraction: float, color: str, size: int = 92,
                 thickness: int = 10, parent=None):
        super().__init__(parent)
        self._target = max(0.0, min(1.0, fraction))
        self._fraction = 0.0
        self._color = QColor(color)
        self._size = size
        self._thickness = thickness
        self.setFixedSize(size, size)

        self._anim = QPropertyAnimation(self, b"fraction")
        self._anim.setDuration(750)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(self._target)
        self._anim.start()

    def getFraction(self) -> float:
        return self._fraction

    def setFraction(self, value: float):
        self._fraction = value
        self.update()

    fraction = Property(float, getFraction, setFraction)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pad = self._thickness / 2 + 3
        rect = QRectF(pad, pad, self._size - 2 * pad, self._size - 2 * pad)

        track_pen = QPen(QColor(P.INPUT_BG))
        track_pen.setWidth(self._thickness)
        track_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, 360 * 16)

        fill_pen = QPen(self._color)
        fill_pen.setWidth(self._thickness)
        fill_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(fill_pen)
        span = -int(self._fraction * 360 * 16)
        painter.drawArc(rect, 90 * 16, span)

        painter.setPen(QColor(P.TXT))
        font = QFont(P.FONT_FAMILY, int(self._size * 0.19))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, f"{int(round(self._fraction * 100))}%")
        painter.end()


def _pulse(widget: QWidget):
    """Slow opacity pulse — used on the hero icon when the alert is high."""
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(900)
    anim.setStartValue(1.0)
    anim.setKeyValueAt(0.5, 0.55)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.InOutSine)
    anim.setLoopCount(-1)
    anim.start(QPropertyAnimation.DeletionPolicy.KeepWhenStopped)
    widget._pulse_anim = anim  # keep alive


def _stat_card(icon: str, title: str, big_text: str, big_color: str,
               sub: str = "", gauge: QWidget | None = None) -> QWidget:
    card = _card(radius=P.RADIUS_LG)
    card.setFixedHeight(150)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(6)

    hdr = QHBoxLayout()
    hdr.setSpacing(6)
    icon_lbl = QLabel(icon)
    icon_lbl.setStyleSheet("font-size:18px; border:none; background:transparent;")
    hdr.addWidget(icon_lbl)
    hdr.addWidget(_he_lbl(title, 12, bold=True, color=P.TXT2))
    hdr.addStretch()
    lay.addLayout(hdr)

    body = QHBoxLayout()
    body.setSpacing(10)
    txt_col = QVBoxLayout()
    txt_col.setSpacing(2)
    txt_col.addWidget(_he_lbl(big_text, 22, bold=True, color=big_color))
    if sub:
        txt_col.addWidget(_he_lbl(sub, 11, color=P.TXT3))
    txt_col.addStretch()
    body.addLayout(txt_col, 1)
    if gauge is not None:
        body.addWidget(gauge)
    lay.addLayout(body, 1)
    return card


def _insight_row(icon: str, text: str, pct: float | None = None,
                  color: str | None = None) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    icon_lbl = QLabel(icon)
    icon_lbl.setStyleSheet("font-size:15px; border:none; background:transparent;")
    icon_lbl.setFixedWidth(22)
    lay.addWidget(icon_lbl)
    lay.addWidget(_he_lbl(text, 12, color=P.TXT2), 1)
    if pct is not None:
        lay.addWidget(_he_lbl(f"{int(pct * 100)}%", 13, bold=True, color=color or P.TXT))
    return w


# ─────────────────────────────────────────────────────────────────────────────
#  Story page
# ─────────────────────────────────────────────────────────────────────────────

class StoryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self.setLayoutDirection(Qt.RightToLeft)
        self._setup_ui()

    def reset_view(self):
        if not self._busy:
            asyncio.ensure_future(self._load_async())

    def _setup_ui(self):
        self.setStyleSheet(S.window_bg_qss())
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(_he_lbl("✨  מה המודל חושב?", 19, bold=True, color=P.TXT))
        header.addStretch()

        self._refresh_btn = QPushButton("🔄  רענן")
        self._refresh_btn.setFixedSize(96, 32)
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background:{P.INDIGO}; color:#fff; border:none; border-radius:8px;
                font-size:13px; font-weight:800;
            }}
            QPushButton:hover {{ background:{P.INDIGO_D}; }}
            QPushButton:disabled {{ background:{P.INPUT_BG}; color:{P.TXT3}; }}
        """)
        self._refresh_btn.clicked.connect(self._on_refresh)
        header.addWidget(self._refresh_btn)
        root.addLayout(header)

        self._status_lbl = _he_lbl("", 12, color=P.TXT3)
        root.addWidget(self._status_lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")

        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(2, 4, 2, 12)
        self._content_lay.setSpacing(14)
        scroll.setWidget(self._content)
        root.addWidget(scroll, 1)

        self._show_placeholder("לוחצים על 'רענן' כדי לטעון תובנות 🔄")

    # ── Loading / placeholder ────────────────────────────────────────────────

    def _clear_content(self):
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_placeholder(self, text: str):
        self._clear_content()
        self._content_lay.addWidget(_he_lbl(text, 13, color=P.TXT3))
        self._content_lay.addStretch()

    def _on_refresh(self):
        if not self._busy:
            asyncio.ensure_future(self._load_async())

    async def _load_async(self):
        self._busy = True
        self._refresh_btn.setEnabled(False)
        self._status_lbl.setText("טוען תובנות... 🧠")
        self._status_lbl.setStyleSheet(f"color:{P.TXT3}; font-size:12px; border:none;")
        try:
            def _run():
                bank_meta = get_bank_meta()
                conn = psycopg2.connect(DB_DSN)
                try:
                    next_attack = predict_next_attack(conn)
                    intel, insights = None, None
                    if bank_meta is not None:
                        intel, insights = predict_intel_full(conn)
                finally:
                    conn.close()
                return bank_meta, intel, insights, next_attack

            bank_meta, intel, insights, next_attack = await asyncio.to_thread(_run)
            self._render(bank_meta, intel, insights, next_attack)
            self._status_lbl.setText("")
        except Exception as exc:
            self._show_placeholder(f"אופס, קרתה תקלה בטעינה 😅\n{exc}")
            self._status_lbl.setText("")
        finally:
            self._busy = False
            self._refresh_btn.setEnabled(True)

    # ── Render ────────────────────────────────────────────────────────────────

    def _render(self, bank_meta: dict | None, intel: dict | None,
                insights: dict | None, next_attack: dict):
        self._clear_content()
        lay = self._content_lay

        if "error" in next_attack:
            lay.addWidget(_he_lbl(f"אין עדיין מספיק נתונים היסטוריים: {next_attack['error']}",
                                   13, color=P.TXT3))
            lay.addStretch()
            return

        lay.addWidget(self._build_hero(intel, next_attack))

        if bank_meta is None:
            notice = _card(radius=P.RADIUS_MD, border=P.AMBER)
            nlay = QHBoxLayout(notice)
            nlay.setContentsMargins(14, 10, 14, 10)
            nlay.addWidget(_he_lbl(
                "🤖 המודל החכם עוד לא אומן — הנתונים למטה מבוססים רק על היסטוריה. "
                "כדי לקבל גם תחזית חכמה, אפשר ללחוץ 'Create Model' בעמוד Predict.",
                12, color=P.AMBER,
            ))
            lay.addWidget(notice)

        lay.addWidget(self._build_when_where_what(next_attack))
        lay.addWidget(self._build_severity(next_attack))

        if intel is not None and insights is not None:
            feed = self._build_insights_feed(intel, insights)
            if feed is not None:
                lay.addWidget(feed)

        data_through = intel.get("data_through") if intel else next_attack.get("discourse_to", "")
        lay.addWidget(_hline())
        lay.addWidget(_he_lbl(f"מבוסס על נתונים עד {data_through}", 10, color=P.TXT3))
        lay.addStretch()

    def _build_hero(self, intel: dict | None, next_attack: dict) -> QWidget:
        if intel is not None:
            level = intel.get("warning_level", "NONE")
            color, emoji, text = _WARNING_HE.get(level, _WARNING_HE["NONE"])
        else:
            level, color, emoji = None, P.INDIGO, "🔎"
            text = "עדיין אין מודל חכם מאומן — אבל יש כבר ניתוח על סמך היסטוריה דומה"

        card = _card(radius=P.RADIUS_XL, border=color)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(18)

        icon_lbl = QLabel(emoji)
        icon_lbl.setStyleSheet("font-size:52px; border:none; background:transparent;")
        lay.addWidget(icon_lbl)
        if level in ("CRITICAL", "HIGH"):
            _pulse(icon_lbl)

        txt_col = QVBoxLayout()
        txt_col.setSpacing(4)
        txt_col.addWidget(_he_lbl(text, 19, bold=True, color=color))
        lay.addLayout(txt_col, 1)
        return card

    def _build_when_where_what(self, next_attack: dict) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)

        # מתי
        days = next_attack.get("days_estimate")
        drange = next_attack.get("days_range")
        if days is not None:
            big = f"בעוד כ-{days:.0f} ימים"
            sub = f"טווח: {drange[0]:.0f}–{drange[1]:.0f} ימים" if drange else ""
        else:
            big, sub = "לא ידוע", "אין מספיק תקדימים דומים"
        h.addWidget(_stat_card("📅", "מתי?", big, P.INDIGO_L, sub), 1)

        # איפה
        region_dist = next_attack.get("region_dist") or []
        if region_dist:
            place, frac = region_dist[0]
            gauge = _RingGauge(frac, P.VIOLET, size=70, thickness=8)
            h.addWidget(_stat_card("📍", "איפה הכי צפוי?", place, P.VIOLET, gauge=gauge), 1)
        else:
            h.addWidget(_stat_card("📍", "איפה הכי צפוי?", "לא ידוע", P.TXT3), 1)

        # איזה סוג
        type_dist = next_attack.get("type_dist") or []
        if type_dist:
            raw, frac = type_dist[0]
            he_label, icon = _he_type(raw)
            gauge = _RingGauge(frac, P.CYAN, size=70, thickness=8)
            h.addWidget(_stat_card(icon, "איזה סוג?", he_label, P.CYAN, gauge=gauge), 1)
        else:
            h.addWidget(_stat_card("🎯", "איזה סוג?", "לא ידוע", P.TXT3), 1)

        return row

    def _build_severity(self, next_attack: dict) -> QWidget:
        card = _card(radius=P.RADIUS_LG)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)
        lay.addWidget(_he_lbl("כמה חמור זה עלול להיות?", 13, bold=True, color=P.TXT))

        row = QHBoxLayout()
        row.setSpacing(28)
        for icon, label, key, color in [
            ("🔥", "שריפה", "fire_rate", P.RED),
            ("🎯", "פגיעה מאושרת", "hit_rate", P.AMBER),
            ("🕳️", "חדירה עמוקה", "deep_rate", P.VIOLET),
        ]:
            frac = next_attack.get(key, 0.0)
            col = QVBoxLayout()
            col.setSpacing(6)
            col.setAlignment(Qt.AlignHCenter)
            col.addWidget(_RingGauge(frac, color, size=76, thickness=9), 0, Qt.AlignHCenter)
            lbl = _he_lbl(f"{icon}  {label}", 11, color=P.TXT2)
            lbl.setAlignment(Qt.AlignHCenter)
            col.addWidget(lbl)
            row.addLayout(col)
        row.addStretch()
        lay.addLayout(row)
        return card

    def _build_insights_feed(self, intel: dict, insights: dict) -> QWidget | None:
        sections: list[QWidget] = []

        # ── multi-horizon: top passing signal per band, across all dimensions ──
        horizon_cards = []
        for band_key in ("near_term", "weekly", "extended"):
            band = intel.get(band_key, {})
            passing = [s for sigs in band.get("signals", {}).values() for s in sigs]
            if not passing:
                continue
            top = max(passing, key=lambda s: s["prob"])
            band_label, band_icon = _BAND_HE[band_key]
            dim_icon = _HE_DIM_ICONS.get(top["dimension"], "🔹")
            text = f"{band_icon} {band_label} — {dim_icon} {_he_target(top['target'])}"
            horizon_cards.append(_insight_row(
                "•", text, pct=top["prob"], color=_tier_color(top["tier"]),
            ))
        if horizon_cards:
            group = _card(radius=P.RADIUS_MD)
            glay = QVBoxLayout(group)
            glay.setContentsMargins(16, 12, 16, 12)
            glay.setSpacing(8)
            glay.addWidget(_he_lbl("לפי טווח זמן", 13, bold=True, color=P.TXT))
            for c in horizon_cards:
                glay.addWidget(c)
            sections.append(group)

        # ── feature ↔ feature relationships ──────────────────────────────────
        combos = insights.get("notable_combos", [])[:4]
        if combos:
            group = _card(radius=P.RADIUS_MD)
            glay = QVBoxLayout(group)
            glay.setContentsMargins(16, 12, 16, 12)
            glay.setSpacing(8)
            glay.addWidget(_he_lbl("קשרים מעניינים בין תופעות", 13, bold=True, color=P.TXT))
            for c in combos:
                glay.addWidget(_he_lbl(_combo_to_hebrew(c), 12, color=P.TXT2))
            sections.append(group)

        # ── high-confidence time windows ─────────────────────────────────────
        high_sigs = [s for s in insights.get("signal_insights", []) if s.get("tier") == "high"]
        if high_sigs:
            group = _card(radius=P.RADIUS_MD)
            glay = QVBoxLayout(group)
            glay.setContentsMargins(16, 12, 16, 12)
            glay.setSpacing(8)
            glay.addWidget(_he_lbl("חלונות זמן בוודאות גבוהה", 13, bold=True, color=P.TXT))
            for s in high_sigs:
                lb, hh = s.get("best_lb", "?"), s.get("best_h", "?")
                text = (
                    f"🎯 כדי לחזות {_he_target(s['target'])}, המודל מסתכל על {lb} הימים "
                    f"האחרונים ומעריך עד {hh} ימים קדימה"
                )
                glay.addWidget(_insight_row("", text, pct=s["prob"], color=P.RED))
            sections.append(group)

        if not sections:
            return None

        wrap = QWidget()
        wlay = QVBoxLayout(wrap)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(14)
        wlay.addWidget(_he_lbl("תובנות מעניינות", 15, bold=True, color=P.TXT))
        for s in sections:
            wlay.addWidget(s)
        return wrap