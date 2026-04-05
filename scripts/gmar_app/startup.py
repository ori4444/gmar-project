"""
Startup dialog — choose date range and review mode before the async loop starts.
Pure Qt, blocking QDialog.exec().
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QDateEdit,
)

from .ui import palette as P


def _to_qdate(v: date) -> QDate:
    return QDate(v.year, v.month, v.day)


def _from_qdate(v: QDate) -> date:
    return date(v.year(), v.month(), v.day())


class StartupDialog(QDialog):
    def __init__(self, default_start: date, default_end: date,
                 default_mode: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gmar — בחר תאריכים")
        self.setFixedSize(500, 440)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self._mode = default_mode
        self._build(default_start, default_end)
        self.setStyleSheet(self._css())

    def _build(self, start: date, end: date):
        root = QVBoxLayout(self)
        root.setContentsMargins(44, 36, 44, 36)
        root.setSpacing(0)

        # Logo + title
        logo = QLabel("G")
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet(
            f"color:{P.INDIGO}; font-size:48px; font-weight:900; "
            f"margin-bottom:4px;"
        )
        root.addWidget(logo)

        title = QLabel("סקירת תקיפות")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"font-size:22px; font-weight:900; color:{P.TXT}; "
            f"margin-bottom:4px; direction: rtl;"
        )
        root.addWidget(title)

        sub = QLabel("בחר את טווח התאריכים לסקירה")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"font-size:13px; color:{P.TXT2}; margin-bottom:24px;")
        root.addWidget(sub)

        # Date card
        card = QFrame()
        card.setObjectName("StartCard")
        card_lay = QGridLayout(card)
        card_lay.setContentsMargins(24, 20, 24, 20)
        card_lay.setHorizontalSpacing(16)
        card_lay.setVerticalSpacing(14)

        for row, (lbl_text, attr, val) in enumerate([
            ("מ",  "start_edit", start),
            ("עד", "end_edit",   end),
        ]):
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(
                f"font-size:15px; font-weight:700; color:{P.TXT2}; "
                f"direction:rtl;"
            )
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            w = QDateEdit()
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd  MMM  yyyy")
            w.setDate(_to_qdate(val))
            w.setFixedHeight(44)
            setattr(self, attr, w)
            card_lay.addWidget(lbl, row, 0)
            card_lay.addWidget(w,   row, 1)

        root.addWidget(card)
        root.addSpacing(16)

        # Mode toggle
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        self._mode_btns: dict[str, QPushButton] = {}
        for val, label in [("manual", "סקירה ידנית"), ("blind", "הכנסה אוטומטית")]:
            btn = QPushButton(label)
            btn.setFixedHeight(44)
            btn.clicked.connect(lambda _, v=val: self._set_mode(v))
            self._mode_btns[val] = btn
            mode_row.addWidget(btn)
        root.addLayout(mode_row)
        root.addSpacing(20)
        self._refresh_mode()

        # Go button
        go = QPushButton("התחל סקירה  →")
        go.setObjectName("GoBtn")
        go.setFixedHeight(52)
        go.clicked.connect(self._on_go)
        go.setDefault(True)
        root.addWidget(go)

    def _set_mode(self, val: str):
        self._mode = val
        self._refresh_mode()

    def _refresh_mode(self):
        for val, btn in self._mode_btns.items():
            if val == self._mode:
                btn.setStyleSheet(
                    f"QPushButton{{background:{P.INDIGO};color:white;"
                    f"border:3px solid {P.INDIGO};border-radius:10px;"
                    f"font-size:14px;font-weight:800;padding:0 16px;}}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:{P.CARD_BG};color:{P.TXT2};"
                    f"border:2px solid {P.CARD_BORDER};border-radius:10px;"
                    f"font-size:14px;font-weight:600;padding:0 16px;}}"
                    f"QPushButton:hover{{border-color:{P.INDIGO};color:{P.INDIGO};}}"
                )

    def _on_go(self):
        s = _from_qdate(self.start_edit.date())
        e = _from_qdate(self.end_edit.date())
        if s > e:
            QMessageBox.warning(self, "שגיאה", "תאריך ההתחלה חייב להיות לפני תאריך הסיום.")
            return
        self.accept()

    def options(self):
        return (
            self._mode,
            _from_qdate(self.start_edit.date()),
            _from_qdate(self.end_edit.date()),
        )

    @staticmethod
    def _css() -> str:
        return f"""
            QDialog   {{ background:{P.BG}; color:{P.TXT}; }}
            QFrame#StartCard {{
                background:{P.CARD_BG}; border:3px solid {P.CARD_BORDER};
                border-radius:16px;
            }}
            QDateEdit {{
                background:{P.INPUT_BG}; border:2px solid {P.INPUT_BORDER};
                border-radius:9px; padding:6px 12px;
                font-size:15px; font-weight:700; color:{P.TXT};
            }}
            QDateEdit:focus {{ border:3px solid {P.INPUT_FOCUS}; }}
            QDateEdit::drop-down {{ border:none; }}
            QPushButton#GoBtn {{
                background:{P.INDIGO}; color:white;
                border:none; border-radius:12px;
                font-size:17px; font-weight:800;
                direction: rtl;
            }}
            QPushButton#GoBtn:hover  {{ background:{P.INDIGO_D}; }}
            QCalendarWidget {{ background:{P.CARD_BG}; color:{P.TXT}; }}
            QCalendarWidget QAbstractItemView:enabled {{
                font-size:13px; color:{P.TXT};
                selection-background-color:{P.INDIGO};
                selection-color:white;
            }}
        """


def show_startup_dialog(default_start: date, default_end: date, default_mode: str):
    dlg = StartupDialog(default_start, default_end, default_mode)
    if dlg.exec() == QDialog.Accepted:
        return dlg.options()
    return None
