import asyncio
import os
import sys
from copy import deepcopy
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.text_utils import snippet, safe_bool_str


# ── Palette ───────────────────────────────────────────────────────────────────

P_BG       = "#f4f6fc"
P_CARD     = "#ffffff"
P_BORDER   = "#dde3f0"
P_INDIGO   = "#4f46e5"
P_INDIGO_D = "#3730a3"
P_INDIGO_L = "#eef2ff"
P_GREEN    = "#059669"
P_GREEN_D  = "#047857"
P_GREEN_L  = "#d1fae5"
P_SLATE    = "#475569"
P_SLATE_L  = "#e2e8f0"
P_RED      = "#dc2626"
P_RED_D    = "#b91c1c"
P_RED_L    = "#fef2f2"
P_AMBER    = "#d97706"
P_AMBER_L  = "#fef3c7"
P_TXT      = "#0f172a"
P_TXT2     = "#475569"
P_TXT3     = "#94a3b8"

# Per-field accent colors used in ButtonSelector
_ATK_COLORS  = {"drone": "#2563eb", "missile": "#dc2626",
                 "combined": "#7c3aed", "unknown": P_SLATE}
_DMG_COLORS  = {"low": "#16a34a", "medium": "#d97706", "high": "#dc2626"}
_SCALE_COLORS = {"few": "#3b82f6", "swarm": "#2563eb",
                  "massive": "#1e40af", "": P_SLATE}

APP_INSTANCE = None


def get_app():
    global APP_INSTANCE
    app = QApplication.instance()
    if app is not None:
        return app
    APP_INSTANCE = QApplication(sys.argv)
    APP_INSTANCE.setFont(QFont("Segoe UI", 11))
    return APP_INSTANCE


def _to_qdate(v: date) -> QDate:
    return QDate(v.year, v.month, v.day)


def _from_qdate(v: QDate) -> date:
    return date(v.year(), v.month(), v.day())


# ─────────────────────────────────────────────────────────────────────────────
#  Startup dialog
# ─────────────────────────────────────────────────────────────────────────────

class DatePickerDialog(QDialog):
    def __init__(self, default_start: date, default_end: date,
                 default_mode: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Attack Review")
        self.setFixedSize(480, 420)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self._mode = default_mode
        self._build(default_start, default_end, default_mode)
        self.setStyleSheet(self._css())

    def _build(self, start, end, mode):
        root = QVBoxLayout(self)
        root.setContentsMargins(44, 38, 44, 38)
        root.setSpacing(0)

        # Header
        icon_lbl = QLabel("🗓")
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_lbl.setStyleSheet("font-size:42px; margin-bottom:4px;")
        root.addWidget(icon_lbl)

        title = QLabel("Select Date Range")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"font-size:24px; font-weight:800; color:{P_TXT}; margin-bottom:2px;")
        root.addWidget(title)

        sub = QLabel("Choose the period you want to review")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"font-size:14px; color:{P_TXT3}; margin-bottom:26px;")
        root.addWidget(sub)

        # Date card
        card = QFrame()
        card.setObjectName("StartCard")
        card_layout = QGridLayout(card)
        card_layout.setContentsMargins(24, 20, 24, 20)
        card_layout.setHorizontalSpacing(16)
        card_layout.setVerticalSpacing(14)

        for row, (lbl_text, attr, val) in enumerate([
            ("From", "start_edit", start),
            ("To",   "end_edit",   end),
        ]):
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(
                f"font-size:15px; font-weight:700; color:{P_TXT2};")
            w = QDateEdit()
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd  MMM  yyyy")
            w.setDate(_to_qdate(val))
            w.setFixedHeight(42)
            setattr(self, attr, w)
            card_layout.addWidget(lbl, row, 0)
            card_layout.addWidget(w,   row, 1)

        root.addWidget(card)
        root.addSpacing(16)

        # Mode toggle
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        self._mode_btns = {}
        for val, label, icon in [("manual", "Manual Review", "👁"),
                                   ("blind",  "Auto-Insert",  "⚡")]:
            btn = QPushButton(f"{icon}  {label}")
            btn.setFixedHeight(44)
            btn.clicked.connect(lambda _, v=val: self._set_mode(v))
            self._mode_btns[val] = btn
            mode_row.addWidget(btn)
        root.addLayout(mode_row)
        root.addSpacing(20)
        self._refresh_mode()

        # Start button
        self.go_btn = QPushButton("  Start Review  →")
        self.go_btn.setObjectName("GoBtn")
        self.go_btn.setFixedHeight(52)
        self.go_btn.clicked.connect(self._on_go)
        self.go_btn.setDefault(True)
        root.addWidget(self.go_btn)

    def _set_mode(self, val):
        self._mode = val
        self._refresh_mode()

    def _refresh_mode(self):
        for val, btn in self._mode_btns.items():
            if val == self._mode:
                btn.setStyleSheet(
                    f"QPushButton{{background:{P_INDIGO};color:white;"
                    f"border:none;border-radius:10px;font-size:14px;"
                    f"font-weight:700;padding:0 16px;}}")
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:{P_CARD};color:{P_TXT2};"
                    f"border:2px solid {P_BORDER};border-radius:10px;"
                    f"font-size:14px;font-weight:600;padding:0 16px;}}"
                    f"QPushButton:hover{{border-color:{P_INDIGO};"
                    f"color:{P_INDIGO};}}")

    def _on_go(self):
        s = _from_qdate(self.start_edit.date())
        e = _from_qdate(self.end_edit.date())
        if s > e:
            QMessageBox.warning(self, "Invalid range",
                                "Start date must be on or before the end date.")
            return
        self.accept()

    def options(self):
        return (self._mode,
                _from_qdate(self.start_edit.date()),
                _from_qdate(self.end_edit.date()))

    @staticmethod
    def _css():
        return f"""
            QDialog   {{ background:{P_BG}; }}
            QFrame#StartCard {{
                background:{P_CARD}; border:2px solid {P_BORDER};
                border-radius:16px;
            }}
            QDateEdit {{
                background:{P_BG}; border:2px solid {P_BORDER};
                border-radius:9px; padding:6px 12px;
                font-size:15px; font-weight:700; color:{P_TXT};
            }}
            QDateEdit:focus {{ border-color:{P_INDIGO}; background:white; }}
            QDateEdit::drop-down {{ border:none; }}
            QPushButton#GoBtn {{
                background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 {P_INDIGO},stop:1 {P_INDIGO_D});
                color:white; border:none; border-radius:12px;
                font-size:17px; font-weight:800;
            }}
            QPushButton#GoBtn:hover  {{
                background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #6366f1,stop:1 {P_INDIGO});
            }}
            QPushButton#GoBtn:pressed {{ background:{P_INDIGO_D}; }}
            QCalendarWidget {{ background:{P_CARD}; }}
            QCalendarWidget QAbstractItemView:enabled {{
                font-size:13px; color:{P_TXT};
                selection-background-color:{P_INDIGO};
                selection-color:white;
            }}
        """


def show_startup_dialog(default_start: date, default_end: date,
                        default_mode: str):
    dlg = DatePickerDialog(default_start, default_end, default_mode)
    if dlg.exec() == QDialog.Accepted:
        return dlg.options()
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Option lists
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
    "Orenburg", "Penza", "Ulyanovsk", "Nizhny Novgorod",
})

# ─────────────────────────────────────────────────────────────────────────────
#  Custom input widgets
# ─────────────────────────────────────────────────────────────────────────────

class ButtonSelector(QWidget):
    """
    A row of mutually-exclusive toggle buttons.
    Each button shows a display label; the selected one is highlighted.
    """

    def __init__(self, options: list[str], labels: dict[str, str] | None = None,
                 accents: dict[str, str] | None = None, parent=None):
        super().__init__(parent)
        self._options = options
        self._labels  = labels  or {}
        self._accents = accents or {}
        self._value   = options[0] if options else None
        self._btns: dict[str, QPushButton] = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)

        for opt in options:
            display = self._labels.get(opt) or (
                opt.replace("_", " ").title() if opt else "—"
            )
            btn = QPushButton(display)
            btn.setFixedHeight(36)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, v=opt: self._select(v))
            self._btns[opt] = btn
            row.addWidget(btn)

        row.addStretch()
        self._refresh()

    def _select(self, value):
        self._value = value
        self._refresh()

    def _refresh(self):
        for opt, btn in self._btns.items():
            accent = self._accents.get(opt, P_INDIGO)
            if opt == self._value:
                btn.setStyleSheet(
                    f"QPushButton{{background:{accent};color:white;"
                    f"border:2px solid {accent};border-radius:9px;"
                    f"font-size:13px;font-weight:800;padding:0 14px;}}"
                    f"QPushButton:hover{{opacity:0.9;}}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:#f1f5fb;color:{P_TXT2};"
                    f"border:2px solid {P_BORDER};border-radius:9px;"
                    f"font-size:13px;font-weight:600;padding:0 14px;}}"
                    f"QPushButton:hover{{border-color:{accent};"
                    f"color:{accent};background:{P_INDIGO_L};}}"
                )

    def python_value(self) -> str:
        return self._value if self._value is not None else ""

    def set_python_value(self, value):
        if value in self._btns:
            self._value = value
        elif self._options:
            self._value = self._options[0]
        self._refresh()


class BoolToggle(QWidget):
    """
    YES / NO toggle buttons.  Nullable adds a '—' (None) option.
    YES = green  ·  NO = red  ·  — = slate
    """

    def __init__(self, nullable: bool = False, parent=None):
        super().__init__(parent)
        self._nullable = nullable
        self._value    = None
        self._btns: dict = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)

        specs = []
        if nullable:
            specs.append((None,  "—",   P_SLATE))
        specs += [(True, "Yes", P_GREEN), (False, "No", P_RED)]

        for val, label, color in specs:
            btn = QPushButton(label)
            btn.setFixedHeight(36)
            btn.setFixedWidth(60 if label == "—" else 74)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, v=val: self._select(v))
            self._btns[val] = btn
            row.addWidget(btn)

        row.addStretch()
        self._refresh()

    def _select(self, value):
        self._value = value
        self._refresh()

    _COLORS = {True: P_GREEN, False: P_RED, None: P_SLATE}

    def _refresh(self):
        for val, btn in self._btns.items():
            color = self._COLORS[val]
            if val == self._value:
                btn.setStyleSheet(
                    f"QPushButton{{background:{color};color:white;"
                    f"border:2px solid {color};border-radius:9px;"
                    f"font-size:13px;font-weight:800;}}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:#f1f5fb;color:{P_TXT3};"
                    f"border:2px solid {P_BORDER};border-radius:9px;"
                    f"font-size:13px;font-weight:600;}}"
                    f"QPushButton:hover{{border-color:{color};"
                    f"color:{color};background:{P_INDIGO_L};}}"
                )

    def python_value(self):
        return self._value

    def set_python_value(self, value):
        self._value = True if value is True else (False if value is False else None)
        self._refresh()


class SmartCombo(QComboBox):
    """Editable combo with fuzzy completer — used for free-text fields."""
    def __init__(self, items=None, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.addItems(items or [])
        c = QCompleter(items or [], self)
        c.setCaseSensitivity(Qt.CaseInsensitive)
        c.setFilterMode(Qt.MatchContains)
        self.setCompleter(c)

    def set_python_value(self, value):
        text = "" if value is None else str(value)
        idx = self.findText(text, Qt.MatchExactly)
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:
            self.setEditText(text)

    def python_value(self) -> str:
        return self.currentText().strip()


# ─────────────────────────────────────────────────────────────────────────────
#  Candidate edit form  (LEFT panel)
# ─────────────────────────────────────────────────────────────────────────────

def _section(title: str, layout: QVBoxLayout):
    """Append a bold section label + 1px divider to a VBox layout."""
    if layout.count() > 0:
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{P_BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(6)
    lbl = QLabel(title)
    lbl.setStyleSheet(
        f"font-size:11px;font-weight:800;color:{P_TXT3};"
        f"letter-spacing:1px;margin-bottom:4px;")
    layout.addWidget(lbl)


def _field(label: str, widget: QWidget, layout: QVBoxLayout):
    """Append a label + widget row."""
    row = QWidget()
    row_l = QHBoxLayout(row)
    row_l.setContentsMargins(0, 0, 0, 0)
    row_l.setSpacing(14)
    lbl = QLabel(label)
    lbl.setFixedWidth(130)
    lbl.setStyleSheet(f"font-size:13px;font-weight:600;color:{P_TXT2};")
    row_l.addWidget(lbl)
    row_l.addWidget(widget, 1)
    layout.addWidget(row)
    layout.addSpacing(4)


class CandidateForm(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.original_parsed = None
        self.current_parsed  = None
        self.inputs: dict    = {}

        # Outer scroll area
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setObjectName("FormScroll")

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(20, 18, 20, 24)
        layout.setSpacing(6)

        # ── IDENTITY ─────────────────────────────────────────────────────────
        _section("IDENTITY", layout)

        self.inputs["attack_date"] = self._date_w(date.today())
        _field("Attack Date", self.inputs["attack_date"], layout)

        self.inputs["area"] = SmartCombo(AREA_OPTIONS)
        self.inputs["area"].setFixedHeight(38)
        _field("Area", self.inputs["area"], layout)

        # ── ATTACK ───────────────────────────────────────────────────────────
        layout.addSpacing(6)
        _section("ATTACK", layout)

        self.inputs["drone_scale"] = ButtonSelector(
            ["", "few", "swarm", "massive"],
            labels={"": "—", "few": "Few", "swarm": "Swarm", "massive": "Massive"},
            accents=_SCALE_COLORS,
        )
        _field("Drone Scale", self.inputs["drone_scale"], layout)

        self.inputs["combined_strike"] = BoolToggle(nullable=False)
        _field("Combined Strike", self.inputs["combined_strike"], layout)

        # ── SIGNALS ──────────────────────────────────────────────────────────
        layout.addSpacing(6)
        _section("SIGNALS", layout)

        for key, label, nullable in [
            ("fire",               "Fire",           False),
            ("shutdown",          "Shutdown",       False),
            ("hit_confirmed",     "Hit Confirmed",  False),
            ("air_defense_active","Air Defense",    False),
        ]:
            self.inputs[key] = BoolToggle(nullable=nullable)
            _field(label, self.inputs[key], layout)

        self.inputs["explosions_reported"] = self._spin(0)
        _field("Explosions", self.inputs["explosions_reported"], layout)

        # ── ASSESSMENT ───────────────────────────────────────────────────────
        layout.addSpacing(6)
        _section("ASSESSMENT", layout)

        self.inputs["damage_level"] = ButtonSelector(
            ["low", "medium", "high"],
            labels={"low": "Low", "medium": "Medium", "high": "High"},
            accents=_DMG_COLORS,
        )
        _field("Damage", self.inputs["damage_level"], layout)

        self.inputs["target_type"] = ButtonSelector(
            ["unknown", "oil_depot", "refinery", "pipeline",
             "gas_facility", "power_facility", "mixed"],
            labels={"unknown": "?", "oil_depot": "Depot",
                    "refinery": "Refinery", "pipeline": "Pipeline",
                    "gas_facility": "Gas", "power_facility": "Power",
                    "mixed": "Mixed"},
        )
        _field("Target Type", self.inputs["target_type"], layout)

        self.inputs["report_type"] = ButtonSelector(
            ["direct_report", "official_confirmation",
             "indirect_aftermath", "correction_update"],
            labels={"direct_report": "Direct", "official_confirmation": "Official",
                    "indirect_aftermath": "Indirect",
                    "correction_update": "Correction"},
        )
        _field("Report Type", self.inputs["report_type"], layout)

        layout.addStretch()
        scroll.setWidget(inner)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _date_w(self, v: date) -> QDateEdit:
        w = QDateEdit()
        w.setCalendarPopup(True)
        w.setDisplayFormat("yyyy-MM-dd")
        w.setDate(_to_qdate(v if isinstance(v, date) else date.today()))
        w.setFixedHeight(38)
        return w

    def _spin(self, value: int) -> QSpinBox:
        w = QSpinBox()
        w.setRange(0, 999)
        w.setValue(int(value or 0))
        w.setFixedHeight(36)
        return w



    # ── Public API ─────────────────────────────────────────────────────────────

    def load_parsed(self, parsed):
        self.original_parsed = deepcopy(parsed)
        self.current_parsed  = deepcopy(parsed)
        self.reset_to_original()

    def build_parsed_copy(self):
        if self.current_parsed is None:
            raise ValueError("No candidate loaded")
        obj = deepcopy(self.current_parsed)

        setattr(obj, "attack_date",
                _from_qdate(self.inputs["attack_date"].date()))
        setattr(obj, "area",
                self.inputs["area"].python_value())
        setattr(obj, "target_type",
                self.inputs["target_type"].python_value() or "unknown")
        ds = self.inputs["drone_scale"].python_value().strip()
        setattr(obj, "drone_scale",        ds if ds else None)
        setattr(obj, "combined_strike",    self.inputs["combined_strike"].python_value())
        setattr(obj, "air_defense_active", self.inputs["air_defense_active"].python_value())
        setattr(obj, "shutdown",           self.inputs["shutdown"].python_value())
        setattr(obj, "fire",               self.inputs["fire"].python_value())
        setattr(obj, "explosions_reported",self.inputs["explosions_reported"].value())
        setattr(obj, "hit_confirmed",      self.inputs["hit_confirmed"].python_value())
        setattr(obj, "report_type",
                self.inputs["report_type"].python_value() or "direct_report")
        setattr(obj, "damage_level",
                self.inputs["damage_level"].python_value() or "low")
        return obj

    def reset_to_original(self):
        if self.original_parsed is None:
            return
        p = self.original_parsed
        self.inputs["attack_date"].setDate(
            _to_qdate(getattr(p, "attack_date", date.today())))
        self.inputs["area"].set_python_value(
            getattr(p, "area", ""))
        self.inputs["target_type"].set_python_value(
            getattr(p, "target_type", "unknown"))
        self.inputs["combined_strike"].set_python_value(
            getattr(p, "combined_strike", False))
        ds = getattr(p, "drone_scale", "") or ""
        self.inputs["drone_scale"].set_python_value(ds)
        self.inputs["air_defense_active"].set_python_value(
            getattr(p, "air_defense_active", False))
        self.inputs["shutdown"].set_python_value(
            getattr(p, "shutdown", False))
        self.inputs["fire"].set_python_value(
            getattr(p, "fire", False))
        self.inputs["explosions_reported"].setValue(
            int(getattr(p, "explosions_reported", 0) or 0))
        self.inputs["hit_confirmed"].set_python_value(
            getattr(p, "hit_confirmed", False))
        self.inputs["report_type"].set_python_value(
            getattr(p, "report_type", "direct_report"))
        self.inputs["damage_level"].set_python_value(
            getattr(p, "damage_level", "low"))


# ─────────────────────────────────────────────────────────────────────────────
#  Info tile (top strip)
# ─────────────────────────────────────────────────────────────────────────────

class InfoTile(QFrame):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("InfoTile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(3)
        self._lbl = QLabel(label.upper())
        self._lbl.setObjectName("TileLabel")
        self._val = QLabel("—")
        self._val.setObjectName("TileValue")
        self._val.setWordWrap(True)
        lay.addWidget(self._lbl)
        lay.addWidget(self._val)

    def set_value(self, v: str):
        self._val.setText(v or "—")

    def set_accent(self, color: str):
        self.setStyleSheet(
            f"QFrame#InfoTile{{border-left:4px solid {color};"
            f"background:{P_CARD};border-radius:10px;}}")


# ─────────────────────────────────────────────────────────────────────────────
#  Signal pill  (read-only display, top area)
# ─────────────────────────────────────────────────────────────────────────────

_PILL = {
    "FIRE":     ("#dc2626", "#fef2f2"),
    "SHUTDOWN": ("#d97706", "#fffbeb"),
    "HIT":      ("#059669", "#ecfdf5"),
    "AIR DEF":  ("#2563eb", "#eff6ff"),
    "COMBINED": ("#7c3aed", "#f5f3ff"),
    "PRIMARY":  (P_INDIGO,  P_INDIGO_L),
}


class SignalPill(QLabel):
    def __init__(self, key: str, label: str, parent=None):
        super().__init__(label, parent)
        self._key = key
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(30)
        self.setMinimumWidth(90)
        self.set_value(False)

    def set_value(self, on: bool):
        if on and self._key in _PILL:
            fg, bg = _PILL[self._key]
            self.setStyleSheet(
                f"background:{bg};color:{fg};border:2px solid {fg};"
                f"border-radius:15px;font-size:12px;font-weight:800;"
                f"padding:0 12px;")
        else:
            self.setStyleSheet(
                f"background:{P_BORDER};color:{P_TXT3};border:2px solid {P_BORDER};"
                f"border-radius:15px;font-size:12px;font-weight:600;"
                f"padding:0 12px;")


# ─────────────────────────────────────────────────────────────────────────────
#  Main review window
# ─────────────────────────────────────────────────────────────────────────────

class ReviewMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Attack Review")
        self.resize(1640, 980)

        self.result_action       = None
        self.result_parsed       = None
        self.current_action_name = None
        self._decision_future    = None

        self._build_ui()
        self.setStyleSheet(_stylesheet())
        self._install_shortcuts()

    # ── Shortcuts ─────────────────────────────────────────────────────────────

    def _install_shortcuts(self):
        QShortcut(QKeySequence("S"),      self, self._save_decision)
        QShortcut(QKeySequence("Return"), self, self._save_decision)
        QShortcut(QKeySequence("D"),      self, self._dup_decision)
        QShortcut(QKeySequence("N"),      self, self._skip_decision)
        QShortcut(QKeySequence("Q"),      self, self._quit_decision)
        QShortcut(QKeySequence("R"),      self, self._reset_form)

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        # Top strip — info tiles
        strip = QHBoxLayout()
        strip.setSpacing(8)
        self.tile_action  = InfoTile("Action")
        self.tile_counter = InfoTile("#")
        self.tile_date    = InfoTile("Date")
        self.tile_area    = InfoTile("Area")
        self.tile_targets = InfoTile("Target")
        self.tile_score   = InfoTile("Signal")
        self.tile_damage  = InfoTile("Damage")
        for t in [self.tile_action, self.tile_counter, self.tile_date,
                  self.tile_area, self.tile_targets, self.tile_score,
                  self.tile_damage]:
            strip.addWidget(t)
        outer.addLayout(strip)

        # Signal pills row
        pill_frame = QFrame()
        pill_frame.setObjectName("PillRow")
        pill_l = QHBoxLayout(pill_frame)
        pill_l.setContentsMargins(14, 8, 14, 8)
        pill_l.setSpacing(8)
        self._pill_fire     = SignalPill("FIRE",     "🔥  Fire")
        self._pill_shutdown = SignalPill("SHUTDOWN", "⛔  Shutdown")
        self._pill_hit      = SignalPill("HIT",      "✅  Hit Confirmed")
        self._pill_airdef   = SignalPill("AIR DEF",  "🛡  Air Defense")
        self._pill_combined = SignalPill("COMBINED", "🔗  Combined")
        self._pill_primary  = SignalPill("PRIMARY",  "🎯  Primary")
        for p in [self._pill_fire, self._pill_shutdown, self._pill_hit,
                  self._pill_airdef, self._pill_combined, self._pill_primary]:
            pill_l.addWidget(p)
        self._expl_lbl = QLabel("Explosions: —")
        self._expl_lbl.setObjectName("ExplLabel")
        pill_l.addSpacing(8)
        pill_l.addWidget(self._expl_lbl)
        pill_l.addStretch()
        outer.addWidget(pill_frame)

        # ── Main splitter ──────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # LEFT: editing controls
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)

        form_card = QFrame()
        form_card.setObjectName("Card")
        fc_l = QVBoxLayout(form_card)
        fc_l.setContentsMargins(0, 0, 0, 0)
        self.form = CandidateForm()
        fc_l.addWidget(self.form)
        ll.addWidget(form_card, 1)

        reset_row = QHBoxLayout()
        self.reset_btn = QPushButton("↺  Reset  [R]")
        self.reset_btn.setObjectName("ResetBtn")
        self.reset_btn.clicked.connect(self._reset_form)
        reset_row.addStretch()
        reset_row.addWidget(self.reset_btn)
        ll.addLayout(reset_row)

        # RIGHT: display only
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        # Original message
        ru_card = QFrame()
        ru_card.setObjectName("Card")
        ru_l = QVBoxLayout(ru_card)
        ru_l.setContentsMargins(16, 12, 16, 12)
        ru_title = QLabel("Original Message")
        ru_title.setObjectName("CardTitle")
        ru_l.addWidget(ru_title)
        self.msg_box = QPlainTextEdit()
        self.msg_box.setReadOnly(True)
        self.msg_box.setObjectName("RuText")
        self.msg_box.setFixedHeight(170)
        ru_l.addWidget(self.msg_box)
        rl.addWidget(ru_card)

        # Hebrew translation  (slightly larger, more prominent)
        he_card = QFrame()
        he_card.setObjectName("HeCard")
        he_l = QVBoxLayout(he_card)
        he_l.setContentsMargins(16, 12, 16, 12)
        he_title = QLabel("Translation")
        he_title.setObjectName("CardTitle")
        he_l.addWidget(he_title)
        self.trans_box = QPlainTextEdit()
        self.trans_box.setReadOnly(True)
        self.trans_box.setObjectName("HeText")
        self.trans_box.setFixedHeight(190)
        he_l.addWidget(self.trans_box)
        rl.addWidget(he_card)

        # Changes (hidden until UPDATE)
        self.diff_card = QFrame()
        self.diff_card.setObjectName("Card")
        self.diff_layout = QVBoxLayout(self.diff_card)
        self.diff_layout.setContentsMargins(16, 12, 16, 12)
        diff_title = QLabel("Changes")
        diff_title.setObjectName("CardTitle")
        self.diff_layout.addWidget(diff_title)
        self.diff_card.setVisible(False)
        rl.addWidget(self.diff_card)

        # Log
        log_card = QFrame()
        log_card.setObjectName("Card")
        log_l = QVBoxLayout(log_card)
        log_l.setContentsMargins(16, 12, 16, 12)
        log_title = QLabel("Log")
        log_title.setObjectName("CardTitle")
        log_l.addWidget(log_title)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setObjectName("LogBox")
        self.log_box.setFixedHeight(120)
        log_l.addWidget(self.log_box)
        rl.addWidget(log_card)
        rl.addStretch()

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([700, 720])
        outer.addWidget(splitter, 1)

        # ── Action bar ─────────────────────────────────────────────────────────
        action_card = QFrame()
        action_card.setObjectName("ActionCard")
        bar = QHBoxLayout(action_card)
        bar.setContentsMargins(20, 12, 20, 12)
        bar.setSpacing(12)

        self.status_label = QLabel("Starting up…")
        self.status_label.setObjectName("StatusLbl")
        bar.addWidget(self.status_label, 1)

        self.save_btn = QPushButton("✔   Save   [S]")
        self.save_btn.setObjectName("SaveBtn")
        self.save_btn.setFixedHeight(46)
        self.save_btn.setMinimumWidth(160)
        self.save_btn.clicked.connect(self._save_decision)

        self.dup_btn = QPushButton("⊕   Insert + Duplicate   [D]")
        self.dup_btn.setObjectName("DupBtn")
        self.dup_btn.setFixedHeight(46)
        self.dup_btn.setMinimumWidth(220)
        self.dup_btn.clicked.connect(self._dup_decision)
        self.dup_btn.setCursor(Qt.PointingHandCursor)

        self.skip_btn = QPushButton("→   Skip   [N]")
        self.skip_btn.setObjectName("SkipBtn")
        self.skip_btn.setFixedHeight(46)
        self.skip_btn.setMinimumWidth(130)
        self.skip_btn.clicked.connect(self._skip_decision)

        self.quit_btn = QPushButton("✕   Quit   [Q]")
        self.quit_btn.setObjectName("QuitBtn")
        self.quit_btn.setFixedHeight(46)
        self.quit_btn.setMinimumWidth(130)
        self.quit_btn.clicked.connect(self._quit_decision)

        bar.addWidget(self.save_btn)
        bar.addWidget(self.dup_btn)
        bar.addWidget(self.skip_btn)
        bar.addWidget(self.quit_btn)
        outer.addWidget(action_card)

        self._set_review_enabled(False)

    # ── Async helpers ──────────────────────────────────────────────────────────

    def _resolve_future(self, f, v):
        if f is not None and not f.done():
            f.set_result(v)

    async def async_wait_for_decision(self):
        loop = asyncio.get_running_loop()
        self._decision_future = loop.create_future()
        return await self._decision_future

    # ── View helpers ───────────────────────────────────────────────────────────

    def _set_review_enabled(self, enabled: bool):
        for btn in (self.save_btn, self.dup_btn, self.skip_btn, self.reset_btn):
            btn.setEnabled(enabled)
        self.form.setEnabled(enabled)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _render_diffs(self, diffs):
        # Remove everything after the title (index 0)
        while self.diff_layout.count() > 1:
            item = self.diff_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        if not diffs:
            self.diff_card.setVisible(False)
            return

        self.diff_card.setVisible(True)
        for field, old, new in diffs:
            lbl = QLabel(
                f"<span style='font-weight:700;color:{P_TXT}'>{field}</span>"
                f"<span style='color:{P_TXT3}'>&nbsp;&nbsp;{old}&nbsp;&nbsp;</span>"
                f"<span style='color:{P_TXT3}'>→</span>"
                f"<span style='color:{P_AMBER};font-weight:800'>"
                f"&nbsp;&nbsp;{new}</span>"
            )
            lbl.setTextFormat(Qt.RichText)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size:13px; padding:2px 0;")
            self.diff_layout.addWidget(lbl)

    def _score(self, parsed) -> int:
        s = 0
        if getattr(parsed, "fire", False):              s += 3
        if getattr(parsed, "hit_confirmed", False):     s += 2
        if getattr(parsed, "shutdown", False):          s += 3
        s += min(int(getattr(parsed, "explosions_reported", 0) or 0), 5)
        if getattr(parsed, "primary_attack", False):    s += 2
        if getattr(parsed, "air_defense_active", False): s += 1
        s += {"high":3,"medium":2,"low":1}.get(
            getattr(parsed, "damage_level", ""), 0)
        s += {"official_confirmation":2,"direct_report":1}.get(
            getattr(parsed, "report_type", ""), 0)
        return s

    def set_status(self, text: str):
        self.status_label.setText(text)

    def append_log(self, text: str):
        self.log_box.appendPlainText(text)
        c = self.log_box.textCursor()
        c.movePosition(QTextCursor.End)
        self.log_box.setTextCursor(c)

    def load_candidate(self, action, parsed, original_text,
                       translated_text="", diffs=None, counter=0):
        self.current_action_name = action
        self.result_action = None
        self.result_parsed = None

        # Info tiles
        self.tile_action.set_value(action)
        self.tile_counter.set_value(f"#{counter}" if counter else "—")
        self.tile_date.set_value(str(getattr(parsed, "attack_date", "")))
        self.tile_area.set_value(str(getattr(parsed, "area", "")))
        self.tile_targets.set_value(str(getattr(parsed, "target_type", "")))
        self.tile_score.set_value(str(self._score(parsed)))
        self.tile_damage.set_value(str(getattr(parsed, "damage_level", "")))
        self.tile_action.set_accent(P_GREEN if action == "INSERT" else P_AMBER)

        # Signal pills
        self._pill_fire.set_value(bool(getattr(parsed, "fire", False)))
        self._pill_shutdown.set_value(bool(getattr(parsed, "shutdown", False)))
        self._pill_hit.set_value(bool(getattr(parsed, "hit_confirmed", False)))
        self._pill_airdef.set_value(bool(getattr(parsed, "air_defense_active", False)))
        self._pill_combined.set_value(bool(getattr(parsed, "combined_strike", False)))
        self._pill_primary.set_value(bool(getattr(parsed, "primary_attack", False)))
        expl = int(getattr(parsed, "explosions_reported", 0) or 0)
        self._expl_lbl.setText(
            f"💥  {expl} explosion{'s' if expl != 1 else ''}" if expl
            else "Explosions: 0")

        # Display panels
        self._render_diffs(diffs or [])
        self.msg_box.setPlainText(snippet(original_text or ""))
        self.trans_box.setPlainText(snippet(translated_text) if translated_text else "")

        # Form
        self.form.load_parsed(parsed)
        self.save_btn.setText(
            "✔   Save   [S]" if action == "INSERT" else "✔   Update   [S]")
        self.dup_btn.setVisible(action == "INSERT")
        self._set_review_enabled(True)
        self.save_btn.setFocus()
        self.set_status(
            f"#{counter}  ·  {action}  ·  "
            + ("S = save  ·  D = insert+dup  ·  N = skip  ·  R = reset  ·  Q = quit"
               if action == "INSERT"
               else "S = save  ·  N = skip  ·  R = reset  ·  Q = quit"))
        self.append_log(
            f"[{action}] #{counter}  "
            f"{getattr(parsed, 'target_type', '')}")
        self.dup_btn.setEnabled(action == "INSERT")
    # ── Decision callbacks ─────────────────────────────────────────────────────

    def _resolve_decision(self, code: str, obj=None):
        self.result_action = code
        self.result_parsed = obj
        self._resolve_future(self._decision_future, (code, obj))

    def _save_decision(self):
        if not self.save_btn.isEnabled():
            return
        try:
            parsed = self.form.build_parsed_copy()

            if (self.current_action_name == "INSERT"
                    and str(getattr(parsed, "area", "")).strip().lower()
                    in ("", "unknown area")):
                reply = QMessageBox.question(
                    self, "Area not set",
                    "The area is still 'Unknown Area'.\n\nSave anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
                )
                if reply != QMessageBox.Yes:
                    self.set_status("Save cancelled — please fill in the area.")
                    return

            self.set_status("Saving…")
            self._resolve_decision("y", parsed)
        except Exception as exc:
            QMessageBox.critical(self, "Validation error", str(exc))

    def _dup_decision(self):
        if not self.dup_btn.isEnabled() or not self.dup_btn.isVisible():
            return
        try:
            parsed = self.form.build_parsed_copy()

            if (str(getattr(parsed, "area", "")).strip().lower()
                    in ("", "unknown area")):
                reply = QMessageBox.question(
                    self, "Area not set",
                    "The area is still 'Unknown Area'.\n\nSave anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
                )
                if reply != QMessageBox.Yes:
                    self.set_status("Save cancelled — please fill in the area.")
                    return

            self.set_status("Inserting (duplicate)…")
            self._resolve_decision("yd", parsed)
        except Exception as exc:
            QMessageBox.critical(self, "Validation error", str(exc))

    def _skip_decision(self):
        if not self.skip_btn.isEnabled():
            return
        self.set_status("Skipped.")
        self._resolve_decision("n", None)

    def _quit_decision(self):
        self.set_status("Quitting…")
        self._resolve_decision("q", None)

    def _reset_form(self):
        if not self.reset_btn.isEnabled():
            return
        self.form.reset_to_original()
        self.set_status("Form reset to original values.")

    def closeEvent(self, event):
        self._resolve_decision("q", None)
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
#  Stylesheet
# ─────────────────────────────────────────────────────────────────────────────

def _stylesheet() -> str:
    return f"""

    /* ── Base ── */
    QWidget {{
        background: {P_BG};
        color: {P_TXT};
        font-family: "Segoe UI", "Inter", "Helvetica Neue", sans-serif;
        font-size: 14px;
    }}
    QMainWindow {{ background: {P_BG}; }}

    /* ── Cards ── */
    QFrame#Card, QFrame#HeCard, QFrame#ActionCard {{
        background: {P_CARD};
        border: 2px solid {P_BORDER};
        border-radius: 14px;
    }}
    QFrame#HeCard {{
        border-color: {P_INDIGO_L};
        border-width: 2px;
    }}
    QFrame#ActionCard {{
        border-top: 2px solid {P_BORDER};
        border-radius: 14px;
    }}
    QFrame#PillRow {{
        background: {P_CARD};
        border: 2px solid {P_BORDER};
        border-radius: 12px;
    }}
    QLabel#CardTitle {{
        font-size: 11px;
        font-weight: 800;
        color: {P_TXT3};
        letter-spacing: 0.8px;
        margin-bottom: 6px;
    }}

    /* ── Info tiles ── */
    QFrame#InfoTile {{
        background: {P_CARD};
        border: 2px solid {P_BORDER};
        border-radius: 12px;
    }}
    QLabel#TileLabel {{
        font-size: 10px;
        font-weight: 800;
        color: {P_TXT3};
        letter-spacing: 0.5px;
    }}
    QLabel#TileValue {{
        font-size: 16px;
        font-weight: 800;
        color: {P_TXT};
    }}

    /* ── Text inputs (area / target combos) ── */
    QComboBox, QDateEdit, QSpinBox {{
        background: {P_BG};
        border: 2px solid {P_BORDER};
        border-radius: 9px;
        padding: 5px 10px;
        font-size: 14px;
        font-weight: 600;
        color: {P_TXT};
    }}
    QComboBox:focus, QDateEdit:focus, QSpinBox:focus {{
        border-color: {P_INDIGO};
        background: white;
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: {P_CARD};
        border: 2px solid {P_BORDER};
        border-radius: 10px;
        padding: 4px;
        font-size: 14px;
        selection-background-color: {P_INDIGO_L};
        selection-color: {P_INDIGO_D};
    }}

    /* ── Text display areas ── */
    QPlainTextEdit {{
        background: {P_BG};
        border: 2px solid {P_BORDER};
        border-radius: 10px;
        padding: 10px;
        font-size: 14px;
        color: {P_TXT};
        line-height: 1.5;
    }}
    QPlainTextEdit#HeText {{
        font-size: 15px;
        font-weight: 500;
        line-height: 1.6;
        background: {P_INDIGO_L};
        border-color: #c7d2fe;
    }}
    QPlainTextEdit#LogBox {{
        font-family: "Consolas", "Courier New", monospace;
        font-size: 12px;
        color: {P_TXT2};
        background: #f8fafc;
    }}

    /* ── Scroll ── */
    QScrollArea#FormScroll {{ border: none; background: transparent; }}
    QScrollBar:vertical {{
        background: {P_BG}; width: 8px; border-radius: 4px;
    }}
    QScrollBar::handle:vertical {{
        background: {P_BORDER}; border-radius: 4px; min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {P_TXT3}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

    /* ── Splitter ── */
    QSplitter::handle {{ background: {P_BORDER}; width: 2px; }}

    /* ── Default button (fallback) ── */
    QPushButton {{
        background: {P_SLATE_L};
        color: {P_SLATE};
        border: 2px solid {P_BORDER};
        border-radius: 9px;
        padding: 7px 18px;
        font-size: 13px;
        font-weight: 700;
    }}
    QPushButton:hover   {{ background: #d4dce8; }}
    QPushButton:pressed {{ background: #c2ccd8; }}
    QPushButton:disabled {{
        background: {P_BG}; color: {P_TXT3}; border-color: {P_BORDER};
    }}

    /* ── Save (green gradient) ── */
    QPushButton#SaveBtn {{
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
            stop:0 #34d399, stop:1 {P_GREEN});
        color: white; border: none;
        border-radius: 11px; font-size: 15px; font-weight: 800;
    }}
    QPushButton#SaveBtn:hover {{
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
            stop:0 #4ade80, stop:1 #16a34a);
    }}
    QPushButton#SaveBtn:pressed {{ background: {P_GREEN_D}; }}
    QPushButton#SaveBtn:disabled {{
        background: {P_GREEN_L}; color: #9dd9bc; border: none;
    }}

    /* ── Insert + Duplicate (amber) ── */
    QPushButton#DupBtn {{
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
            stop:0 #fbbf24, stop:1 {P_AMBER});
        color: white; border: none;
        border-radius: 11px; font-size: 15px; font-weight: 800;
    }}
    QPushButton#DupBtn:hover {{
        background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
            stop:0 #fcd34d, stop:1 #b45309);
    }}
    QPushButton#DupBtn:pressed {{ background: #92400e; }}
    QPushButton#DupBtn:disabled {{
        background: {P_AMBER_L}; color: #d4a054; border: none;
    }}

    /* ── Skip (indigo outline) ── */
    QPushButton#SkipBtn {{
        background: {P_INDIGO_L};
        color: {P_INDIGO_D};
        border: 2px solid {P_INDIGO};
        border-radius: 11px; font-size: 15px; font-weight: 800;
    }}
    QPushButton#SkipBtn:hover   {{ background: #e0e7ff; }}
    QPushButton#SkipBtn:pressed {{ background: #c7d2fe; }}
    QPushButton#SkipBtn:disabled {{
        background: {P_BG}; color: {P_TXT3}; border-color: {P_BORDER};
    }}

    /* ── Quit (soft red outline) ── */
    QPushButton#QuitBtn {{
        background: {P_RED_L};
        color: {P_RED};
        border: 2px solid #fca5a5;
        border-radius: 11px; font-size: 15px; font-weight: 800;
    }}
    QPushButton#QuitBtn:hover   {{ background: #fee2e2; }}
    QPushButton#QuitBtn:pressed {{ background: #fecaca; }}

    /* ── Reset (small, quiet) ── */
    QPushButton#ResetBtn {{
        background: transparent;
        color: {P_TXT2};
        border: 2px solid {P_BORDER};
        border-radius: 8px;
        font-size: 12px;
        font-weight: 700;
        padding: 5px 16px;
    }}
    QPushButton#ResetBtn:hover {{ background: {P_SLATE_L}; }}

    /* ── Status label ── */
    QLabel#StatusLbl {{
        font-size: 13px; font-weight: 700; color: {P_TXT2};
    }}

    /* ── Explosions label ── */
    QLabel#ExplLabel {{
        font-size: 13px; font-weight: 700; color: {P_TXT2};
        padding: 0 8px;
    }}

    /* ── Calendar ── */
    QCalendarWidget {{ background: {P_CARD}; }}
    QCalendarWidget QToolButton {{
        color: {P_TXT}; font-weight: 700; padding: 4px 8px;
    }}
    QCalendarWidget QAbstractItemView:enabled {{
        font-size: 13px; color: {P_TXT};
        selection-background-color: {P_INDIGO};
        selection-color: white;
    }}
    """


# ─────────────────────────────────────────────────────────────────────────────
#  Singleton window
# ─────────────────────────────────────────────────────────────────────────────

_main_window = None


def get_main_window() -> ReviewMainWindow:
    global _main_window
    if _main_window is None:
        get_app()
        _main_window = ReviewMainWindow()
        _main_window.show()
    return _main_window
