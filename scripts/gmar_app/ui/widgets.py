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
        parent=None,
    ):
        super().__init__(parent)
        self._options = options
        self._labels  = labels  or {}
        self._accents = accents or {}
        self._value   = options[0] if options else None
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
            row.addWidget(btn)

        row.addStretch()
        self._refresh()

    def _select(self, value: str):
        self._value = value
        self._refresh()
        self.value_changed.emit(value)

    def _refresh(self):
        for opt, btn in self._btns.items():
            accent = self._accents.get(opt, P.INDIGO)
            if opt == self._value:
                btn.setStyleSheet(
                    f"QPushButton{{background:{accent};color:#fff;"
                    f"border:3px solid {accent};border-radius:8px;"
                    f"font-size:13px;font-weight:800;padding:0 14px;}}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:{P.CARD_BG};color:{P.TXT2};"
                    f"border:2px solid {P.CARD_BORDER};border-radius:8px;"
                    f"font-size:13px;font-weight:600;padding:0 14px;}}"
                    f"QPushButton:hover{{border-color:{accent};color:{accent};}}"
                )

    def python_value(self) -> str:
        return self._value

    def set_python_value(self, v: str):
        if v in self._options:
            self._value = v
        else:
            self._value = self._options[0] if self._options else None
        self._refresh()


# ─────────────────────────────────────────────────────────────────────────────
#  BoolToggle — YES / NO / — nullable boolean
# ─────────────────────────────────────────────────────────────────────────────

class BoolToggle(QWidget):
    """Three-state toggle: True (YES), False (NO), None (—)."""

    value_changed = Signal(object)

    def __init__(self, nullable: bool = True, parent=None):
        super().__init__(parent)
        self._nullable = nullable
        self._value = None

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        configs = [
            (True,  "YES", P.GREEN,  P.GREEN_D),
            (False, "NO",  P.RED,    P.RED_D),
        ]
        if nullable:
            configs.append((None, "—", P.TXT3, P.TXT3))

        self._btns: dict = {}
        for val, lbl, color, hover in configs:
            btn = QPushButton(lbl)
            btn.setFixedHeight(34)
            btn.setFixedWidth(56)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, v=val: self._select(v))
            self._btns[val] = (btn, color)
            row.addWidget(btn)

        row.addStretch()
        self._refresh()

    def _select(self, v):
        self._value = v
        self._refresh()
        self.value_changed.emit(v)

    def _refresh(self):
        for val, (btn, color) in self._btns.items():
            if val == self._value:
                btn.setStyleSheet(
                    f"QPushButton{{background:{color};color:#fff;"
                    f"border:3px solid {color};border-radius:8px;"
                    f"font-size:13px;font-weight:800;}}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:{P.CARD_BG};color:{P.TXT2};"
                    f"border:2px solid {P.CARD_BORDER};border-radius:8px;"
                    f"font-size:13px;font-weight:600;}}"
                    f"QPushButton:hover{{border-color:{color};color:{color};}}"
                )

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

    def __init__(self, options: list[str], parent=None):
        super().__init__(parent)
        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.NoInsert)
        self._combo.addItems(options)
        self._combo.setFixedHeight(36)

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
                border:2px solid {P.INPUT_BORDER}; border-radius:8px;
                padding:4px 10px; font-size:13px; font-weight:600;
            }}
            QComboBox:focus {{ border:3px solid {P.INPUT_FOCUS}; }}
            QComboBox::drop-down {{ border:none; width:24px; }}
            QComboBox QAbstractItemView {{
                background:{P.CARD_BG}; color:{P.TXT};
                selection-background-color:{P.INDIGO};
                border:2px solid {P.CARD_BORDER};
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
