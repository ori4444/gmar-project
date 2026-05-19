"""
Reusable custom widgets for the unified Gmar app.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QCompleter, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from . import palette as P


# ─────────────────────────────────────────────────────────────────────────────
#  ButtonSelector — mutually exclusive toggle-button row
# ─────────────────────────────────────────────────────────────────────────────

class ButtonSelector(QWidget):
    """Row of mutually-exclusive toggle buttons; accent per value."""

    value_changed = Signal(str)

    def __init__(
        self,
        options: list[str],
        labels: dict[str, str] | None = None,
        accents: dict[str, str] | None = None,
        nullable: bool = False,
        stretch: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._options  = options
        self._labels   = labels  or {}
        self._accents  = accents or {}
        self._nullable = nullable
        self._value    = None if nullable else (options[0] if options else None)
        self._btns: dict[str, QPushButton] = {}

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        for opt in options:
            display = self._labels.get(opt) or (
                opt.replace("_", " ").title() if opt else "—"
            )
            btn = QPushButton(display)
            btn.setFixedHeight(34)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, v=opt: self._select(v))
            self._btns[opt] = btn
            row.addWidget(btn, 1 if stretch else 0)

        if not stretch:
            row.addStretch()
        self._refresh()

    def _select(self, value: str):
        if self._nullable and self._value == value:
            self._value = None
        else:
            self._value = value
        self._refresh()
        self.value_changed.emit(self._value or "")

    def _refresh(self):
        for opt, btn in self._btns.items():
            accent = self._accents.get(opt, P.INDIGO)
            if opt == self._value:
                btn.setStyleSheet(
                    f"QPushButton{{background:transparent;color:{accent};"
                    f"border:2px solid {accent};border-radius:8px;"
                    f"font-size:13px;font-weight:800;padding:0 14px;}}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:transparent;color:{P.TXT2};"
                    f"border:2px solid {P.TXT3};border-radius:8px;"
                    f"font-size:13px;font-weight:600;padding:0 14px;}}"
                    f"QPushButton:hover{{border-color:{accent};color:{accent};}}"
                )

    def python_value(self) -> str | None:
        return self._value

    def set_python_value(self, v: str | None):
        if v in self._options:
            self._value = v
        else:
            self._value = None if self._nullable else (self._options[0] if self._options else None)
        self._refresh()


# ─────────────────────────────────────────────────────────────────────────────
#  MultiButtonSelector — independent multi-toggle button row
# ─────────────────────────────────────────────────────────────────────────────

class MultiButtonSelector(QWidget):
    """Grid of independently toggleable buttons; value is ' | '-joined selected set."""

    value_changed = Signal(str)

    def __init__(
        self,
        options: list[str],
        labels: dict[str, str] | None = None,
        accents: dict[str, str] | None = None,
        rows: int = 1,
        btn_height: int = 34,
        stretch: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._options  = options
        self._labels   = labels  or {}
        self._accents  = accents or {}
        self._selected: set[str] = set()
        self._btns: dict[str, QPushButton] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        per_row = -(-len(options) // rows)  # ceiling division
        chunks = [options[i:i + per_row] for i in range(0, len(options), per_row)]

        for chunk in chunks:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            for opt in chunk:
                display = self._labels.get(opt) or opt.replace("_", " ").title()
                btn = QPushButton(display)
                btn.setFixedHeight(btn_height)
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda _, v=opt: self._toggle(v))
                self._btns[opt] = btn
                row.addWidget(btn, 1 if stretch else 0)
            if not stretch:
                row.addStretch()
            outer.addLayout(row)

        self._refresh()

    def _toggle(self, value: str):
        if value in self._selected:
            self._selected.discard(value)
        else:
            self._selected.add(value)
        self._refresh()
        self.value_changed.emit(self.python_value())

    def _refresh(self):
        for opt, btn in self._btns.items():
            accent = self._accents.get(opt, P.INDIGO)
            if opt in self._selected:
                btn.setStyleSheet(
                    f"QPushButton{{background:transparent;color:{accent};"
                    f"border:2px solid {accent};border-radius:8px;"
                    f"font-size:13px;font-weight:800;padding:0 14px;}}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:transparent;color:{P.TXT2};"
                    f"border:2px solid {P.TXT3};border-radius:8px;"
                    f"font-size:13px;font-weight:600;padding:0 14px;}}"
                    f"QPushButton:hover{{border-color:{accent};color:{accent};}}"
                )

    def python_value(self) -> str:
        ordered = [o for o in self._options if o in self._selected]
        return " | ".join(ordered)

    def set_python_value(self, v: str):
        parts = {p.strip() for p in v.split(" | ") if p.strip()} if v else set()
        self._selected = parts & set(self._options)
        self._refresh()


# ─────────────────────────────────────────────────────────────────────────────
#  BoolToggle — single cycling circular button: None → True → False → None
# ─────────────────────────────────────────────────────────────────────────────

class BoolToggle(QWidget):
    """Single circular cycling button: None → True (green border) → False (red border) → None."""

    value_changed = Signal(object)

    _CYCLE  = [None, True, False]
    _COLORS = {None: None, True: P.GREEN, False: P.RED}
    _SIZE   = 26

    def __init__(self, nullable: bool = True, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._nullable = nullable
        self._value = None

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self._btn = QPushButton()
        self._btn.setFixedSize(self._SIZE, self._SIZE)
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.clicked.connect(self._cycle)
        row.addWidget(self._btn)
        row.addStretch()

        self._refresh()

    def _cycle(self):
        cycle = self._CYCLE if self._nullable else [True, False]
        idx = cycle.index(self._value) if self._value in cycle else 0
        self._value = cycle[(idx + 1) % len(cycle)]
        self._refresh()
        self.value_changed.emit(self._value)

    def _refresh(self):
        r = self._SIZE // 2
        color = self._COLORS[self._value]
        if color is None:
            self._btn.setStyleSheet(
                f"QPushButton{{background:transparent;"
                f"border:2px solid {P.TXT3};border-radius:{r}px;}}"
                f"QPushButton:hover{{border-color:{P.TXT2};}}"
            )
        else:
            self._btn.setStyleSheet(
                f"QPushButton{{background:transparent;"
                f"border:2px solid {color};border-radius:{r}px;}}"
                f"QPushButton:hover{{border-color:{color}cc;}}"
            )
        self._btn.setText("")

    def python_value(self):
        return self._value

    def set_python_value(self, v):
        self._value = v
        self._refresh()


# ─────────────────────────────────────────────────────────────────────────────
#  SmartCombo — editable combobox with autocomplete
# ─────────────────────────────────────────────────────────────────────────────

class SmartCombo(QWidget):
    """Editable combo with case-insensitive prefix completer."""

    value_changed = Signal(str)

    def __init__(self, options: list[str], height: int = 36, parent=None):
        super().__init__(parent)
        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.NoInsert)
        self._combo.addItems(options)
        self._combo.setFixedHeight(height)

        completer = QCompleter(options)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self._combo.setCompleter(completer)

        self._combo.currentTextChanged.connect(self.value_changed.emit)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._combo)

        self._apply_style()

    def _apply_style(self):
        self._combo.setStyleSheet(f"""
            QComboBox {{
                background:{P.INPUT_BG}; color:{P.TXT};
                border:1px solid {P.INPUT_BORDER}; border-radius:8px;
                padding:4px 10px; font-size:13px; font-weight:600;
            }}
            QComboBox:focus {{ border:2px solid {P.INPUT_FOCUS}; }}
            QComboBox::drop-down {{ border:none; width:24px; }}
            QComboBox QAbstractItemView {{
                background:{P.BG}; color:{P.TXT};
                selection-background-color:{P.INDIGO}; selection-color:#fff;
                border:1px solid {P.DIVIDER};
            }}
        """)

    def python_value(self) -> str:
        return self._combo.currentText().strip()

    def set_python_value(self, v: str):
        idx = self._combo.findText(v, Qt.MatchFixedString | Qt.MatchCaseSensitive)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        else:
            self._combo.setCurrentText(v)


# ─────────────────────────────────────────────────────────────────────────────
#  CircularSpinner — circular number display + separate +/− buttons
# ─────────────────────────────────────────────────────────────────────────────

class CircularSpinner(QWidget):
    """Circular count display with separate +/− buttons, no backgrounds."""

    value_changed = Signal(int)

    _DISPLAY_SIZE = 34
    _STEP_SIZE    = 20

    def __init__(self, min_val: int = 0, max_val: int = 999, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._value   = 0
        self._min_val = min_val
        self._max_val = max_val

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        r_d = self._DISPLAY_SIZE // 2
        r_s = self._STEP_SIZE // 2

        self._display = QPushButton("0")
        self._display.setFixedSize(self._DISPLAY_SIZE, self._DISPLAY_SIZE)
        self._display.setCursor(Qt.PointingHandCursor)
        self._display.setStyleSheet(
            f"QPushButton{{background:transparent;color:{P.TXT};"
            f"border:2px solid {P.TXT3};border-radius:{r_d}px;"
            f"font-size:13px;font-weight:800;}}"
            f"QPushButton:hover{{border-color:{P.TXT2};}}"
        )
        self._display.clicked.connect(self._reset)

        def _step_btn(symbol: str) -> QPushButton:
            b = QPushButton(symbol)
            b.setFixedSize(self._STEP_SIZE, self._STEP_SIZE)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:transparent;color:{P.TXT2};"
                f"border:none;border-radius:{r_s}px;"
                f"font-size:14px;font-weight:900;padding:0;}}"
                f"QPushButton:hover{{color:{P.TXT};}}"
            )
            return b

        self._dec = _step_btn("−")
        self._inc = _step_btn("+")
        self._dec.clicked.connect(self._decrement)
        self._inc.clicked.connect(self._increment)

        # + above −, stacked to the right of the circle.
        # A left spacer mirrors the button column width so the circle stays
        # centered in the widget and lands directly under its label.
        btn_col = QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(2)
        btn_col.addWidget(self._inc)
        btn_col.addWidget(self._dec)

        left_spacer = QWidget()
        left_spacer.setFixedWidth(self._STEP_SIZE + lay.spacing())
        left_spacer.setAttribute(Qt.WA_TranslucentBackground)

        lay.addWidget(left_spacer)
        lay.addWidget(self._display)
        lay.addLayout(btn_col)

    def _increment(self):
        if self._value < self._max_val:
            self._value += 1
            self._refresh()
            self.value_changed.emit(self._value)

    def _decrement(self):
        if self._value > self._min_val:
            self._value -= 1
            self._refresh()
            self.value_changed.emit(self._value)

    def _reset(self):
        self._value = 0
        self._refresh()
        self.value_changed.emit(self._value)

    def _refresh(self):
        self._display.setText(str(self._value))

    def value(self) -> int:
        return self._value

    def setValue(self, v: int):
        self._value = max(self._min_val, min(self._max_val, v))
        self._refresh()


# ─────────────────────────────────────────────────────────────────────────────
#  SectionLabel — bold uppercase section header
# ─────────────────────────────────────────────────────────────────────────────

class SectionLabel(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setStyleSheet(
            f"color:{P.TXT3}; font-size:10px; font-weight:800; "
            f"letter-spacing:2px; padding:12px 0 4px 0;"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  FieldRow — label + widget pair
# ─────────────────────────────────────────────────────────────────────────────

def field_row(label: str, widget: QWidget) -> QWidget:
    """Returns a QWidget containing a right-aligned Hebrew label + editor widget."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(3)

    lbl = QLabel(label)
    lbl.setStyleSheet(f"color:{P.TXT2}; font-size:12px; font-weight:700;")
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

    lay.addWidget(lbl)
    lay.addWidget(widget)
    return w
