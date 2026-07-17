"""
Attacks review page — start panel + review interface (English UI).
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, timedelta

from PySide6.QtCore import QDate, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut, QTextOption
from PySide6.QtWidgets import (
    QCalendarWidget,
    QDateEdit,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import palette as P
from . import styles as S
from .widgets import (
    BoolToggle, ButtonSelector, CircularSpinner, LoadingOverlay,
    MultiButtonSelector, SmartCombo,
)

# Display labels for the internal action codes ("INSERT"/"UPDATE") used as DB
# business logic elsewhere — only the on-screen text changes here.
_ACTION_LABELS = {"INSERT": "הוספה", "UPDATE": "עדכון"}

# ─────────────────────────────────────────────────────────────────────────────
#  Option lists
# ─────────────────────────────────────────────────────────────────────────────

AREA_OPTIONS = sorted({
    # ── Specific cities / districts (auto-detected) ───────────────────────────
    # Central Russia
    "Moscow Oblast", "Tula", "Serpukhov", "Aleksin", "Kaluga", "Uzlovaya",
    "Ryazan", "Tambov", "Lipetsk", "Voronezh", "Liski", "Oryol", "Bryansk",
    "Karachev", "Smolensk", "Yartsevo", "Vladimir",
    # Volga / Ural
    "Samara", "Syzran", "Novokuibyshevsk", "Ufa", "Bashkortostan",
    "Saratov", "Engels", "Petrovsk", "Penza", "Ulyanovsk",
    "Nizhny Novgorod", "Chuvashia", "Cheboksary", "Kazan", "Tatarstan",
    "Almetyevsk", "Naberezhnye Chelny", "Nizhnekamsk",
    "Orenburg", "Perm", "Izhevsk", "Volgograd",
    # Sverdlovsk / Tyumen
    "Ekaterinburg", "Sverdlovsk Oblast", "Tyumen Oblast", "Omsk",
    # Khanty-Mansi
    "Khanty-Mansi Autonomous Okrug", "Surgut", "Nizhnevartovsk",
    # South Russia
    "Krasnodar", "Tuapse", "Novorossiysk", "Gay-Kodzor", "Rostov",
    "Novoshakhtinsk", "Chertkovo", "Astrakhan", "Dagestan",
    # North / Northwest
    "Leningrad Oblast", "Kirishi", "Pskov", "Murmansk", "Vologda", "Komi Republic",
    # Central Black Earth
    "Kursk", "Belgorod",
    # Tver / North Central
    "Tver", "Udomlya", "Yaroslavl",
    # Far East / Siberia
    "Sakhalin Oblast", "Primorsky Krai", "Vladivostok",
    "Zabaykalsky Krai", "Chita", "Kemerovo",

    # ── Oblast / Krai full names ───────────────────────────────────────────────
    "Tula Oblast", "Ryazan Oblast", "Voronezh Oblast", "Rostov Oblast",
    "Bryansk Oblast", "Vladimir Oblast", "Volgograd Oblast", "Samara Oblast",
    "Nizhny Novgorod Oblast", "Krasnodar Krai", "Perm Krai",
    "Republic of Bashkortostan", "Oryol Oblast",

    # ── Occupied Territories ──────────────────────────────────────────────────
    "Crimea", "Occupied Crimea",
    "Luhansk Oblast", "Zaporizhzhia Oblast",
    "Occupied Kherson", "Occupied Donetsk",

    # ── Fallback / macro regions ──────────────────────────────────────────────
    "Central Russia", "Western Russia", "Southern Russia",
    "Northern Russia", "Eastern Russia", "Far East Russia",
    "Siberia", "Ural", "European Russia",
    "Central Federal District", "Black Sea Region", "Caspian Sea",
    "Russia", "Occupied Territories",

    # ── Unknown ───────────────────────────────────────────────────────────────
    "Unknown Area",
})



# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _card(parent=None) -> QWidget:
    """Floating card — real bg/border/shadow so grouped content (message,
    changes, log, form) reads as raised off the page, not flat against it."""
    w = QFrame(parent)
    w.setStyleSheet(S.card_qss("QFrame", radius=P.RADIUS_LG))
    S.apply_card_shadow(w, blur=30, alpha=95, y_offset=10)
    return w


def _hline() -> QWidget:
    """Thin hairline divider."""
    w = QWidget()
    w.setFixedHeight(1)
    w.setStyleSheet(S.divider_qss())
    return w


def _section_lbl(text: str) -> QLabel:
    lb = QLabel(text.upper())
    lb.setStyleSheet(
        f"color:{P.TXT3}; font-family:{P.FONT_STACK}; font-size:10px; font-weight:700; "
        f"letter-spacing:1.5px; border:none; padding:6px 0 2px 0;"
    )
    return lb


def _field_lbl(text: str, width: int = 90) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet(
        f"color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:12px; font-weight:600; border:none;"
    )
    lb.setFixedWidth(width)
    lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return lb


def _btn(text: str, color: str, hover: str, parent=None) -> QPushButton:
    b = QPushButton(text, parent)
    b.setFixedHeight(32)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton{{background:transparent;color:{color};"
        f"border:1px solid {color};border-radius:{P.RADIUS_MD}px;"
        f"font-family:{P.FONT_STACK};font-size:15px;font-weight:700;padding:0 10px;}}"
        f"QPushButton:hover{{background:{color};color:#0a0b0d;}}"
        f"QPushButton:pressed{{background:{hover};}}"
    )
    S.bind_floating_button(b, idle=(10, 55, 2), hover=(20, 100, 6), pressed=(4, 30, 1),
                            flash_color=color)
    return b


def _date_edit_style() -> str:
    return (
        f"QDateEdit{{background:{P.INPUT_BG};color:{P.TXT};"
        f"border:1px solid {P.INPUT_BORDER};border-radius:{P.RADIUS_MD}px;"
        f"padding:4px 10px;font-family:{P.FONT_STACK};font-size:13px;font-weight:600;}}"
        f"QDateEdit:hover{{border:1px solid {P.BORDER_STRONG};}}"
        f"QDateEdit:focus{{border:1px solid {P.INPUT_FOCUS};}}"
        f"QDateEdit::drop-down{{border:none;width:22px;}}"
        f"QCalendarWidget{{background:{P.CARD_BG};color:{P.TXT};"
        f"font-size:13px;}}"
        f"QCalendarWidget QAbstractItemView:enabled{{"
        f"background:{P.CARD_BG};color:{P.TXT};"
        f"selection-background-color:{P.INDIGO};selection-color:#fff;"
        f"font-size:13px;}}"
        f"QCalendarWidget QToolButton{{color:{P.TXT};font-weight:700;"
        f"font-size:13px;background:{P.INPUT_BG};}}"
        f"QCalendarWidget QWidget#qt_calendar_navigationbar{{"
        f"background:{P.INPUT_BG};}}"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Recent attacks picker dialog
# ─────────────────────────────────────────────────────────────────────────────

class _RecentAttacksPicker(QDialog):
    """Small dialog to pick a recent attack for manual update."""

    def __init__(self, attacks: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("בחירת תקיפה לעדכון")
        self.setMinimumWidth(580)
        self.setStyleSheet(
            f"QDialog {{ background:{P.CARD_BG}; border:1px solid {P.BORDER_STRONG}; "
            f"border-radius:{P.RADIUS_LG}px; }}"
            f"QLabel  {{ color:{P.TXT}; font-family:{P.FONT_STACK}; border:none; background:transparent; }}"
        )
        self._selected = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)

        hdr = QLabel("5 התקיפות האחרונות — בחרו אחת לעדכון:")
        hdr.setStyleSheet(
            f"color:{P.TXT}; font-family:{P.FONT_STACK}; font-size:14px; font-weight:700; border:none;"
        )
        lay.addWidget(hdr)

        if not attacks:
            lbl = QLabel("לא נמצאו תקיפות במסד הנתונים.")
            lbl.setStyleSheet(f"color:{P.TXT2}; border:none;")
            lay.addWidget(lbl)
        else:
            for atk in attacks:
                lay.addWidget(self._make_attack_btn(atk))

        cancel = QPushButton("ביטול")
        cancel.setFixedHeight(38)
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setStyleSheet(
            f"QPushButton{{background:transparent;color:{P.TXT2};"
            f"border:1px solid {P.BORDER_STRONG};border-radius:{P.RADIUS_MD}px;"
            f"font-family:{P.FONT_STACK};font-size:13px;font-weight:600;}}"
            f"QPushButton:hover{{border-color:{P.TXT2};color:{P.TXT};}}"
        )
        cancel.clicked.connect(self.reject)
        lay.addWidget(cancel)

    def _make_attack_btn(self, atk: dict) -> QPushButton:
        atk_id  = atk.get("attack_id", "?")
        area    = atk.get("area", "?")
        d       = atk.get("attack_date", "?")
        targets = (atk.get("target_type") or "?")[:45]
        damage  = atk.get("damage_level") or "—"
        label   = f"#{atk_id}   {d}   {area}   {targets}   נזק: {damage}"

        btn = QPushButton(label)
        btn.setFixedHeight(46)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton{{background:{P.INPUT_BG};color:{P.TXT};"
            f"border:1px solid {P.CARD_BORDER};border-radius:{P.RADIUS_MD}px;"
            f"font-family:{P.FONT_STACK};font-size:12px;font-weight:600;padding:0 14px;text-align:left;}}"
            f"QPushButton:hover{{background:transparent;border:1px solid {P.VIOLET};color:{P.VIOLET};}}"
        )
        btn.clicked.connect(lambda checked=False, a=atk: self._pick(a))
        return btn

    def _pick(self, atk: dict):
        self._selected = atk
        self.accept()

    @property
    def selected(self):
        return self._selected


# ─────────────────────────────────────────────────────────────────────────────
#  Date range picker dialog
# ─────────────────────────────────────────────────────────────────────────────

class _DateRangePickerDialog(QDialog):
    """Minimal centered modal for picking a start/end date pair."""

    def __init__(self, start: date, end: date, parent=None):
        super().__init__(parent)
        self.setWindowTitle("טווח תאריכים")
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self._start = start
        self._end   = end
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(
            f"QDialog  {{ background:{P.CARD_BG}; border:1px solid {P.BORDER_STRONG}; "
            f"border-radius:{P.RADIUS_LG}px; }}"
            f"QLabel   {{ color:{P.TXT}; font-family:{P.FONT_STACK}; border:none; background:transparent; }}"
        )
        S.apply_card_shadow(self, blur=40, alpha=110, y_offset=12)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(18)

        title = QLabel("טווח תאריכים")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color:{P.TXT}; font-family:{P.FONT_STACK}; font-size:15px; font-weight:700; border:none;"
        )
        lay.addWidget(title)

        cals_row = QHBoxLayout()
        cals_row.setSpacing(24)

        # Hebrew reads right-to-left, so the "from" column is added last —
        # in this left-to-right layout that places it on the right, where a
        # Hebrew reader looks first.
        for lbl_text, attr, init_date in [
            ("עד",   "_end_cal",   self._end),
            ("מ-", "_start_cal", self._start),
        ]:
            col = QVBoxLayout()
            col.setSpacing(6)

            lbl = QLabel(lbl_text)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                f"color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:12px; font-weight:600; border:none;"
            )

            cal = QCalendarWidget()
            cal.setGridVisible(False)
            cal.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
            cal.setSelectedDate(QDate(init_date.year, init_date.month, init_date.day))
            setattr(self, attr, cal)

            col.addWidget(lbl)
            col.addWidget(cal)
            cals_row.addLayout(col)

        lay.addLayout(cals_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        cancel_btn = QPushButton("ביטול")
        cancel_btn.setFixedHeight(38)
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{P.TXT2};"
            f"border:1px solid {P.BORDER_STRONG};border-radius:{P.RADIUS_MD}px;"
            f"font-family:{P.FONT_STACK};font-size:13px;font-weight:600;}}"
            f"QPushButton:hover{{border-color:{P.TXT2};color:{P.TXT};}}"
        )
        cancel_btn.clicked.connect(self.reject)

        apply_btn = QPushButton("אישור")
        apply_btn.setFixedHeight(38)
        apply_btn.setCursor(Qt.PointingHandCursor)
        apply_btn.setStyleSheet(
            f"QPushButton{{background:{P.INDIGO};color:#fff;"
            f"border:none;border-radius:{P.RADIUS_MD}px;"
            f"font-family:{P.FONT_STACK};font-size:13px;font-weight:700;}}"
            f"QPushButton:hover{{background:{P.INDIGO_L};}}"
        )
        apply_btn.clicked.connect(self._apply)

        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(apply_btn)
        lay.addLayout(btn_row)

    def _apply(self):
        qd_s = self._start_cal.selectedDate()
        qd_e = self._end_cal.selectedDate()
        self._start = date(qd_s.year(), qd_s.month(), qd_s.day())
        self._end   = date(qd_e.year(), qd_e.month(), qd_e.day())
        self.accept()

    @property
    def result_dates(self) -> tuple[date, date]:
        return self._start, self._end


# ─────────────────────────────────────────────────────────────────────────────
#  AttacksPage
# ─────────────────────────────────────────────────────────────────────────────

class AttacksPage(QWidget):
    """
    Two-state page:
      State 0 (start panel)  — date range picker + mode selector
      State 1 (review panel) — attack review interface
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._decision_future: asyncio.Future | None = None
        self._start_future: asyncio.Future | None = None
        self._current_parsed = None
        self._action = None
        self._counter = 0
        self._manual_update_target: dict | None = None
        self._fetch_recent_cb = None
        self._setup_ui()
        self._setup_shortcuts()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._main_stack = QStackedWidget()
        root.addWidget(self._main_stack)

        self._main_stack.addWidget(self._build_start_panel())
        self._main_stack.addWidget(self._build_review_panel())
        self._main_stack.setCurrentIndex(0)

        # Covers the whole page the instant Start is pressed, until the first
        # candidate is actually on screen — see set_status()/load_candidate().
        self._loading_overlay = LoadingOverlay(self)

    # ── Start panel ───────────────────────────────────────────────────────────

    def _build_start_panel(self) -> QWidget:
        outer = QWidget()
        outer.setStyleSheet(S.window_bg_qss())
        outer_lay = QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setAlignment(Qt.AlignCenter)

        card = QFrame()
        card.setObjectName("StartCard")
        card.setFixedWidth(480)
        card.setStyleSheet(S.card_qss("QFrame#StartCard", radius=P.RADIUS_XL))
        S.apply_card_shadow(card, blur=40, alpha=90, y_offset=12)

        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(40, 36, 40, 36)
        card_lay.setSpacing(16)

        icon_lbl = QLabel("⚡")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("font-size:44px; border:none; background:transparent;")
        card_lay.addWidget(icon_lbl)

        title_lbl = QLabel("בדיקת תקיפות")
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet(
            f"color:{P.TXT}; font-family:{P.FONT_STACK}; font-size:21px; font-weight:700; "
            f"border:none; background:transparent;"
        )
        card_lay.addWidget(title_lbl)

        sub_lbl = QLabel("בחרו טווח תאריכים")
        sub_lbl.setAlignment(Qt.AlignCenter)
        sub_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:13px; border:none; "
            f"padding-bottom:4px; background:transparent;"
        )
        card_lay.addWidget(sub_lbl)

        # Date range frame
        date_frame = QFrame()
        date_frame.setStyleSheet(
            f"QFrame {{ background:transparent; border:none; }}"
        )
        date_g = QGridLayout(date_frame)
        date_g.setContentsMargins(18, 14, 18, 14)
        date_g.setHorizontalSpacing(14)
        date_g.setVerticalSpacing(10)

        yesterday = date.today() - timedelta(days=1)
        for row_i, (lbl_txt, attr_name) in enumerate([
            ("מ-", "_sp_start_edit"),
            ("עד",   "_sp_end_edit"),
        ]):
            lbl = QLabel(lbl_txt)
            lbl.setStyleSheet(
                f"color:{P.TXT2}; font-size:13px; font-weight:700; border:none;"
            )
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lbl.setFixedWidth(36)

            edit = QDateEdit()
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("dd  MMM  yyyy")
            edit.setDate(QDate(yesterday.year, yesterday.month, yesterday.day))
            edit.setFixedHeight(44)
            edit.setStyleSheet(_date_edit_style())
            setattr(self, attr_name, edit)

            date_g.addWidget(lbl,  row_i, 1)
            date_g.addWidget(edit, row_i, 0)

        card_lay.addWidget(date_frame)

        # Start button
        start_btn = QPushButton("▶  התחלת בדיקה")
        start_btn.setFixedHeight(50)
        start_btn.setCursor(Qt.PointingHandCursor)
        start_btn.setStyleSheet(
            f"QPushButton{{background:{P.AMBER};color:#0a0b0d;"
            f"border:none;border-radius:{P.RADIUS_LG}px;"
            f"font-family:{P.FONT_STACK};font-size:16px;font-weight:700;}}"
            f"QPushButton:hover{{background:{P.AMBER}dd;}}"
            f"QPushButton:pressed{{background:{P.AMBER}aa;}}"
        )
        S.apply_button_shadow(start_btn, blur=26, alpha=110, y_offset=8)
        start_btn.clicked.connect(self._on_start)
        card_lay.addWidget(start_btn)

        self._sp_status_lbl = QLabel("")
        self._sp_status_lbl.setAlignment(Qt.AlignCenter)
        self._sp_status_lbl.setStyleSheet(
            f"color:{P.RED}; font-size:12px; border:none;"
        )
        card_lay.addWidget(self._sp_status_lbl)

        outer_lay.addWidget(card)
        return outer

    def _on_start(self):
        qd_s = self._sp_start_edit.date()
        qd_e = self._sp_end_edit.date()
        s = date(qd_s.year(), qd_s.month(), qd_s.day())
        e = date(qd_e.year(), qd_e.month(), qd_e.day())
        if s > e:
            self._sp_status_lbl.setText("תאריך ההתחלה חייב להיות לפני תאריך הסיום.")
            return

        self._sp_status_lbl.setText("")
        self._main_stack.setCurrentIndex(1)
        self._loading_overlay.start("מתחברים לטלגרם…")

        if self._start_future and not self._start_future.done():
            self._start_future.set_result(("manual", s, e))
        self._start_future = None

    # ── Review panel ──────────────────────────────────────────────────────────

    def _build_review_panel(self) -> QWidget:
        outer = QWidget()
        outer.setStyleSheet(S.window_bg_qss())

        root = QHBoxLayout(outer)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet(f"""
            QSplitter::handle {{ background: {P.DIVIDER}; width: 1px; }}
            QSplitter::handle:hover {{ background: {P.INDIGO}; }}
        """)
        root.addWidget(splitter)

        # ── LEFT: message panels ──────────────────────────────────────────────
        left_widget = QWidget()
        left_widget.setMinimumWidth(300)
        left_widget.setStyleSheet(f"background:transparent;")
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(14, 10, 10, 10)
        left_lay.setSpacing(16)

        self._build_right_panels(left_lay)
        splitter.addWidget(left_widget)

        # ── RIGHT: editing form ───────────────────────────────────────────────
        right_widget = QWidget()
        right_widget.setStyleSheet(f"background:transparent;")
        right_widget.setMinimumWidth(285)
        right_widget.setMaximumWidth(435)
        self._left_lay = QVBoxLayout(right_widget)
        self._left_lay.setContentsMargins(8, 6, 10, 6)
        self._left_lay.setSpacing(4)

        self._build_status_bar()
        self._build_form()
        self._build_action_buttons()

        splitter.addWidget(right_widget)

        splitter.setSizes([700, 345])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self._set_idle()
        return outer

    def _build_status_bar(self):
        bar = QWidget()
        bar.setFixedHeight(33)
        bar.setStyleSheet("background:transparent;")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(12, 0, 12, 0)
        bar_lay.setSpacing(12)

        self._action_pill = QLabel("—")
        self._action_pill.setFixedWidth(96)
        self._action_pill.setAlignment(Qt.AlignCenter)
        self._action_pill.setStyleSheet(
            f"background:transparent; color:{P.TXT3}; border:none; "
            f"font-family:{P.FONT_STACK}; font-size:11px; font-weight:700; padding:3px 8px;"
        )

        self._counter_lbl = QLabel("#0")
        self._counter_lbl.setStyleSheet(
            f"color:{P.TXT2}; font-size:13px; font-weight:700; border:none;"
        )

        self._cal_btn = QPushButton("📅")
        self._cal_btn.setFixedSize(28, 28)
        self._cal_btn.setCursor(Qt.PointingHandCursor)
        self._cal_btn.setToolTip("שינוי טווח תאריכים")
        self._cal_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{P.TXT2};"
            f"border:none;border-radius:{P.RADIUS_SM}px;font-size:16px;padding:0;}}"
            f"QPushButton:hover{{color:{P.INDIGO};background:{P.INDIGO_BG};}}"
        )
        self._cal_btn.clicked.connect(self._on_open_date_picker)

        self._switch_update_btn = QPushButton("↕ מעבר לעדכון")
        self._switch_update_btn.setFixedHeight(28)
        self._switch_update_btn.setCursor(Qt.PointingHandCursor)
        self._switch_update_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{P.VIOLET};"
            f"border:1px solid {P.VIOLET};border-radius:{P.RADIUS_SM}px;"
            f"font-family:{P.FONT_STACK};font-size:11px;font-weight:600;padding:0 10px;}}"
            f"QPushButton:hover{{background:{P.VIOLET};color:#0a0b0d;}}"
            f"QPushButton:disabled{{color:{P.TXT3};border-color:{P.TXT3};}}"
        )
        self._switch_update_btn.clicked.connect(self._on_switch_to_update)
        self._switch_update_btn.setVisible(False)

        bar_lay.addWidget(self._action_pill)
        bar_lay.addWidget(self._counter_lbl)
        bar_lay.addWidget(self._switch_update_btn)
        bar_lay.addStretch()
        bar_lay.addWidget(self._cal_btn)

        self._left_lay.addWidget(bar)

    def _build_form(self):
        form_card = _card()
        form_lay = QVBoxLayout(form_card)
        form_lay.setContentsMargins(8, 4, 8, 4)
        form_lay.setSpacing(3)

        # ── IDENTITY — label above, left-aligned ─────────────────────────────
        form_lay.addWidget(_section_lbl("פרטים"))

        def _vert_field(label_text: str, widget: QWidget) -> QVBoxLayout:
            v = QVBoxLayout()
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(
                f"color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:11px; font-weight:600; border:none;"
            )
            v.addWidget(lbl)
            v.addWidget(widget)
            return v

        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDisplayFormat("dd  MMM  yyyy")
        self._date_edit.setFixedHeight(21)
        self._date_edit.setStyleSheet(_date_edit_style())

        self._area_combo = SmartCombo(list(AREA_OPTIONS), height=21)
        self._area_combo.value_changed.connect(self._on_area_changed)

        for widget, lbl_text in [
            (self._date_edit,  "תאריך"),
            (self._area_combo, "אזור"),
        ]:
            form_lay.addLayout(_vert_field(lbl_text, widget))

        form_lay.addWidget(_hline())

        # Target type — centered, equal button widths, label below
        form_lay.addWidget(_section_lbl("סוג המטרה"))
        self._target_type_sel = MultiButtonSelector(
            ["refinery", "oil_depot", "pipeline", "gas_facility", "power_facility", "nuclear"],
            labels={
                "refinery":       "בית זיקוק",
                "oil_depot":      "מחסן דלק",
                "pipeline":       "צינור",
                "gas_facility":   "גז",
                "power_facility": "חשמל",
                "nuclear":        "גרעיני",
            },
            rows=2,
            btn_height=21,
            stretch=True,
        )
        form_lay.addWidget(self._target_type_sel)

        form_lay.addWidget(_hline())

        # ── SIGNALS ──────────────────────────────────────────────────────────
        form_lay.addWidget(_section_lbl("סימנים"))

        self._air_def_toggle  = BoolToggle(nullable=True)
        self._fire_toggle     = BoolToggle(nullable=True)
        self._shutdown_toggle = BoolToggle(nullable=True)
        self._hit_toggle      = BoolToggle(nullable=True)
        self._combined_toggle = BoolToggle(nullable=True)

        self._explosions_spin = CircularSpinner(min_val=0, max_val=999)

        # Labels below buttons — two rows of 3 labelled cells
        def _labelled_cell(widget: QWidget, label_text: str) -> QWidget:
            cell = QWidget()
            cell.setAttribute(Qt.WA_TranslucentBackground)
            v = QVBoxLayout(cell)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            v.setAlignment(Qt.AlignHCenter)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(
                f"color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:11px; font-weight:600; border:none;"
            )
            lbl.setAlignment(Qt.AlignCenter)
            v.addWidget(lbl)
            v.addWidget(widget, 0, Qt.AlignHCenter)
            return cell

        for row_toggles in [
            [("הגנה אווירית", self._air_def_toggle),
             ("שריפה",     self._fire_toggle),
             ("פגיעה ✓",   self._hit_toggle)],
            [("השבתה", self._shutdown_toggle),
             ("משולב", self._combined_toggle),
             ("פיצוצים", self._explosions_spin)],
        ]:
            row_lay = QHBoxLayout()
            row_lay.setSpacing(8)
            for lbl_text, widget in row_toggles:
                row_lay.addWidget(_labelled_cell(widget, lbl_text), 1)
            form_lay.addLayout(row_lay)

        form_lay.addWidget(_hline())

        # ── Scale / Damage / Source — equal-width buttons, label centered below ──
        self._drone_scale_sel = ButtonSelector(
            ["few", "swarm", "massive"],
            labels={"few": "מעט", "swarm": "נחיל", "massive": "מסיבי"},
            accents=P.SCALE_COLORS,
            nullable=True,
            stretch=True,
        )
        self._damage_sel = ButtonSelector(
            ["low", "medium", "high"],
            labels={"low": "נמוך", "medium": "בינוני", "high": "גבוה"},
            accents=P.DMG_COLORS,
            nullable=True,
            stretch=True,
        )
        self._report_type_sel = ButtonSelector(
            ["direct_report", "official_confirmation", "hearsay"],
            labels={
                "direct_report":         "ישיר",
                "official_confirmation": "רשמי",
                "hearsay":               "שמועה",
            },
            stretch=True,
        )

        def _selector_group(widget: QWidget, label_text: str) -> QWidget:
            grp = QWidget()
            grp.setAttribute(Qt.WA_TranslucentBackground)
            v = QVBoxLayout(grp)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(3)
            lbl = QLabel(label_text)
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            lbl.setStyleSheet(
                f"color:{P.TXT2}; font-family:{P.FONT_STACK}; font-size:11px; font-weight:600; border:none;"
            )
            v.addWidget(lbl)
            v.addWidget(widget)
            return grp

        for widget, lbl_text in [
            (self._drone_scale_sel, "היקף"),
            (self._damage_sel,      "נזק"),
            (self._report_type_sel, "מקור"),
        ]:
            form_lay.addWidget(_selector_group(widget, lbl_text))

        # Reset button — bottom-right corner of the form card
        self._reset_btn = QPushButton("↺")
        self._reset_btn.setFixedSize(21, 21)
        self._reset_btn.setCursor(Qt.PointingHandCursor)
        self._reset_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{P.AMBER};"
            f"border:none;border-radius:{P.RADIUS_SM}px;font-size:16px;font-weight:800;}}"
            f"QPushButton:hover{{background:{P.AMBER};color:#0a0b0d;}}"
        )
        self._reset_btn.clicked.connect(self._on_reset)
        reset_corner = QHBoxLayout()
        reset_corner.setContentsMargins(0, 2, 0, 0)
        reset_corner.addStretch()
        reset_corner.addWidget(self._reset_btn)
        form_lay.addLayout(reset_corner)

        # Live diff refresh when in UPDATE mode
        for sig in (
            self._area_combo.value_changed,
            self._target_type_sel.value_changed,
            self._air_def_toggle.value_changed,
            self._fire_toggle.value_changed,
            self._shutdown_toggle.value_changed,
            self._hit_toggle.value_changed,
            self._combined_toggle.value_changed,
            self._explosions_spin.value_changed,
            self._drone_scale_sel.value_changed,
            self._damage_sel.value_changed,
            self._report_type_sel.value_changed,
        ):
            sig.connect(self._refresh_changes_panel)
        self._date_edit.dateChanged.connect(self._refresh_changes_panel)

        self._left_lay.addWidget(form_card, 1)

    def _build_action_buttons(self):
        self._nav_on_style = (
            f"QPushButton{{background:transparent;color:{P.TXT};"
            f"border:none;font-size:24px;font-weight:700;padding:0 8px;}}"
            f"QPushButton:hover{{color:{P.INDIGO};}}"
            f"QPushButton:pressed{{color:{P.TXT3};}}"
        )
        self._nav_off_style = (
            f"QPushButton{{background:transparent;color:{P.TXT3};"
            f"border:none;font-size:24px;font-weight:700;padding:0 8px;}}"
        )

        self._prev_btn = QPushButton("←")
        self._prev_btn.setFixedHeight(36)
        self._prev_btn.setCursor(Qt.PointingHandCursor)
        self._prev_btn.setStyleSheet(self._nav_off_style)
        self._prev_btn.setEnabled(False)

        self._next_btn = QPushButton("→")
        self._next_btn.setFixedHeight(36)
        self._next_btn.setCursor(Qt.PointingHandCursor)
        self._next_btn.setStyleSheet(self._nav_off_style)
        self._next_btn.setEnabled(False)

        self._save_btn = QPushButton("+")
        self._save_btn.setFixedHeight(46)
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{P.GREEN};"
            f"border:1px solid {P.GREEN};border-radius:{P.RADIUS_LG}px;"
            f"font-size:24px;font-weight:700;padding:0;}}"
            f"QPushButton:hover{{background:{P.GREEN};color:#0a0b0d;}}"
            f"QPushButton:pressed{{background:{P.GREEN_D};}}"
        )
        S.apply_button_shadow(self._save_btn, blur=24, alpha=100, y_offset=8)

        self._save_btn.clicked.connect(self._on_save)
        self._prev_btn.clicked.connect(self._on_prev)
        self._next_btn.clicked.connect(self._on_next)

        arrows_row = QHBoxLayout()
        arrows_row.setContentsMargins(0, 0, 0, 0)
        arrows_row.setSpacing(0)
        arrows_row.addWidget(self._prev_btn)
        arrows_row.addStretch()
        arrows_row.addWidget(self._next_btn)

        self._left_lay.addLayout(arrows_row)
        self._left_lay.addWidget(self._save_btn)

    def _build_right_panels(self, parent_lay: QVBoxLayout):
        # ── Message panel: English (top) + Hebrew emphasized (bottom) ──────────
        msg_card = _card()
        msg_v = QVBoxLayout(msg_card)
        msg_v.setContentsMargins(14, 10, 14, 10)
        msg_v.setSpacing(4)

        # English message (LTR, readable)
        orig_hdr = QLabel("ההודעה המקורית")
        orig_hdr.setStyleSheet(
            f"color:{P.TXT3}; font-family:{P.FONT_STACK}; font-size:10px; font-weight:700; "
            f"letter-spacing:1.5px; border:none; padding:2px 0; background:transparent;"
        )
        self._orig_edit = QPlainTextEdit()
        self._orig_edit.setReadOnly(True)
        self._orig_edit.setLayoutDirection(Qt.LeftToRight)
        self._orig_edit.setStyleSheet(
            f"background:transparent; color:{P.TXT}; border:none; "
            f"font-family:{P.FONT_STACK}; font-size:16px; font-weight:600; line-height:1.6;"
        )

        # Hebrew translation — right-aligned, large, bold, takes remaining space
        sep_lbl = QLabel("תרגום לעברית")
        sep_lbl.setStyleSheet(
            f"color:{P.TXT3}; font-family:{P.FONT_STACK}; font-size:10px; font-weight:700; "
            f"letter-spacing:1.5px; border:none; padding:2px 0; background:transparent;"
        )
        self._trans_edit = QPlainTextEdit()
        self._trans_edit.setReadOnly(True)
        self._trans_edit.setMinimumHeight(100)
        self._trans_edit.setLayoutDirection(Qt.RightToLeft)
        self._trans_edit.setStyleSheet(
            f"background:transparent; color:{P.TXT}; border:none; "
            f"font-family:{P.FONT_STACK}; font-size:16px; font-weight:600; line-height:1.6;"
        )
        # Force true RTL paragraph alignment so each line starts from the right
        _rtl_opt = QTextOption(Qt.AlignRight)
        _rtl_opt.setTextDirection(Qt.RightToLeft)
        self._trans_edit.document().setDefaultTextOption(_rtl_opt)

        msg_v.addWidget(orig_hdr)
        msg_v.addWidget(self._orig_edit, 1)
        msg_v.addWidget(_hline())
        msg_v.addWidget(sep_lbl)
        msg_v.addWidget(self._trans_edit, 1)
        parent_lay.addWidget(msg_card, 1)       # msg_card takes all available stretch

        # ── Changes panel ─────────────────────────────────────────────────────
        changes_card = _card()
        changes_v = QVBoxLayout(changes_card)
        changes_v.setContentsMargins(14, 10, 14, 10)
        changes_v.setSpacing(4)

        changes_hdr = QLabel("שינויים")
        changes_hdr.setStyleSheet(
            f"color:{P.TXT3}; font-family:{P.FONT_STACK}; font-size:10px; font-weight:700; "
            f"letter-spacing:1.5px; border:none; padding:2px 0; background:transparent;"
        )
        self._changes_edit = QPlainTextEdit()
        self._changes_edit.setReadOnly(True)
        self._changes_edit.setFixedHeight(64)
        self._changes_edit.setStyleSheet(
            f"background:transparent; color:{P.AMBER}; border:none; "
            f"font-family:{P.FONT_MONO}; font-size:12px;"
        )
        changes_v.addWidget(changes_hdr)
        changes_v.addWidget(self._changes_edit)
        parent_lay.addWidget(changes_card)      # no stretch — fixed size

        # ── Log panel ─────────────────────────────────────────────────────────
        log_card = _card()
        log_v = QVBoxLayout(log_card)
        log_v.setContentsMargins(14, 10, 14, 10)
        log_v.setSpacing(4)

        log_hdr = QLabel("יומן פעולות")
        log_hdr.setStyleSheet(
            f"color:{P.TXT3}; font-family:{P.FONT_STACK}; font-size:10px; font-weight:700; "
            f"letter-spacing:1.5px; border:none; padding:2px 0; background:transparent;"
        )
        self._log_edit = QPlainTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setFixedHeight(72)
        self._log_edit.setStyleSheet(
            f"background:transparent; color:{P.TXT2}; border:none; "
            f"font-size:11px; font-family:{P.FONT_MONO};"
        )
        log_v.addWidget(log_hdr)
        log_v.addWidget(self._log_edit)
        parent_lay.addWidget(log_card)          # no stretch — minimal fixed size

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("R"), self).activated.connect(self._on_reset)
        QShortcut(QKeySequence(Qt.Key_Left),  self).activated.connect(self._on_prev)
        QShortcut(QKeySequence(Qt.Key_Right), self).activated.connect(self._on_next)
        QShortcut(QKeySequence(Qt.Key_Return), self).activated.connect(self._on_save)
        QShortcut(QKeySequence(Qt.Key_Enter),  self).activated.connect(self._on_save)

    # ── Auto-fill ─────────────────────────────────────────────────────────────

    def _on_area_changed(self, text: str):
        pass

    # ── Switch INSERT → manual UPDATE ─────────────────────────────────────────

    def _switch_to_insert(self):
        """Switch from UPDATE mode to INSERT.

        During an active Telegram review (_current_parsed is set), restores the
        form to the original parsed values so the pending future can still be
        resolved correctly.  In quick-insert mode (no live candidate) the form
        is fully cleared instead.
        """
        self._action = "INSERT"
        self._manual_update_target = None

        self._action_pill.setText(_ACTION_LABELS["INSERT"])
        self._action_pill.setStyleSheet(
            f"background:transparent; color:{P.GREEN}; border:none; "
            f"font-family:{P.FONT_STACK}; font-size:11px; font-weight:700; padding:3px 8px;"
        )
        self._switch_update_btn.setText("↕ מעבר לעדכון")
        self._changes_edit.clear()

        if self._current_parsed is not None:
            # Active review — restore form to original parsed values, keep messages.
            p = self._current_parsed
            if p.attack_date:
                self._date_edit.setDate(QDate(p.attack_date.year, p.attack_date.month, p.attack_date.day))
            else:
                self._date_edit.setDate(QDate.currentDate())
            self._area_combo.set_python_value(p.area or "")
            self._target_type_sel.set_python_value(p.target_type or "")
            self._drone_scale_sel.set_python_value(p.drone_scale or "")
            self._air_def_toggle.set_python_value(p.air_defense_active)
            self._fire_toggle.set_python_value(p.fire)
            self._shutdown_toggle.set_python_value(p.shutdown)
            self._hit_toggle.set_python_value(p.hit_confirmed)
            self._combined_toggle.set_python_value(p.combined_strike)
            self._explosions_spin.setValue(p.explosions_reported or 0)
            self._damage_sel.set_python_value(p.damage_level or None)
            self._report_type_sel.set_python_value(p.report_type or "hearsay")
        else:
            # No active candidate — full blank reset.
            self._current_parsed = None
            self._orig_edit.clear()
            self._trans_edit.clear()
            self._date_edit.setDate(QDate.currentDate())
            self._area_combo.set_python_value("")
            self._target_type_sel.set_python_value("")
            self._drone_scale_sel.set_python_value("")
            self._air_def_toggle.set_python_value(False)
            self._fire_toggle.set_python_value(False)
            self._shutdown_toggle.set_python_value(False)
            self._hit_toggle.set_python_value(False)
            self._combined_toggle.set_python_value(False)
            self._explosions_spin.setValue(0)
            self._damage_sel.set_python_value(None)
            self._report_type_sel.set_python_value("hearsay")

        self.append_log("עברנו למצב הוספה.")

    def _on_switch_to_update(self):
        if self._action == "UPDATE":
            self._switch_to_insert()
            return

        if not self._fetch_recent_cb:
            self.append_log("אין חיבור למסד הנתונים.")
            return
        try:
            recent = self._fetch_recent_cb()
        except Exception as exc:
            self.append_log(f"שגיאה בטעינת תקיפות אחרונות: {exc}")
            return

        dlg = _RecentAttacksPicker(recent, parent=self)
        if dlg.exec() != QDialog.Accepted or dlg.selected is None:
            return

        atk = dlg.selected
        self._manual_update_target = atk
        self._action = "UPDATE"

        self._action_pill.setText(_ACTION_LABELS["UPDATE"])
        self._action_pill.setStyleSheet(
            f"background:transparent; color:{P.AMBER}; border:none; "
            f"font-family:{P.FONT_STACK}; font-size:11px; font-weight:700; padding:3px 8px;"
        )
        self._switch_update_btn.setText("↕ מעבר להוספה")

        self.append_log(
            f"עדכון #{atk.get('attack_id')} — {atk.get('area','?')} | {atk.get('attack_date','?')}"
        )

        self._refresh_changes_panel()

    def _refresh_changes_panel(self, *_):
        """Recompute and display diff whenever form changes in UPDATE mode."""
        if self._action != "UPDATE" or self._manual_update_target is None:
            return
        try:
            from gmar_app.db import build_updated_record, diff_update_fields
            current_data = self._collect_form_as_db_dict()
            existing = self._manual_update_target
            merged = build_updated_record(existing, current_data)
            diffs = diff_update_fields(existing, merged)
            diffs = [(f, o, n) for f, o, n in diffs if f != "source"]
            if diffs:
                lines = [f"{f}: {o!r} → {n!r}" for f, o, n in diffs]
                self._changes_edit.setPlainText("\n".join(lines))
            else:
                self._changes_edit.setPlainText("— אין שינויים —")
        except Exception as exc:
            self._changes_edit.setPlainText(f"(שגיאה בהשוואה: {exc})")

    def reset_to_insert(self):
        """Clear all form fields and reset UI state to INSERT mode."""
        # Clear state
        self._manual_update_target = None
        self._current_parsed = None
        self._action = "INSERT"

        # Clear text fields
        self._orig_edit.clear()
        self._trans_edit.clear()
        self._changes_edit.clear()

        # Reset form inputs to defaults
        self._date_edit.setDate(QDate.currentDate())
        self._area_combo.set_python_value("")
        self._target_type_sel.set_python_value("")
        self._drone_scale_sel.set_python_value("")
        self._air_def_toggle.set_python_value(False)
        self._fire_toggle.set_python_value(False)
        self._shutdown_toggle.set_python_value(False)
        self._hit_toggle.set_python_value(False)
        self._combined_toggle.set_python_value(False)
        self._explosions_spin.setValue(0)
        self._damage_sel.set_python_value(None)
        self._report_type_sel.set_python_value("hearsay")

        # Restore INSERT mode UI
        self._action_pill.setText(_ACTION_LABELS["INSERT"])
        self._action_pill.setStyleSheet(
            f"background:transparent; color:{P.GREEN}; border:none; "
            f"font-family:{P.FONT_STACK}; font-size:11px; font-weight:700; padding:3px 8px;"
        )
        self._switch_update_btn.setText("↕ מעבר לעדכון")
        self._switch_update_btn.setEnabled(True)
        self._switch_update_btn.setVisible(True)


    # ── Public API ────────────────────────────────────────────────────────────

    def set_fetch_recent_callback(self, cb):
        """Provide a callable() → list[dict] for fetching recent attacks."""
        self._fetch_recent_cb = cb

    def get_manual_update_target(self) -> dict | None:
        """Return the manually selected existing attack record (or None)."""
        return self._manual_update_target

    def set_status(self, msg: str):
        self._loading_overlay.set_message(msg)

    def reset_view(self):
        """Called on re-entry: intentionally a no-op — an in-progress review
        (form values, current candidate, pending decision future) must survive
        switching to another page and back."""
        pass

    def _on_open_date_picker(self):
        qd_s = self._sp_start_edit.date()
        qd_e = self._sp_end_edit.date()
        s = date(qd_s.year(), qd_s.month(), qd_s.day())
        e = date(qd_e.year(), qd_e.month(), qd_e.day())

        dlg = _DateRangePickerDialog(s, e, parent=self)
        dlg.adjustSize()
        if self.window():
            geo = self.window().geometry()
            dlg.move(
                geo.center().x() - dlg.width() // 2,
                geo.center().y() - dlg.height() // 2,
            )
        if dlg.exec() == QDialog.Accepted:
            new_s, new_e = dlg.result_dates
            self._sp_start_edit.setDate(QDate(new_s.year, new_s.month, new_s.day))
            self._sp_end_edit.setDate(QDate(new_e.year, new_e.month, new_e.day))

    def append_log(self, msg: str):
        self._log_edit.appendPlainText(msg)

    def show_start_panel(self):
        """Switch back to start panel to allow a new session."""
        self._loading_overlay.stop()
        self._sp_status_lbl.setText("")
        self._main_stack.setCurrentIndex(0)

    def _collect_form_as_db_dict(self) -> dict:
        """Build a DB-ready dict from current form values (used in quick-insert mode)."""
        from datetime import date as _date
        qd = self._date_edit.date()
        return {
            "attack_date":        _date(qd.year(), qd.month(), qd.day()),
            "area":               self._area_combo.python_value() or "",
            "target_type":        self._target_type_sel.python_value() or "",
            "combined_strike":    bool(self._combined_toggle.python_value()),
            "drone_scale":        self._drone_scale_sel.python_value() or None,
            "air_defense_active": bool(self._air_def_toggle.python_value()),
            "shutdown":           bool(self._shutdown_toggle.python_value()),
            "fire":               bool(self._fire_toggle.python_value()),
            "explosions_reported":self._explosions_spin.value(),
            "hit_confirmed":      bool(self._hit_toggle.python_value()),
            "report_type":        self._report_type_sel.python_value() or "hearsay",
            "damage_level":       self._damage_sel.python_value() or None,
            "source":             "",
        }

    async def wait_for_start(self):
        """Async: resolves with (mode, start_date, end_date) when user clicks Start."""
        loop = asyncio.get_event_loop()
        self._start_future = loop.create_future()
        return await self._start_future

    def load_candidate(
        self,
        action: str,
        parsed,
        original_text: str,
        translated_text: str,
        diffs,
        counter: int,
    ):
        self._loading_overlay.stop()
        self._current_parsed = deepcopy(parsed)
        self._action = action
        self._counter = counter
        self._manual_update_target = None

        color = P.GREEN if action == "INSERT" else P.AMBER
        self._action_pill.setStyleSheet(
            f"background:transparent; color:{color}; border:none; "
            f"font-family:{P.FONT_STACK}; font-size:11px; font-weight:700; padding:3px 8px;"
        )
        self._action_pill.setText(_ACTION_LABELS.get(action, action))

        self._switch_update_btn.setVisible(True)
        self._switch_update_btn.setEnabled(True)
        self._switch_update_btn.setText(
            "↕ מעבר לעדכון" if action == "INSERT" else "↕ מעבר להוספה"
        )

        self._counter_lbl.setText(f"#{counter}")

        if parsed.attack_date:
            d = parsed.attack_date
            self._date_edit.setDate(QDate(d.year, d.month, d.day))

        self._area_combo.set_python_value(parsed.area or "")
        self._target_type_sel.set_python_value(parsed.target_type or "")
        self._drone_scale_sel.set_python_value(parsed.drone_scale or "")
        self._air_def_toggle.set_python_value(parsed.air_defense_active)
        self._fire_toggle.set_python_value(parsed.fire)
        self._shutdown_toggle.set_python_value(parsed.shutdown)
        self._hit_toggle.set_python_value(parsed.hit_confirmed)
        self._combined_toggle.set_python_value(parsed.combined_strike)
        self._explosions_spin.setValue(parsed.explosions_reported or 0)
        self._damage_sel.set_python_value(parsed.damage_level or None)
        self._report_type_sel.set_python_value(parsed.report_type or "hearsay")

        # English message (top, readable) + Hebrew translation (bottom, emphasized)
        self._orig_edit.setPlainText(original_text or "")
        self._trans_edit.setPlainText(translated_text or "")

        if diffs:
            lines = [f"{f}: {o!r} → {n!r}" for f, o, n in diffs]
            self._changes_edit.setPlainText("\n".join(lines))
        else:
            self._changes_edit.setPlainText("— אין שינויים —")

        self._enable_controls(True)

    def _set_idle(self):
        self._enable_controls(False)
        self._action_pill.setText("—")
        self._action_pill.setStyleSheet(
            f"background:transparent; color:{P.TXT3}; border:none; "
            f"font-family:{P.FONT_STACK}; font-size:11px; font-weight:700; padding:3px 8px;"
        )

    def _enable_controls(self, enabled: bool):
        for w in (self._save_btn, self._reset_btn):
            w.setEnabled(enabled)
        if not enabled:
            self._prev_btn.setEnabled(False)
            self._next_btn.setEnabled(False)
            self._prev_btn.setStyleSheet(self._nav_off_style)
            self._next_btn.setStyleSheet(self._nav_off_style)

    def _collect_edited_parsed(self):
        from datetime import date as _date
        if self._current_parsed is None:
            return None
        p = deepcopy(self._current_parsed)
        qd = self._date_edit.date()
        p.attack_date         = _date(qd.year(), qd.month(), qd.day())
        p.area                = self._area_combo.python_value()
        p.target_type         = self._target_type_sel.python_value()
        p.drone_scale         = self._drone_scale_sel.python_value() or None
        p.air_defense_active  = self._air_def_toggle.python_value()
        p.fire                = self._fire_toggle.python_value()
        p.shutdown            = self._shutdown_toggle.python_value()
        p.hit_confirmed       = self._hit_toggle.python_value()
        p.combined_strike     = self._combined_toggle.python_value()
        p.explosions_reported = self._explosions_spin.value()
        p.damage_level        = self._damage_sel.python_value()
        p.report_type         = self._report_type_sel.python_value()
        return p

    # ── Decision callbacks ────────────────────────────────────────────────────

    def _resolve(self, code: str):
        if self._decision_future and not self._decision_future.done():
            if code in ("y", "mu"):
                edited = self._collect_edited_parsed()
                if edited is None:
                    # No valid parsed — treat as skip to avoid leaving the future unresolved.
                    self._decision_future.set_result(("n", None))
                    self._decision_future = None
                    self.append_log("אזהרה: אין מועמד פעיל לשמירה.")
                    return
            else:
                edited = self._current_parsed
            self._decision_future.set_result((code, edited))
        self._decision_future = None

    def _on_prev(self):
        if self._prev_btn.isEnabled():
            self._resolve("back")

    def _on_next(self):
        if self._next_btn.isEnabled():
            self._resolve("fwd")

    def set_nav_state(self, can_back: bool, can_fwd: bool):
        self._prev_btn.setEnabled(can_back)
        self._next_btn.setEnabled(can_fwd)
        self._prev_btn.setStyleSheet(self._nav_on_style if can_back else self._nav_off_style)
        self._next_btn.setStyleSheet(self._nav_on_style if can_fwd  else self._nav_off_style)

    def _flash_save_btn(self):
        on_style = (
            f"QPushButton{{background:{P.GREEN};color:#0a0b0d;"
            f"border:1px solid {P.GREEN};border-radius:{P.RADIUS_LG}px;"
            f"font-size:24px;font-weight:700;padding:0;}}"
        )
        off_style = (
            f"QPushButton{{background:transparent;color:{P.GREEN};"
            f"border:1px solid {P.GREEN};border-radius:{P.RADIUS_LG}px;"
            f"font-size:24px;font-weight:700;padding:0;}}"
            f"QPushButton:hover{{background:{P.GREEN};color:#0a0b0d;}}"
            f"QPushButton:pressed{{background:{P.GREEN_D};}}"
        )
        self._save_btn.setStyleSheet(on_style)
        QTimer.singleShot(400, lambda: self._save_btn.setStyleSheet(off_style))

    def _on_save(self):
        self._flash_save_btn()
        if self._manual_update_target is not None:
            self._resolve("mu")
            return
        area = self._area_combo.python_value()
        if (not area or area in ("", "Unknown Area")) and self._action == "INSERT":
            reply = QMessageBox.question(
                self,
                "אזהרה: אזור לא ידוע",
                "האזור מוגדר כ'לא ידוע'. לשמור בכל זאת?",
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
                original_text=self._orig_edit.toPlainText(),
                translated_text=self._trans_edit.toPlainText(),
                diffs=None,
                counter=self._counter,
            )

    async def async_wait_for_decision(self):
        loop = asyncio.get_event_loop()
        self._decision_future = loop.create_future()
        return await self._decision_future
