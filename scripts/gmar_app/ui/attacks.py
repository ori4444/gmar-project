"""
Attacks review page — Hebrew-only, single-screen layout.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import palette as P
from .widgets import BoolToggle, ButtonSelector, SectionLabel, SmartCombo, field_row

# ─────────────────────────────────────────────────────────────────────────────
#  Option lists  (Hebrew labels)
# ─────────────────────────────────────────────────────────────────────────────

AREA_OPTIONS = sorted({
    "Krasnodar", "Tuapse", "Volgograd", "Ufa", "Bashkortostan", "Samara",
    "Syzran", "Novokuibyshevsk", "Ryazan", "Smolensk", "Yartsevo", "Saratov",
    "Engels", "Bryansk", "Tver", "Udomlya", "Kursk", "Astrakhan", "Rostov",
    "Novoshakhtinsk", "Chertkovo", "Leningrad Oblast", "Kirishi", "Chuvashia",
    "Cheboksary", "Serpukhov", "Moscow Oblast", "Tambov", "Petrovsk",
    "Gay-Kodzor", "Karachev", "Voronezh", "Liski", "Lipetsk", "Uzlovaya",
    "Novorossiysk", "Oryol", "Kazan", "Almetyevsk", "Aleksin", "Tula",
    "Naberezhnye Chelny", "Nizhnekamsk", "Perm", "Izhevsk",
    "Orenburg", "Penza", "Ulyanovsk", "Nizhny Novgorod", "Unknown Area",
})

TARGET_OPTIONS = sorted({
    "Smolensk Oil Depot", "Ryazan Oil Refinery", "Syzran Oil Refinery",
    "Novokuibyshevsk Oil Refinery", "Ufa Oil Refinery Complex",
    "KINEF Oil Refinery", "Tuapse Oil Refinery",
    "Russkaya Compressor Station", "Kalinin Nuclear Power Plant",
    "Kursk Power Grid", "Novoshakhtinsk Oil Pipeline",
    "PSP-915 Pumping Station", "Sokhranovka Gas Infrastructure",
    "Cheboksary Oil Depot", "Oka-Center Oil Depot",
    "Davydovskaya Compressor Station", "Novopetrovskaya Compressor Station",
    "Aksinino Pumping Station", "Volgograd Lukoil Refinery",
    "Yefimovskaya Pipeline Station", "Petrovsk Gas Infrastructure",
    "Astrakhan Gas Processing Plant", "Ust-Luga Oil Terminal",
    "Gazprom LPG Terminal (Kazan)", "Aleksin Power Plant (ТЭЦ)",
    "Kristal Oil Depot (Engels)",
})

CANONICAL_TARGET_AREA: dict[str, str] = {
    "Smolensk Oil Depot":               "Smolensk",
    "Ryazan Oil Refinery":              "Ryazan",
    "Syzran Oil Refinery":              "Syzran",
    "Novokuibyshevsk Oil Refinery":     "Novokuibyshevsk",
    "Ufa Oil Refinery Complex":         "Ufa",
    "KINEF Oil Refinery":               "Kirishi",
    "Tuapse Oil Refinery":              "Tuapse",
    "Russkaya Compressor Station":      "Krasnodar",
    "Kalinin Nuclear Power Plant":      "Udomlya",
    "Kursk Power Grid":                 "Kursk",
    "Novoshakhtinsk Oil Pipeline":      "Novoshakhtinsk",
    "PSP-915 Pumping Station":          "Serpukhov",
    "Sokhranovka Gas Infrastructure":   "Chertkovo",
    "Cheboksary Oil Depot":             "Cheboksary",
    "Oka-Center Oil Depot":             "Serpukhov",
    "Davydovskaya Compressor Station":  "Liski",
    "Novopetrovskaya Compressor Station": "Voronezh",
    "Aksinino Pumping Station":         "Moscow Oblast",
    "Volgograd Lukoil Refinery":        "Volgograd",
    "Yefimovskaya Pipeline Station":    "Leningrad Oblast",
    "Petrovsk Gas Infrastructure":      "Petrovsk",
    "Astrakhan Gas Processing Plant":   "Astrakhan",
    "Ust-Luga Oil Terminal":            "Leningrad Oblast",
    "Gazprom LPG Terminal (Kazan)":     "Kazan",
    "Aleksin Power Plant (ТЭЦ)":        "Aleksin",
    "Kristal Oil Depot (Engels)":       "Engels",
}

# Hebrew labels for fields
_HE = {
    "area":             "אזור",
    "targets":          "מטרות",
    "target_type":      "סוג מטרה",
    "attack_type":      "סוג תקיפה",
    "drone_scale":      "היקף רחפן",
    "damage_level":     "רמת נזק",
    "report_type":      "סוג דיווח",
    "air_defense":      "הגנה אווירית",
    "fire":             "שריפה",
    "shutdown":         "השבתה",
    "hit_confirmed":    "פגיעה אושרה",
    "combined_strike":  "תקיפה משולבת",
    "explosions":       "פיצוצים",
    "attack_date":      "תאריך",
    "save":             "שמור  S",
    "skip":             "דלג  N",
    "quit":             "יציאה  Q",
    "reset":            "איפוס  R",
    "changes":          "שינויים",
    "translation":      "תרגום",
    "status":           "סטטוס",
    "log":              "יומן",
    "action":           "פעולה",
    "counter":          "מס׳",
}


def _card(parent=None) -> QFrame:
    f = QFrame(parent)
    f.setStyleSheet(
        f"QFrame {{ background:{P.CARD_BG}; border:3px solid {P.CARD_BORDER};"
        f"border-radius:14px; }}"
    )
    return f


def _btn(text: str, color: str, hover: str, parent=None) -> QPushButton:
    b = QPushButton(text, parent)
    b.setFixedHeight(44)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton{{background:{color};color:#fff;"
        f"border:3px solid {color};border-radius:10px;"
        f"font-size:14px;font-weight:800;padding:0 18px;}}"
        f"QPushButton:hover{{background:{hover};border-color:{hover};}}"
        f"QPushButton:pressed{{background:{P.BG};}}"
    )
    return b


# ─────────────────────────────────────────────────────────────────────────────
#  AttacksPage
# ─────────────────────────────────────────────────────────────────────────────

class AttacksPage(QWidget):
    """
    Full-screen attacks review page.
    Layout: LEFT = editing form | RIGHT = translation + changes + log
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._decision_future: asyncio.Future | None = None
        self._current_parsed = None
        self._action = None
        self._counter = 0
        self._setup_ui()
        self._setup_shortcuts()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # LEFT: editing form (fixed width)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFixedWidth(560)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_content = QWidget()
        left_content.setStyleSheet(f"background:{P.BG};")
        self._left_lay = QVBoxLayout(left_content)
        self._left_lay.setContentsMargins(4, 4, 4, 4)
        self._left_lay.setSpacing(8)
        left_scroll.setWidget(left_content)
        root.addWidget(left_scroll)

        # RIGHT: info panels
        right = QVBoxLayout()
        right.setSpacing(8)
        root.addLayout(right, 1)

        self._build_status_bar()
        self._build_form()
        self._build_action_buttons()
        self._build_right_panels(right)

        self._set_idle()

    def _build_status_bar(self):
        bar = QWidget()
        bar.setStyleSheet(
            f"background:{P.CARD_BG}; border-radius:10px; "
            f"border:2px solid {P.CARD_BORDER};"
        )
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(16, 8, 16, 8)
        bar_lay.setSpacing(16)

        self._action_pill = QLabel("—")
        self._action_pill.setFixedWidth(100)
        self._action_pill.setAlignment(Qt.AlignCenter)
        self._action_pill.setStyleSheet(
            f"background:{P.TXT3}; color:#fff; border-radius:8px; "
            f"font-size:12px; font-weight:800; padding:4px 10px; border:none;"
        )

        self._counter_lbl = QLabel(f"{_HE['counter']}: 0")
        self._counter_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-size:13px; font-weight:700; border:none;"
        )

        self._status_lbl = QLabel(_HE["status"] + ": —")
        self._status_lbl.setStyleSheet(
            f"color:{P.TXT}; font-size:13px; font-weight:700; border:none;"
        )
        self._status_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        bar_lay.addWidget(self._action_pill)
        bar_lay.addWidget(self._counter_lbl)
        bar_lay.addStretch()
        bar_lay.addWidget(self._status_lbl)

        self._left_lay.addWidget(bar)

    def _build_form(self):
        form_card = _card()
        form_lay = QVBoxLayout(form_card)
        form_lay.setContentsMargins(20, 16, 20, 16)
        form_lay.setSpacing(4)

        # ── IDENTITY ─────────────────────────────────────────────────────────
        form_lay.addWidget(SectionLabel("זיהוי"))

        # Date
        date_row = QHBoxLayout()
        date_lbl = QLabel(_HE["attack_date"])
        date_lbl.setStyleSheet(f"color:{P.TXT2}; font-size:12px; font-weight:700; border:none;")
        date_lbl.setFixedWidth(120)
        date_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDisplayFormat("dd  MMM  yyyy")
        self._date_edit.setFixedHeight(36)
        self._date_edit.setStyleSheet(
            f"QDateEdit{{background:{P.INPUT_BG};color:{P.TXT};"
            f"border:2px solid {P.INPUT_BORDER};border-radius:8px;"
            f"padding:4px 10px;font-size:13px;font-weight:700;}}"
            f"QDateEdit:focus{{border:3px solid {P.INPUT_FOCUS};}}"
            f"QDateEdit::drop-down{{border:none;}}"
        )
        date_row.addWidget(date_lbl)
        date_row.addWidget(self._date_edit)
        date_row.addStretch()
        form_lay.addLayout(date_row)

        # Area
        area_row = QHBoxLayout()
        area_lbl = QLabel(_HE["area"])
        area_lbl.setStyleSheet(f"color:{P.TXT2}; font-size:12px; font-weight:700; border:none;")
        area_lbl.setFixedWidth(120)
        area_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._area_combo = SmartCombo(list(AREA_OPTIONS))
        self._area_combo.value_changed.connect(self._on_area_changed)
        area_row.addWidget(area_lbl)
        area_row.addWidget(self._area_combo, 1)
        form_lay.addLayout(area_row)

        # Targets
        tgt_row = QHBoxLayout()
        tgt_lbl = QLabel(_HE["targets"])
        tgt_lbl.setStyleSheet(f"color:{P.TXT2}; font-size:12px; font-weight:700; border:none;")
        tgt_lbl.setFixedWidth(120)
        tgt_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._target_combo = SmartCombo(list(TARGET_OPTIONS))
        self._target_combo.value_changed.connect(self._on_target_changed)
        tgt_row.addWidget(tgt_lbl)
        tgt_row.addWidget(self._target_combo, 1)
        form_lay.addLayout(tgt_row)

        # Target type
        tt_row = QHBoxLayout()
        tt_lbl = QLabel(_HE["target_type"])
        tt_lbl.setStyleSheet(f"color:{P.TXT2}; font-size:12px; font-weight:700; border:none;")
        tt_lbl.setFixedWidth(120)
        tt_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._target_type_sel = ButtonSelector(
            ["refinery", "depot", "pipeline", "power", "compressor", "nuclear", "other"],
            labels={
                "refinery":   "מזקקה",
                "depot":      "מאגר",
                "pipeline":   "צינור",
                "power":      "חשמל",
                "compressor": "דחסן",
                "nuclear":    "גרעין",
                "other":      "אחר",
            },
        )
        tt_row.addWidget(tt_lbl)
        tt_row.addWidget(self._target_type_sel, 1)
        form_lay.addLayout(tt_row)

        # ── ATTACK ───────────────────────────────────────────────────────────
        form_lay.addWidget(SectionLabel("תקיפה"))

        atk_row = QHBoxLayout()
        atk_lbl = QLabel(_HE["attack_type"])
        atk_lbl.setStyleSheet(f"color:{P.TXT2}; font-size:12px; font-weight:700; border:none;")
        atk_lbl.setFixedWidth(120)
        atk_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._atk_type_sel = ButtonSelector(
            ["drone", "missile", "combined", "unknown"],
            labels={"drone": "רחפן", "missile": "טיל", "combined": "משולב", "unknown": "לא ידוע"},
            accents=P.ATK_COLORS,
        )
        atk_row.addWidget(atk_lbl)
        atk_row.addWidget(self._atk_type_sel, 1)
        form_lay.addLayout(atk_row)

        scale_row = QHBoxLayout()
        scale_lbl = QLabel(_HE["drone_scale"])
        scale_lbl.setStyleSheet(f"color:{P.TXT2}; font-size:12px; font-weight:700; border:none;")
        scale_lbl.setFixedWidth(120)
        scale_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._drone_scale_sel = ButtonSelector(
            ["few", "swarm", "massive", ""],
            labels={"few": "מעטים", "swarm": "להקה", "massive": "המוני", "": "—"},
            accents=P.SCALE_COLORS,
        )
        scale_row.addWidget(scale_lbl)
        scale_row.addWidget(self._drone_scale_sel, 1)
        form_lay.addLayout(scale_row)

        # ── SIGNALS ──────────────────────────────────────────────────────────
        form_lay.addWidget(SectionLabel("אותות"))

        bool_grid = QGridLayout()
        bool_grid.setHorizontalSpacing(12)
        bool_grid.setVerticalSpacing(8)

        def _bool_cell(label_he: str, widget: QWidget, row: int, col: int):
            lbl = QLabel(label_he)
            lbl.setStyleSheet(f"color:{P.TXT2}; font-size:12px; font-weight:700; border:none;")
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            bool_grid.addWidget(lbl,   row, col * 2)
            bool_grid.addWidget(widget, row, col * 2 + 1)

        self._air_def_toggle   = BoolToggle(nullable=True)
        self._fire_toggle      = BoolToggle(nullable=True)
        self._shutdown_toggle  = BoolToggle(nullable=True)
        self._hit_toggle       = BoolToggle(nullable=True)
        self._combined_toggle  = BoolToggle(nullable=True)

        _bool_cell(_HE["air_defense"],   self._air_def_toggle,  0, 0)
        _bool_cell(_HE["fire"],          self._fire_toggle,     0, 1)
        _bool_cell(_HE["shutdown"],      self._shutdown_toggle, 1, 0)
        _bool_cell(_HE["hit_confirmed"], self._hit_toggle,      1, 1)
        _bool_cell(_HE["combined_strike"], self._combined_toggle, 2, 0)

        form_lay.addLayout(bool_grid)

        # Explosions
        expl_row = QHBoxLayout()
        expl_lbl = QLabel(_HE["explosions"])
        expl_lbl.setStyleSheet(f"color:{P.TXT2}; font-size:12px; font-weight:700; border:none;")
        expl_lbl.setFixedWidth(120)
        expl_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._explosions_spin = QSpinBox()
        self._explosions_spin.setRange(0, 999)
        self._explosions_spin.setFixedHeight(36)
        self._explosions_spin.setFixedWidth(90)
        self._explosions_spin.setStyleSheet(
            f"QSpinBox{{background:{P.INPUT_BG};color:{P.TXT};"
            f"border:2px solid {P.INPUT_BORDER};border-radius:8px;"
            f"padding:4px 8px;font-size:13px;font-weight:700;}}"
            f"QSpinBox:focus{{border:3px solid {P.INPUT_FOCUS};}}"
        )
        expl_row.addWidget(expl_lbl)
        expl_row.addWidget(self._explosions_spin)
        expl_row.addStretch()
        form_lay.addLayout(expl_row)

        # ── ASSESSMENT ───────────────────────────────────────────────────────
        form_lay.addWidget(SectionLabel("הערכה"))

        dmg_row = QHBoxLayout()
        dmg_lbl = QLabel(_HE["damage_level"])
        dmg_lbl.setStyleSheet(f"color:{P.TXT2}; font-size:12px; font-weight:700; border:none;")
        dmg_lbl.setFixedWidth(120)
        dmg_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._damage_sel = ButtonSelector(
            ["low", "medium", "high"],
            labels={"low": "נמוך", "medium": "בינוני", "high": "גבוה"},
            accents=P.DMG_COLORS,
        )
        dmg_row.addWidget(dmg_lbl)
        dmg_row.addWidget(self._damage_sel, 1)
        form_lay.addLayout(dmg_row)

        rpt_row = QHBoxLayout()
        rpt_lbl = QLabel(_HE["report_type"])
        rpt_lbl.setStyleSheet(f"color:{P.TXT2}; font-size:12px; font-weight:700; border:none;")
        rpt_lbl.setFixedWidth(120)
        rpt_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._report_type_sel = ButtonSelector(
            ["direct_report", "official_confirmation", "hearsay"],
            labels={
                "direct_report":         "ישיר",
                "official_confirmation": "רשמי",
                "hearsay":               "שמועה",
            },
        )
        rpt_row.addWidget(rpt_lbl)
        rpt_row.addWidget(self._report_type_sel, 1)
        form_lay.addLayout(rpt_row)

        form_lay.addStretch()
        self._left_lay.addWidget(form_card, 1)

    def _build_action_buttons(self):
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._save_btn = _btn(f"✓  {_HE['save']}", P.GREEN, P.GREEN_D)
        self._skip_btn = _btn(f"→  {_HE['skip']}", P.TXT3, "#334155")
        self._quit_btn = _btn(f"✕  {_HE['quit']}", P.RED, P.RED_D)
        self._reset_btn = _btn(f"↺  {_HE['reset']}", P.AMBER, "#b45309")

        self._save_btn.clicked.connect(self._on_save)
        self._skip_btn.clicked.connect(self._on_skip)
        self._quit_btn.clicked.connect(self._on_quit)
        self._reset_btn.clicked.connect(self._on_reset)

        btn_row.addWidget(self._save_btn, 3)
        btn_row.addWidget(self._skip_btn, 2)
        btn_row.addWidget(self._reset_btn, 2)
        btn_row.addWidget(self._quit_btn, 2)

        self._left_lay.addLayout(btn_row)

    def _build_right_panels(self, parent_lay: QVBoxLayout):
        # Translation panel
        trans_card = _card()
        trans_v = QVBoxLayout(trans_card)
        trans_v.setContentsMargins(16, 12, 16, 12)
        trans_v.setSpacing(6)

        trans_hdr = QLabel(_HE["translation"])
        trans_hdr.setStyleSheet(
            f"color:{P.INDIGO}; font-size:12px; font-weight:800; "
            f"letter-spacing:2px; border:none;"
        )
        self._trans_edit = QPlainTextEdit()
        self._trans_edit.setReadOnly(True)
        self._trans_edit.setMinimumHeight(120)
        self._trans_edit.setMaximumHeight(200)
        self._trans_edit.setLayoutDirection(Qt.RightToLeft)
        self._trans_edit.setStyleSheet(
            f"background:{P.BG}; color:{P.TXT}; border:none; "
            f"font-size:13px; line-height:1.6;"
        )

        trans_v.addWidget(trans_hdr)
        trans_v.addWidget(self._trans_edit)
        parent_lay.addWidget(trans_card)

        # Changes panel
        changes_card = _card()
        changes_v = QVBoxLayout(changes_card)
        changes_v.setContentsMargins(16, 12, 16, 12)
        changes_v.setSpacing(6)

        changes_hdr = QLabel(_HE["changes"])
        changes_hdr.setStyleSheet(
            f"color:{P.AMBER}; font-size:12px; font-weight:800; "
            f"letter-spacing:2px; border:none;"
        )
        self._changes_edit = QPlainTextEdit()
        self._changes_edit.setReadOnly(True)
        self._changes_edit.setFixedHeight(100)
        self._changes_edit.setStyleSheet(
            f"background:{P.BG}; color:{P.AMBER}; border:none; "
            f"font-size:12px;"
        )

        changes_v.addWidget(changes_hdr)
        changes_v.addWidget(self._changes_edit)
        parent_lay.addWidget(changes_card)

        # Log panel
        log_card = _card()
        log_v = QVBoxLayout(log_card)
        log_v.setContentsMargins(16, 12, 16, 12)
        log_v.setSpacing(6)

        log_hdr = QLabel(_HE["log"])
        log_hdr.setStyleSheet(
            f"color:{P.CYAN}; font-size:12px; font-weight:800; "
            f"letter-spacing:2px; border:none;"
        )
        self._log_edit = QPlainTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setStyleSheet(
            f"background:{P.BG}; color:{P.TXT2}; border:none; "
            f"font-size:11px; font-family:Consolas,monospace;"
        )

        log_v.addWidget(log_hdr)
        log_v.addWidget(self._log_edit, 1)
        parent_lay.addWidget(log_card, 1)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("S"), self).activated.connect(self._on_save)
        QShortcut(QKeySequence("N"), self).activated.connect(self._on_skip)
        QShortcut(QKeySequence("Q"), self).activated.connect(self._on_quit)
        QShortcut(QKeySequence("R"), self).activated.connect(self._on_reset)

    # ── Auto-fill ─────────────────────────────────────────────────────────────

    def _on_target_changed(self, text: str):
        mapped = CANONICAL_TARGET_AREA.get(text)
        if mapped:
            current_area = self._area_combo.python_value()
            if not current_area or current_area in ("", "Unknown Area"):
                self._area_combo.set_python_value(mapped)

    def _on_area_changed(self, text: str):
        pass  # reserved for future use

    # ── Public API ────────────────────────────────────────────────────────────

    def set_status(self, msg: str):
        self._status_lbl.setText(f"{_HE['status']}: {msg}")

    def append_log(self, msg: str):
        self._log_edit.appendPlainText(msg)

    def load_candidate(
        self,
        action: str,
        parsed,
        original_text: str,
        translated_text: str,
        diffs,
        counter: int,
    ):
        self._current_parsed = deepcopy(parsed)
        self._action = action
        self._counter = counter

        # Status bar
        color = P.GREEN if action == "INSERT" else P.AMBER
        self._action_pill.setStyleSheet(
            f"background:{color}; color:#fff; border-radius:8px; "
            f"font-size:12px; font-weight:800; padding:4px 10px; border:none;"
        )
        self._action_pill.setText(action)
        self._counter_lbl.setText(f"{_HE['counter']}: {counter}")

        # Populate form
        if parsed.attack_date:
            d = parsed.attack_date
            self._date_edit.setDate(QDate(d.year, d.month, d.day))

        self._area_combo.set_python_value(parsed.area or "")
        self._target_combo.set_python_value(
            (parsed.targets_names or "").split(" | ")[0]
        )
        self._target_type_sel.set_python_value(parsed.target_type or "other")
        self._atk_type_sel.set_python_value(parsed.attack_type or "unknown")
        self._drone_scale_sel.set_python_value(parsed.drone_scale or "")
        self._air_def_toggle.set_python_value(parsed.air_defense_active)
        self._fire_toggle.set_python_value(parsed.fire)
        self._shutdown_toggle.set_python_value(parsed.shutdown)
        self._hit_toggle.set_python_value(parsed.hit_confirmed)
        self._combined_toggle.set_python_value(parsed.combined_strike)
        self._explosions_spin.setValue(parsed.explosions_reported or 0)
        self._damage_sel.set_python_value(parsed.damage_level or "low")
        self._report_type_sel.set_python_value(parsed.report_type or "direct_report")

        # Translation
        self._trans_edit.setPlainText(translated_text or "")

        # Changes
        if diffs:
            lines = [f"{f}: {o!r} → {n!r}" for f, o, n in diffs]
            self._changes_edit.setPlainText("\n".join(lines))
        else:
            self._changes_edit.setPlainText("—")

        self._enable_controls(True)

    def _set_idle(self):
        self._enable_controls(False)
        self._action_pill.setText("—")
        self._action_pill.setStyleSheet(
            f"background:{P.TXT3}; color:#fff; border-radius:8px; "
            f"font-size:12px; font-weight:800; padding:4px 10px; border:none;"
        )

    def _enable_controls(self, enabled: bool):
        for w in (self._save_btn, self._skip_btn, self._reset_btn):
            w.setEnabled(enabled)

    def _collect_edited_parsed(self):
        """Return a modified copy of _current_parsed with current widget values."""
        from datetime import date as _date
        p = deepcopy(self._current_parsed)
        qd = self._date_edit.date()
        p.attack_date  = _date(qd.year(), qd.month(), qd.day())
        p.area         = self._area_combo.python_value()
        p.targets_names = self._target_combo.python_value()
        p.target_type  = self._target_type_sel.python_value()
        p.attack_type  = self._atk_type_sel.python_value()
        p.drone_scale  = self._drone_scale_sel.python_value() or None
        p.air_defense_active = self._air_def_toggle.python_value()
        p.fire         = self._fire_toggle.python_value()
        p.shutdown     = self._shutdown_toggle.python_value()
        p.hit_confirmed = self._hit_toggle.python_value()
        p.combined_strike = self._combined_toggle.python_value()
        p.explosions_reported = self._explosions_spin.value()
        p.damage_level = self._damage_sel.python_value()
        p.report_type  = self._report_type_sel.python_value()
        return p

    # ── Decision callbacks ────────────────────────────────────────────────────

    def _resolve(self, code: str):
        if self._decision_future and not self._decision_future.done():
            edited = self._collect_edited_parsed() if code == "y" else self._current_parsed
            self._decision_future.set_result((code, edited))
        self._decision_future = None

    def _on_save(self):
        area = self._area_combo.python_value()
        if (not area or area in ("", "Unknown Area")) and self._action == "INSERT":
            reply = QMessageBox.question(
                self,
                "אזהרה: אזור לא ידוע",
                "האזור מסומן כ'לא ידוע'. לשמור בכל זאת?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self._resolve("y")

    def _on_skip(self):
        self._resolve("n")

    def _on_quit(self):
        self._resolve("q")

    def _on_reset(self):
        if self._current_parsed:
            self.load_candidate(
                action=self._action,
                parsed=self._current_parsed,
                original_text="",
                translated_text=self._trans_edit.toPlainText(),
                diffs=None,
                counter=self._counter,
            )

    async def async_wait_for_decision(self):
        loop = asyncio.get_event_loop()
        self._decision_future = loop.create_future()
        return await self._decision_future
