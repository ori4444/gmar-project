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
    def __init__(self, default_start: date, default_end: date, parent=None):
        super().__init__(parent)
        self.setWindowTitle("גמר — בחירת תאריכים")
        self.setFixedSize(500, 360)
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
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

        title = QLabel("בדיקת תקיפות")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"font-size:22px; font-weight:900; color:{P.TXT}; "
            f"margin-bottom:4px;"
        )
        root.addWidget(title)

        sub = QLabel("בחרו טווח תאריכים לבדיקה")
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
            ("מתאריך", "start_edit", start),
            ("עד תאריך",   "end_edit",   end),
        ]):
            lbl = QLabel(lbl_text)
            lbl.setStyleSheet(
                f"font-size:15px; font-weight:700; color:{P.TXT2};"
            )
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            w = QDateEdit()
            w.setCalendarPopup(True)
            w.setDisplayFormat("dd  MMM  yyyy")
            w.setDate(_to_qdate(val))
            w.setFixedHeight(44)
            setattr(self, attr, w)
            card_lay.addWidget(lbl, row, 1)
            card_lay.addWidget(w,   row, 0)

        root.addWidget(card)
        root.addSpacing(20)

        # Go button
        go = QPushButton("התחלת בדיקה  ←")
        go.setObjectName("GoBtn")
        go.setFixedHeight(52)
        go.clicked.connect(self._on_go)
        go.setDefault(True)
        root.addWidget(go)

    def _on_go(self):
        s = _from_qdate(self.start_edit.date())
        e = _from_qdate(self.end_edit.date())
        if s > e:
            QMessageBox.warning(self, "שגיאה", "תאריך ההתחלה חייב להיות לפני תאריך הסיום.")
            return
        self.accept()

    def options(self):
        return (
            "manual",
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


def show_startup_dialog(default_start: date, default_end: date):
    dlg = StartupDialog(default_start, default_end)
    if dlg.exec() == QDialog.Accepted:
        return dlg.options()
    return None
